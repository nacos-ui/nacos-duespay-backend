import json
import logging
from decimal import Decimal
from datetime import datetime, timedelta

from django.conf import settings
from django.db import models
from django.http import HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from association.models import Association, Session
from payers.models import Payer
from payments.models import PaymentItem, ReceiverBankAccount
from transactions.models import Transaction

print("DEBUG: Starting to import paystackServices")

from .ercaspayServices import (
    ercaspay_init_payment,
    verify_ercaspay_transaction
)
from .models import Transaction, TransactionReceipt
from .serializers import TransactionReceiptDetailSerializer, TransactionSerializer

logger = logging.getLogger(__name__)

class TransactionPagination(PageNumberPagination):
    page_size = 7  
    page_size_query_param = 'page_size'  
    max_page_size = 1000

class TransactionViewSet(viewsets.ModelViewSet):
    queryset = Transaction.objects.all()
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TransactionPagination

    def get_queryset(self):
        association = getattr(self.request.user, "association", None)
        queryset = Transaction.objects.none()

        if association:
            # Get session_id from query params or use current session
            session_id = self.request.query_params.get("session_id")

            if session_id:
                # Validate that session belongs to this association
                try:
                    session = Session.objects.get(
                        id=session_id, association=association
                    )
                    queryset = Transaction.objects.filter(session=session)
                except Session.DoesNotExist:
                    queryset = Transaction.objects.none()
            elif association.current_session:
                # Use current session if no session_id provided
                queryset = Transaction.objects.filter(
                    session=association.current_session
                )
            else:
                # No session available, return empty queryset
                queryset = Transaction.objects.none()

        # Filter by verification status (case-insensitive)
        status_param = self.request.query_params.get("status")
        if status_param is not None:
            if status_param.lower() == "verified":
                queryset = queryset.filter(is_verified=True)
            elif status_param.lower() == "unverified":
                queryset = queryset.filter(is_verified=False)

        # Search by payer name or reference id (case-insensitive)
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                models.Q(reference_id__icontains=search)
                | models.Q(payer__first_name__icontains=search)
                | models.Q(payer__last_name__icontains=search)
                | models.Q(payer__matric_number__icontains=search)
            )

        return queryset

    def perform_create(self, serializer):
        association = getattr(self.request.user, "association", None)
        if not association or not association.current_session:
            raise ValidationError(
                "No current session available. Please create a session first."
            )

        serializer.save(
            payer=self.request.user.payer,
            association=association,
            session=association.current_session,  # Auto-assign current session
        )

    def list(self, request, *args, **kwargs):
        # Check if association has a current session
        association = getattr(self.request.user, "association", None)
        if not association:
            return Response(
                {"error": "No association found for user"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session_id = self.request.query_params.get("session_id")
        current_session = None

        if session_id:
            try:
                current_session = Session.objects.get(
                    id=session_id, association=association
                )
            except Session.DoesNotExist:
                return Response(
                    {
                        "error": "Session not found or does not belong to your association"
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
        elif association.current_session:
            current_session = association.current_session
        else:
            return Response(
                {
                    "error": "No session available. Please create a session first.",
                    "results": [],
                    "count": 0,
                    "next": None,
                    "previous": None,
                    "meta": {
                        "total_collections": 0,
                        "completed_payments": 0,
                        "pending_payments": 0,
                        "total_transactions": 0,
                        "percent_collections": "-",
                        "percent_completed": "-",
                        "percent_pending": "-",
                        "current_session": None,
                    },
                }
            )

        queryset = self.filter_queryset(self.get_queryset()).order_by("-submitted_at")
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = serializer.data
        else:
            serializer = self.get_serializer(queryset, many=True)
            data = serializer.data

        total_collections = (
            queryset.aggregate(total=models.Sum("amount_paid"))["total"] or 0
        )

        # Completed Payments (assuming is_verified=True means completed)
        completed_count = queryset.filter(is_verified=True).count()

        # Pending Payments (assuming is_verified=False means pending)
        pending_count = queryset.filter(is_verified=False).count()

        # Calculate percentages
        total_count = queryset.count()
        percent_completed = (
            round((completed_count / total_count * 100), 1) if total_count > 0 else 0
        )
        percent_pending = (
            round((pending_count / total_count * 100), 1) if total_count > 0 else 0
        )

        meta = {
            "total_collections": float(total_collections),
            "completed_payments": completed_count,
            "pending_payments": pending_count,
            "total_transactions": total_count,
            "percent_collections": "-",  # You can calculate this based on your business logic
            "percent_completed": f"{percent_completed}%",
            "percent_pending": f"{percent_pending}%",
            "current_session": (
                {
                    "id": current_session.id,
                    "title": current_session.title,
                    "start_date": current_session.start_date,
                    "end_date": current_session.end_date,
                    "is_active": current_session.is_active,
                }
                if current_session
                else None
            ),
        }

        if page is not None:
            paginated_response = self.get_paginated_response(data)
            response_data = paginated_response.data
            response_data["meta"] = meta
            return Response(response_data)
        else:
            return Response(
                {
                    "results": data,
                    "count": len(data),
                    "next": None,
                    "previous": None,
                    "meta": meta,
                }
            )


class TransactionReceiptDetailView(RetrieveAPIView):
    queryset = TransactionReceipt.objects.select_related(
        "transaction__payer", "transaction__association", "transaction__session"
    ).prefetch_related("transaction__payment_items")
    serializer_class = TransactionReceiptDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = "receipt_id"


class InitiatePaymentView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        data = request.data or {}
        required = ["payer_id", "association_id", "session_id", "payment_item_ids"]
        missing = [k for k in required if k not in data]
        if missing:
            return Response(
                {"error": f"Missing fields: {', '.join(missing)}"}, status=400
            )

        try:
            payer = Payer.objects.get(pk=data["payer_id"])
            association = Association.objects.get(pk=data["association_id"])
            session = Session.objects.get(
                pk=data["session_id"], association=association
            )
        except (Payer.DoesNotExist, Association.DoesNotExist, Session.DoesNotExist):
            return Response(
                {"error": "Invalid payer_id, association_id, or session_id"}, status=400
            )

        item_ids = data.get("payment_item_ids") or []
        if not isinstance(item_ids, list) or not item_ids:
            return Response(
                {"error": "payment_item_ids must be a non-empty list"}, status=400
            )
        items_qs = PaymentItem.objects.filter(id__in=item_ids, session=session)
        if items_qs.count() != len(set(item_ids)):
            return Response(
                {"error": "One or more payment items not found for the session"},
                status=400,
            )

        # Calculate total amount from payment items (base amount)
        base_amount = sum((item.amount for item in items_qs), Decimal("0.00"))

        # Ercaspay handles fees on the checkout page (Customer Bears Fees setting)
        total_with_fees = base_amount
        transaction_fee = Decimal("0.00")

        # Create pending transaction with BASE amount (what association receives)
        txn = Transaction.objects.create(
            payer=payer,
            association=association,
            amount_paid=base_amount,  # Store base amount
            is_verified=False,
            session=session,
        )
        txn.payment_items.set(items_qs)

        # Customer details - always use payer information
        full_name = f"{getattr(payer, 'first_name', '')} {getattr(payer, 'last_name', '')}".strip() or "DuesPay User"
        email = getattr(payer, "email", None) or getattr(settings, "PLATFORM_EMAIL", "justondev05@gmail.com")
        
        # Ensure valid email format
        if "@" not in str(email):
            email = getattr(settings, "PLATFORM_EMAIL", "justondev05@gmail.com")
        
        phone = getattr(payer, "phone_number", "")
        if not phone:
            phone = "0000000000"

        customer = {
            "name": full_name, 
            "email": email,
            "phone_number": phone
        }

        # Frontend redirect - Ercaspay will redirect to /pay after payment
        frontend = getattr(settings, "FRONTEND_URL", "https://nacos-duespay.vercel.app/")
        redirect_url = f"{str(frontend).rstrip('/')}/pay"

        # Metadata for reconciliation
        metadata = {
            "txn_ref": txn.reference_id,
            "association_id": association.id,
            "payer_id": payer.id,
            "base_amount": str(base_amount),
            "transaction_fee": str(transaction_fee),
            "total_amount": str(total_with_fees),
        }

        logger.info(
            f"[INITIATE] ref={txn.reference_id} base={base_amount} fee={transaction_fee} total={total_with_fees}"
        )
        print(
            f"[{timezone.now().isoformat()}] INITIATE ref={txn.reference_id} base={base_amount} fee={transaction_fee} total={total_with_fees}"
        )

        try:
            # Initialize payment with TOTAL amount (including fees)
            ercas_res = ercaspay_init_payment(
                amount=str(total_with_fees),  # Customer pays this
                currency="NGN",
                reference=txn.reference_id,
                customer=customer,
                redirect_url=redirect_url,
                metadata=metadata,
            )
        except Exception as e:
            logger.exception(
                f"[INITIATE][ERROR] ref={txn.reference_id} Ercaspay init failed: {e}"
            )
            return Response({"error": str(e)}, status=400)

        ercas_reference = ercas_res.get("data", {}).get("ercas_reference")
        if ercas_reference:
            txn.payment_provider_reference = ercas_reference
            txn.save(update_fields=["payment_provider_reference"])

        data_obj = ercas_res.get("data") or {}
        ercas_reference = data_obj.get("ercas_reference")
        
        if ercas_reference:
            txn.payment_provider_reference = ercas_reference
            txn.save(update_fields=["payment_provider_reference"])

        checkout_url = data_obj.get("authorization_url")
        if not checkout_url:
            logger.error(
                f"[INITIATE][ERROR] ref={txn.reference_id} Missing authorization_url resp={ercas_res}"
            )
            return Response(
                {
                    "error": "Ercaspay did not return an authorization URL",
                    "provider_response": ercas_res,
                },
                status=502,
            )

        logger.info(
            f"[INITIATE][OK] ref={txn.reference_id} authorization_url={checkout_url}"
        )
        
        # Return breakdown for frontend display
        return Response(
            {
                "reference_id": txn.reference_id,
                "base_amount": str(base_amount),
                "transaction_fee": str(transaction_fee),
                "total_amount": str(total_with_fees),
                "checkout_url": checkout_url,
                "ercas_reference": data_obj.get("ercas_reference"),
            },
            status=201,
        )

# New Ercaspay Webhook
@csrf_exempt
@require_http_methods(["POST"])
def ercaspay_webhook(request):
    """
    Handles Ercaspay webhook events for payment verification
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        logger.error("[ERCASPAY_WEBHOOK] invalid JSON")
        return HttpResponse(status=200)

    # Log the entire payload for debugging
    logger.info(f"[ERCASPAY_WEBHOOK] payload={payload}")
    print(f"[{timezone.now().isoformat()}] ERCASPAY_WEBHOOK payload received")

    # Important: Since documentation on webhook signature verification is sparse/assumed,
    # we will rely on strict transaction verification via the API.
    # We extract the reference and verify it directly with Ercaspay.

    # Based on provided webhook payload:
    # "transaction_reference": Ercas internal ref (e.g. ERCS|...)
    # "payment_reference": Your Merchant Ref (e.g. R5md7gd9b4s3h2xxd67g)

    tx_ref = payload.get("transaction_reference")
    pay_ref = payload.get("payment_reference")
    
    # We prioritize your merchant ref (payment_reference) if available, as that matches our local reference_id
    # But we also fall back to checking the Ercas ref against our stored provider ref
    
    if not tx_ref and not pay_ref:
        logger.warning("[ERCASPAY_WEBHOOK] No reference found in payload")
        return HttpResponse(status=200)

    try:
        # Try to find transaction by either reference
        txn = Transaction.objects.filter(
            models.Q(reference_id=pay_ref) | 
            models.Q(reference_id=tx_ref) |
            models.Q(payment_provider_reference=tx_ref)
        ).first()
        
        if not txn:
             # Last ditch effort: if pay_ref was missing, maybe it was in tx_ref field?
            logger.warning(f"[ERCASPAY_WEBHOOK] No transaction found for pay_ref={pay_ref} tx_ref={tx_ref}")
            return HttpResponse(status=200)
    except Exception as e:
        logger.error(f"[ERCASPAY_WEBHOOK] Error finding transaction: {e}")
        return HttpResponse(status=200)

    if txn.is_verified:
        logger.info(f"[ERCASPAY_WEBHOOK] Already verified ref={txn.reference_id}")
        return HttpResponse(status=200)

    # Verify transaction with Ercaspay API to be sure
    try:
        verification_result = verify_ercaspay_transaction(txn.reference_id)
        
        if not verification_result.get("status"):
            logger.warning(f"[ERCASPAY_WEBHOOK] Verification failed for ref={txn.reference_id}: {verification_result.get('message')}")
            return HttpResponse(status=200)

        data = verification_result.get("data", {})
        status_str = data.get("status", "").lower()
        
        # Check against success statuses (adjust based on actual API response, usually 'success' or 'successful')
        if status_str in ["success", "successful", "paid"]:
            # Correct amount check could go here, but we trust the verification call
            amount_paid = data.get("amount", 0)
            
            txn.is_verified = True
            txn.save(update_fields=["is_verified"])
            
            logger.info(f"[ERCASPAY_WEBHOOK][VERIFIED] ref={txn.reference_id} status={status_str} amount={amount_paid}")
            print(f"[{timezone.now().isoformat()}] ERCASPAY VERIFIED ref={txn.reference_id}")
        else:
             logger.info(f"[ERCASPAY_WEBHOOK] Transaction not successful yet ref={txn.reference_id} status={status_str}")

    except Exception as e:
        logger.error(f"[ERCASPAY_WEBHOOK] Error verifying transaction ref={txn.reference_id}: {str(e)}")

    return HttpResponse(status=200)


class PaymentStatusView(APIView):
    """
    Simple polling endpoint for frontend after redirect.
    """

    permission_classes = [AllowAny]

    def get(self, request, reference_id: str):
        try:
            txn = Transaction.objects.select_related(
                "payer", "association", "session"
            ).get(reference_id=reference_id)
        except Transaction.DoesNotExist:
            return Response({"exists": False}, status=200)

        # If not verified locally, check directly with Ercaspay
        if not txn.is_verified:
            try:
                # Use the payment_provider_reference if available (the Ercas-specific ref), 
                # otherwise fall back to our own reference_id
                verify_ref = txn.payment_provider_reference or reference_id
                
                verification_result = verify_ercaspay_transaction(verify_ref)
                
                # Check if API call was successful
                if verification_result.get("status"):
                    data = verification_result.get("data", {})
                    # Ercaspay status string check (tolerant to case/variations)
                    status_str = str(data.get("status", "")).lower()
                    
                    if status_str in ["success", "successful", "paid"]:
                        txn.is_verified = True
                        txn.save(update_fields=["is_verified"])
                        
                        logger.info(f"[PAYMENT_STATUS][POLLING_VERIFIED] ref={txn.reference_id} status={status_str}")
                        print(f"[{timezone.now().isoformat()}] POLLING VERIFIED ref={txn.reference_id}")
            except Exception as e:
                # Log error but don't crash, just return current state
                logger.error(f"[PAYMENT_STATUS][ERROR] ref={reference_id} verify failed: {e}")

        receipt = getattr(txn, "receipt", None)
        payload = {
            "exists": True,
            "reference_id": txn.reference_id,
            "is_verified": txn.is_verified,
            "amount_paid": str(txn.amount_paid),
            "receipt_id": getattr(receipt, "receipt_id", None),
        }
        return Response(payload, status=200)

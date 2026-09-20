from django.db import models
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from association.models import Association, Session

from .models import Payer
from .serializers import PayerCheckSerializer, PayerSerializer
from .services import PayerService


import jwt
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import random
from django.core.mail import send_mail

class PayerPagination(PageNumberPagination):
    page_size = 7
    page_size_query_param = 'page_size'  
    max_page_size = 1000

class PayerLookupView(APIView):
    permission_classes = [] # Public

    def get(self, request):
        matric_number = request.query_params.get("matric_number")
        association_short_name = request.query_params.get("association_short_name")
        
        if not matric_number:
            return Response({"error": "matric_number is required"}, status=400)
            
        try:
            if association_short_name:
                association = Association.objects.get(association_short_name=association_short_name)
            else:
                association = Association.objects.first()
                if not association:
                    return Response({"error": "No association found"}, status=404)
        except Association.DoesNotExist:
            return Response({"error": "Association not found"}, status=404)
            
        if not association.current_session:
            return Response({"error": "Association has no active session"}, status=400)
            
        try:
            payer = Payer.objects.get(
                matric_number=matric_number,
                association=association,
                session=association.current_session
            )
            serializer = PayerSerializer(payer)
            data = serializer.data
            
            # Hide sensitive fields
            data.pop("otp_code", None)
            data.pop("otp_expires_at", None)
            
            return Response({"exists": True, "data": data}, status=200)
        except Payer.DoesNotExist:
            return Response({"exists": False}, status=404)

class StudentAuthRequestOTPView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        matric_number = request.data.get("matric_number")
        
        if not matric_number:
            return Response({"error": "matric_number is required"}, status=400)
            
        try:
            association = Association.objects.first()
            if not association:
                return Response({"error": "No association found"}, status=404)
                
            payer = Payer.objects.filter(
                matric_number=matric_number,
                association=association
            ).order_by('-created_at').first()
            
            if not payer:
                return Response({"error": "Student not found in our records."}, status=404)
                
        except Exception as e:
            return Response({"error": "An error occurred"}, status=500)
            
        if not payer.email:
            return Response({"error": "No email associated with this student record."}, status=400)
            
        # Generate 6 digit OTP
        otp = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        payer.otp_code = otp
        payer.otp_expires_at = timezone.now() + timedelta(minutes=10)
        payer.save(update_fields=['otp_code', 'otp_expires_at'])
        
        # Send OTP email
        from django.core.mail import EmailMultiAlternatives
        from django.template.loader import render_to_string
        
        context = {
            "first_name": payer.first_name,
            "otp": otp,
            "current_year": timezone.now().year
        }
        html_content = render_to_string("emails/login_otp.html", context)
        
        msg = EmailMultiAlternatives(
            subject='Your DuesPay Login OTP',
            body=f'Your OTP is {otp}. It expires in 10 minutes.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[payer.email],
        )
        msg.attach_alternative(html_content, "text/html")
        try:
            msg.send(fail_silently=True)
        except Exception:
            pass
        
        # Return masked email for display
        email_parts = payer.email.split('@')
        masked_email = f"{email_parts[0][:3]}***@{email_parts[1]}"
        
        return Response({
            "message": "OTP sent successfully",
            "masked_email": masked_email
        }, status=200)

class StudentAuthVerifyOTPView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        matric_number = request.data.get("matric_number")
        otp_code = request.data.get("otp_code")
        
        if not all([matric_number, otp_code]):
            return Response({"error": "Missing required fields"}, status=400)
            
        try:
            association = Association.objects.first()
            if not association:
                return Response({"error": "No association found"}, status=404)
                
            payer = Payer.objects.filter(
                matric_number=matric_number,
                association=association
            ).order_by('-created_at').first()
            
            if not payer:
                return Response({"error": "Student not found"}, status=404)
                
        except Exception as e:
            return Response({"error": "An error occurred"}, status=500)
            
        if not payer.otp_code or payer.otp_code != otp_code:
            return Response({"error": "Invalid OTP"}, status=400)
            
        if not payer.otp_expires_at or payer.otp_expires_at < timezone.now():
            return Response({"error": "OTP has expired"}, status=400)
            
        # Clear OTP
        payer.otp_code = None
        payer.otp_expires_at = None
        payer.save(update_fields=["otp_code", "otp_expires_at"])
        
        # Generate token
        payload = {
            "payer_id": payer.id,
            "matric_number": payer.matric_number,
            "exp": timezone.now() + timedelta(days=7),
            "iat": timezone.now(),
        }
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
        
        return Response({
            "message": "Login successful",
            "access": token,
            "student": PayerSerializer(payer).data
        }, status=200)

class StudentTransactionsView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        auth_header = request.headers.get('Authorization')
        print("==== AUTH HEADER ====", auth_header)
        if not auth_header or not auth_header.startswith('Bearer '):
            return Response({"error": "Unauthorized"}, status=401)
            
        token = auth_header.split(' ')[1]
        print("==== TOKEN ====", token)
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            print("==== JWT PAYLOAD ====", payload)
            payer_id = payload.get("payer_id")
        except jwt.ExpiredSignatureError:
            print("==== Token Expired ====")
            return Response({"error": "Token has expired"}, status=401)
        except jwt.InvalidTokenError as e:
            print("==== Invalid Token ====", str(e))
            return Response({"error": "Invalid token"}, status=401)
            
        print("==== REACHED StudentTransactionsView ====", "payer_id:", payer_id)
        try:
            payer = Payer.objects.get(id=payer_id)
        except Payer.DoesNotExist:
            print("==== Payer DoesNotExist! ====")
            return Response({"error": "User not found"}, status=404)
            
        # Fetch transactions specifically for this payer
        from transactions.models import Transaction
        from transactions.serializers import TransactionSerializer
        
        transactions = Transaction.objects.filter(payer__matric_number=payer.matric_number, association=payer.association).order_by('-submitted_at')
        serializer = TransactionSerializer(transactions, many=True)
        print("==== RETURNING TRANSACTIONS ====", len(transactions))
        return Response({"transactions": serializer.data}, status=200)

class PayerCheckView(APIView):
    def post(self, request):
        serializer = PayerCheckSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data

        assoc_short_name = data.get("association_short_name")
        try:
            association = Association.objects.get(
                association_short_name=assoc_short_name
            )
        except Association.DoesNotExist:
            return Response(
                {"error": "Association not found."}, status=status.HTTP_404_NOT_FOUND
            )

        # Check if association has a current session
        if not association.current_session:
            return Response(
                {
                    "error": "Association has no active session. Please contact the association admin."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        payer, error = PayerService.check_or_update_payer(
            association,
            association.current_session,  # Pass the session instance
            data["matric_number"],
            data["email"],
            data["level"],
            data["phone_number"],
            data["first_name"],
            data["last_name"],
            data.get("faculty", ""),
            data.get("department", ""),
        )
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "success": True,
                "payer_id": payer.id,
                "message": "Payer Created or updated successfully.",
            },
            status=200,
        )


class PayerViewSet(viewsets.ModelViewSet):
    queryset = Payer.objects.all()
    serializer_class = PayerSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = PayerPagination

    def get_queryset(self):
        association = getattr(self.request.user, "association", None)
        queryset = Payer.objects.none()

        if association:
            # Get session_id from query params or use current session
            session_id = self.request.query_params.get("session_id")

            if session_id:
                # Validate that session belongs to this association
                try:
                    session = Session.objects.get(
                        id=session_id, association=association
                    )
                    queryset = Payer.objects.filter(session=session)
                except Session.DoesNotExist:
                    queryset = Payer.objects.none()
            elif association.current_session:
                # Use current session if no session_id provided
                queryset = Payer.objects.filter(session=association.current_session)
            else:
                # No session available, return empty queryset
                queryset = Payer.objects.none()

        # Order by creation date
        queryset = queryset.order_by("-created_at")

        # Search by name, matric number, email, faculty, department
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                models.Q(first_name__icontains=search)
                | models.Q(last_name__icontains=search)
                | models.Q(matric_number__icontains=search)
                | models.Q(email__icontains=search)
                | models.Q(faculty__icontains=search)
                | models.Q(department__icontains=search)
            )

        # Filter by faculty
        faculty = self.request.query_params.get("faculty")
        if faculty:
            queryset = queryset.filter(faculty__icontains=faculty)

        # Filter by department
        department = self.request.query_params.get("department")
        if department:
            queryset = queryset.filter(department__icontains=department)

        # Filter by level
        level = self.request.query_params.get("level")
        if level:
            queryset = queryset.filter(level=level)

        return queryset

    def perform_create(self, serializer):
        association = getattr(self.request.user, "association", None)
        if not association or not association.current_session:
            raise ValidationError(
                "No current session available. Please create a session first."
            )

        serializer.save(
            association=association,
            session=association.current_session,  # Auto-assign current session
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)

        return Response(serializer.data)

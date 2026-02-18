import hashlib
import hmac
import json
import logging
import requests
from django.conf import settings
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

def get_ercaspay_secret_key() -> str:
    return getattr(settings, "ERCASPAY_SECRET_KEY", "")


def format_phone_number(phone: str) -> str:
    """
    Format phone number to Nigerian standard (080...)
    """
    if not phone:
        return "0000000000"
    
    # Remove non-digits
    digits = "".join(filter(str.isdigit, str(phone)))
    
    # Check for 234 prefix (13 digits usually: 2348012345678)
    if digits.startswith("234") and len(digits) == 13:
        return "0" + digits[3:]
    
    # Check for 234 prefix without leading + (if it was stripped already) or entered as 234...
    if digits.startswith("234") and len(digits) > 10:
         return "0" + digits[3:]

    return digits or "0000000000"

def get_ercaspay_base_url() -> str:
    return getattr(settings, "ERCASPAY_BASE_URL", "https://api.ercaspay.com/api/v1")

def compute_ercaspay_signature(raw: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha512).hexdigest()

def is_valid_ercaspay_signature(raw_body: bytes, header_signature: str) -> bool:
    secret = get_ercaspay_secret_key()
    if not secret or not header_signature:
        return False
    expected = compute_ercaspay_signature(raw_body, secret)
    # Ercaspay documentation usually specifies the signature format.
    # Assuming standard HMAC-SHA512 based on typical payment gateways in the region.
    # If comparison fails, we might need to check if they use a different algo.
    return hmac.compare_digest(expected, header_signature)

def ercaspay_init_payment(
    *,
    amount: str,
    currency: str = "NGN",
    reference: str,
    customer: dict,
    redirect_url: str,
    metadata: dict | None = None,
    description: str = "Dues Payment"
) -> dict:
    """
    Initialize Ercaspay payment
    Endpoint: POST /payment/initiate
    """
    base_url = get_ercaspay_base_url()
    secret_key = get_ercaspay_secret_key()
    
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    
    phone_number = format_phone_number(customer.get("phone_number"))

    payload = {
        "amount": float(amount),
        "paymentReference": reference,
        "paymentMethods": "card,bank-transfer,qrcode,ussd",
        "customerName": customer.get("name", "DuesPay User"),
        "customerEmail": customer.get("email"),
        "customerPhoneNumber": phone_number,
        "currency": currency,
        "redirectUrl": redirect_url,
        "description": description,
        "metadata": metadata or {}
    }

    try:
        url = f"{base_url}/payment/initiate"
        logger.info(f"[ERCASPAY][REQ] url={url} ref={reference} amount={amount}")
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response_data = response.json()
        
        logger.info(f"[ERCASPAY][RES] status={response.status_code} data={str(response_data)[:200]}")

        if response.status_code in [200, 201] and response_data.get("requestSuccessful"):
            data = response_data.get("responseBody", {})
            return {
                "status": True,
                "data": {
                    "authorization_url": data.get("checkoutUrl"),
                    "access_code": data.get("paymentReference"),
                    "reference": reference,
                    "ercas_reference": data.get("transactionReference")
                },
                "message": response_data.get("responseMessage", "Success")
            }
        else:
            error_msg = response_data.get("responseMessage") or response_data.get("errorMessage", "Failed to initiate payment")
            logger.error(f"[ERCASPAY][ERR] {error_msg}")
            raise Exception(error_msg)

    except Exception as e:
        logger.error(f"[ERCASPAY][EXCEPTION] {str(e)}")
        raise

def verify_ercaspay_transaction(reference: str) -> dict:
    """
    Verify transaction status
    Endpoint: GET /payment/transaction/verify/{reference}
    """
    base_url = get_ercaspay_base_url()
    secret_key = get_ercaspay_secret_key()
    
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Accept": "application/json",
    }

    try:
        url = f"{base_url}/payment/transaction/verify/{reference}"
        response = requests.get(url, headers=headers, timeout=30)
        response_data = response.json()

        if response.status_code == 200 and response_data.get("requestSuccessful"):
             return {
                "status": True,
                "data": response_data.get("responseBody", {}),
                "message": response_data.get("responseMessage", "Success")
             }
        
        return {
            "status": False,
            "message": response_data.get("responseMessage", "Verification failed")
        }

    except Exception as e:
        logger.error(f"[ERCASPAY][VERIFY_EXCEPTION] {str(e)}")
        raise

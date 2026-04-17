from django.db.models.signals import post_save
from django.dispatch import receiver
from .emails import send_admin_new_transaction_email, send_receipt_email
from .models import Transaction, TransactionReceipt


@receiver(post_save, sender=Transaction)
def notify_admin_on_transaction(sender, instance, created, **kwargs):
    print(f"DEBUG: notify_admin_on_transaction triggered. Created={created}")
    if created:
        association = instance.association
        print(f"DEBUG: Association: {association}")
        admin = getattr(association, "admin", None)
        print(f"DEBUG: Admin: {admin}")
        if admin and admin.email:
            print(f"DEBUG: Sending email to admin: {admin.email}")
            send_admin_new_transaction_email(admin, association, instance)
        else:
            print("DEBUG: Admin or admin email missing.")


@receiver(post_save, sender=Transaction)
def create_receipt_on_verification(sender, instance, created, **kwargs):
    """Signal: Create and send receipt when transaction is verified"""
    # Only proceed if transaction was just verified
    # Check if 'is_verified' was changed in this save
    update_fields = kwargs.get('update_fields')
    
    if instance.is_verified and (created or (update_fields and 'is_verified' in update_fields)):
        try:
            # Use get_or_create but only send the email if it was JUST created
            receipt, receipt_created = TransactionReceipt.objects.get_or_create(
                transaction=instance
            )

            if receipt_created:
                send_receipt_email(receipt)
                print(
                    f"✅ New receipt created and sent for transaction {instance.reference_id}"
                )
            else:
                # Receipt already exists, don't resend email on every status poll
                print(
                    f"ℹ️ Receipt already exists for transaction {instance.reference_id}"
                )

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Failed to process receipt for transaction {instance.reference_id}: {str(e)}"
            )

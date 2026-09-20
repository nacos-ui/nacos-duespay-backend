from django.db.models.signals import post_save
from django.dispatch import receiver

from main.models import AdminUser
from transactions.models import Transaction

from .models import Association


# Signal removed per user request (System is now single-association only)


@receiver(post_save, sender=Transaction)
def create_notification_for_transaction(sender, instance, created, **kwargs):
    if created:
        association = instance.association
        payer = f"{instance.payer.first_name} {instance.payer.last_name}"
        message = f"New transaction of ₦{instance.amount_paid} from {payer}."
        association.notifications.create(message=message)

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from transactions.models import Transaction

class Command(BaseCommand):
    help = 'Normalize old unverified transactions to be marked as expired'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=24,
            help='Number of hours before an unverified transaction is considered expired (default: 24)'
        )

    def handle(self, *args, **options):
        hours = options['hours']
        cutoff_time = timezone.now() - timedelta(hours=hours)

        # Find transactions that are not verified, not already expired, and older than cutoff
        transactions_to_expire = Transaction.objects.filter(
            is_verified=False,
            is_expired=False,
            submitted_at__lt=cutoff_time
        )

        count = transactions_to_expire.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS(f'No transactions older than {hours} hours need to be expired.'))
            return

        # Update them
        transactions_to_expire.update(is_expired=True)

        self.stdout.write(
            self.style.SUCCESS(f'Successfully marked {count} unverified transactions as expired (older than {hours} hours).')
        )

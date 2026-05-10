from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from decouple import config


class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        User = get_user_model()
        
        email = config("DEFAULT_SUPERUSER_EMAIL", default="chinonsoali2005@gmail.com")
        username = config("DEFAULT_SUPERUSER_USERNAME", default="")
        password = config("DEFAULT_SUPERUSER_PASSWORD", default="")
        
        if not User.objects.filter(email=email).exists():
            User.objects.create_superuser(
                username=username, email=email, password=password
            )
            self.stdout.write(f">>>> Superuser '{username}' created.")
        else:
            self.stdout.write(f"!!!! Superuser with email '{email}' already exists.")

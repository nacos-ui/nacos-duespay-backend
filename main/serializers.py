import re

from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import AdminUser


def check_password(value):
    if len(value) < 6:
        raise serializers.ValidationError(
            "Password must be at least 6 characters long."
        )

    if not re.search(r"[A-Z]", value):
        raise serializers.ValidationError(
            "Password must contain at least one uppercase letter."
        )

    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', value):
        raise serializers.ValidationError(
            "Password must contain at least one special character."
        )


class AdminUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6, required=False)

    class Meta:
        model = AdminUser
        fields = [
            "username",
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "password",
            "role",
        ]
        read_only_fields = ["is_first_login", "role"]
        extra_kwargs = {"password": {"write_only": True}}

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = AdminUser.objects.create(**validated_data)
        if password:
            check_password(password)  
            user.set_password(password)
            user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            check_password(password)  
            instance.set_password(password)
        instance.save()
        return instance
    

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['token_version'] = user.token_version 
        token["email"] = user.email
        return token

    def validate(self, attrs):
        user = AdminUser.objects.filter(email=attrs["email"]).first()
        if not user:
            raise serializers.ValidationError(
                "No active account found with the given credentials"
            )

        # Prevent Google users from logging in with password
        if user.auth_mode == "google":
            raise serializers.ValidationError(
                "This account was registered with Google. Please use Google login."
            )

        data = super().validate(attrs)
        data["is_first_login"] = self.user.is_first_login

        if self.user.is_first_login:
            self.user.is_first_login = False
            self.user.save()

        data["message"] = "Login successful"
        
        # Remove refresh token from response
        data.pop('refresh', None)

        return data


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = AdminUser
        fields = ["email", "first_name", "last_name", "phone_number", "password"]

    def validate_password(self, value):
        check_password(value)
        return value

    def create(self, validated_data):
        from association.models import Association
        
        user = AdminUser.objects.create_user(
            username=validated_data["email"],
            email=validated_data["email"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            phone_number=validated_data.get("phone_number", ""),
            password=validated_data["password"],
            auth_mode="email",
            association=Association.objects.first(),
        )
        return user


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, required=True)
    token = serializers.CharField(required=True)
    uid = serializers.CharField(required=True)

    def validate_password(self, value):
        check_password(value)
        return value


class AdminUserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminUser
        fields = ["id", "first_name", "last_name", "email", "phone_number", "role", "created_at"]


class AdminUserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True)
    
    class Meta:
        model = AdminUser
        fields = ["first_name", "last_name", "email", "phone_number", "password", "role"]
        
    def validate_password(self, value):
        check_password(value)
        return value
        
    def create(self, validated_data):
        user = AdminUser.objects.create_user(
            username=validated_data['email'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
            phone_number=validated_data.get('phone_number', ''),
            role=validated_data.get('role', 'admin'),
            association=self.context['request'].user.association,
            auth_mode="email"
        )
        return user

class AdminUserUpdateRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminUser
        fields = ["role"]

    def validate_role(self, value):
        if value not in dict(AdminUser.ROLE_CHOICES):
            raise serializers.ValidationError("Invalid role.")
        return value

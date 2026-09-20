from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    PayerCheckView, PayerViewSet, PayerLookupView,
    StudentAuthRequestOTPView, StudentAuthVerifyOTPView, StudentTransactionsView
)

router = DefaultRouter()

router.register("", PayerViewSet)

urlpatterns = [
    path('lookup/', PayerLookupView.as_view(), name='payer-lookup'),
    path('auth/request-otp/', StudentAuthRequestOTPView.as_view(), name='student-request-otp'),
    path('auth/verify-otp/', StudentAuthVerifyOTPView.as_view(), name='student-verify-otp'),
    path('me/transactions/', StudentTransactionsView.as_view(), name='student-transactions'),
    path('check/', PayerCheckView.as_view(), name='payer-check'),  # /api/payers/check/
] + router.urls
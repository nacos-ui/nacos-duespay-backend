from django.urls import path
from .views import (
    UserProfileView, 
    ping_view, 
    AssociationAdminsAPIView, 
    AssociationAdminDetailAPIView
)

urlpatterns = [
    path("adminuser/", UserProfileView.as_view(), name="user-profile"),
    path("association-admins/", AssociationAdminsAPIView.as_view(), name="association-admins-list"),
    path("association-admins/<int:pk>/", AssociationAdminDetailAPIView.as_view(), name="association-admins-detail"),
    path("ping/", ping_view, name="ping-view"),
]

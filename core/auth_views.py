# core/auth_views.py
from rest_framework_simplejwt.views import TokenObtainPairView
from .auth import EmailOrUsernameTokenObtainPairSerializer

class EmailOrUsernameTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailOrUsernameTokenObtainPairSerializer

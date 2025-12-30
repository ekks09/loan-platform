from __future__ import annotations

import logging

from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import User
from .authentication import _jwt_encode
from .serializers import RegisterSerializer, LoginSerializer, MeSerializer

logger = logging.getLogger(__name__)


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)

        if not serializer.is_valid():
            errors = serializer.errors
            # Flatten first error for cleaner frontend display
            first_error = next(iter(errors.values()))
            if isinstance(first_error, list):
                first_error = first_error[0]
            return Response({"error": str(first_error)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = serializer.save()
            logger.info(f"New user registered: {user.phone}")

            # Do NOT return token here to prevent auto-login
            return Response({
                "ok": True,
                "message": "Registration successful. Please login to continue."
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(f"Registration error: {e}")
            return Response(
                {"error": "Registration failed. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)

        if not serializer.is_valid():
            errors = serializer.errors
            first_error = next(iter(errors.values()))
            if isinstance(first_error, list):
                first_error = first_error[0]
            return Response({"error": str(first_error)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = serializer.validated_data["user"]
            token = _jwt_encode(user)
            logger.info(f"User logged in: {user.phone}")

            return Response({
                "access": token,
                "message": "Login successful"
            })

        except Exception as e:
            logger.exception(f"Login error: {e}")
            return Response(
                {"error": "Login failed. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)

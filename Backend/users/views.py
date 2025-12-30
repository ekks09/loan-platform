from __future__ import annotations

import logging

from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import serializers as drf_serializers  # ADD THIS IMPORT

from .models import User
from .authentication import _jwt_encode
from .serializers import RegisterSerializer, LoginSerializer, MeSerializer

logger = logging.getLogger(__name__)


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        
        if not s.is_valid():
            # Extract first error message for cleaner frontend display
            errors = s.errors
            
            if "phone" in errors:
                error_msg = errors["phone"]
                if isinstance(error_msg, list):
                    error_msg = error_msg[0]
                return Response({"error": str(error_msg)}, status=status.HTTP_400_BAD_REQUEST)
            
            if "national_id" in errors:
                error_msg = errors["national_id"]
                if isinstance(error_msg, list):
                    error_msg = error_msg[0]
                return Response({"error": str(error_msg)}, status=status.HTTP_400_BAD_REQUEST)
            
            if "password" in errors:
                error_msg = errors["password"]
                if isinstance(error_msg, list):
                    error_msg = error_msg[0]
                return Response({"error": str(error_msg)}, status=status.HTTP_400_BAD_REQUEST)
            
            # Generic error
            first_error = next(iter(errors.values()))
            if isinstance(first_error, list):
                first_error = first_error[0]
            return Response({"error": str(first_error)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = s.save()
            token = _jwt_encode(user)
            logger.info(f"New user registered: {user.phone}")
            
            return Response({
                "ok": True,
                "access": token,
                "message": "Registration successful"
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
        s = LoginSerializer(data=request.data)
        
        if not s.is_valid():
            errors = s.errors
            
            # Handle specific field errors
            if "phone" in errors:
                error_msg = errors["phone"]
                if isinstance(error_msg, list):
                    error_msg = error_msg[0]
                return Response({"error": str(error_msg)}, status=status.HTTP_400_BAD_REQUEST)
            
            if "password" in errors:
                error_msg = errors["password"]
                if isinstance(error_msg, list):
                    error_msg = error_msg[0]
                return Response({"error": str(error_msg)}, status=status.HTTP_400_BAD_REQUEST)
            
            # Handle non-field errors (invalid credentials)
            if "non_field_errors" in errors:
                error_msg = errors["non_field_errors"]
                if isinstance(error_msg, list):
                    error_msg = error_msg[0]
                return Response({"error": str(error_msg)}, status=status.HTTP_401_UNAUTHORIZED)
            
            # Generic error
            first_error = next(iter(errors.values()))
            if isinstance(first_error, list):
                first_error = first_error[0]
            return Response({"error": str(first_error)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = s.validated_data["user"]
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

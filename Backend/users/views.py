from __future__ import annotations

import logging
from django.db import transaction
from django.utils import timezone

from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from .models import User
from .authentication import _jwt_encode
from .serializers import (
    RegisterSerializer,
    RegisterWithPhotosSerializer,
    LoginSerializer,
    MeSerializer,
)
from .storage import SupabaseStorage, StorageError

logger = logging.getLogger(__name__)


class RegisterView(APIView):
    """
    Handle user registration with photo ID uploads.
    Accepts multipart/form-data with photos or JSON without photos.
    """
    permission_classes = [permissions.AllowAny]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        # Check if this is a multipart request with files
        has_files = bool(request.FILES)
        
        if has_files:
            return self._register_with_photos(request)
        else:
            return self._register_without_photos(request)

    def _register_without_photos(self, request):
        """Original registration without photo uploads."""
        serializer = RegisterSerializer(data=request.data)

        if not serializer.is_valid():
            errors = serializer.errors
            first_error = next(iter(errors.values()))
            if isinstance(first_error, list):
                first_error = first_error[0]
            return Response({"error": str(first_error)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = serializer.save()
            logger.info(f"New user registered (no photos): {user.phone}")

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

    def _register_with_photos(self, request):
        """Registration with photo ID uploads."""
        # Get files
        id_front = request.FILES.get('id_front')
        id_back = request.FILES.get('id_back')
        selfie = request.FILES.get('selfie')

        # Validate all files are present
        if not all([id_front, id_back, selfie]):
            missing = []
            if not id_front:
                missing.append("ID front photo")
            if not id_back:
                missing.append("ID back photo")
            if not selfie:
                missing.append("selfie photo")
            return Response(
                {"error": f"Missing required photos: {', '.join(missing)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate form data
        serializer = RegisterWithPhotosSerializer(data=request.data)
        if not serializer.is_valid():
            errors = serializer.errors
            first_error = next(iter(errors.values()))
            if isinstance(first_error, list):
                first_error = first_error[0]
            return Response({"error": str(first_error)}, status=status.HTTP_400_BAD_REQUEST)

        # Validate files before upload
        try:
            SupabaseStorage.validate_file(id_front)
            SupabaseStorage.validate_file(id_back)
            SupabaseStorage.validate_file(selfie)
        except StorageError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # Use phone as identifier for storage path
        phone = serializer.validated_data['phone']
        uploaded_paths = []

        try:
            # Upload files to Supabase Storage
            id_front_data = SupabaseStorage.upload_file(id_front, 'id_front', phone)
            uploaded_paths.append(id_front_data['path'])

            id_back_data = SupabaseStorage.upload_file(id_back, 'id_back', phone)
            uploaded_paths.append(id_back_data['path'])

            selfie_data = SupabaseStorage.upload_file(selfie, 'selfie', phone)
            uploaded_paths.append(selfie_data['path'])

            # Add photo data to validated data
            serializer.validated_data['photo_data'] = {
                'id_front_url': id_front_data['url'],
                'id_front_path': id_front_data['path'],
                'id_back_url': id_back_data['url'],
                'id_back_path': id_back_data['path'],
                'selfie_url': selfie_data['url'],
                'selfie_path': selfie_data['path'],
            }

            # Create user with photo data
            with transaction.atomic():
                user = serializer.save()

            logger.info(f"New user registered with photos: {user.phone}")

            return Response({
                "ok": True,
                "message": "Registration successful. Your documents are pending verification. Please login to continue.",
                "verification_status": "pending"
            }, status=status.HTTP_201_CREATED)

        except StorageError as e:
            # Clean up any uploaded files
            SupabaseStorage.delete_user_files(*uploaded_paths)
            logger.error(f"Storage error during registration: {e}")
            return Response(
                {"error": f"Failed to upload photos: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        except Exception as e:
            # Clean up any uploaded files
            SupabaseStorage.delete_user_files(*uploaded_paths)
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
                "message": "Login successful",
                "verification_status": user.verification_status,
                "is_verified": user.is_verified,
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


class VerificationStatusView(APIView):
    """Check current user's verification status."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            "verification_status": user.verification_status,
            "is_verified": user.is_verified,
            "has_uploaded_documents": user.has_uploaded_documents,
            "verification_notes": user.verification_notes if user.verification_status == 'rejected' else None,
        })

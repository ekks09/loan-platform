"""
Supabase Storage utility for handling file uploads.
"""
from __future__ import annotations

import uuid
import logging
from typing import BinaryIO
from django.conf import settings

logger = logging.getLogger(__name__)

# Try to import supabase, gracefully handle if not installed
try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False
    logger.warning("supabase-py not installed. File uploads will be disabled.")


class StorageError(Exception):
    """Custom exception for storage operations."""
    pass


class SupabaseStorage:
    """
    Handles file uploads to Supabase Storage.
    """
    BUCKET_NAME = "identity-documents"
    ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp']
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

    _client: Client | None = None

    @classmethod
    def get_client(cls) -> Client:
        """Get or create Supabase client."""
        if not SUPABASE_AVAILABLE:
            raise StorageError("Supabase client not available. Install supabase-py.")

        if cls._client is None:
            url = getattr(settings, 'SUPABASE_URL', None)
            key = getattr(settings, 'SUPABASE_SERVICE_KEY', None)

            if not url or not key:
                raise StorageError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be configured.")

            cls._client = create_client(url, key)

        return cls._client

    @classmethod
    def validate_file(cls, file) -> None:
        """
        Validate uploaded file.
        
        Args:
            file: Django UploadedFile object
            
        Raises:
            StorageError: If validation fails
        """
        if not file:
            raise StorageError("No file provided.")

        # Check content type
        content_type = getattr(file, 'content_type', None)
        if content_type not in cls.ALLOWED_TYPES:
            raise StorageError(
                f"Invalid file type: {content_type}. "
                f"Allowed types: JPG, PNG, WebP"
            )

        # Check file size
        file_size = getattr(file, 'size', 0)
        if file_size > cls.MAX_FILE_SIZE:
            size_mb = file_size / (1024 * 1024)
            raise StorageError(
                f"File too large: {size_mb:.1f}MB. Maximum size: 5MB"
            )

    @classmethod
    def upload_file(
        cls,
        file,
        folder: str,
        user_identifier: str
    ) -> dict:
        """
        Upload a file to Supabase Storage.
        
        Args:
            file: Django UploadedFile object
            folder: Subfolder name (e.g., 'id_front', 'id_back', 'selfie')
            user_identifier: Unique identifier for the user (phone or temp ID)
            
        Returns:
            dict with 'path' and 'url' keys
            
        Raises:
            StorageError: If upload fails
        """
        cls.validate_file(file)

        try:
            client = cls.get_client()

            # Generate unique filename
            original_name = getattr(file, 'name', 'upload')
            extension = original_name.split('.')[-1].lower()
            if extension not in ['jpg', 'jpeg', 'png', 'webp']:
                extension = 'jpg'

            # Sanitize user identifier for path
            safe_identifier = user_identifier.replace('+', '').replace(' ', '')
            unique_filename = f"{safe_identifier}/{folder}/{uuid.uuid4()}.{extension}"

            # Read file content
            file_content = file.read()

            # Upload to Supabase Storage
            result = client.storage.from_(cls.BUCKET_NAME).upload(
                path=unique_filename,
                file=file_content,
                file_options={
                    "content-type": file.content_type,
                    "upsert": "false"
                }
            )

            # Check for errors
            if hasattr(result, 'error') and result.error:
                raise StorageError(f"Upload failed: {result.error}")

            # Get public URL
            public_url = client.storage.from_(cls.BUCKET_NAME).get_public_url(unique_filename)

            logger.info(f"File uploaded successfully: {unique_filename}")

            return {
                'path': unique_filename,
                'url': public_url
            }

        except StorageError:
            raise
        except Exception as e:
            logger.exception(f"Storage upload error: {e}")
            raise StorageError(f"Failed to upload file: {str(e)}")

    @classmethod
    def delete_file(cls, path: str) -> bool:
        """
        Delete a file from Supabase Storage.
        
        Args:
            path: Storage path of the file
            
        Returns:
            True if successful
        """
        if not path:
            return True

        try:
            client = cls.get_client()
            client.storage.from_(cls.BUCKET_NAME).remove([path])
            logger.info(f"File deleted: {path}")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete file {path}: {e}")
            return False

    @classmethod
    def delete_user_files(cls, *paths: str) -> None:
        """
        Delete multiple files (used for cleanup on error).
        
        Args:
            paths: Variable number of file paths to delete
        """
        for path in paths:
            if path:
                cls.delete_file(path)

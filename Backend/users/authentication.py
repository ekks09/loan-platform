from __future__ import annotations

import time
import logging
import jwt
from typing import Optional, Tuple

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.authentication import BaseAuthentication

logger = logging.getLogger(__name__)
User = get_user_model()


def _jwt_encode(user) -> str:
    """
    Generates a signed JWT access token for the given user.
    """
    now = int(time.time())
    ttl_minutes = int(getattr(settings, "JWT_ACCESS_TTL_MINUTES", 30))
    exp = now + ttl_minutes * 60
    
    payload = {
        "sub": str(user.pk),
        "phone": user.phone,
        "iat": now,
        "exp": exp,
        "iss": getattr(settings, "JWT_ISSUER", "loan-platform"),
        "aud": getattr(settings, "JWT_AUDIENCE", "loan-platform-users"),
        "type": "access",
    }
    
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
    
    # PyJWT >= 2.0 returns string, older versions return bytes
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    
    return token


def create_token(user) -> str:
    """Alias for _jwt_encode for consistency."""
    return _jwt_encode(user)


class JWTAuthentication(BaseAuthentication):
    """
    Custom JWT authentication for DRF.
    Reads Authorization header, decodes JWT, and returns (user, None).
    Works with User model where USERNAME_FIELD = phone.
    """
    keyword = "Bearer"

    def authenticate(self, request) -> Optional[Tuple[User, None]]:
        auth = request.headers.get("Authorization", "")
        if not auth:
            return None

        parts = auth.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            raise AuthenticationFailed("Invalid Authorization header format.")

        token = parts[1]
        
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=["HS256"],
                audience=getattr(settings, "JWT_AUDIENCE", "loan-platform-users"),
                issuer=getattr(settings, "JWT_ISSUER", "loan-platform"),
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed("Session expired. Please login again.")
        except jwt.InvalidAudienceError:
            raise AuthenticationFailed("Invalid token. Please login again.")
        except jwt.InvalidIssuerError:
            raise AuthenticationFailed("Invalid token. Please login again.")
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            raise AuthenticationFailed("Invalid token. Please login again.")

        if payload.get("type") != "access":
            raise AuthenticationFailed("Invalid token type.")

        user_id = payload.get("sub")
        if not user_id:
            raise AuthenticationFailed("Invalid token payload.")

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise AuthenticationFailed("User not found.")

        if not user.is_active:
            raise AuthenticationFailed("Account disabled. Please contact support.")

        return (user, None)

    def authenticate_header(self, request):
        return self.keyword

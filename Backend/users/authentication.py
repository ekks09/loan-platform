from __future__ import annotations

import logging
import time
import uuid
from datetime import timezone as dt_timezone
from typing import Optional, Tuple

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

logger = logging.getLogger(__name__)
User = get_user_model()


def _jwt_encode(user) -> str:
    """Issue a signed, audience-scoped HS256 access token."""
    now = int(time.time())
    ttl_minutes = int(getattr(settings, "JWT_ACCESS_TTL_MINUTES", 30))
    payload = {
        "sub": str(user.pk),
        "iat": now,
        "nbf": now,
        "exp": now + ttl_minutes * 60,
        "iss": getattr(settings, "JWT_ISSUER", "loan-platform"),
        "aud": getattr(settings, "JWT_AUDIENCE", "loan-platform-users"),
        "type": "access",
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
    return token.decode("utf-8") if isinstance(token, bytes) else token


def create_token(user) -> str:
    return _jwt_encode(user)


class JWTAuthentication(BaseAuthentication):
    """Bearer-token authentication with strict claim validation."""

    keyword = "Bearer"

    def authenticate(self, request) -> Optional[Tuple[User, None]]:
        header = request.headers.get("Authorization", "")
        if not header:
            return None

        parts = header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            raise AuthenticationFailed("Invalid Authorization header format.")

        try:
            payload = jwt.decode(
                parts[1],
                settings.SECRET_KEY,
                algorithms=["HS256"],
                audience=getattr(settings, "JWT_AUDIENCE", "loan-platform-users"),
                issuer=getattr(settings, "JWT_ISSUER", "loan-platform"),
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed("Session expired. Please log in again.")
        except jwt.InvalidAudienceError:
            raise AuthenticationFailed("Invalid token. Please log in again.")
        except jwt.InvalidIssuerError:
            raise AuthenticationFailed("Invalid token. Please log in again.")
        except jwt.InvalidTokenError:
            raise AuthenticationFailed("Invalid token. Please log in again.")

        if payload.get("type") != "access":
            raise AuthenticationFailed("Invalid token type.")

        user_id = payload.get("sub")
        if not user_id:
            raise AuthenticationFailed("Invalid token payload.")

        try:
            user = User.objects.get(pk=user_id)
        except (User.DoesNotExist, ValueError, TypeError):
            raise AuthenticationFailed("User not found.")

        if not user.is_active:
            raise AuthenticationFailed("Account disabled. Please contact support.")

        # A password reset must end sessions opened with the old password.
        # ``iat`` has one-second precision, so a token minted in the same
        # second as the change is still accepted; anything older is refused.
        changed_at = getattr(user, "password_changed_at", None)
        if changed_at is not None:
            if changed_at.tzinfo is None:
                changed_at = changed_at.replace(tzinfo=dt_timezone.utc)
            if int(changed_at.timestamp()) > int(payload.get("iat", 0)):
                raise AuthenticationFailed(
                    "Your password changed. Sign in again with the new password."
                )

        return (user, None)

    def authenticate_header(self, request):
        return self.keyword

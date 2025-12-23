from __future__ import annotations

import time
import jwt
from typing import Optional, Tuple

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.authentication import BaseAuthentication

User = get_user_model()


def _jwt_encode(user: User) -> str:
    """
    Generates a signed JWT access token for the given user.
    """
    now = int(time.time())
    exp = now + int(getattr(settings, "JWT_ACCESS_TTL_MINUTES", 30)) * 60  # default 30 mins
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
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


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
            raise AuthenticationFailed("Invalid Authorization header.")

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
            raise AuthenticationFailed("Token expired.")
        except jwt.InvalidTokenError:
            raise AuthenticationFailed("Invalid token.")

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
            raise AuthenticationFailed("Account disabled.")

        return (user, None)

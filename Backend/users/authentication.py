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
    now = int(time.time())
    exp = now + int(settings.JWT_ACCESS_TTL_MINUTES) * 60
    payload = {
        "sub": str(user.pk),
        "phone": user.phone,
        "iat": now,
        "exp": exp,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "type": "access",
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


class JWTAuthentication(BaseAuthentication):
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
                audience=settings.JWT_AUDIENCE,
                issuer=settings.JWT_ISSUER,
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed("Token expired.")
        except jwt.InvalidTokenError:
            raise AuthenticationFailed("Invalid token.")

        if payload.get("type") != "access":
            raise AuthenticationFailed("Invalid token type.")
        user_id = payload.get("sub")
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise AuthenticationFailed("User not found.")
        if not user.is_active:
            raise AuthenticationFailed("Account disabled.")
        return (user, None)

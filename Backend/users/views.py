from __future__ import annotations

from django.contrib.auth import get_user_model

from rest_framework import permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .authentication import _jwt_encode
from .serializers import RegisterSerializer, LoginSerializer, MeSerializer

User = get_user_model()



class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            s = RegisterSerializer(data=request.data)
            s.is_valid(raise_exception=True)
            s.save()
            return Response({"ok": True}, status=201)
        except serializers.ValidationError as e:
            return Response({"error": e.detail}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({"error": "An unexpected error occurred."}, status=500)





class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            s = LoginSerializer(data=request.data)
            s.is_valid(raise_exception=True)
            user = s.validated_data["user"]
            token = _jwt_encode(user)
            return Response({"access": token})
        except serializers.ValidationError as e:
            return Response({"error": e.detail}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response(
                {"error": "An unexpected error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )



class MeView(APIView):
    def get(self, request):
        return Response(MeSerializer(request.user).data)

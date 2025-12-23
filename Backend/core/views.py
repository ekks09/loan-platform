from django.shortcuts import render
from django.http import JsonResponse
import os

def debug_env(request):
    return JsonResponse({
        "DJANGO_SECRET_KEY": bool(os.getenv("DJANGO_SECRET_KEY")),
        "SUPABASE_DB_HOST": bool(os.getenv("SUPABASE_DB_HOST")),
        "PAYSTACK_SECRET_KEY": bool(os.getenv("PAYSTACK_SECRET_KEY")),
    })

def index(request):
    return render(request, "index.html")

def register(request):
    return render(request, "register.html")

def login_view(request):
    return render(request, "login.html")

def dashboard(request):
    return render(request, "dashboard.html")

from django.contrib import admin
from django.urls import path, include
from core import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("debug-env/", views.debug_env),

    path("api/users/", include("users.urls")),
    path("api/loans/", include("loans.urls")),
    path("api/payments/", include("payments.urls")),

    path("", views.index, name="index"),
    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login_view"),
    path("dashboard/", views.dashboard, name="dashboard"),
]


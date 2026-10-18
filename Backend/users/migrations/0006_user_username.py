"""Add the optional operator sign-in name.

Borrowers continue to authenticate with their MSISDN (``USERNAME_FIELD``).
The nullable ``username`` column exists so back-office operators can sign in
to the Django admin and the API with a display name such as "Ken Admin".
"""
from __future__ import annotations

from django.db import migrations, models

from users.models import username_validator


class Migration(migrations.Migration):
    dependencies = [("users", "0005_alter_user_phone")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="username",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Operator sign-in name. Borrowers sign in with their phone number."
                ),
                max_length=150,
                null=True,
                unique=True,
                validators=[username_validator],
            ),
        ),
    ]

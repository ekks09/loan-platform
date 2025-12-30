# Generated manually for photo verification fields

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        # Add photo URL fields
        migrations.AddField(
            model_name="user",
            name="id_front_url",
            field=models.URLField(
                blank=True,
                help_text="Front of National ID",
                max_length=500,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="id_front_path",
            field=models.CharField(
                blank=True,
                help_text="Storage path for ID front",
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="id_back_url",
            field=models.URLField(
                blank=True,
                help_text="Back of National ID",
                max_length=500,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="id_back_path",
            field=models.CharField(
                blank=True,
                help_text="Storage path for ID back",
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="selfie_url",
            field=models.URLField(
                blank=True,
                help_text="Selfie photo",
                max_length=500,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="selfie_path",
            field=models.CharField(
                blank=True,
                help_text="Storage path for selfie",
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="verification_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending Review"),
                    ("verified", "Verified"),
                    ("rejected", "Rejected"),
                ],
                default="pending",
                help_text="ID verification status",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="verification_notes",
            field=models.TextField(
                blank=True,
                help_text="Admin notes on verification",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="verified_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="verified_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="verifications_done",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Add index for verification status
        migrations.AddIndex(
            model_name="user",
            index=models.Index(
                fields=["verification_status"],
                name="users_user_verific_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(
                fields=["phone"],
                name="users_user_phone_idx",
            ),
        ),
    ]

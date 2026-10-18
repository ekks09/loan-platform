"""Identity-data safeguards: consent, security and audit models.

Data-preserving upgrade notes
-----------------------------
* ``national_id`` gains a uniqueness constraint. If an existing database holds
  duplicates the migration stops with an explicit error instead of silently
  discarding rows. Run ``python manage.py data_preflight`` first.
* No columns are dropped and no existing values are rewritten.
"""
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def assert_unique_national_ids(apps, schema_editor):
    User = apps.get_model("users", "User")
    duplicates = (
        User.objects.values("national_id")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
    )
    if duplicates:
        sample = ", ".join(row["national_id"] for row in duplicates[:5])
        raise RuntimeError(
            "Cannot add the unique national_id constraint: duplicate values exist "
            f"({sample}). Resolve duplicates with `python manage.py data_preflight --details` "
            "before upgrading."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_add_photo_verification"),
    ]

    operations = [
        migrations.AlterModelManagers(
            name="user",
            managers=[("objects", __import__("users.models", fromlist=["UserManager"]).UserManager())],
        ),
        migrations.AddField(
            model_name="user",
            name="documents_uploaded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="deletion_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(assert_unique_national_ids, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                fields=("national_id",), name="users_user_national_id_uniq"
            ),
        ),
        migrations.RenameIndex(
            model_name="user",
            old_name="users_user_verific_idx",
            new_name="users_verif_status_idx",
        ),
        migrations.RemoveIndex(
            model_name="user",
            name="users_user_phone_idx",
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(fields=["created_at"], name="users_created_at_idx"),
        ),
        migrations.CreateModel(
            name="ConsentRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("purpose", models.CharField(choices=[("identity_processing", "Identity document processing"), ("terms", "Terms of service"), ("marketing", "Marketing communications")], max_length=32)),
                ("version", models.CharField(max_length=32)),
                ("granted_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("withdrawn_at", models.DateTimeField(blank=True, null=True)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=255)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="consents", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-granted_at"],
                "indexes": [models.Index(fields=["user", "purpose"], name="users_consent_user_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("user", "purpose", "version"), name="users_consent_unique_version")
                ],
            },
        ),
        migrations.CreateModel(
            name="PasswordResetCode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code_hash", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="password_reset_codes", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="DocumentAccessLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("document", models.CharField(max_length=16)),
                ("purpose", models.CharField(default="verification_review", max_length=64)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("staff_user", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="document_views", to=settings.AUTH_USER_MODEL)),
                ("subject_user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="document_access_events", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["subject_user"], name="users_docaccess_subject_idx")],
            },
        ),
    ]

"""Loan lifecycle: offers, acceptance, disbursement states and audit events.

Data-preserving upgrade notes
-----------------------------
* Existing loans keep their amount, fee, reference and status.
* New lifecycle columns are nullable or have deterministic defaults.
* An existing user with more than one open loan would violate the new partial
  unique constraint; the migration fails explicitly rather than deleting rows.
  Run ``python manage.py data_preflight`` first.
* The redundant index on ``paystack_reference`` is removed because the unique
  constraint already provides an index.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


def assert_single_open_loan(apps, schema_editor):
    Loan = apps.get_model("loans", "Loan")
    duplicates = (
        Loan.objects.filter(status__in=["PENDING", "APPROVED", "DISBURSEMENT_PENDING"])
        .values("user_id")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
    )
    if duplicates:
        users = ", ".join(str(row["user_id"]) for row in duplicates[:5])
        raise RuntimeError(
            "Cannot enforce one open loan per user: users with multiple open "
            f"loans exist (user ids: {users}). Resolve them before upgrading."
        )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("loans", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="loan",
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddField(
            model_name="loan",
            name="currency",
            field=models.CharField(default="KES", max_length=3),
        ),
        migrations.AddField(
            model_name="loan",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="loan",
            name="approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="loans_approved",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="loan",
            name="rejection_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="loan",
            name="offer_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="loan",
            name="offer_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="loan",
            name="offer_terms_version",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="loan",
            name="disbursed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="loan",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="loans",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="loan",
            name="amount",
            field=models.PositiveIntegerField(help_text="Loan principal in KES"),
        ),
        migrations.AlterField(
            model_name="loan",
            name="service_fee",
            field=models.PositiveIntegerField(editable=False, help_text="Fee in KES"),
        ),
        migrations.AlterField(
            model_name="loan",
            name="mpesa_phone",
            field=models.CharField(max_length=12),
        ),
        migrations.AlterField(
            model_name="loan",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending review"),
                    ("APPROVED", "Approved — offer issued"),
                    ("DISBURSEMENT_PENDING", "Disbursement pending"),
                    ("DISBURSED", "Disbursed"),
                    ("REPAID", "Repaid"),
                    ("REJECTED", "Rejected"),
                    ("EXPIRED", "Offer expired"),
                    ("CANCELLED", "Cancelled"),
                    ("FAILED", "Failed"),
                ],
                default="PENDING",
                max_length=32,
            ),
        ),
        # These two columns were missing from 0001 (the audit's schema-drift
        # finding); they are added here rather than altering a missing field.
        migrations.AddField(
            model_name="loan",
            name="paystack_authorization_url",
            field=models.URLField(
                blank=True,
                help_text="Paystack checkout URL for service fee payment",
                max_length=500,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="loan",
            name="paystack_access_code",
            field=models.CharField(
                blank=True,
                help_text="Paystack access code for inline payment",
                max_length=100,
                null=True,
            ),
        ),
        migrations.RemoveIndex(
            model_name="loan",
            name="loans_paystack_reference_idx",
        ),
        migrations.AddIndex(
            model_name="loan",
            index=models.Index(fields=["status", "created_at"], name="loans_status_created_idx"),
        ),
        migrations.RunPython(assert_single_open_loan, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="loan",
            constraint=models.UniqueConstraint(
                condition=Q(status__in=["PENDING", "APPROVED", "DISBURSEMENT_PENDING"]),
                fields=("user",),
                name="loans_one_open_per_user",
            ),
        ),
        migrations.CreateModel(
            name="LoanEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("from_status", models.CharField(max_length=32)),
                ("to_status", models.CharField(max_length=32)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="loan_events", to=settings.AUTH_USER_MODEL)),
                ("loan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="loans.loan")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["loan", "created_at"], name="loans_event_loan_idx")],
            },
        ),
    ]

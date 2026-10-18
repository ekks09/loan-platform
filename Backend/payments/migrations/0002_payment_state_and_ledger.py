"""Payment/transfer state machines, ledger, refunds and webhook journal.

This migration is hand-written because the automatic detector would drop and
re-create ``loan_id``/``user_id`` columns. Here the columns are *renamed* and
then altered into foreign keys, preserving every existing row.

Upgrade notes
-------------
* Existing ``Payment`` rows are backfilled: ``verified`` -> ``SUCCEEDED``,
  an existing checkout URL -> ``INITIALIZED``, otherwise ``PENDING``.
* Existing ``Transfer`` rows map ``initiated`` -> ``PENDING_PROVIDER``,
  ``success`` -> ``SUCCESS``, ``failed`` -> ``FAILED``, ``reversed`` -> ``REVERSED``.
* ``initiated`` is removed only after its meaning has been captured in
  ``attempts`` and ``status``.
* The migration fails explicitly if an existing payment references a missing
  loan or user, rather than deleting the row.
"""
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


def assert_payment_integrity(apps, schema_editor):
    Payment = apps.get_model("payments", "Payment")
    Loan = apps.get_model("loans", "Loan")
    User = apps.get_model("users", "User")

    bad_payments = Payment.objects.exclude(loan__in=Loan.objects.values("id")).values_list("id", flat=True)[:5]
    if bad_payments:
        raise RuntimeError(
            f"Payments reference missing loans (ids: {list(bad_payments)}). "
            "Resolve with `python manage.py data_preflight --details` before upgrading."
        )
    bad_users = Payment.objects.exclude(user__in=User.objects.values("id")).values_list("id", flat=True)[:5]
    if bad_users:
        raise RuntimeError(
            f"Payments reference missing users (ids: {list(bad_users)}). "
            "Resolve with `python manage.py data_preflight --details` before upgrading."
        )


def assert_transfer_integrity(apps, schema_editor):
    Transfer = apps.get_model("payments", "Transfer")
    Loan = apps.get_model("loans", "Loan")
    bad_transfers = Transfer.objects.exclude(loan__in=Loan.objects.values("id")).values_list("id", flat=True)[:5]
    if bad_transfers:
        raise RuntimeError(
            f"Transfers reference missing loans (ids: {list(bad_transfers)}). "
            "Resolve with `python manage.py data_preflight --details` before upgrading."
        )


def backfill_payments(apps, schema_editor):
    Payment = apps.get_model("payments", "Payment")
    for payment in Payment.objects.all().iterator():
        if payment.verified:
            status = "SUCCEEDED"
        elif payment.authorization_url:
            status = "INITIALIZED"
        else:
            status = "PENDING"
        Payment.objects.filter(pk=payment.pk).update(
            purpose="service_fee",
            currency="KES",
            status=status,
            idempotency_key=f"legacy:payment:{payment.pk}",
            provider="paystack",
            provider_reference=(payment.paystack_transaction_id or payment.reference or "")[:64],
        )


def backfill_transfers(apps, schema_editor):
    Transfer = apps.get_model("payments", "Transfer")
    Loan = apps.get_model("loans", "Loan")
    mapping = {
        "success": "SUCCESS",
        "failed": "FAILED",
        "reversed": "REVERSED",
        "initiated": "PENDING_PROVIDER",
    }
    for transfer in Transfer.objects.all().iterator():
        amount = (
            Loan.objects.filter(pk=transfer.loan)
            .values_list("amount", flat=True)
            .first()
        )
        if amount is None:
            raise RuntimeError(
                f"Transfer {transfer.pk} references missing loan {transfer.loan}."
            )
        Transfer.objects.filter(pk=transfer.pk).update(
            amount_kes=amount,
            currency="KES",
            status=mapping.get((transfer.status or "").lower(), "PENDING"),
            idempotency_key=f"legacy:transfer:{transfer.pk}",
            attempts=1 if transfer.initiated else 0,
        )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("loans", "0002_loan_lifecycle"),
        ("payments", "0001_initial"),
    ]

    operations = [
        # -------------------------------------------------------------
        # Payment: preserve ids, add lifecycle columns
        # -------------------------------------------------------------
        # Indexes referencing the old column names are removed before the
        # rename; equivalent indexes are re-created with explicit names below.
        migrations.RemoveIndex(model_name="payment", name="payments_pa_referen_c8d45e_idx"),
        migrations.RemoveIndex(model_name="payment", name="payments_pa_loan_id_39a7f2_idx"),
        migrations.RemoveIndex(model_name="transfer", name="payments_tr_referen_5f8b21_idx"),
        migrations.RemoveIndex(model_name="transfer", name="payments_tr_loan_id_a93c14_idx"),
        migrations.RenameField(model_name="payment", old_name="loan_id", new_name="loan"),
        migrations.RenameField(model_name="payment", old_name="user_id", new_name="user"),
        migrations.AddField(
            model_name="payment",
            name="purpose",
            field=models.CharField(
                choices=[("service_fee", "Service fee"), ("repayment", "Loan repayment")],
                default="service_fee",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="currency",
            field=models.CharField(default="KES", max_length=3),
        ),
        migrations.AddField(
            model_name="payment",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending initialisation"),
                    ("INITIALIZED", "Checkout created"),
                    ("SUCCEEDED", "Paid"),
                    ("FAILED", "Failed"),
                    ("ABANDONED", "Abandoned"),
                    ("REFUND_PENDING", "Refund pending"),
                    ("REFUNDED", "Refunded"),
                ],
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="idempotency_key",
            field=models.CharField(max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="provider",
            field=models.CharField(default="paystack", max_length=32),
        ),
        migrations.AddField(
            model_name="payment",
            name="provider_reference",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="payment",
            name="channel",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="payment",
            name="gateway_response",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="payment",
            name="failure_reason",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="payment",
            name="authorization_url",
            field=models.URLField(
                blank=True,
                help_text="Paystack checkout URL",
                max_length=500,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="access_code",
            field=models.CharField(
                blank=True,
                help_text="Paystack access code for inline payment",
                max_length=100,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="last_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.RunPython(assert_payment_integrity, migrations.RunPython.noop),
        migrations.RunPython(backfill_payments, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="payment",
            name="idempotency_key",
            field=models.CharField(max_length=64, unique=True),
        ),
        migrations.AlterField(
            model_name="payment",
            name="loan",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payments",
                to="loans.loan",
            ),
        ),
        migrations.AlterField(
            model_name="payment",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payments",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterModelOptions(
            name="payment",
            options={"ordering": ["-created_at"]},
        ),
        # -------------------------------------------------------------
        # Transfer: preserve ids, add durable operation state
        # -------------------------------------------------------------
        migrations.RenameField(model_name="transfer", old_name="loan_id", new_name="loan"),
        migrations.RunPython(assert_transfer_integrity, migrations.RunPython.noop),
        migrations.AddField(
            model_name="transfer",
            name="amount_kes",
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name="transfer",
            name="idempotency_key",
            field=models.CharField(max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="transfer",
            name="currency",
            field=models.CharField(default="KES", max_length=3),
        ),
        migrations.AddField(
            model_name="transfer",
            name="provider_transfer_code",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="transfer",
            name="provider_reference",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="transfer",
            name="attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="transfer",
            name="max_attempts",
            field=models.PositiveSmallIntegerField(default=5),
        ),
        migrations.AddField(
            model_name="transfer",
            name="last_error",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="transfer",
            name="next_retry_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="transfer",
            name="initiated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="transfer",
            name="confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="transfer",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Not started"),
                    ("RECIPIENT_CREATED", "Recipient created"),
                    ("INITIATING", "Request in flight"),
                    ("PENDING_PROVIDER", "Accepted by provider, awaiting confirmation"),
                    ("SUCCESS", "Confirmed paid"),
                    ("FAILED", "Failed"),
                    ("REVERSED", "Reversed"),
                    ("UNKNOWN", "Outcome unknown — reconcile before retry"),
                    ("CANCELLED", "Cancelled"),
                ],
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.RunPython(backfill_transfers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="transfer",
            name="amount_kes",
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterField(
            model_name="transfer",
            name="idempotency_key",
            field=models.CharField(max_length=64, unique=True),
        ),
        migrations.AlterField(
            model_name="transfer",
            name="loan",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="transfer",
                to="loans.loan",
            ),
        ),
        migrations.RemoveField(model_name="transfer", name="initiated"),
        migrations.AlterModelOptions(
            name="transfer",
            options={"ordering": ["-created_at"]},
        ),
        # Replace auto-generated index names with explicit, stable names.
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["loan"], name="payments_loan_idx"),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["status", "created_at"], name="payments_status_created_idx"),
        ),
        migrations.AddIndex(
            model_name="transfer",
            index=models.Index(fields=["loan"], name="transfers_loan_idx"),
        ),
        migrations.AddIndex(
            model_name="transfer",
            index=models.Index(fields=["status", "next_retry_at"], name="transfers_status_retry_idx"),
        ),
        # -------------------------------------------------------------
        # New models
        # -------------------------------------------------------------
        migrations.CreateModel(
            name="PaymentEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("from_status", models.CharField(max_length=20)),
                ("to_status", models.CharField(max_length=20)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="payments.payment")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["payment"], name="payments_event_payment_idx")],
            },
        ),
        migrations.CreateModel(
            name="TransferEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("from_status", models.CharField(max_length=20)),
                ("to_status", models.CharField(max_length=20)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("transfer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="payments.transfer")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["transfer"], name="transfers_event_transfer_idx")],
            },
        ),
        migrations.CreateModel(
            name="Refund",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference", models.CharField(max_length=64, unique=True)),
                ("amount_kes", models.PositiveIntegerField()),
                ("currency", models.CharField(default="KES", max_length=3)),
                ("reason", models.CharField(max_length=255)),
                ("status", models.CharField(choices=[("PENDING", "Requested"), ("PROCESSING", "Submitted to provider"), ("SUCCEEDED", "Refunded"), ("FAILED", "Failed"), ("CANCELLED", "Cancelled")], default="PENDING", max_length=16)),
                ("provider_refund_id", models.CharField(blank=True, default="", max_length=64)),
                ("provider_response", models.JSONField(blank=True, default=dict)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("loan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="refunds", to="loans.loan")),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="refunds", to="payments.payment")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="refunds_requested", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["status"], name="refunds_status_idx")],
            },
        ),
        migrations.CreateModel(
            name="WebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(default="paystack", max_length=32)),
                ("event_id", models.CharField(max_length=128, unique=True)),
                ("event_type", models.CharField(blank=True, default="", max_length=64)),
                ("reference", models.CharField(blank=True, default="", max_length=64)),
                ("payload", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("RECEIVED", "Received"), ("PROCESSED", "Processed"), ("IGNORED", "Ignored"), ("FAILED", "Failed")], default="RECEIVED", max_length=16)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("error", models.CharField(blank=True, default="", max_length=500)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-received_at"],
                "indexes": [
                    models.Index(fields=["status"], name="webhook_status_idx"),
                    models.Index(fields=["reference"], name="webhook_reference_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="LedgerEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entry_group", models.UUIDField(db_index=True, default=uuid.uuid4)),
                ("account", models.CharField(choices=[("CUSTOMER", "Customer"), ("PLATFORM_REVENUE", "Platform revenue"), ("PROVIDER_CLEARING", "Payment provider clearing"), ("LOAN_RECEIVABLE", "Loan receivable"), ("BORROWER_PAYABLE", "Borrower payable"), ("REFUNDS", "Refunds")], max_length=24)),
                ("direction", models.CharField(choices=[("DEBIT", "Debit"), ("CREDIT", "Credit")], max_length=6)),
                ("amount_kes", models.PositiveIntegerField()),
                ("currency", models.CharField(default="KES", max_length=3)),
                ("reference", models.CharField(blank=True, default="", max_length=64)),
                ("memo", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("loan", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="loans.loan")),
                ("payment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="payments.payment")),
                ("refund", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="payments.refund")),
                ("transfer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="payments.transfer")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["entry_group"], name="ledger_group_idx"),
                    models.Index(fields=["reference"], name="ledger_reference_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ReconciliationRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("RUNNING", "Running"), ("COMPLETED", "Completed"), ("FAILED", "Failed")], default="RUNNING", max_length=16)),
                ("dry_run", models.BooleanField(default=True)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("issues", models.JSONField(blank=True, default=list)),
                ("error", models.CharField(blank=True, default="", max_length=500)),
            ],
            options={"ordering": ["-started_at"]},
        ),
    ]

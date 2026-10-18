"""Copy legacy Paystack transaction ids into provider_transaction_id, then drop
the redundant column. No values are lost."""
from django.db import migrations, models
from django.db.models import F


def copy_transaction_ids(apps, schema_editor):
    Payment = apps.get_model("payments", "Payment")
    Payment.objects.exclude(paystack_transaction_id="").update(
        provider_transaction_id=F("paystack_transaction_id")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0002_payment_state_and_ledger"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="provider_transaction_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RunPython(copy_transaction_ids, migrations.RunPython.noop),
        migrations.RemoveField(model_name="payment", name="paystack_transaction_id"),
    ]

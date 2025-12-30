from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Payment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("loan_id", models.IntegerField()),
                ("user_id", models.IntegerField()),
                ("reference", models.CharField(max_length=64, unique=True)),
                ("amount_kes", models.PositiveIntegerField()),
                ("verified", models.BooleanField(default=False)),
                (
                    "paystack_transaction_id",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="Transfer",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("loan_id", models.IntegerField(unique=True)),
                ("reference", models.CharField(max_length=64, unique=True)),
                (
                    "recipient_code",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("initiated", models.BooleanField(default=False)),
                ("status", models.CharField(blank=True, default="", max_length=32)),
                ("raw_last_event", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["reference"], name="payments_pa_referen_c8d45e_idx"),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["loan_id"], name="payments_pa_loan_id_39a7f2_idx"),
        ),
        migrations.AddIndex(
            model_name="transfer",
            index=models.Index(fields=["reference"], name="payments_tr_referen_5f8b21_idx"),
        ),
        migrations.AddIndex(
            model_name="transfer",
            index=models.Index(fields=["loan_id"], name="payments_tr_loan_id_a93c14_idx"),
        ),
    ]

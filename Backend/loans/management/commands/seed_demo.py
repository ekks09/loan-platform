"""Seed deterministic demo data for stakeholder walkthroughs.

Creates four borrowers, one per stage of the loan funnel, plus the staff
reviewer who issued their offers. Every row is created only through the same
domain methods the real flow uses (``issue_offer``, ``accept_offer``,
``transition_to``), so seeded loans always satisfy the state machine and the
``LoanEvent`` audit trail reads like a real history.

Safe to re-run: users and loans are looked up by stable keys first, so a
second invocation is a no-op. Pass ``--reset`` to wipe the seeded rows and
start over. NEVER run this in production — it creates known credentials.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from loans.models import Loan
from users.models import ConsentRecord, User

STAFF_PHONE = "254700000001"
STAFF_ID = "10000001"
STAFF_PASSWORD = "StaffDemo!2026"  # nosec B105 -- deliberate demo-only credential, never production
BORROWER_PASSWORD = "DemoPass!2026"  # nosec B105 -- deliberate demo-only credential, never production

DOC_PATHS = {
    "id_front_path": "demo/seeded/id-front.jpg",
    "id_back_path": "demo/seeded/id-back.jpg",
    "selfie_path": "demo/seeded/selfie.jpg",
}

# (phone, national_id, amount in KES, target stage)
BORROWERS = (
    ("254711111111", "20445566", 1500, "pending"),
    ("254722222222", "30445567", 8000, "approved"),
    ("254733333333", "40445568", 20000, "disbursement_pending"),
    ("254744444444", "50445569", 35000, "disbursed"),
)


class Command(BaseCommand):
    help = "Seed demo borrowers and loans across every lifecycle stage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the seeded users (and cascaded loans) before seeding.",
        )

    def handle(self, *args, **options):
        if settings.DEPLOY_ENVIRONMENT == "production":
            raise CommandError("seed_demo refuses to run against production.")

        seeded_phones = [p for p, _nid, _amt, _stage in BORROWERS] + [STAFF_PHONE]
        if options["reset"]:
            deleted, _ = User.objects.filter(phone__in=seeded_phones).delete()
            self.stdout.write(f"Reset: removed {deleted} rows.")

        staff = self._staff()
        for phone, national_id, amount, stage in BORROWERS:
            user = self._borrower(phone, national_id, staff)
            loan, created = Loan.objects.get_or_create(
                user=user,
                amount=amount,
                defaults={"mpesa_phone": phone},
            )
            if not created:
                self.stdout.write(f"  exists: {phone} loan #{loan.pk} ({loan.status})")
                continue
            self._advance(loan, stage, staff)
            self.stdout.write(
                f"  seeded: {phone} — KES {amount:,} ({stage}, fee {loan.service_fee})"
            )

        self.stdout.write(self.style.SUCCESS("\nDemo logins (password for all borrowers):"))
        self.stdout.write(f"  borrower password : {BORROWER_PASSWORD}")
        self.stdout.write(f"  staff login       : {STAFF_PHONE} / {STAFF_PASSWORD}")
        for phone, _nid, _amt, stage in BORROWERS:
            self.stdout.write(f"  {phone}  →  {stage}")

    # --------------------------------------------------------------
    def _staff(self) -> User:
        staff, created = User.objects.get_or_create(
            phone=STAFF_PHONE,
            defaults={
                "national_id": STAFF_ID,
                "is_staff": True,
                "verification_status": User.VerificationStatus.VERIFIED,
                "verified_at": timezone.now(),
            },
        )
        if created:
            staff.set_password(STAFF_PASSWORD)
            staff.save()
        return staff

    def _borrower(self, phone: str, national_id: str, staff: User) -> User:
        user, created = User.objects.get_or_create(
            phone=phone,
            defaults={
                "national_id": national_id,
                "verification_status": User.VerificationStatus.VERIFIED,
                "verified_at": timezone.now(),
                "verified_by": staff,
                "documents_uploaded_at": timezone.now(),
                **DOC_PATHS,
            },
        )
        if created:
            user.set_password(BORROWER_PASSWORD)
            user.save()
        ConsentRecord.objects.get_or_create(
            user=user,
            purpose=ConsentRecord.Purpose.IDENTITY_PROCESSING,
            version=settings.CONSENT_VERSION,
        )
        ConsentRecord.objects.get_or_create(
            user=user,
            purpose=ConsentRecord.Purpose.TERMS,
            version=settings.TERMS_VERSION,
        )
        return user

    def _advance(self, loan: Loan, stage: str, staff: User) -> None:
        if stage == "pending":
            return
        loan.issue_offer(actor=staff)
        if stage == "approved":
            return
        loan.accept_offer(version=loan.offer_terms_version, actor=loan.user)
        if settings.FEE_COLLECTION_MODE == "upfront":
            loan.service_fee_paid = True
            loan.save(update_fields=["service_fee_paid", "updated_at"])
        loan.transition_to(
            Loan.Status.DISBURSEMENT_PENDING,
            actor=staff,
            reason="Service fee paid (seeded); queued for disbursement",
        )
        if stage == "disbursed":
            loan.transition_to(
                Loan.Status.DISBURSED,
                actor=staff,
                reason="Disbursed (seeded demo record — no real money moved)",
            )

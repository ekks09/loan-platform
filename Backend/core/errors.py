"""Domain-level errors with stable API codes.

Views should raise these instead of returning ad-hoc dicts. The DRF exception
handler (``core.exceptions``) converts them into the documented error envelope.
"""
from __future__ import annotations


class DomainError(Exception):
    """Base class for expected, client-actionable business errors."""

    code = "domain_error"
    http_status = 400
    default_message = "Request could not be processed."

    def __init__(self, message: str | None = None, *, details: dict | None = None):
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


class InvalidLoanAmount(DomainError, ValueError):
    code = "invalid_loan_amount"
    http_status = 400
    default_message = "Loan amount is not supported."


class InvalidTransition(DomainError, ValueError):
    code = "invalid_state_transition"
    http_status = 409
    default_message = "This action is not allowed in the loan's current state."


class LoanNotFound(DomainError):
    code = "loan_not_found"
    http_status = 404
    default_message = "Loan not found."


class FeeCollectionDisabled(DomainError):
    code = "fee_collection_disabled"
    http_status = 409
    default_message = (
        "Upfront fee collection is disabled pending product and legal approval. "
        "No payment has been taken."
    )


class PaymentsDisabled(DomainError):
    code = "payments_disabled"
    http_status = 409
    default_message = "Payment processing is currently disabled."


class LivePaymentsBlocked(DomainError):
    code = "live_payments_blocked"
    http_status = 409
    default_message = (
        "Live payment operations are disabled in this environment. "
        "Use Paystack test credentials or enable live mode explicitly."
    )


class StorageUnavailable(DomainError):
    code = "storage_unavailable"
    http_status = 503
    default_message = "Document storage is not configured. Please try again later."


class InvalidFileUpload(DomainError):
    code = "invalid_file_upload"
    http_status = 400
    default_message = "The uploaded file is not valid."


class ConsentRequired(DomainError):
    code = "consent_required"
    http_status = 403
    default_message = (
        "You must accept the current privacy notice and terms before continuing."
    )


class IdentityNotVerified(DomainError):
    code = "identity_not_verified"
    http_status = 403
    default_message = "Your identity must be verified before you can borrow."


class PaymentVerificationFailed(DomainError):
    code = "payment_verification_failed"
    http_status = 502
    default_message = "We could not verify the payment with Paystack. Please retry."


class DisbursementProviderError(DomainError):
    code = "disbursement_provider_error"
    http_status = 502
    default_message = "The payment provider could not start the disbursement."


class ReconciliationRequired(DomainError):
    code = "reconciliation_required"
    http_status = 409
    default_message = (
        "The previous operation has an uncertain outcome and is queued for "
        "reconciliation before it can be retried."
    )

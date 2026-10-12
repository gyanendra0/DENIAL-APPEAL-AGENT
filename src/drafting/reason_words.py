"""The plain words for each denial reason category, as a drafting call gives them to the model.

From prompt `v3` the model is sent these words in place of the category value, so it cannot
copy a value such as `other` into a letter. They are the wordings the denial letters
themselves use; a test keeps the two copies equal.
"""

from src.db.models import DenialReasonCategory

REASON_WORDS = {
    DenialReasonCategory.NONCOVERED: "the service is not covered",
    DenialReasonCategory.MEDICAL_NECESSITY: "the service was not found to be medically necessary",
    DenialReasonCategory.DUPLICATE: "the service duplicates one that was already processed",
    DenialReasonCategory.BENEFITS_EXHAUSTED: "the benefit limit for the service has been reached",
    DenialReasonCategory.COORDINATION_OF_BENEFITS: "another payer is primary for the service",
    DenialReasonCategory.OTHER: "the service could not be paid as submitted",
}

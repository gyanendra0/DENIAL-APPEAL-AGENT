"""Builds the question a claim's evidence search asks.

A denial letter holds a reason category and procedure codes, and no words about the service.
So the question is one fixed phrase for the category, followed by the codes. It is a weak
question: almost no code appears in the evidence text, so the passages found may not be
about the claim's service. A pure function: no model call, no database.

Any change to a phrase or to the way the codes are added changes what a search finds, so
it needs a new `SEARCH_QUESTION_VERSION`. The version is stored with every draft.
"""

from collections.abc import Mapping, Sequence

from src.db.models import DenialReasonCategory

# Names the phrases below and the way the codes are added. Stored with every draft.
SEARCH_QUESTION_VERSION = "v1"
SEARCH_PHRASES: Mapping[DenialReasonCategory, str] = {
    DenialReasonCategory.NONCOVERED: (
        "Medicare coverage of the item or service and the conditions under which it is covered"
    ),
    DenialReasonCategory.MEDICAL_NECESSITY: (
        "reasonable and necessary: indications and limitations of coverage for the service"
    ),
    DenialReasonCategory.DUPLICATE: "duplicate claim for the same service",
    DenialReasonCategory.BENEFITS_EXHAUSTED: "frequency limits and the number of covered services",
    DenialReasonCategory.COORDINATION_OF_BENEFITS: (
        "Medicare as secondary payer and coverage by another insurer"
    ),
    DenialReasonCategory.OTHER: "coverage of the item or service",
}
# Put between the phrase and the codes.
PROCEDURE_CODES_LEAD = "Procedure codes:"


def build_search_question(
    category: DenialReasonCategory | None, procedure_codes: Sequence[str]
) -> str:
    """Return the search question for a denial of `category` with these procedure codes.

    A letter whose reason is unknown (`None`) gets the phrase of `other`. Each code is added
    once, in the order given; with no code the question is the phrase alone.
    """
    phrase = SEARCH_PHRASES[category or DenialReasonCategory.OTHER]
    codes = list(dict.fromkeys(procedure_codes))
    if not codes:
        return phrase
    return f"{phrase}. {PROCEDURE_CODES_LEAD} {', '.join(codes)}."

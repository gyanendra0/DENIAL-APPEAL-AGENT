"""Builds the question a claim's evidence search asks.

A denial letter holds a reason category and procedure codes, and no words about the service.
So the question is one fixed phrase for the category, and nothing else. The phrases ask for
general rules (what is excluded, what "not reasonable and necessary" means, the right to a
second look), so every claim with the same reason gets the same passages. The procedure codes
are not in the question: no code appears in the evidence text, and a code only lowers the
similarity of the passages that fit. A pure function: no model call, no database.

A search result that is too unlike the question is not evidence for the claim, so the
caller passes `MIN_EVIDENCE_SIMILARITY` to the search and a weaker match is left out.

Any change to a phrase, to what else goes into the question or to the minimum similarity
changes what a search finds, so it needs a new `SEARCH_QUESTION_VERSION`. The version is stored
with every draft.
"""

from collections.abc import Mapping

from src.db.models import DenialReasonCategory

# Names the phrases below, what else goes into the question and the minimum similarity. Stored
# with every draft. `v2` added the minimum; the phrases and the codes were those of `v1`.
# `v3`: new phrases for `other`, `noncovered` and `medical_necessity`, and no procedure codes.
SEARCH_QUESTION_VERSION = "v3"
SEARCH_PHRASES: Mapping[DenialReasonCategory, str] = {
    DenialReasonCategory.NONCOVERED: (
        "services excluded from Medicare coverage and the exceptions to the exclusions"
    ),
    DenialReasonCategory.MEDICAL_NECESSITY: (
        "services that are not reasonable and necessary for the diagnosis or treatment of"
        " illness or injury and limitation on liability for such a denial"
    ),
    DenialReasonCategory.DUPLICATE: "duplicate claim for the same service",
    DenialReasonCategory.BENEFITS_EXHAUSTED: "frequency limits and the number of covered services",
    DenialReasonCategory.COORDINATION_OF_BENEFITS: (
        "Medicare as secondary payer and coverage by another insurer"
    ),
    DenialReasonCategory.OTHER: (
        "how to appeal a denied claim: the right to request a redetermination of an initial"
        " determination"
    ),
}
# The lowest similarity a match may have to be shown to the drafting model. A judgement from
# 19 hand-made questions and 55 claims, not a tuned value: it keeps 17 of the 19 correct hits
# and drops most passages that do not fit the claim. A correct passage below it is lost.
MIN_EVIDENCE_SIMILARITY = 0.55


def build_search_question(category: DenialReasonCategory | None) -> str:
    """Return the search question for a denial of `category`: the category's phrase.

    A letter whose reason is unknown (`None`) gets the phrase of `other`.
    """
    return SEARCH_PHRASES[category or DenialReasonCategory.OTHER]

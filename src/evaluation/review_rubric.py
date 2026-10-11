"""The rubric a reviewer scores a drafted appeal letter with, and the share that must be met.

The Stage 4 done condition (docs/05-build-plan.md): a reviewer accepts at least 60% of drafts
with only light edits. The reviewer is a person. Nothing here scores a draft.

A draft gets one of three verdicts. Two of them count as accepted; a `reject` carries one
reason, so the rejected drafts show what to improve next.
"""

from decimal import Decimal
from enum import StrEnum

ACCEPTANCE_TARGET = Decimal("0.60")
# How many sentences a reviewer may reword in a draft that still counts as accepted.
LIGHT_EDIT_SENTENCES = 3


class Verdict(StrEnum):
    ACCEPT = "accept"
    ACCEPT_LIGHT_EDITS = "accept_light_edits"
    REJECT = "reject"


class RejectReason(StrEnum):
    WRONG_FACT = "wrong_fact"
    NO_POLICY_ARGUMENT = "no_policy_argument"
    CITATION_DOES_NOT_FIT = "citation_does_not_fit"
    POOR_WORDING = "poor_wording"
    OTHER = "other"


ACCEPTED_VERDICTS = frozenset({Verdict.ACCEPT, Verdict.ACCEPT_LIGHT_EDITS})

VERDICT_MEANINGS = {
    Verdict.ACCEPT: "you would send the draft as it is.",
    Verdict.ACCEPT_LIGHT_EDITS: (
        "you would send it after changing the wording of at most"
        f" {LIGHT_EDIT_SENTENCES} sentences. No fact is corrected and no policy argument is"
        " added or removed."
    ),
    Verdict.REJECT: (
        "anything else. A fact that differs from the denial letter (name, claim number, date,"
        " amount, service), a cited quote that does not support the sentence it sits under, no"
        f" real argument for overturning the denial, or a rewrite of more than"
        f" {LIGHT_EDIT_SENTENCES} sentences."
    ),
}

REJECT_REASON_MEANINGS = {
    RejectReason.WRONG_FACT: "a fact differs from the denial letter.",
    RejectReason.NO_POLICY_ARGUMENT: "the draft gives no real argument for overturning the denial.",
    RejectReason.CITATION_DOES_NOT_FIT: (
        "a cited quote does not support the sentence it sits under."
    ),
    RejectReason.POOR_WORDING: f"more than {LIGHT_EDIT_SENTENCES} sentences need rewording.",
    RejectReason.OTHER: "none of the above; say why in the note.",
}

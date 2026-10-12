"""Drafts one appeal letter from a denial letter's fields and the retrieved evidence.

One model call per letter, through the LLM gateway. The answer is parsed into `AppealDraft`
and then checked by the citation validator, for placeholder text and for wrong facts. An
answer that was cut off, is not JSON, does not fit the shape, fails the validator, holds text
in square brackets or states a wrong fact is an error: nothing is repaired and no second call
is made. Only a draft that passed comes back, as an `AppealLetter`.

Letter text, quotes, evidence text and the model's answer never appear in a log line or an
error message; the one log line per call is the gateway's. Nothing here sends a letter
anywhere: the result is for a person to review.
"""

import json
from collections import Counter
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from src.db.models import LlmProvider
from src.drafting.citation_validator import CitationProblem, validate_citations
from src.drafting.fact_check import FactProblem, find_fact_problems
from src.drafting.placeholder_check import find_placeholder_paragraphs
from src.drafting.prompts import DRAFTING_PURPOSE, DraftingPrompt
from src.drafting.reason_words import REASON_WORDS
from src.drafting.schemas import (
    AppealDraft,
    AppealLetter,
    Citation,
    LetterParagraph,
    evidence_label,
)
from src.extraction.schemas import DenialLetterExtraction, ExtractedDeniedLine
from src.llm.gateway import LlmGateway, LlmRequest
from src.rag.retrieval import EvidenceMatch

# The output limit sent with every drafting call. An estimate, not measured: a letter of a
# few paragraphs with its quotes.
DRAFTING_MAX_OUTPUT_TOKENS = 1500
# The stop reason a provider gives when the answer reached the output limit.
LENGTH_STOP_REASON = "length"
# A label has at most two digits (`E99`), so a draft is never written from more matches.
MAX_EVIDENCE_MATCHES = 99
# The prompt versions written for the first message: every value of the letter, the two
# claim totals and the category values included. Every later version gets the message
# without the totals and with the reason in plain words.
FULL_VALUE_MESSAGE_VERSIONS = frozenset({"v1", "v2"})
# The claim totals. A prompt from `v3` on never sees them, so it cannot give one as the
# amount of a service line.
TOTAL_FIELDS = frozenset({"total_allowed_charge_amount", "total_payment_amount"})


class DraftingError(Exception):
    """The model answered, but the answer cannot be used. Its text never quotes the answer."""


class DraftTruncatedError(DraftingError):
    """The answer reached the output limit before it was complete."""


class DraftParseError(DraftingError):
    """The answer is not JSON, or does not fit the shape of a draft."""


class DraftBlockedError(DraftingError):
    """The citation validator found a problem. `problems` holds every one of them."""

    def __init__(self, problems: tuple[CitationProblem, ...]) -> None:
        counts = Counter(problem.kind.value for problem in problems)
        summary = ", ".join(f"{kind} x{count}" for kind, count in sorted(counts.items()))
        super().__init__(f"the draft is blocked by the citation validator: {summary}")
        self.problems = problems


class DraftPlaceholderError(DraftingError):
    """A paragraph holds text in square brackets. `paragraph_indexes` counts from 0."""

    def __init__(self, paragraph_indexes: tuple[int, ...]) -> None:
        super().__init__(
            f"the draft holds text in square brackets in {len(paragraph_indexes)} paragraph(s)"
        )
        self.paragraph_indexes = paragraph_indexes


class DraftFactError(DraftingError):
    """The fact check found a wrong fact. `problems` holds every one of them."""

    def __init__(self, problems: tuple[FactProblem, ...]) -> None:
        counts = Counter(problem.kind.value for problem in problems)
        summary = ", ".join(f"{kind} x{count}" for kind, count in sorted(counts.items()))
        super().__init__(f"the draft is refused by the fact check: {summary}")
        self.problems = problems


class DraftResult(BaseModel):
    """One checked letter, and who produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    letter: AppealLetter
    provider: LlmProvider
    model_name: str
    prompt_version: str


def draft_appeal(
    gateway: LlmGateway,
    prompt: DraftingPrompt,
    letter: DenialLetterExtraction,
    matches: Sequence[EvidenceMatch],
) -> DraftResult:
    """Draft an appeal for the denial letter `letter`, citing only the evidence in `matches`.

    Spends money and can take several seconds: it makes one model call through `gateway`.
    `matches` is the search result in its order; the model sees the first as `E1`. Raises
    `DraftTruncatedError`, `DraftParseError`, `DraftBlockedError`, `DraftPlaceholderError`
    or `DraftFactError` when the answer cannot be used; the gateway's own errors (over
    budget, no provider available) pass through.
    """
    result = gateway.complete(
        LlmRequest(
            system=prompt.system,
            user=build_user_message(letter, matches, prompt_version=prompt.version),
            max_tokens=DRAFTING_MAX_OUTPUT_TOKENS,
            prompt_version=prompt.version,
            purpose=DRAFTING_PURPOSE,
            response_schema=AppealDraft.model_json_schema(),
        )
    )
    if result.stop_reason == LENGTH_STOP_REASON:
        raise DraftTruncatedError(
            f"the answer reached the output limit ({DRAFTING_MAX_OUTPUT_TOKENS} tokens)"
        )
    try:
        draft = AppealDraft.model_validate_json(result.text)
    except ValidationError:
        # `from None`: the parser's error quotes the answer, which must not reach a traceback.
        raise DraftParseError(
            "the answer is not JSON or does not fit the shape of a draft"
        ) from None
    check = validate_citations(draft, matches)
    if not check.passed:
        raise DraftBlockedError(check.problems)
    placeholder_paragraphs = find_placeholder_paragraphs(draft)
    if placeholder_paragraphs:
        raise DraftPlaceholderError(placeholder_paragraphs)
    fact_problems = find_fact_problems(draft, letter)
    if fact_problems:
        raise DraftFactError(fact_problems)
    return DraftResult(
        letter=_cited_letter(draft, matches),
        provider=result.provider,
        model_name=result.model_name,
        prompt_version=result.prompt_version,
    )


def build_user_message(
    letter: DenialLetterExtraction, matches: Sequence[EvidenceMatch], *, prompt_version: str
) -> str:
    """Return the user message of a drafting call: the letter's values and the evidence.

    The values go without their confidence scores. Each match gets its label by position.
    For the prompts `v1` and `v2` every value is sent as it is stored. For a later prompt
    the two claim totals are left out, and the reason of the denial and of each denied line
    goes in plain words under the keys `denial_reason` and `reason`.
    """
    if len(matches) > MAX_EVIDENCE_MATCHES:
        raise ValueError(
            f"a draft is written from at most {MAX_EVIDENCE_MATCHES} matches, got {len(matches)}"
        )
    if prompt_version in FULL_VALUE_MESSAGE_VERSIONS:
        values = {name: field["value"] for name, field in letter.model_dump(mode="json").items()}
    else:
        values = _values_in_plain_words(letter)
    evidence = [
        {
            "label": evidence_label(position),
            "document_title": match.document_title,
            "section_number": match.section_number,
            "section_title": match.section_title,
            "text": match.text,
        }
        for position, match in enumerate(matches)
    ]
    return json.dumps({"denial_letter": values, "evidence": evidence}, ensure_ascii=False, indent=2)


def _values_in_plain_words(letter: DenialLetterExtraction) -> dict[str, Any]:
    """Return the letter's values with no claim total and no category value."""
    values: dict[str, Any] = {}
    for name, field in letter.model_dump(mode="json").items():
        if name in TOTAL_FIELDS:
            continue
        if name == "denied_lines":
            lines = letter.denied_lines.value
            values[name] = None if lines is None else [_line_in_plain_words(line) for line in lines]
        elif name == "denial_reason_category":
            category = letter.denial_reason_category.value
            values["denial_reason"] = None if category is None else REASON_WORDS[category]
        else:
            values[name] = field["value"]
    return values


def _line_in_plain_words(line: ExtractedDeniedLine) -> dict[str, Any]:
    """Return one denied line with its reason in plain words under the key `reason`."""
    values = line.model_dump(mode="json")
    del values["reason_category"]
    values["reason"] = REASON_WORDS[line.reason_category]
    return values


def _cited_letter(draft: AppealDraft, matches: Sequence[EvidenceMatch]) -> AppealLetter:
    """Turn each label of a draft that PASSED the validator into the four citation fields."""
    match_by_label = {evidence_label(position): match for position, match in enumerate(matches)}
    return AppealLetter(
        paragraphs=tuple(
            LetterParagraph(
                kind=paragraph.kind,
                text=paragraph.text,
                citations=tuple(
                    Citation(
                        source=match_by_label[citation.label].source,
                        source_document_id=match_by_label[citation.label].source_document_id,
                        chunk_index=match_by_label[citation.label].chunk_index,
                        text_sha256=match_by_label[citation.label].text_sha256,
                        quote=citation.quote,
                    )
                    for citation in paragraph.citations
                ),
            )
            for paragraph in draft.paragraphs
        )
    )

import re
from pathlib import Path

import pytest

from src.db.models import DenialReasonCategory
from src.drafting.citation_validator import MIN_QUOTE_CHARACTERS
from src.drafting.prompts import (
    DRAFTING_PROMPT_VERSION,
    DRAFTING_PURPOSE,
    DraftingPromptError,
    load_drafting_prompt,
)
from src.drafting.reason_words import REASON_WORDS
from src.drafting.schemas import AppealDraft, DraftCitation, DraftParagraph, ParagraphKind
from src.extraction.schemas import DenialLetterExtraction, ExtractedDeniedLine
from src.llm.gateway import LlmRequest
from src.synth.denial_letter import REASON_WORDING

# The prompt files in the repository: instructions only, so they are not test data.
REAL_PROMPT_DIR = Path(__file__).parents[2] / "config" / "prompts"
REAL_VERSIONS = ["v1", "v2", "v3"]
# The keys of the user message that are not a value of the denial letter.
USER_MESSAGE_KEYS = {"denial_letter", "document_title", "section_number", "section_title"}
# The keys of the message a prompt from `v3` on is sent: no claim total, and the reason in
# plain words under `denial_reason` and, for a denied line, `reason`.
PLAIN_LETTER_KEYS = (
    set(DenialLetterExtraction.model_fields)
    - {"total_allowed_charge_amount", "total_payment_amount", "denial_reason_category"}
) | {"denial_reason"}
PLAIN_LINE_KEYS = (set(ExtractedDeniedLine.model_fields) - {"reason_category"}) | {"reason"}
# A name in double quotes with an underscore: a key or a category value, never a plain word.
QUOTED_NAME = re.compile(r'"([a-z]+(?:_[a-z]+)+)"')


def _write(prompt_dir: Path, version: str, text: str) -> Path:
    folder = prompt_dir / "drafting" / version
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "appeal_letter.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_purpose_is_drafting_and_the_default_version_is_v2() -> None:
    assert DRAFTING_PURPOSE == "drafting"
    assert DRAFTING_PROMPT_VERSION == "v2"


def test_loads_the_prompt_of_the_asked_version(tmp_path: Path) -> None:
    _write(tmp_path, "v1", "made-up drafting instruction, v1")
    _write(tmp_path, "v2", "made-up drafting instruction, v2")

    prompt = load_drafting_prompt(tmp_path, "v2")

    assert prompt.system == "made-up drafting instruction, v2"
    assert prompt.version == "v2"


def test_the_text_is_read_as_utf8_without_the_space_around_it(tmp_path: Path) -> None:
    _write(tmp_path, "v1", "\n  appeal: sí, naïve  \n\n")

    assert load_drafting_prompt(tmp_path, "v1").system == "appeal: sí, naïve"


def test_a_version_that_does_not_exist_is_an_error_naming_the_file(tmp_path: Path) -> None:
    _write(tmp_path, "v1", "made-up drafting instruction")

    with pytest.raises(DraftingPromptError, match=r"not found: .*drafting/v9/appeal_letter\.txt"):
        load_drafting_prompt(tmp_path, "v9")


@pytest.mark.parametrize("text", ["", "  \n\t\n"])
def test_an_empty_prompt_file_is_an_error(tmp_path: Path, text: str) -> None:
    _write(tmp_path, "v1", text)

    with pytest.raises(DraftingPromptError, match="is empty"):
        load_drafting_prompt(tmp_path, "v1")


@pytest.mark.parametrize("version", ["", "1", "v", "V1", "v1.0", "../v1", "v1/..", "latest", "v1 "])
def test_a_badly_formed_version_is_refused_before_any_file_is_read(
    tmp_path: Path, version: str
) -> None:
    _write(tmp_path, "v1", "made-up drafting instruction")

    with pytest.raises(DraftingPromptError, match="not of the form"):
        load_drafting_prompt(tmp_path, version)


@pytest.mark.parametrize("version", REAL_VERSIONS)
def test_each_real_prompt_loads_and_fits_a_request(version: str) -> None:
    prompt = load_drafting_prompt(REAL_PROMPT_DIR, version)

    request = LlmRequest(
        system=prompt.system,
        user="{}",
        max_tokens=10,
        prompt_version=prompt.version,
        purpose=DRAFTING_PURPOSE,
    )
    assert request.prompt_version == version


def test_the_default_version_is_one_of_the_real_prompts() -> None:
    assert DRAFTING_PROMPT_VERSION in REAL_VERSIONS


def test_a_version_with_no_real_prompt_file_is_refused() -> None:
    with pytest.raises(DraftingPromptError, match="not found"):
        load_drafting_prompt(REAL_PROMPT_DIR, "v999")


@pytest.mark.parametrize("version", REAL_VERSIONS)
def test_each_real_prompt_names_every_key_and_kind_of_the_draft_shape(version: str) -> None:
    system = load_drafting_prompt(REAL_PROMPT_DIR, version).system

    for model in (AppealDraft, DraftParagraph, DraftCitation):
        for name in model.model_fields:
            assert f'"{name}"' in system
    for kind in ParagraphKind:
        assert f'"{kind.value}"' in system


@pytest.mark.parametrize("version", REAL_VERSIONS)
def test_each_real_prompt_gives_the_validators_minimum_quote_length(version: str) -> None:
    system = load_drafting_prompt(REAL_PROMPT_DIR, version).system

    assert f"at least {MIN_QUOTE_CHARACTERS} characters" in system


def test_the_v2_prompt_is_the_v1_prompt_up_to_the_rules_it_adds() -> None:
    v1 = load_drafting_prompt(REAL_PROMPT_DIR, "v1").system
    v2 = load_drafting_prompt(REAL_PROMPT_DIR, "v2").system

    # The JSON shape, the two kinds and the quote rule are word for word those of `v1`.
    shared = v1[: v1.index("- Every other paragraph is")]
    assert v2.startswith(shared)
    assert v2 != v1


@pytest.mark.parametrize("category", list(DenialReasonCategory))
def test_the_v2_prompt_gives_the_denial_letters_plain_words_for_each_category(
    category: DenialReasonCategory,
) -> None:
    system = load_drafting_prompt(REAL_PROMPT_DIR, "v2").system

    assert f'- "{category.value}": {REASON_WORDING[category]}\n' in system + "\n"


def test_every_name_the_v2_prompt_quotes_is_a_real_key_or_category_value() -> None:
    system = load_drafting_prompt(REAL_PROMPT_DIR, "v2").system
    known = (
        USER_MESSAGE_KEYS
        | set(DenialLetterExtraction.model_fields)
        | set(ExtractedDeniedLine.model_fields)
        | {category.value for category in DenialReasonCategory}
    )

    quoted = set(QUOTED_NAME.findall(system))

    assert quoted - known == set()
    # The values the rules lean on are really named.
    assert {
        "patient_name",
        "payer_name",
        "member_id",
        "denied_lines",
        "hcpcs_code",
        "allowed_charge_amount",
        "payment_amount",
        "reason_category",
        "denial_reason_category",
        "total_allowed_charge_amount",
        "total_payment_amount",
    } <= quoted


@pytest.mark.parametrize(
    "words",
    [
        "no address block, no date line and no subject line",
        "Never write text in square",
        "sample letter or a sample notice",
        "Call them the claim totals",
        "Never write a\n  category value in the letter",
        "against no other reason",
        "A quote alone is not a paragraph",
        "Add no fact about the care",
        "do not ask the payer to send documents",
        'write the\n  letter with "other" paragraphs only',
        # Added after the ten rules: what a letter says when the denial names no reason.
        'When "denial_reason_category" is "other", the denial names no specific reason',
        "ask the payer to state the specific reason for the denial",
        "the right to ask for a review\n  of a denied claim",
    ],
)
def test_the_v2_prompt_holds_each_rule_of_the_design(words: str) -> None:
    assert words in load_drafting_prompt(REAL_PROMPT_DIR, "v2").system


def test_the_v2_prompt_holds_no_square_bracket_of_its_own() -> None:
    # An example such as `[Your Name]` in the prompt could be copied into a letter.
    system = load_drafting_prompt(REAL_PROMPT_DIR, "v2").system

    assert "[" not in system
    assert "]" not in system


def test_the_v3_prompt_starts_as_the_first_two_do() -> None:
    v1 = load_drafting_prompt(REAL_PROMPT_DIR, "v1").system
    v2 = load_drafting_prompt(REAL_PROMPT_DIR, "v2").system
    v3 = load_drafting_prompt(REAL_PROMPT_DIR, "v3").system

    # The JSON shape, the two kinds and the quote rule are word for word those of `v1`.
    assert v3.startswith(v1[: v1.index("- Every other paragraph is")])
    assert v3 != v2


@pytest.mark.parametrize(
    "words",
    [
        # The three rules for the citation faults of the third review.
        "Say nothing about a rule that the quote itself does not say",
        "improper, wrong or against the rules unless the quote says\n  so",
        "Never cite a rule that supports the denial",
        "not reasonable and necessary is not covered supports the denial",
        'write no "policy" paragraph',
        # The rules that fit the message without totals and category values.
        '"denial_letter" holds no amount for the whole claim',
        "Write no claim total and no sum of\n  several lines",
        "give the reason in plain words",
        "Never give a single word or the name of a category",
        'When "denial_reason" is "the service could not be paid as submitted"',
        # Kept from `v2`.
        "no address block, no date line and no subject line",
        "Never write text in square",
        "sample letter or a sample notice",
        "against no other reason",
        "A quote alone is not a paragraph",
        "Add no fact about the care",
        "do not ask the payer to send documents",
        "ask the payer to state the specific reason for the\n  denial",
        "right to ask for a review of a denied claim",
    ],
)
def test_the_v3_prompt_holds_each_rule_of_the_design(words: str) -> None:
    assert words in load_drafting_prompt(REAL_PROMPT_DIR, "v3").system


def test_every_name_the_v3_prompt_quotes_is_a_key_of_the_message_it_is_sent() -> None:
    system = load_drafting_prompt(REAL_PROMPT_DIR, "v3").system

    quoted = set(QUOTED_NAME.findall(system))

    assert quoted - (USER_MESSAGE_KEYS | PLAIN_LETTER_KEYS | PLAIN_LINE_KEYS) == set()
    assert {
        "patient_name",
        "payer_name",
        "member_id",
        "denied_lines",
        "hcpcs_code",
        "allowed_charge_amount",
        "payment_amount",
        "denial_reason",
    } <= quoted
    assert '"reason"' in system


@pytest.mark.parametrize("category", list(DenialReasonCategory))
def test_the_v3_prompt_names_no_category_value(category: DenialReasonCategory) -> None:
    system = load_drafting_prompt(REAL_PROMPT_DIR, "v3").system

    # `"other"` is also a paragraph kind, so that one is looked for as an entry of a list.
    assert f'- "{category.value}":' not in system
    if category is not DenialReasonCategory.OTHER:
        assert f'"{category.value}"' not in system


def test_the_v3_prompt_names_no_claim_total_and_no_category_key() -> None:
    system = load_drafting_prompt(REAL_PROMPT_DIR, "v3").system

    for name in ("total_allowed_charge_amount", "total_payment_amount", "reason_category"):
        assert name not in system


def test_the_words_the_v3_prompt_gives_for_a_denial_with_no_reason_are_the_real_ones() -> None:
    system = load_drafting_prompt(REAL_PROMPT_DIR, "v3").system

    assert f'When "denial_reason" is "{REASON_WORDS[DenialReasonCategory.OTHER]}"' in system


def test_the_v3_prompt_holds_no_square_bracket_of_its_own() -> None:
    system = load_drafting_prompt(REAL_PROMPT_DIR, "v3").system

    assert "[" not in system
    assert "]" not in system

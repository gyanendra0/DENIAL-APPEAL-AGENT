from pathlib import Path

import pytest

from src.drafting.citation_validator import MIN_QUOTE_CHARACTERS
from src.drafting.prompts import (
    DRAFTING_PROMPT_VERSION,
    DRAFTING_PURPOSE,
    DraftingPromptError,
    load_drafting_prompt,
)
from src.drafting.schemas import AppealDraft, DraftCitation, DraftParagraph, ParagraphKind
from src.llm.gateway import LlmRequest

# The prompt file in the repository: instructions only, so it is not test data.
REAL_PROMPT_DIR = Path(__file__).parents[2] / "config" / "prompts"


def _write(prompt_dir: Path, version: str, text: str) -> Path:
    folder = prompt_dir / "drafting" / version
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "appeal_letter.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_purpose_is_drafting_and_the_default_version_is_v1() -> None:
    assert DRAFTING_PURPOSE == "drafting"
    assert DRAFTING_PROMPT_VERSION == "v1"


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


def test_the_real_v1_prompt_loads_and_fits_a_request() -> None:
    prompt = load_drafting_prompt(REAL_PROMPT_DIR, DRAFTING_PROMPT_VERSION)

    request = LlmRequest(
        system=prompt.system,
        user="{}",
        max_tokens=10,
        prompt_version=prompt.version,
        purpose=DRAFTING_PURPOSE,
    )
    assert request.prompt_version == "v1"


def test_the_real_prompt_names_every_key_and_kind_of_the_draft_shape() -> None:
    system = load_drafting_prompt(REAL_PROMPT_DIR, DRAFTING_PROMPT_VERSION).system

    for model in (AppealDraft, DraftParagraph, DraftCitation):
        for name in model.model_fields:
            assert f'"{name}"' in system
    for kind in ParagraphKind:
        assert f'"{kind.value}"' in system


def test_the_real_prompt_gives_the_validators_minimum_quote_length() -> None:
    system = load_drafting_prompt(REAL_PROMPT_DIR, DRAFTING_PROMPT_VERSION).system

    assert f"at least {MIN_QUOTE_CHARACTERS} characters" in system

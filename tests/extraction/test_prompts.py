import re
from pathlib import Path

import pytest

from src.db.models import DenialReasonCategory, DocumentType
from src.extraction.prompts import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_PURPOSE,
    PromptError,
    load_extraction_prompt,
)
from src.extraction.schemas import ExtractedDeniedLine, schema_for
from src.llm.gateway import LlmRequest
from src.synth.prior_auth import PriorAuthStatus

# The prompt files in the repository: instructions only, so they are not test data.
REAL_PROMPT_DIR = Path(__file__).parents[2] / "config" / "prompts"


def _write(prompt_dir: Path, version: str, document_type: DocumentType, text: str) -> Path:
    folder = prompt_dir / "extraction" / version
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{document_type.value}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_the_prompt_of_the_asked_version_and_document_type(tmp_path: Path) -> None:
    _write(tmp_path, "v1", DocumentType.DENIAL_LETTER, "made-up letter instruction, v1")
    _write(tmp_path, "v2", DocumentType.DENIAL_LETTER, "made-up letter instruction, v2")
    _write(tmp_path, "v2", DocumentType.CLINICAL_NOTE, "made-up note instruction, v2")

    prompt = load_extraction_prompt(tmp_path, "v2", DocumentType.DENIAL_LETTER)

    assert prompt.system == "made-up letter instruction, v2"
    assert prompt.version == "v2"
    assert prompt.document_type is DocumentType.DENIAL_LETTER


def test_the_text_is_read_as_utf8_without_the_space_around_it(tmp_path: Path) -> None:
    _write(tmp_path, "v1", DocumentType.PRIOR_AUTH, "\n  authorisation: sí, naïve  \n\n")

    prompt = load_extraction_prompt(tmp_path, "v1", DocumentType.PRIOR_AUTH)

    assert prompt.system == "authorisation: sí, naïve"


def test_a_missing_prompt_file_is_an_error_naming_the_file(tmp_path: Path) -> None:
    _write(tmp_path, "v1", DocumentType.DENIAL_LETTER, "made-up letter instruction")

    with pytest.raises(PromptError, match=r"not found: .*extraction/v1/clinical_note\.txt"):
        load_extraction_prompt(tmp_path, "v1", DocumentType.CLINICAL_NOTE)


def test_a_version_that_does_not_exist_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "v1", DocumentType.DENIAL_LETTER, "made-up letter instruction")

    with pytest.raises(PromptError, match=r"not found: .*extraction/v9/denial_letter\.txt"):
        load_extraction_prompt(tmp_path, "v9", DocumentType.DENIAL_LETTER)


@pytest.mark.parametrize("text", ["", "  \n\t\n"])
def test_an_empty_prompt_file_is_an_error_naming_the_file(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path, "v1", DocumentType.DENIAL_LETTER, text)

    with pytest.raises(PromptError, match=f"empty: {re.escape(str(path))}"):
        load_extraction_prompt(tmp_path, "v1", DocumentType.DENIAL_LETTER)


@pytest.mark.parametrize("version", ["", "1", "V1", "v", "v1.0", "latest", "../v1", "v1/../../x"])
def test_rejects_a_version_that_is_not_v_and_a_number(tmp_path: Path, version: str) -> None:
    _write(tmp_path, "v1", DocumentType.DENIAL_LETTER, "made-up letter instruction")

    with pytest.raises(PromptError, match="not of the form"):
        load_extraction_prompt(tmp_path, version, DocumentType.DENIAL_LETTER)


def test_a_version_cannot_point_outside_the_prompt_folder(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    outside = tmp_path / "extraction" / "v1"
    outside.mkdir(parents=True)
    (outside / "denial_letter.txt").write_text("text outside the prompt folder", encoding="utf-8")
    (prompt_dir / "extraction").mkdir(parents=True)

    with pytest.raises(PromptError, match="not of the form"):
        load_extraction_prompt(prompt_dir, "../../extraction/v1", DocumentType.DENIAL_LETTER)


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_the_repository_has_a_prompt_for_every_document_type(document_type: DocumentType) -> None:
    prompt = load_extraction_prompt(REAL_PROMPT_DIR, EXTRACTION_PROMPT_VERSION, document_type)

    assert prompt.version == "v2"
    assert prompt.document_type is document_type


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_the_first_prompt_version_is_kept_because_stored_results_name_it(
    document_type: DocumentType,
) -> None:
    first = load_extraction_prompt(REAL_PROMPT_DIR, "v1", document_type)
    current = load_extraction_prompt(REAL_PROMPT_DIR, EXTRACTION_PROMPT_VERSION, document_type)

    assert first.system != current.system


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_the_current_prompt_says_what_a_single_service_date_means(
    document_type: DocumentType,
) -> None:
    prompt = load_extraction_prompt(REAL_PROMPT_DIR, EXTRACTION_PROMPT_VERSION, document_type)

    assert "one service date and not a range" in prompt.system


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_each_real_prompt_lists_exactly_its_schemas_fields(document_type: DocumentType) -> None:
    prompt = load_extraction_prompt(REAL_PROMPT_DIR, EXTRACTION_PROMPT_VERSION, document_type)
    fields = list(schema_for(document_type).model_fields)

    # A field is listed as a line "- name: kind", before the first indented or blank-led block.
    listed = re.findall(r"^- ([a-z_]+):", prompt.system, flags=re.MULTILINE)

    assert [name for name in listed if name in fields] == fields
    assert f"exactly these {len(fields)} keys" in prompt.system
    assert '"value"' in prompt.system
    assert '"confidence"' in prompt.system


def test_the_real_letter_prompt_lists_a_denied_lines_fields_and_every_category() -> None:
    prompt = load_extraction_prompt(
        REAL_PROMPT_DIR, EXTRACTION_PROMPT_VERSION, DocumentType.DENIAL_LETTER
    )
    listed = re.findall(r"^- ([a-z_]+):", prompt.system, flags=re.MULTILINE)
    line_fields = list(ExtractedDeniedLine.model_fields)

    assert [name for name in listed if name in line_fields] == line_fields
    for category in DenialReasonCategory:
        assert category.value in prompt.system


def test_the_real_prior_auth_prompt_names_every_status() -> None:
    prompt = load_extraction_prompt(
        REAL_PROMPT_DIR, EXTRACTION_PROMPT_VERSION, DocumentType.PRIOR_AUTH
    )

    for status in PriorAuthStatus:
        assert status.value in prompt.system


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_a_real_prompt_never_mentions_the_label_or_the_split(document_type: DocumentType) -> None:
    prompt = load_extraction_prompt(REAL_PROMPT_DIR, EXTRACTION_PROMPT_VERSION, document_type)

    for word in ("proxy", "appeal_success", "split", "train", "noise"):
        assert word not in prompt.system.lower()


def test_the_version_and_purpose_fit_a_gateway_request() -> None:
    prompt = load_extraction_prompt(
        REAL_PROMPT_DIR, EXTRACTION_PROMPT_VERSION, DocumentType.CLINICAL_NOTE
    )

    request = LlmRequest(
        system=prompt.system,
        user="made-up document",
        max_tokens=100,
        prompt_version=prompt.version,
        purpose=EXTRACTION_PURPOSE,
    )

    assert request.prompt_version == "v2"
    assert request.purpose == "extraction"

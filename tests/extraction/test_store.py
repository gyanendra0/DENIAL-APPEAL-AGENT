import hashlib
from collections.abc import Iterator
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import DatasetSplit, DocumentExtraction, GeneratedDocument, LlmProvider
from src.db.session import session_scope
from src.extraction.extractor import ExtractionResult
from src.extraction.schemas import DenialLetterExtraction, schema_for
from src.extraction.store import (
    DocumentExtractionRow,
    StoredDocument,
    build_extraction_row,
    read_extraction_rows,
    save_extraction_row,
    select_documents,
    text_sha256,
)
from tests.extraction.helpers import (
    ANSWER_KEYS,
    CLAIM_1,
    CLAIM_3,
    LETTER,
    MODEL_NAME,
    NOTE,
    PATIENT_NAME,
    PROMPT_VERSION,
    VALIDATION_KEYS,
    document_text,
    fields_json,
    stored_documents,
)


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with stored_documents(engine) as session_factory:
        yield session_factory


def _document(claim_id: str = CLAIM_1) -> StoredDocument:
    return StoredDocument(
        source_claim_id=claim_id,
        document_type=LETTER,
        text=document_text(claim_id, LETTER),
        answer_key=ANSWER_KEYS[LETTER],
        missing_field=None,
    )


def _result(provider: LlmProvider = LlmProvider.PRIMARY, **changed: object) -> ExtractionResult:
    return ExtractionResult(
        document_type=LETTER,
        fields=schema_for(LETTER).model_validate(fields_json(LETTER, **changed)),
        provider=provider,
        model_name=MODEL_NAME,
        prompt_version=PROMPT_VERSION,
    )


def test_the_text_hash_is_the_sha256_of_the_utf8_text() -> None:
    assert text_sha256("café") == hashlib.sha256("café".encode()).hexdigest()
    assert len(text_sha256("x")) == 64


def test_selects_the_documents_of_one_split_in_claim_then_type_order(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        validation = select_documents(session, DatasetSplit.VALIDATION)
        test = select_documents(session, DatasetSplit.TEST)
        train = select_documents(session, DatasetSplit.TRAIN)

    assert [document.key for document in validation] == VALIDATION_KEYS
    assert [document.key for document in test] == [(CLAIM_3, LETTER), (CLAIM_3, NOTE)]
    assert train == []


def test_a_limit_takes_the_first_documents_of_the_split(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as session:
        documents = select_documents(session, DatasetSplit.VALIDATION, limit=2)

    assert [document.key for document in documents] == VALIDATION_KEYS[:2]


def test_a_selected_document_carries_its_text_answer_key_and_blanked_field(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(GeneratedDocument)
            .where(GeneratedDocument.source_claim_id == CLAIM_1)
            .where(GeneratedDocument.document_type == NOTE)
            .values(
                noise_record={
                    "level": "light",
                    "swaps": 1,
                    "drops": 0,
                    "missing_field": "member_id",
                    "page_width": 72,
                }
            )
        )
    with session_scope(factory) as session:
        letter, note = select_documents(session, DatasetSplit.VALIDATION, limit=2)

    assert letter.text == document_text(CLAIM_1, LETTER)
    assert letter.answer_key == ANSWER_KEYS[LETTER]
    assert letter.missing_field is None
    assert note.missing_field == "member_id"


def test_the_repr_of_a_document_and_of_a_row_shows_no_text_and_no_value() -> None:
    document = _document()
    row = build_extraction_row(document, _result())

    assert PATIENT_NAME not in repr(document)
    assert PATIENT_NAME not in repr(row)


def test_a_row_is_built_with_who_answered_the_text_hash_and_json_values() -> None:
    row = build_extraction_row(_document(), _result(LlmProvider.FALLBACK))

    assert row.key == (CLAIM_1, LETTER)
    assert row.prompt_version == PROMPT_VERSION
    assert row.model_name == MODEL_NAME
    assert row.provider is LlmProvider.FALLBACK
    assert row.text_sha256 == text_sha256(document_text(CLAIM_1, LETTER))
    # Dates and amounts are text, so the JSON column can hold them without a float step.
    assert row.fields["letter_date"] == {"value": "2008-03-20", "confidence": 0.9}
    assert row.fields["total_allowed_charge_amount"]["value"] == "130.10"


@pytest.mark.parametrize(
    "changed",
    [
        {"source_claim_id": "12345"},
        {"prompt_version": ""},
        {"model_name": ""},
        {"model_name": "m" * 101},
        {"text_sha256": "ABC"},
        {"fields": {}},
    ],
)
def test_a_row_the_table_could_not_hold_is_rejected(changed: dict[str, object]) -> None:
    row = build_extraction_row(_document(), _result()).model_dump()

    with pytest.raises(ValidationError):
        DocumentExtractionRow(**{**row, **changed})


def test_a_saved_row_is_read_back_by_claim_and_type(factory: sessionmaker[Session]) -> None:
    row = build_extraction_row(_document(), _result())

    with session_scope(factory) as session:
        save_extraction_row(session, row)
    with session_scope(factory) as session:
        stored = read_extraction_rows(session, PROMPT_VERSION)

    assert stored == {(CLAIM_1, LETTER): row}
    letter = DenialLetterExtraction.model_validate(stored[(CLAIM_1, LETTER)].fields)
    assert letter.total_allowed_charge_amount.value == Decimal("130.10")


def test_only_the_rows_of_the_asked_prompt_version_are_read(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        save_extraction_row(session, build_extraction_row(_document(), _result()))
    with session_scope(factory) as session:
        assert read_extraction_rows(session, "v998") == {}


def test_saving_again_replaces_the_result_of_the_same_claim_type_and_prompt(
    factory: sessionmaker[Session],
) -> None:
    first = build_extraction_row(_document(), _result())
    second = build_extraction_row(
        _document().model_copy(update={"text": "Made-up text that was generated again."}),
        _result(LlmProvider.FALLBACK, member_id=None),
    )

    with session_scope(factory) as session:
        save_extraction_row(session, first)
    with session_scope(factory) as session:
        save_extraction_row(session, second)
    with session_scope(factory) as session:
        stored = read_extraction_rows(session, PROMPT_VERSION)
        count = session.scalar(
            select(func.count())
            .select_from(DocumentExtraction)
            .where(DocumentExtraction.prompt_version == PROMPT_VERSION)
        )

    assert count == 1
    assert stored == {(CLAIM_1, LETTER): second}
    assert second.text_sha256 != first.text_sha256

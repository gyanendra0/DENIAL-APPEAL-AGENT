import logging
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import DatasetSplit, DocumentExtraction, GeneratedDocument, LlmCall, LlmProvider
from src.db.session import session_scope
from src.extraction import run
from src.extraction.run import BUDGET_REACHED, NO_PROVIDER, ExtractionRunSummary, run_extraction
from src.extraction.store import (
    StoredDocument,
    read_extraction_rows,
    select_documents,
    text_sha256,
)
from src.llm.gateway import LlmRequest, ProviderUnavailableError
from tests.extraction.helpers import (
    CLAIM_1,
    CLAIM_2,
    LETTER,
    MODEL_NAME,
    NOTE,
    PATIENT_NAME,
    PROMPT_VERSION,
    VALIDATION_KEYS,
    StubProvider,
    document_text,
    gateway,
    is_about,
    prompts,
    right_answer,
    stored_documents,
)


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with stored_documents(engine) as session_factory:
        yield session_factory


def _validation(factory: sessionmaker[Session]) -> list[StoredDocument]:
    with session_scope(factory) as session:
        return select_documents(session, DatasetSplit.VALIDATION)


def _stored_keys(factory: sessionmaker[Session]) -> set[tuple[str, object]]:
    with session_scope(factory) as session:
        return set(read_extraction_rows(session, PROMPT_VERSION))


def _run(
    factory: sessionmaker[Session], primary: StubProvider, fallback: StubProvider | None = None
) -> ExtractionRunSummary:
    return run_extraction(
        factory, gateway(factory, primary, fallback), prompts(), _validation(factory)
    )


def _summary(**counts: int | str) -> ExtractionRunSummary:
    values: dict[str, int | str | None] = {
        "selected": 5,
        "already_extracted": 0,
        "extracted": 0,
        "failed_to_parse": 0,
        "stopped_for_length": 0,
        "answered_by_fallback": 0,
        "stop_reason": None,
    }
    return ExtractionRunSummary.model_validate({**values, **counts})


def test_extracts_every_document_and_stores_one_result_each(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider()

    summary = _run(factory, primary)

    assert summary == _summary(extracted=5)
    assert summary.not_tried == 0
    assert len(primary.requests) == 5
    with session_scope(factory) as session:
        stored = read_extraction_rows(session, PROMPT_VERSION)
    assert set(stored) == set(VALIDATION_KEYS)
    note = stored[(CLAIM_2, NOTE)]
    assert note.model_name == MODEL_NAME
    assert note.provider is LlmProvider.PRIMARY
    assert note.text_sha256 == text_sha256(document_text(CLAIM_2, NOTE))


def test_each_document_is_sent_with_the_prompt_of_its_type(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider()

    _run(factory, primary)

    assert [request.user for request in primary.requests] == [
        document_text(claim_id, kind) for claim_id, kind in VALIDATION_KEYS
    ]
    assert [request.system for request in primary.requests] == [
        prompts()[kind].system for _, kind in VALIDATION_KEYS
    ]


def test_a_second_run_makes_no_call_for_a_document_already_extracted(
    factory: sessionmaker[Session],
) -> None:
    _run(factory, StubProvider())
    second = StubProvider()

    summary = _run(factory, second)

    assert summary == _summary(already_extracted=5)
    assert second.requests == []


def test_a_result_of_another_prompt_version_does_not_count_as_done(
    factory: sessionmaker[Session],
) -> None:
    _run(factory, StubProvider())
    primary = StubProvider()
    other_version = "v998"
    try:
        summary = run_extraction(
            factory, gateway(factory, primary), prompts(other_version), _validation(factory)
        )
        with session_scope(factory) as session:
            both = session.scalar(select(func.count()).select_from(DocumentExtraction))
    finally:
        with session_scope(factory) as session:
            session.execute(delete(LlmCall).where(LlmCall.prompt_version == other_version))

    assert summary == _summary(extracted=5)
    assert both == 10


def test_a_document_whose_text_changed_is_extracted_again_and_its_result_replaced(
    factory: sessionmaker[Session],
) -> None:
    _run(factory, StubProvider())
    new_text = f"Made-up letter of claim {CLAIM_1}, generated again."
    with session_scope(factory) as session:
        session.execute(
            update(GeneratedDocument)
            .where(GeneratedDocument.source_claim_id == CLAIM_1)
            .where(GeneratedDocument.document_type == LETTER)
            .values(text=new_text)
        )
    second = StubProvider()

    summary = _run(factory, second)

    assert summary == _summary(already_extracted=4, extracted=1)
    assert [request.user for request in second.requests] == [new_text]
    with session_scope(factory) as session:
        stored = read_extraction_rows(session, PROMPT_VERSION)
    assert len(stored) == 5
    assert stored[(CLAIM_1, LETTER)].text_sha256 == text_sha256(new_text)


def test_a_result_is_stored_before_the_next_call_is_made(
    factory: sessionmaker[Session],
) -> None:
    stored_at_each_call: list[int] = []

    def answer(request: LlmRequest) -> str:
        stored_at_each_call.append(len(_stored_keys(factory)))
        return right_answer(request)

    _run(factory, StubProvider(answer))

    assert stored_at_each_call == [0, 1, 2, 3, 4]


def test_a_run_that_fails_half_way_keeps_what_it_finished(
    factory: sessionmaker[Session],
) -> None:
    # A wrong key is not a failure another provider may cover: it propagates as it is.
    broken = StubProvider(
        error=lambda request: PermissionError("wrong key") if is_about(CLAIM_2)(request) else None
    )

    with pytest.raises(PermissionError):
        _run(factory, broken)

    assert _stored_keys(factory) == set(VALIDATION_KEYS[:3])


def test_an_answer_that_does_not_parse_is_counted_and_the_run_continues(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(
        lambda request: "not JSON" if is_about(CLAIM_1)(request) else right_answer(request)
    )

    summary = _run(factory, primary)

    assert summary == _summary(extracted=2, failed_to_parse=3)
    assert len(primary.requests) == 5  # no second call for a bad answer
    assert _stored_keys(factory) == set(VALIDATION_KEYS[3:])


def test_an_answer_that_stopped_for_length_is_counted_apart(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(
        stop_reason=lambda request: "length" if is_about(CLAIM_2)(request) else "stop"
    )

    summary = _run(factory, primary)

    assert summary == _summary(extracted=3, stopped_for_length=2)
    assert _stored_keys(factory) == set(VALIDATION_KEYS[:3])


def test_a_failed_document_is_tried_again_by_the_next_run(
    factory: sessionmaker[Session],
) -> None:
    _run(factory, StubProvider(lambda request: "not JSON"))
    second = StubProvider()

    summary = _run(factory, second)

    assert summary == _summary(extracted=5)


def test_answers_of_the_fallback_are_stored_and_counted(factory: sessionmaker[Session]) -> None:
    primary = StubProvider(
        error=lambda request: (
            ProviderUnavailableError("rate limited") if is_about(CLAIM_2)(request) else None
        )
    )
    fallback = StubProvider(model_name="example-open-model")

    summary = _run(factory, primary, fallback)

    assert summary == _summary(extracted=5, answered_by_fallback=2)
    with session_scope(factory) as session:
        stored = read_extraction_rows(session, PROMPT_VERSION)
    assert stored[(CLAIM_2, LETTER)].provider is LlmProvider.FALLBACK
    assert stored[(CLAIM_2, LETTER)].model_name == "example-open-model"
    assert stored[(CLAIM_1, LETTER)].provider is LlmProvider.PRIMARY


def test_the_run_stops_without_an_error_when_the_budget_is_reached(
    factory: sessionmaker[Session],
) -> None:
    # The highest possible cost of one call is above the whole cap, so no call is allowed.
    primary = StubProvider(highest_cost="5.00")

    summary = _run(factory, primary)

    assert summary == _summary(stop_reason=BUDGET_REACHED)
    assert summary.not_tried == 5
    assert primary.requests == []


def test_a_budget_stop_keeps_the_results_made_before_it(
    factory: sessionmaker[Session],
) -> None:
    _run(factory, StubProvider(lambda r: right_answer(r) if is_about(CLAIM_1)(r) else "not JSON"))

    summary = _run(factory, StubProvider(highest_cost="5.00"))

    assert summary == _summary(already_extracted=3, stop_reason=BUDGET_REACHED)
    assert summary.not_tried == 2
    assert _stored_keys(factory) == set(VALIDATION_KEYS[:3])


def test_the_run_stops_without_an_error_when_no_provider_can_answer(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(
        error=lambda request: (
            ProviderUnavailableError("server error") if is_about(CLAIM_2)(request) else None
        )
    )

    summary = _run(factory, primary)

    assert summary == _summary(extracted=3, stop_reason=NO_PROVIDER)
    assert summary.not_tried == 2
    assert len(primary.requests) == 4  # the run ended at the first call nobody answered


@pytest.mark.parametrize("missing_or_mixed", ["missing", "mixed"])
def test_prompts_must_cover_every_type_with_one_version(
    factory: sessionmaker[Session], missing_or_mixed: str
) -> None:
    given = prompts()
    if missing_or_mixed == "missing":
        del given[NOTE]
    else:
        given[NOTE] = prompts("v998")[NOTE]
    primary = StubProvider()

    with pytest.raises(ValueError, match="one prompt per document type"):
        run_extraction(factory, gateway(factory, primary), given, _validation(factory))

    assert primary.requests == []


def test_the_log_has_progress_counts_but_no_document_text_and_no_value(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(run, "PROGRESS_EVERY", 2)
    caplog.set_level(logging.DEBUG)

    _run(factory, StubProvider(lambda r: "not JSON" if is_about(CLAIM_2)(r) else right_answer(r)))

    progress = [r.getMessage() for r in caplog.records if r.name == "src.extraction.run"]
    assert progress == [
        "extraction progress: 2 of 5 documents (2 extracted, 0 not usable)",
        "extraction progress: 4 of 5 documents (3 extracted, 1 not usable)",
    ]
    assert PATIENT_NAME not in caplog.text
    assert "Made-up" not in caplog.text
    assert "MBR-000001" not in caplog.text

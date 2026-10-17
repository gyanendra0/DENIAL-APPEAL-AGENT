from sqlalchemy.orm import Session

from src.db.models import EvidenceSource
from src.drafting.cited_chunks import read_cited_chunks
from src.drafting.letter_check import ChunkRef
from tests.drafting.letter_helpers import citation, letter, other, policy, sha256
from tests.rag.helpers import CHUNK_TEXT, add_document

DOCUMENT_ID = "7701"


def _stored_text(index: int) -> str:
    # The text `add_document` gives a chunk.
    return f"{CHUNK_TEXT} {DOCUMENT_ID}-{index}."


def _ref(index: int, document_id: str = DOCUMENT_ID) -> ChunkRef:
    return ChunkRef(
        source=EvidenceSource.CMS_NCD, source_document_id=document_id, chunk_index=index
    )


def test_reads_each_cited_chunk_once(session: Session) -> None:
    add_document(session, DOCUMENT_ID, 3)
    cited = letter(
        policy(citation(document_id=DOCUMENT_ID, chunk_index=0)),
        policy(
            citation(document_id=DOCUMENT_ID, chunk_index=2),
            citation(document_id=DOCUMENT_ID, chunk_index=0),
        ),
    )

    chunks = read_cited_chunks(session, cited)

    assert set(chunks) == {_ref(0), _ref(2)}
    assert chunks[_ref(2)].text == _stored_text(2)
    assert chunks[_ref(2)].text_sha256 == sha256(_stored_text(2))


def test_leaves_out_a_chunk_that_is_not_stored(session: Session) -> None:
    add_document(session, DOCUMENT_ID, 1)
    cited = letter(
        policy(
            citation(document_id=DOCUMENT_ID, chunk_index=0),
            citation(document_id=DOCUMENT_ID, chunk_index=5),
            citation(document_id="7702"),
        )
    )

    assert set(read_cited_chunks(session, cited)) == {_ref(0)}


def test_a_chunk_of_another_source_with_the_same_id_is_not_read(session: Session) -> None:
    add_document(session, DOCUMENT_ID, 1, source=EvidenceSource.CMS_MANUAL)
    cited = letter(policy(citation(document_id=DOCUMENT_ID, source=EvidenceSource.CMS_NCD)))

    assert read_cited_chunks(session, cited) == {}


def test_a_letter_without_citations_reads_nothing(session: Session) -> None:
    assert read_cited_chunks(session, letter(other())) == {}

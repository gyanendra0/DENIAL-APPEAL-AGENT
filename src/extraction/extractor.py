"""Turns the text of one document into typed fields, through the LLM gateway.

One model call per document. An answer that was cut off, is not JSON or does not fit the
schema is an error: nothing is repaired and no second call is made. Document text and
extracted values never appear in a log line or an error message; the one log line per call
is the gateway's.
"""

from pydantic import BaseModel, ConfigDict

from src.db.models import DocumentType, LlmProvider
from src.extraction.prompts import EXTRACTION_PURPOSE, ExtractionPrompt
from src.extraction.schemas import (
    EXTRACTION_MAX_OUTPUT_TOKENS,
    ExtractionSchema,
    parse_extraction,
    schema_for,
)
from src.llm.gateway import LlmGateway, LlmRequest

# The stop reason a provider gives when the answer reached the output limit.
LENGTH_STOP_REASON = "length"


class ExtractionError(Exception):
    """The model answered, but the answer cannot be used. Its text never quotes the answer."""


class ExtractionTruncatedError(ExtractionError):
    """The answer reached the output limit before it was complete."""


class ExtractionParseError(ExtractionError):
    """The answer is not JSON, or does not fit the schema of the document type."""


class ExtractionResult(BaseModel):
    """The fields of one document, and who produced them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_type: DocumentType
    fields: ExtractionSchema
    provider: LlmProvider
    model_name: str
    prompt_version: str


def extract_document(gateway: LlmGateway, prompt: ExtractionPrompt, text: str) -> ExtractionResult:
    """Extract the fields of one document of `prompt.document_type` from `text`.

    Spends money and can take several seconds: it makes one model call through `gateway`.
    Raises `ExtractionTruncatedError` or `ExtractionParseError` when the answer cannot be
    used; the gateway's own errors (over budget, no provider available) pass through.
    """
    document_type = prompt.document_type
    result = gateway.complete(
        LlmRequest(
            system=prompt.system,
            user=text,
            max_tokens=EXTRACTION_MAX_OUTPUT_TOKENS,
            prompt_version=prompt.version,
            purpose=EXTRACTION_PURPOSE,
            response_schema=schema_for(document_type).model_json_schema(),
        )
    )
    if result.stop_reason == LENGTH_STOP_REASON:
        raise ExtractionTruncatedError(
            f"the answer for a {document_type.value} reached the output limit "
            f"({EXTRACTION_MAX_OUTPUT_TOKENS} tokens)"
        )
    try:
        fields = parse_extraction(document_type, result.text)
    except ValueError:
        # `from None`: the parser's error quotes the answer, which must not reach a traceback.
        raise ExtractionParseError(
            f"the answer for a {document_type.value} is not JSON or does not fit its schema"
        ) from None
    return ExtractionResult(
        document_type=document_type,
        fields=fields,
        provider=result.provider,
        model_name=result.model_name,
        prompt_version=result.prompt_version,
    )

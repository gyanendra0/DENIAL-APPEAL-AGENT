"""Builds the `JobContext` every upload job runs with, once, when the API starts.

A missing LLM key, prompt file or model file fails here, at startup, not at the first upload.
"""

from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import LlmGatewaySettings, Settings
from src.db.models import DocumentType
from src.drafting.prompts import DRAFTING_PROMPT_VERSION, load_drafting_prompt
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION, load_extraction_prompt
from src.jobs.run import JobContext
from src.llm.openai_provider import build_openai_gateway
from src.ml.inference import load_win_model


def build_job_context(
    settings: Settings,
    gateway_settings: LlmGatewaySettings,
    session_factory: sessionmaker[Session],
) -> JobContext:
    """Load the gateway, the default letter extraction and drafting prompts and the win model.

    Raises `PromptError` / `DraftingPromptError` for a missing prompt file and
    `ModelFileError` for a missing or out-of-date model.
    """
    return JobContext(
        gateway=build_openai_gateway(gateway_settings, session_factory),
        extraction_prompt=load_extraction_prompt(
            settings.prompt_dir, EXTRACTION_PROMPT_VERSION, DocumentType.DENIAL_LETTER
        ),
        drafting_prompt=load_drafting_prompt(settings.prompt_dir, DRAFTING_PROMPT_VERSION),
        model=load_win_model(settings.model_dir),
        amount_floor=settings.rules_amount_floor_usd,
        win_threshold=settings.agent_win_threshold,
        embedding_model=gateway_settings.llm_embedding_model,
    )

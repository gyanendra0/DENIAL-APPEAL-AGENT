"""Building the job context from settings: the repository's own prompts, a made-up model."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import sessionmaker

from src.config.settings import LlmGatewaySettings, Settings
from src.db.models import DatasetSplit, DocumentType
from src.drafting.prompts import DRAFTING_PROMPT_VERSION
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION, PromptError
from src.jobs.context import build_job_context
from src.ml.inference import ModelFileError
from src.ml.training import SplitFacts, save_win_model, train_win_model
from tests.ml.made_up_rows import training_rows

PROMPT_DIR = Path(__file__).resolve().parents[2] / "config" / "prompts"
URL = "postgresql+psycopg://made:up@localhost:5433/none"
SPLITS = {DatasetSplit.TRAIN: SplitFacts(rows=288, proxy_true=120, auc=0.9)}


def _gateway_settings() -> LlmGatewaySettings:
    fields: dict[str, Any] = {
        "llm_monthly_budget_usd": "3.00",
        "llm_timeout_seconds": "30",
        "llm_primary_base_url": "https://primary.example.test/v1",
        "llm_primary_model": "example-small-model",
        "llm_primary_api_key": "made-up-key",
        "llm_primary_input_usd_per_mtok": "0.15",
        "llm_primary_output_usd_per_mtok": "0.60",
        "llm_embedding_model": "example-embedding-model",
        "llm_embedding_usd_per_mtok": "0.02",
    }
    return LlmGatewaySettings(_env_file=None, **fields)  # type: ignore[call-arg]


def _settings(model_dir: Path, prompt_dir: Path = PROMPT_DIR) -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_url=URL,
        prompt_dir=prompt_dir,
        model_dir=model_dir,
        rules_amount_floor_usd=Decimal("40"),
        agent_win_threshold=0.45,
        _env_file=None,
    )


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("model")
    save_win_model(train_win_model(training_rows(), seed=7), folder, split_seed=42, splits=SPLITS)
    return folder


def test_builds_the_context_from_the_settings(model_dir: Path) -> None:
    context = build_job_context(_settings(model_dir), _gateway_settings(), sessionmaker())
    assert context.extraction_prompt.version == EXTRACTION_PROMPT_VERSION
    assert context.extraction_prompt.document_type is DocumentType.DENIAL_LETTER
    assert context.drafting_prompt.version == DRAFTING_PROMPT_VERSION
    assert context.model.training_seed == 7
    assert (context.amount_floor, context.win_threshold) == (Decimal("40"), 0.45)
    assert context.embedding_model == "example-embedding-model"


def test_a_missing_model_fails_when_the_context_is_built(tmp_path: Path) -> None:
    with pytest.raises(ModelFileError):
        build_job_context(_settings(tmp_path), _gateway_settings(), sessionmaker())


def test_a_missing_prompt_fails_when_the_context_is_built(model_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(PromptError):
        build_job_context(_settings(model_dir, tmp_path), _gateway_settings(), sessionmaker())

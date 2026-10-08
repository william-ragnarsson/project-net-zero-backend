"""Settings, from env vars (``NETZERO_*``) and ``.env`` at the repo root.

Per-stage model overrides use the nested delimiter, e.g.
``NETZERO_STAGES__REWRITE__MODEL=claude-opus-5-5`` and
``NETZERO_STAGES__REWRITE__EFFORT=high``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from netzero import paths
from netzero.events import Assumptions, LlmStage

DEFAULT_MODEL = "claude-haiku-4-5"

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class StageModelConfig(BaseModel):
    model: str = DEFAULT_MODEL
    effort: Effort | None = None  # only sent to models that support it
    thinking: bool = False  # adaptive thinking where supported, never on Haiku by default
    max_tokens: int = 8000


class Stages(BaseModel):
    triage: StageModelConfig = StageModelConfig(max_tokens=6000)
    tests: StageModelConfig = StageModelConfig()
    tests_repair: StageModelConfig = StageModelConfig()
    rewrite: StageModelConfig = StageModelConfig()
    rewrite_repair: StageModelConfig = StageModelConfig()

    def get(self, stage: LlmStage) -> StageModelConfig:
        return getattr(self, stage)

    def models(self) -> dict[str, str]:
        return {name: getattr(self, name).model for name in type(self).model_fields}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NETZERO_",
        env_nested_delimiter="__",
        env_file=paths.ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "NETZERO_ANTHROPIC_API_KEY"),
    )

    # server
    host: str = "127.0.0.1"
    port: int = 8000
    runs_dir: Path = paths.DEFAULT_RUNS_DIR
    sse_heartbeat_s: float = 15.0

    # history: optional Postgres copy of every run's events (see netzero/history)
    database_url: str | None = None  # e.g. postgresql://netzero:netzero@127.0.0.1:54320/netzero
    history_sync_s: float = 1.5  # how often the server ships new events

    # development: drive runs with the scripted FakePipeline instead of the real one
    fake_pipeline: bool = False
    fake_speed: float = 1.0  # >1 is faster
    fake_scenario: Literal["demo", "short", "fail_env"] = "demo"
    fake_seed: int = 0

    # LLM
    stages: Stages = Stages()
    llm_concurrency: int = 4
    cassette_mode: Literal["off", "record", "replay"] = "off"

    # selection
    max_functions: int = 20  # rows shown in the triage list
    preselect: int = 8
    preselect_min_score: float = 0.15
    triage_llm_top: int = 60

    # tests
    test_repairs: int = 2
    candidate_repairs: int = 1

    # bench + decision
    n_trials: int = 16  # per arm
    trial_target_s: float = 0.75
    alpha: float = 0.05
    min_effect_pct: float = 5.0
    baseline_cv_warn_pct: float = 25.0

    # carbon
    country: str | None = None  # ISO3; None == CodeCarbon world average

    # sandbox
    clone_timeout_s: float = 120.0
    clone_max_mb: int = 200
    clone_max_files: int = 20_000
    install_timeout_s: float = 600.0
    test_timeout_s: float = 120.0

    # cost assumptions (client-side yearly projections)
    eur_per_kwh: float = 0.20
    eu_ets_eur_per_t: float = 70.0
    vcm_eur_per_t: float = 15.0
    calls_per_year: int = 1_000_000

    @property
    def has_api_key(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value().strip())

    def assumptions(self) -> Assumptions:
        return Assumptions(
            eur_per_kwh=self.eur_per_kwh,
            eu_ets_eur_per_t=self.eu_ets_eur_per_t,
            vcm_eur_per_t=self.vcm_eur_per_t,
            default_calls_per_year=self.calls_per_year,
            notes=[
                "electricity: indicative EU non-household price",
                "EU ETS: indicative allowance price",
                "VCM: indicative voluntary carbon market price",
            ],
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

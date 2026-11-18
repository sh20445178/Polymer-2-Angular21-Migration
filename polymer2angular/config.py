"""Runtime configuration and LLM factory."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from functools import lru_cache

DEFAULT_EXCLUDES = ["node_modules", "bower_components", ".git", "build", "dist", "test", "tests", "demo",
                    ".angular", "coverage", "out-tsc"]


@dataclass
class MigrationOptions:
    project_name: str = "migrated-app"
    llm_provider: str | None = None  # azure_openai | openai | anthropic | ollama | google_genai | none
    llm_model: str | None = None
    temperature: float | None = None
    max_fix_iterations: int = 2
    verify_build: bool = False
    install_timeout: int = 900
    dry_run: bool = False
    exclude_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))

    @classmethod
    def from_env(cls, **overrides) -> "MigrationOptions":
        opts = cls(
            llm_provider=os.getenv("P2A_LLM_PROVIDER") or None,
            llm_model=os.getenv("P2A_LLM_MODEL") or None,
            temperature=float(os.environ["P2A_LLM_TEMPERATURE"]) if os.getenv("P2A_LLM_TEMPERATURE") else None,
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(opts, key, value)
        return opts

    def to_dict(self) -> dict:
        return asdict(self)


def llm_enabled(options: dict) -> bool:
    return bool(options.get("llm_provider")) and options.get("llm_provider") != "none" and bool(options.get("llm_model"))


@lru_cache(maxsize=4)
def _build_llm(provider: str, model: str, temperature: float | None):
    kwargs = {} if temperature is None else {"temperature": temperature}
    if provider == "azure_openai":
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            azure_deployment=model,
            api_version=os.getenv("OPENAI_API_VERSION", "2024-10-21"),
            **kwargs,
        )
    from langchain.chat_models import init_chat_model

    return init_chat_model(model, model_provider=provider, **kwargs)


def get_llm(options: dict):
    """Return a LangChain chat model, or None when running in deterministic (rule-based) mode."""
    if not llm_enabled(options):
        return None
    return _build_llm(options["llm_provider"], options["llm_model"], options.get("temperature"))

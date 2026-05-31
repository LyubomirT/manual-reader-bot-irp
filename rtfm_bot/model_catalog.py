from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MODEL_ID = "glm"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str
    display_name: str
    score: int
    context_length: int
    reasoning: bool

    @property
    def heaviness_label(self) -> str:
        return f"{self.heaviness_emoji} {self.heaviness_name}"

    @property
    def heaviness_emoji(self) -> str:
        if self.score <= 25:
            return "🔴"
        if self.score <= 60:
            return "🟠"
        if self.score <= 100:
            return "🟡"
        if self.score <= 130:
            return "🟢"
        return "🔵"

    @property
    def heaviness_name(self) -> str:
        if self.score <= 25:
            return "Very Restricted"
        if self.score <= 60:
            return "Restricted"
        if self.score <= 100:
            return "Default"
        if self.score <= 130:
            return "Relaxed"
        return "Light"

    @property
    def button_label(self) -> str:
        return f"{self.heaviness_emoji} {self.display_name}"

    def scaled_rate_limit(self, base_limit: int) -> int:
        baseline = MODEL_SPECS[DEFAULT_MODEL_ID].score
        return max(1, round(base_limit * (self.score / baseline)))

    def scaled_history_limit(self, base_limit: int) -> int:
        baseline = MODEL_SPECS[DEFAULT_MODEL_ID].context_length
        scaled = round(base_limit * (self.context_length / baseline))
        return max(4, min(24, scaled))

    def scaled_docs_limit(self, base_limit: int) -> int:
        baseline = MODEL_SPECS[DEFAULT_MODEL_ID].context_length
        scaled = round(base_limit * (self.context_length / baseline))
        return max(2, min(8, scaled))

    @property
    def context_label(self) -> str:
        if self.context_length >= 1_000_000:
            return f"{self.context_length // 1_000_000}M"
        return f"{round(self.context_length / 1000)}k"


MODEL_SPECS: dict[str, ModelSpec] = {
    "gpt-5.5": ModelSpec(
        id="gpt-5.5",
        display_name="OpenAI GPT-5.5",
        score=20,
        context_length=1_000_000,
        reasoning=True,
    ),
    "kimi-k2.6": ModelSpec(
        id="kimi-k2.6",
        display_name="Moonshot Kimi K2.6",
        score=45,
        context_length=262_000,
        reasoning=True,
    ),
    "deepseek-pro": ModelSpec(
        id="deepseek-pro",
        display_name="DeepSeek V4 Pro",
        score=45,
        context_length=1_048_576,
        reasoning=True,
    ),
    "gpt-5.4-mini": ModelSpec(
        id="gpt-5.4-mini",
        display_name="OpenAI GPT-5.4 Mini",
        score=60,
        context_length=400_000,
        reasoning=False,
    ),
    "glm": ModelSpec(
        id="glm",
        display_name="Z.ai GLM-5.1",
        score=100,
        context_length=198_000,
        reasoning=True,
    ),
    "claude-fast": ModelSpec(
        id="claude-fast",
        display_name="Anthropic Claude Haiku 4.5",
        score=105,
        context_length=200_000,
        reasoning=False,
    ),
    "grok-4.3": ModelSpec(
        id="grok-4.3",
        display_name="xAI Grok 4.3",
        score=120,
        context_length=262_144,
        reasoning=True,
    ),
    "minimax": ModelSpec(
        id="minimax",
        display_name="MiniMax 2.7",
        score=130,
        context_length=200_000,
        reasoning=True,
    ),
}

MODEL_ORDER = [
    "gpt-5.5",
    "kimi-k2.6",
    "deepseek-pro",
    "gpt-5.4-mini",
    "glm",
    "claude-fast",
    "grok-4.3",
    "minimax",
]

MODEL_ALIASES = {
    "kimi": "kimi-k2.6",
    "deepseek": "deepseek-pro",
    "grok": "grok-4.3",
}


def get_model_spec(model_id: str | None) -> ModelSpec:
    resolved_model_id = resolve_model_id(model_id)
    if resolved_model_id and resolved_model_id in MODEL_SPECS:
        return MODEL_SPECS[resolved_model_id]
    return MODEL_SPECS[DEFAULT_MODEL_ID]


def resolve_model_id(model_id: str | None) -> str:
    return MODEL_ALIASES.get(model_id or "", model_id or DEFAULT_MODEL_ID)


def iter_model_specs() -> list[ModelSpec]:
    return [MODEL_SPECS[model_id] for model_id in MODEL_ORDER]

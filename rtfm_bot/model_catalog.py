from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MODEL_ID = "kimi"


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
        if self.score <= 100:
            return "🔴"
        if self.score <= 160:
            return "🟠"
        if self.score <= 240:
            return "🟡"
        if self.score <= 320:
            return "🟢"
        return "🔵"

    @property
    def heaviness_name(self) -> str:
        if self.score <= 100:
            return "Heavy"
        if self.score <= 160:
            return "Warm"
        if self.score <= 240:
            return "Balanced"
        if self.score <= 320:
            return "Light"
        return "Feather"

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
    "kimi": ModelSpec(
        id="kimi",
        display_name="Moonshot Kimi K2.5",
        score=95,
        context_length=256_000,
        reasoning=True,
    ),
    "claude-fast": ModelSpec(
        id="claude-fast",
        display_name="Anthropic Claude Haiku 4.5",
        score=150,
        context_length=200_000,
        reasoning=False,
    ),
    "minimax": ModelSpec(
        id="minimax",
        display_name="MiniMax M2.5",
        score=400,
        context_length=200_000,
        reasoning=True,
    ),
    "deepseek": ModelSpec(
        id="deepseek",
        display_name="DeepSeek V3.2",
        score=250,
        context_length=163_840,
        reasoning=True,
    ),
    "glm": ModelSpec(
        id="glm",
        display_name="Z.ai GLM-5",
        score=85,
        context_length=198_000,
        reasoning=True,
    ),
    "qwen-large": ModelSpec(
        id="qwen-large",
        display_name="Qwen 3.5 Plus",
        score=85,
        context_length=1_048_576,
        reasoning=True,
    ),
}

MODEL_ORDER = [
    "kimi",
    "claude-fast",
    "minimax",
    "deepseek",
    "glm",
    "qwen-large",
]


def get_model_spec(model_id: str | None) -> ModelSpec:
    if model_id and model_id in MODEL_SPECS:
        return MODEL_SPECS[model_id]
    return MODEL_SPECS[DEFAULT_MODEL_ID]


def iter_model_specs() -> list[ModelSpec]:
    return [MODEL_SPECS[model_id] for model_id in MODEL_ORDER]

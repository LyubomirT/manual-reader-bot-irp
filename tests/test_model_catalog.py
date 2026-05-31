from __future__ import annotations

import unittest

from rtfm_bot.model_catalog import (
    DEFAULT_MODEL_ID,
    get_model_spec,
    iter_model_specs,
    resolve_model_id,
)


class ModelCatalogTests(unittest.TestCase):
    def test_default_model_is_glm_5_1(self) -> None:
        default_model = get_model_spec(None)

        self.assertEqual(DEFAULT_MODEL_ID, "glm")
        self.assertEqual(default_model.id, "glm")
        self.assertEqual(default_model.display_name, "Z.ai GLM-5.1")
        self.assertEqual(default_model.heaviness_name, "Default")

    def test_legacy_model_ids_resolve_to_replacement_models(self) -> None:
        self.assertEqual(get_model_spec("kimi").id, "kimi-k2.6")
        self.assertEqual(get_model_spec("deepseek").id, "deepseek-pro")
        self.assertEqual(get_model_spec("grok").id, "grok-4.3")
        self.assertEqual(resolve_model_id("openai"), "openai")
        self.assertEqual(resolve_model_id("gpt-5.4-nano"), "gpt-5.4-nano")

    def test_catalog_contains_requested_model_set(self) -> None:
        model_ids = [model.id for model in iter_model_specs()]

        self.assertEqual(
            model_ids,
            [
                "gpt-5.5",
                "kimi-k2.6",
                "deepseek-pro",
                "gpt-5.4-mini",
                "glm",
                "claude-fast",
                "grok-4.3",
                "minimax",
            ],
        )

    def test_very_restricted_model_gets_lower_scaled_limit(self) -> None:
        default_model = get_model_spec("glm")
        heavy_model = get_model_spec("gpt-5.5")

        self.assertEqual(heavy_model.heaviness_name, "Very Restricted")
        self.assertLess(
            heavy_model.scaled_rate_limit(48),
            default_model.scaled_rate_limit(48),
        )


if __name__ == "__main__":
    unittest.main()

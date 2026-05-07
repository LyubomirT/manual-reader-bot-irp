from __future__ import annotations

import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rtfm_bot.statuses import BotStatusSpec, choose_next_status, load_statuses_from_file, parse_status_line


class BotStatusSelectionTests(unittest.TestCase):
    def test_choose_next_status_avoids_immediate_repeat_when_possible(self) -> None:
        current = BotStatusSpec(kind="watching", text="the docs page.")
        other = BotStatusSpec(kind="custom", text="The button is literally right there.")

        selected = choose_next_status(
            [current, other],
            rng=random.Random(7),
            current=current,
        )

        self.assertEqual(selected, other)

    def test_choose_next_status_returns_only_option_when_single_status_exists(self) -> None:
        only = BotStatusSpec(kind="playing", text="Find the obvious blue button.")

        selected = choose_next_status(
            [only],
            rng=random.Random(99),
            current=only,
        )

        self.assertEqual(selected, only)

    def test_parse_status_line_uses_activity_tag(self) -> None:
        status = parse_status_line("[listening] confused clicking")

        self.assertEqual(status, BotStatusSpec(kind="listening", text="confused clicking"))

    def test_parse_status_line_defaults_to_custom_without_tag(self) -> None:
        status = parse_status_line("Read the docs challenge: impossible")

        self.assertEqual(
            status,
            BotStatusSpec(kind="custom", text="Read the docs challenge: impossible"),
        )

    def test_load_statuses_from_file_skips_comments_and_bad_tags(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "statuses.txt"
            path.write_text(
                "\n".join(
                    [
                        "# One phrase per line",
                        "[watching] users ignore the FAQ",
                        "[wrong] should be skipped",
                        "[playing] docs roulette",
                    ]
                ),
                encoding="utf-8",
            )

            statuses = load_statuses_from_file(path)

        self.assertEqual(
            statuses,
            (
                BotStatusSpec(kind="watching", text="users ignore the FAQ"),
                BotStatusSpec(kind="playing", text="docs roulette"),
            ),
        )


if __name__ == "__main__":
    unittest.main()

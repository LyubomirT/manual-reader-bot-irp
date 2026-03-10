from __future__ import annotations

import random
import unittest

from rtfm_bot.statuses import BotStatusSpec, choose_next_status


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


if __name__ == "__main__":
    unittest.main()

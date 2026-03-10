from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal, Sequence

StatusKind = Literal["watching", "playing", "listening", "custom"]


@dataclass(frozen=True, slots=True)
class BotStatusSpec:
    kind: StatusKind
    text: str


DEFAULT_ROTATING_STATUSES: tuple[BotStatusSpec, ...] = (
    BotStatusSpec(kind="watching", text="you ignore the search bar."),
    BotStatusSpec(kind="watching", text='users click "Next" without reading.'),
    BotStatusSpec(kind="watching", text="you blindly click around the UI."),
    BotStatusSpec(kind="watching", text='someone ask "How do I save?" again.'),
    BotStatusSpec(kind="watching", text="the settings menu collect dust."),
    BotStatusSpec(kind="watching", text="you hover over the exact button you need."),
    BotStatusSpec(kind="playing", text="Hide and Seek with the Settings menu."),
    BotStatusSpec(kind="playing", text='"Guess the feature" because you won\'t look it up.'),
    BotStatusSpec(kind="playing", text="20 Questions: Software Edition."),
    BotStatusSpec(kind="playing", text="Find the obvious blue button."),
    BotStatusSpec(kind="playing", text="Page 404: Brain Not Found."),
    BotStatusSpec(kind="playing", text="Translating English into English."),
    BotStatusSpec(kind="listening", text="frantic, angry mouse clicking."),
    BotStatusSpec(kind="listening", text='your sighs of frustration.'),
    BotStatusSpec(kind="listening", text='"Where is the import button?!"'),
    BotStatusSpec(
        kind="listening",
        text="you type a paragraph instead of using Ctrl+F.",
    ),
    BotStatusSpec(
        kind="listening",
        text="100 people ask the exact same question.",
    ),
    BotStatusSpec(kind="custom", text="I literally exist because you refuse to scroll."),
    BotStatusSpec(kind="custom", text="Ctrl+F is my love language."),
    BotStatusSpec(kind="custom", text="What is my purpose? To serve text, of course."),
    BotStatusSpec(kind="custom", text="I am begging you to look at the FAQ."),
    BotStatusSpec(
        kind="custom",
        text="0 days since someone asked a question answered in the FAQ.",
    ),
    BotStatusSpec(kind="custom", text="The button is literally right there."),
    BotStatusSpec(kind="custom", text="No, the software isn't broken."),
    BotStatusSpec(
        kind="custom",
        text="I read the manual so you don't have to (even though you should).",
    ),
    BotStatusSpec(kind="custom", text="Sighing in binary."),
    BotStatusSpec(kind="custom", text="Next level of Intense human laziness."),
    BotStatusSpec(
        kind="custom",
        text="Answering questions that a 5-second Google search could have.",
    ),
    BotStatusSpec(kind="custom", text='"Wait, there\'s a manual?" - You, probably.'),
    BotStatusSpec(kind="custom", text="Powered by your reluctance to read."),
    BotStatusSpec(kind="custom", text="I'm just a glorified table of contents."),
    BotStatusSpec(kind="custom", text="Have you tried opening your eyes?"),
    BotStatusSpec(kind="custom", text="Currently mourning the death of common sense."),
)


def choose_next_status(
    statuses: Sequence[BotStatusSpec],
    *,
    rng: random.Random,
    current: BotStatusSpec | None = None,
) -> BotStatusSpec:
    if not statuses:
        raise ValueError("At least one rotating bot status must be configured.")

    available = tuple(statuses)
    if current is None or len(available) == 1:
        return rng.choice(available)

    candidates = tuple(status for status in available if status != current)
    return rng.choice(candidates or available)

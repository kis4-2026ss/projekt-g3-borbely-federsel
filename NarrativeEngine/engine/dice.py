"""Dice rolling utilities. All randomness in the engine goes through here."""

import random
import re
from typing import Optional

from .models import DiceRoll


def roll(
    notation: str,
    modifier: int = 0,
    dc: Optional[int] = None,
    label: str = "",
    roll_type: str = "check",
) -> DiceRoll:
    """Roll dice from standard NdS notation (e.g. '1d20', '2d6').

    modifier is added on top of the dice sum.
    dc sets a difficulty class / AC — success is total >= dc.
    """
    match = re.fullmatch(r"(\d+)[dD](\d+)", notation.strip())
    if not match:
        raise ValueError(
            f"Invalid dice notation {notation!r}. Use NdS format (e.g. '2d6')."
        )
    count = int(match.group(1))
    sides = int(match.group(2))
    if count < 1 or sides < 1:
        raise ValueError("Dice count and sides must be at least 1.")

    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + modifier
    success = (total >= dc) if dc is not None else None

    return DiceRoll(
        dice=notation,
        rolls=rolls,
        modifier=modifier,
        total=total,
        dc=dc,
        success=success,
        label=label,
        roll_type=roll_type,
    )

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
    advantage: int = 0,
    crit: bool = False,
) -> DiceRoll:
    """Roll dice from standard NdS notation (e.g. '1d20', '2d6').

    modifier is added on top of the dice sum.
    dc sets a difficulty class / AC — success is total >= dc.

    advantage  : -1 disadvantage, 0 normal, +1 advantage. Rolls a single die
                 twice and keeps the higher (advantage) or lower (disadvantage)
                 face. Only valid for single-die notation (e.g. '1d20').
    crit       : when True, the number of dice rolled is doubled — used for
                 D&D 5e critical-hit damage (roll the damage dice twice).

    D&D 5e attack rules (roll_type == "attack" on a single d20):
      * a natural 20 always hits (success forced True) and flags is_critical
      * a natural 1 always misses (success forced False) and flags is_fumble
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
    if advantage not in (-1, 0, 1):
        raise ValueError("advantage must be -1 (disadvantage), 0, or +1 (advantage).")
    if advantage != 0 and count != 1:
        raise ValueError(
            "advantage/disadvantage only applies to single-die rolls (e.g. '1d20')."
        )

    dropped: list[int] = []
    if advantage != 0:
        first = random.randint(1, sides)
        second = random.randint(1, sides)
        if advantage > 0:
            kept, other = max(first, second), min(first, second)
        else:
            kept, other = min(first, second), max(first, second)
        rolls = [kept]
        dropped = [other]
    else:
        die_count = count * 2 if crit else count
        rolls = [random.randint(1, sides) for _ in range(die_count)]

    total = sum(rolls) + modifier

    # Natural-roll detection — only meaningful for a single d20.
    natural: Optional[int] = rolls[0] if (sides == 20 and len(rolls) == 1) else None
    is_attack = roll_type == "attack" and natural is not None
    is_critical = is_attack and natural == 20
    is_fumble = is_attack and natural == 1

    if is_critical:
        success = True            # a natural 20 always hits
    elif is_fumble:
        success = False           # a natural 1 always misses
    elif dc is not None:
        success = total >= dc
    else:
        success = None

    return DiceRoll(
        dice=notation,
        rolls=rolls,
        modifier=modifier,
        total=total,
        dc=dc,
        success=success,
        label=label,
        roll_type=roll_type,
        natural=natural,
        is_critical=is_critical,
        is_fumble=is_fumble,
        advantage=advantage,
        dropped=dropped,
    )

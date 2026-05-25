"""Archetype (class) system for ChronosTUI.

All class definitions live in engine/classes.json — this module loads that
file at import time and exposes:

  ARCHETYPES   — the raw dict (backward-compat; class_select_screen imports it)
  apply_archetype(state, key)         — initialize a new player with archetype data
  get_level_bonus(archetype, level)   — dict of HP/stat bonuses at a given level
  get_class_loot_profile(arch, level) — flat loot-guidance dict for the LLM
  get_level_unlock(archetype)         — slot-5 ability definition (or {})

Keep this module free of circular imports — only import from engine.models
inside functions, not at module top level.
"""

import json
import os
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from engine.models import GameState


# ── Load classes.json ──────────────────────────────────────────────────────

_CLASSES_PATH = os.path.join(os.path.dirname(__file__), "classes.json")


def _load_classes() -> Dict[str, Any]:
    with open(_CLASSES_PATH, encoding="utf-8") as f:
        return json.load(f)


ARCHETYPES: Dict[str, Any] = _load_classes()


# ── Public helpers ─────────────────────────────────────────────────────────

def get_level_bonus(archetype: str, level: int) -> Dict[str, Any]:
    """Return the level_bonuses entry for (archetype, level), or {} if not defined.

    Keys in the returned dict:
      hp          — int, max HP increase for this level
      stat_boosts — {stat_name: delta} mapping (may be absent)
      note        — short flavour string shown in the combat log (may be absent)
    """
    return (
        ARCHETYPES
        .get(archetype, {})
        .get("level_bonuses", {})
        .get(str(level), {})
    )


def get_class_loot_profile(archetype: str, player_level: int) -> Dict[str, Any]:
    """Return a flat loot-guidance dict resolved to the player's current level.

    The result is injected directly into current_state.player.loot_profile
    in the LLM context so CHRONOS knows exactly what to generate on boss drops.

    Keys: weapon_type, weapon_examples, armor_type, armor_examples,
          target_damage_dice, target_hit_bonus, target_ac_bonus.
    """
    profile = ARCHETYPES.get(archetype, {}).get("loot_profile", {})
    lvl = str(min(max(player_level, 1), 5))   # clamp to defined range 1–5
    return {
        "weapon_type":        profile.get("weapon_type", "weapon"),
        "weapon_examples":    profile.get("weapon_examples", []),
        "armor_type":         profile.get("armor_type", "armor"),
        "armor_examples":     profile.get("armor_examples", []),
        "target_damage_dice": profile.get("damage_dice_by_level", {}).get(lvl, "1d8"),
        "target_hit_bonus":   profile.get("hit_bonus_by_level",   {}).get(lvl, 1),
        "target_ac_bonus":    profile.get("ac_bonus_by_level",    {}).get(lvl, 2),
    }


def get_level_unlock(archetype: str) -> Dict[str, Any]:
    """Return the level_unlock block for this archetype, or {} if none defined.

    Keys (when present): level, slot, action_id, name, short_desc, type.
    """
    return ARCHETYPES.get(archetype, {}).get("level_unlock", {})


def get_resource_info(archetype: str) -> Dict[str, Any]:
    """Return the resource block for this archetype, or {} if the class has none.

    Keys (when present): name, color, max, regen_per_round, start_at_max,
    ability_costs (dict of action_id → int).
    """
    return ARCHETYPES.get(archetype, {}).get("resource", {})


def get_ability_cost(archetype: str, action_id: str) -> int:
    """Return the resource cost to use an ability (0 = free)."""
    return get_resource_info(archetype).get("ability_costs", {}).get(action_id, 0)


# ── apply_archetype ────────────────────────────────────────────────────────

def apply_archetype(state: "GameState", key: str) -> None:
    """Set archetype, ability scores, max HP, and register + give starting items.

    Reads everything from ARCHETYPES[key] (loaded from classes.json).
    Weapons and armour are auto-equipped; consumables and combat items
    are added to the player's inventory string list.
    """
    from .models import Armor, ItemDefinition, Weapon

    arch = ARCHETYPES[key]
    player = state.player

    player.archetype = key
    player.stats.update(arch["stats"])
    player.max_hp = arch["base_hp"]
    player.hp    = arch["base_hp"]

    # Initialise class resource
    res = arch.get("resource", {})
    player.max_combat_resource = res.get("max", 0)
    player.combat_resource = player.max_combat_resource if res.get("start_at_max", True) else 0

    for raw in arch["starting_items"]:
        iid       = raw["id"]
        item_type = raw["item_type"]
        name      = raw["name"]

        # Build ItemDefinition kwargs — pass only accepted fields
        defn_kwargs: Dict[str, Any] = {
            "id":          iid,
            "name":        name,
            "item_type":   item_type,
            "description": f"Starting gear for {arch['display_name']}.",
        }
        for field in ("damage_dice", "hit_bonus", "damage_type", "ac_bonus", "heal_amount", "tags"):
            if field in raw:
                defn_kwargs[field] = raw[field]

        state.item_registry[iid] = ItemDefinition(**defn_kwargs)

        # Every starting item goes into inventory so it can be swapped or dropped.
        # Weapons and armor are also equipped immediately; consumables/combat
        # items are inventory-only (no dedicated slot).
        if name not in player.inventory:
            player.inventory.append(name)

        if item_type == "weapon":
            player.equipped_weapon = Weapon(
                name=name,
                damage_dice=raw.get("damage_dice", "1d4"),
                hit_bonus=raw.get("hit_bonus", 0),
                damage_type=raw.get("damage_type", "slashing"),
                description=defn_kwargs["description"],
            )
        elif item_type == "armor":
            player.equipped_armor = Armor(
                name=name,
                ac_bonus=raw.get("ac_bonus", 0),
                description=defn_kwargs["description"],
            )

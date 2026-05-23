"""Archetype (class) definitions and starting-item application for ChronosTUI.

Each entry in ARCHETYPES defines:
- display_name / description — shown in the class-select screen
- stats          — initial ability scores (6 D&D stats)
- hp             — starting max HP (overrides the default 10)
- actions        — keys for combat slots [2], [3], [4] in that order
- starting_items — list of item dicts; weapons/armour auto-equip, rest go to inventory

Keep this file free of imports from other engine modules at module level so it
can be imported early in the startup sequence without circular-import issues.
"""

from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from engine.models import GameState

ARCHETYPES: Dict[str, Dict[str, Any]] = {
    "fighter": {
        "display_name": "Fighter",
        "description": "Iron will, stronger arm. Cleave through groups and shrug off wounds.",
        "stats": {
            "strength": 15, "constitution": 14, "dexterity": 10,
            "intelligence": 8,  "wisdom": 10,   "charisma": 9,
        },
        "hp": 14,
        "actions": ["cleave", "second_wind", "defend"],  # slots [2], [3], [4]
        "starting_items": [
            {"id": "ironclad_sword",   "item_type": "weapon",    "name": "Ironclad Sword",
             "damage_dice": "1d8", "hit_bonus": 1, "damage_type": "slashing"},
            {"id": "chainmail",        "item_type": "armor",     "name": "Chainmail",
             "ac_bonus": 4},
            {"id": "health_potion",    "item_type": "consumable","name": "Health Potion",
             "heal_amount": 15},
        ],
    },
    "mage": {
        "display_name": "Mage",
        "description": "Ancient power courses through half-understood words. Strike from range; shield with will.",
        "stats": {
            "strength": 8,  "constitution": 10, "dexterity": 11,
            "intelligence": 14, "wisdom": 12,   "charisma": 12,
        },
        "hp": 10,
        "actions": ["arcane_bolt", "mana_shield", "evade"],
        "starting_items": [
            {"id": "arcane_staff",  "item_type": "weapon",    "name": "Arcane Staff",
             "damage_dice": "1d6", "hit_bonus": 0, "damage_type": "bludgeoning"},
            {"id": "mage_robes",    "item_type": "armor",     "name": "Mage Robes",
             "ac_bonus": 0},
            {"id": "mana_flask",    "item_type": "consumable","name": "Mana Flask",
             "heal_amount": 10},
        ],
    },
    "monk": {
        "display_name": "Monk",
        "description": "Silence and precision. Strike twice for less; brace, centre, endure.",
        "stats": {
            "strength": 10, "constitution": 12, "dexterity": 13,
            "intelligence": 10, "wisdom": 12,   "charisma": 10,
        },
        "hp": 12,
        "actions": ["flurry", "iron_body", "meditate"],
        "starting_items": [
            {"id": "iron_kasa",    "item_type": "armor",     "name": "Iron Kasa",
             "ac_bonus": 1},
            {"id": "bandage_wrap", "item_type": "consumable","name": "Bandage Wrap",
             "heal_amount": 8},
        ],
    },
    "rogue": {
        "display_name": "Rogue",
        "description": "Shadows and desperation. Hit first, hit dirty, vanish before they recover.",
        "stats": {
            "strength": 9,  "constitution": 10, "dexterity": 15,
            "intelligence": 11, "wisdom": 10,   "charisma": 11,
        },
        "hp": 10,
        "actions": ["backstab", "smoke_screen", "poison_strike"],
        "starting_items": [
            {"id": "twin_daggers",       "item_type": "weapon",  "name": "Twin Daggers",
             "damage_dice": "1d4", "hit_bonus": 2, "damage_type": "piercing"},
            {"id": "scoundrel_leathers", "item_type": "armor",   "name": "Scoundrel Leathers",
             "ac_bonus": 2},
            {"id": "smoke_bomb",         "item_type": "combat",  "name": "Smoke Bomb",
             "tags": ["enemy_disadvantage"]},
        ],
    },
}


def apply_archetype(state: "GameState", key: str) -> None:
    """Set archetype, ability scores, max HP, and register + give starting items.

    Weapons and armour are auto-equipped; consumables and combat items are added
    to the player's inventory string list (matching the existing inventory model).
    """
    from .models import Armor, ItemDefinition, Weapon

    arch = ARCHETYPES[key]
    player = state.player

    player.archetype = key
    player.stats.update(arch["stats"])
    player.max_hp = arch["hp"]
    player.hp = arch["hp"]

    for raw in arch["starting_items"]:
        iid = raw["id"]
        item_type = raw["item_type"]
        name = raw["name"]

        # Build the ItemDefinition kwargs — pass only the fields ItemDefinition accepts
        defn_kwargs: Dict[str, Any] = {
            "id": iid,
            "name": name,
            "item_type": item_type,
            "description": f"Starting gear for {arch['display_name']}.",
        }
        optional_fields = [
            "damage_dice", "hit_bonus", "damage_type",
            "ac_bonus", "heal_amount", "tags",
        ]
        for field in optional_fields:
            if field in raw:
                defn_kwargs[field] = raw[field]

        state.item_registry[iid] = ItemDefinition(**defn_kwargs)

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
        else:
            # consumable / combat / utility → inventory string
            if name not in player.inventory:
                player.inventory.append(name)

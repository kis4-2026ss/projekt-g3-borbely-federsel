import random
from typing import Optional, Tuple

from .models import (
    Armor, GameState, ItemDefinition, Location, Player, Weapon, WorldState,
)


_STARTER_ITEMS = [
    ItemDefinition(
        id="rusted_shortsword",
        name="Rusted Shortsword",
        item_type="weapon",
        description="A simple blade pitted with age. Reliable enough.",
        damage_dice="1d6",
        hit_bonus=0,
        damage_type="slashing",
        tags=["weapon", "starter"],
    ),
    ItemDefinition(
        id="patched_leather",
        name="Patched Leather",
        item_type="armor",
        description="Scavenged leather scraps stitched into a serviceable jerkin.",
        ac_bonus=1,
        damage_reduction=1,
        tags=["armor", "starter"],
    ),
    ItemDefinition(
        id="health_potion",
        name="Health Potion",
        item_type="consumable",
        description="A vial of glowing blue liquid. Tastes of copper and rain.",
        heal_amount=8,
        tags=["consumable", "starter"],
    ),
    ItemDefinition(
        id="travelers_cloak",
        name="Traveler's Cloak",
        item_type="lore",
        description="A coarse grey cloak. Smells of woodsmoke and the long road.",
        tags=["lore", "starter"],
    ),
]


class GameEngine:
    def __init__(self, initial_state: Optional[GameState] = None):
        self.state = initial_state or self._create_default_state()

    def _create_default_state(self) -> GameState:
        stats = {
            stat: max(1, 10 + random.randint(-4, 4))
            for stat in (
                "strength", "dexterity", "intelligence",
                "constitution", "wisdom", "charisma",
            )
        }
        player = Player(name="Aurelian Exile", hp=30, max_hp=30, stats=stats)
        start_loc = Location(
            name="The Overgrown Outpost",
            description=(
                "A cluster of simple stone huts huddled in the shadow of a"
                " massive, cracked crystal spire."
            ),
            connections=["The Shattered Plaza"],
        )
        return GameState(
            player=player,
            current_location=start_loc.name,
            locations={start_loc.name: start_loc},
            world=WorldState(weather="Ethereal Mist"),
        )

    def initialize_campaign(self):
        self.state.record_choice(
            event="Awakening in the ruins of Elowen",
            choice="Opened eyes in the Overgrown Outpost",
            tags=["bloodline", "awakening", "elowen", "spire"],
        )
        for item in _STARTER_ITEMS:
            self.state.item_registry[item.id] = item
            self.state.player.add_item(item.name)

        sword = self.state.item_registry["rusted_shortsword"]
        self.state.player.equipped_weapon = Weapon(
            name=sword.name,
            damage_dice=sword.damage_dice or "1d6",
            hit_bonus=sword.hit_bonus,
            damage_type=sword.damage_type,
            description=sword.description,
        )

        armor = self.state.item_registry["patched_leather"]
        self.state.player.equipped_armor = Armor(
            name=armor.name,
            ac_bonus=armor.ac_bonus,
            damage_reduction=armor.damage_reduction,
            description=armor.description,
        )

    # ── Inventory helpers ─────────────────────────────────────────────────

    def _lookup_item(self, name: str) -> Optional[ItemDefinition]:
        return next(
            (it for it in self.state.item_registry.values() if it.name == name),
            None,
        )

    def equip_item_from_inventory(self, item_name: str) -> Tuple[bool, str]:
        if item_name not in self.state.player.inventory:
            return False, f"{item_name} is not in your inventory."
        item = self._lookup_item(item_name)
        if item is None:
            return False, f"{item_name} has no registered definition."
        if item.item_type == "weapon":
            self.state.player.equipped_weapon = Weapon(
                name=item.name,
                damage_dice=item.damage_dice or "1d6",
                hit_bonus=item.hit_bonus,
                damage_type=item.damage_type,
                description=item.description,
            )
            return True, f"Equipped {item.name}."
        if item.item_type == "armor":
            self.state.player.equipped_armor = Armor(
                name=item.name,
                ac_bonus=item.ac_bonus,
                damage_reduction=item.damage_reduction,
                description=item.description,
            )
            return True, f"Equipped {item.name}."
        return False, f"{item.item_type.capitalize()} items cannot be equipped."

    def drop_item(self, item_name: str) -> Tuple[bool, str]:
        if item_name not in self.state.player.inventory:
            return False, f"{item_name} is not in your inventory."
        self.state.player.remove_item(item_name)
        cleared_slot = ""
        if (
            self.state.player.equipped_weapon
            and self.state.player.equipped_weapon.name == item_name
        ):
            self.state.player.equipped_weapon = None
            cleared_slot = " (unequipped)"
        elif (
            self.state.player.equipped_armor
            and self.state.player.equipped_armor.name == item_name
        ):
            self.state.player.equipped_armor = None
            cleared_slot = " (unequipped)"
        return True, f"Dropped {item_name}{cleared_slot}."

    def is_equipped(self, item_name: str) -> bool:
        if (
            self.state.player.equipped_weapon
            and self.state.player.equipped_weapon.name == item_name
        ):
            return True
        if (
            self.state.player.equipped_armor
            and self.state.player.equipped_armor.name == item_name
        ):
            return True
        return False

    def unequip_item(self, item_name: str) -> Tuple[bool, str]:
        """Unequip a weapon or armor without removing it from inventory."""
        player = self.state.player
        if player.equipped_weapon and player.equipped_weapon.name == item_name:
            player.equipped_weapon = None
            return True, f"Unequipped {item_name}."
        if player.equipped_armor and player.equipped_armor.name == item_name:
            player.equipped_armor = None
            return True, f"Unequipped {item_name}."
        return False, f"{item_name} is not currently equipped."

    def use_consumable(self, item_name: str) -> Tuple[bool, str]:
        """Use a consumable. Returns (success, message)."""
        if item_name not in self.state.player.inventory:
            return False, f"{item_name} is not in your inventory."
        item = self._lookup_item(item_name)
        if item is None or item.item_type != "consumable":
            return False, f"{item_name} cannot be used."
        if item.heal_amount > 0:
            self.state.player.remove_item(item_name)
            self.state.player.heal(item.heal_amount)
            self.state.add_log(
                f"Used {item_name}: restored {item.heal_amount} HP"
                f" ({self.state.player.hp}/{self.state.player.max_hp})."
            )
            return True, f"Restored {item.heal_amount} HP."
        return False, f"{item_name} has no use effect defined."

    def save_game(self):
        self.state.save_to_file()

    def load_game(self):
        self.state = GameState.load_from_file()

    def use_potion(self):
        """Legacy shortcut — use the first Health Potion in inventory."""
        ok, msg = self.use_consumable("Health Potion")
        if not ok:
            self.state.add_log("You reach for a potion, but find only empty glass.")

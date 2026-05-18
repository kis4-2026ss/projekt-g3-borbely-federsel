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
    """Central coordinator for game logic. Holds the active GameState and
    manages data-driven content loading."""

    def __init__(self, initial_state: Optional[GameState] = None):
        self.state = initial_state or self._create_default_state()

    def _create_default_state(self) -> GameState:
        player = Player(name="Aurelian Exile", hp=30, max_hp=30)

        start_loc = Location(
            name="The Overgrown Outpost",
            description="A cluster of simple stone huts huddled in the shadow of a massive, cracked crystal spire.",
            connections=["The Shattered Plaza"],
        )

        return GameState(
            player=player,
            current_location=start_loc.name,
            locations={start_loc.name: start_loc},
            world=WorldState(weather="Ethereal Mist"),
        )

    def initialize_campaign(self):
        """Seed the initial world state and starting kit. Quests, encounters,
        and additional items are created dynamically by the LLM via
        define_quest, define_encounter, and define_item ops."""
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
            description=armor.description,
        )

    # ── Inventory actions (called directly by the UI, not via LLM ops) ───

    def _lookup_item(self, name: str) -> Optional[ItemDefinition]:
        return next(
            (it for it in self.state.item_registry.values() if it.name == name),
            None,
        )

    def equip_item_from_inventory(self, item_name: str) -> Tuple[bool, str]:
        """Equip an item from inventory by name. Returns (success, message)."""
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
                description=item.description,
            )
            return True, f"Equipped {item.name}."
        return False, f"{item.item_type.capitalize()} items cannot be equipped."

    def drop_item(self, item_name: str) -> Tuple[bool, str]:
        """Drop an item from inventory. If it is currently equipped, the slot is
        also cleared. Returns (success, message)."""
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
        if self.state.player.equipped_weapon and self.state.player.equipped_weapon.name == item_name:
            return True
        if self.state.player.equipped_armor and self.state.player.equipped_armor.name == item_name:
            return True
        return False

    def save_game(self):
        self.state.save_to_file()

    def load_game(self):
        """Replace the active state with the contents of savegame.json.
        Raises models.IncompatibleSaveError if the save schema is out of date."""
        self.state = GameState.load_from_file()

    def use_potion(self):
        if "Health Potion" in self.state.player.inventory:
            self.state.player.remove_item("Health Potion")
            self.state.player.heal(15)
            self.state.add_log("You consume a vial of glowing blue liquid. The mana soothes your aching bones.")
        else:
            self.state.add_log("You reach for a potion, but find only empty glass.")

from typing import Optional

from .models import GameState, Player, Location, WorldState


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
        """Seeds the initial world state. Quests, encounters, and items are created
        dynamically by the LLM via define_quest, define_encounter, and define_item ops."""
        self.state.record_choice(
            event="Awakening in the ruins of Elowen",
            choice="Opened eyes in the Overgrown Outpost",
            tags=["bloodline", "awakening", "elowen", "spire"],
        )

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

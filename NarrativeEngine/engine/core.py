from typing import Optional
from .models import GameState

class GameEngine:
    """
    The central coordinator for game logic.
    This class holds the ACTIVE GameState and provides methods to modify it.
    """
    
    def __init__(self, initial_state: Optional[GameState] = None):
        self.state = initial_state or self._create_default_state()

    def _create_default_state(self) -> GameState:
        from .models import Player
        return GameState(
            player=Player(name="Adventurer", hp=20, max_hp=20)
        )

    def save_game(self):
        """Persists the current in-memory state to disk."""
        self.state.save_to_file()

    def load_game(self):
        """Loads state from disk into memory."""
        self.state = GameState.load_from_file()

    # Domain methods that the UI or AI will call
    def pick_up_item(self, item: str):
        self.state.player.add_item(item)
        self.state.add_log(f"You picked up: {item}")
        # Active persistence: auto-save or just keep in memory
        # In a TUI, we usually keep in memory and save on exit/milestones

    def use_potion(self):
        if "Health Potion" in self.state.player.inventory:
            self.state.player.remove_item("Health Potion")
            self.state.player.heal(10)
            self.state.add_log("You drank a Health Potion and recovered 10 HP.")
        else:
            self.state.add_log("You don't have any potions!")

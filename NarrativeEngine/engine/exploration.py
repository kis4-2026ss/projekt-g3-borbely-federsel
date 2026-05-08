from .models import GameState

class ExplorationManager:
    """Handles Slice A: World-state tracking and NPC interactions."""
    
    def __init__(self, state: GameState):
        self.state = state

    def move_to(self, new_location: str):
        self.state.location = new_location
        self.state.add_log(f"You have arrived at {new_location}.")

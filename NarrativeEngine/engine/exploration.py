from .models import GameState


class ExplorationManager:
    """Slice A: deterministic helpers for world traversal. Most movement is now
    driven by LLM-proposed `move_to` ops in `state_changes.py`; this class
    remains for engine-side navigation hooks."""

    def __init__(self, state: GameState):
        self.state = state

    def move_to(self, new_location: str):
        self.state.current_location = new_location
        self.state.add_log(f"You have arrived at {new_location}.")

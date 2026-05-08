import random
from .models import GameState, Entity

class CombatManager:
    """Handles Slice B: Mathematical resolution of combat."""
    
    def __init__(self, state: GameState):
        self.state = state

    def roll_dice(self, sides: int = 20) -> int:
        return random.randint(1, sides)

    def resolve_attack(self, attacker: Entity, target: Entity):
        roll = self.roll_dice()
        damage = random.randint(1, 6)
        target.hp -= damage
        self.state.add_log(f"{attacker.name} attacks {target.name} for {damage} damage (Roll: {roll}).")

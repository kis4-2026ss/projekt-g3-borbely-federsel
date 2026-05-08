import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime

@dataclass
class Entity:
    name: str
    hp: int
    max_hp: int
    level: int = 1

@dataclass
class Player(Entity):
    inventory: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=lambda: {
        "strength": 10,
        "dexterity": 10,
        "intelligence": 10
    })

    def take_damage(self, amount: int):
        self.hp = max(0, self.hp - amount)

    def heal(self, amount: int):
        self.hp = min(self.max_hp, self.hp + amount)

    def add_item(self, item: str):
        self.inventory.append(item)

    def remove_item(self, item: str):
        if item in self.inventory:
            self.inventory.remove(item)

@dataclass
class PlotPoint:
    """Represents a significant narrative event or choice."""
    event: str
    choice_made: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass
class GameState:
    player: Player
    location: str = "The Rusty Gear Tavern"
    log: List[str] = field(default_factory=list)
    story_history: List[PlotPoint] = field(default_factory=list)
    turn_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_log(self, message: str):
        self.log.append(message)

    def record_choice(self, event: str, choice: str):
        self.story_history.append(PlotPoint(event=event, choice_made=choice))

    def to_json(self) -> str:
        """Serializes the current state for LLM context or saving."""
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, data: str):
        """Reconstructs state from a JSON string."""
        d = json.loads(data)
        # Handle Player reconstruction
        p_data = d.pop('player')
        player = Player(**p_data)
        # Handle history reconstruction
        history_data = d.pop('story_history', [])
        history = [PlotPoint(**hp) for hp in history_data]
        return cls(player=player, story_history=history, **d)

    def save_to_file(self, filename: str = "savegame.json"):
        with open(filename, 'w') as f:
            f.write(self.to_json())

    @classmethod
    def load_from_file(cls, filename: str = "savegame.json"):
        with open(filename, 'r') as f:
            return cls.from_json(f.read())

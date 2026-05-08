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
    lineage: str = "Aurelian Bloodline"
    bloodline_resonance: int = 0  # To be used for unique dialogues and powers
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
    tags: List[str] = field(default_factory=list)  # Added for Dynamic Recall
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass
class Quest:
    name: str
    description: str
    objectives: List[str]
    status: str = "active"  # active, completed, failed
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class NPC:
    name: str
    location: str
    disposition: int = 50  # 0 (hostile) to 100 (loyal)
    is_known: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class WorldState:
    time_of_day: str = "Morning"
    weather: str = "Gloomy"
    flags: Dict[str, bool] = field(default_factory=dict)

@dataclass
class Location:
    name: str
    description: str
    connections: List[str] = field(default_factory=list)
    interactables: List[str] = field(default_factory=list)

@dataclass
class GameState:
    player: Player
    current_location: str = "The Rusty Gear Tavern"
    locations: Dict[str, Location] = field(default_factory=dict)
    quests: Dict[str, Quest] = field(default_factory=dict)
    npcs: Dict[str, NPC] = field(default_factory=dict)
    world: WorldState = field(default_factory=WorldState)
    log: List[str] = field(default_factory=list)
    story_history: List[PlotPoint] = field(default_factory=list)
    turn_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_log(self, message: str):
        self.log.append(message)

    def record_choice(self, event: str, choice: str, tags: List[str] = None):
        self.story_history.append(PlotPoint(event=event, choice_made=choice, tags=tags or []))

    def to_json(self) -> str:
        """Serializes the current state for LLM context or saving."""
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, data: str):
        """Reconstructs state from a JSON string with robust handling for nested objects."""
        d = json.loads(data)
        
        # Safely extract and reconstruct nested dataclasses
        player_data = d.pop('player', {})
        player = Player(**player_data) if player_data else Player(name="Unknown", hp=10, max_hp=10)
        
        world_data = d.pop('world', {})
        world = WorldState(**world_data) if world_data else WorldState()
        
        history = [PlotPoint(**hp) for hp in d.pop('story_history', [])]
        quests = {k: Quest(**v) for k, v in d.pop('quests', {}).items()}
        npcs = {k: NPC(**v) for k, v in d.pop('npcs', {}).items()}
        locations = {k: Location(**v) for k, v in d.pop('locations', {}).items()}
        
        return cls(
            player=player, 
            world=world,
            story_history=history, 
            quests=quests,
            npcs=npcs,
            locations=locations,
            **d
        )

    def save_to_file(self, filename: str = "savegame.json"):
        with open(filename, 'w') as f:
            f.write(self.to_json())

    @classmethod
    def load_from_file(cls, filename: str = "savegame.json"):
        with open(filename, 'r') as f:
            return cls.from_json(f.read())

import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime

CURRENT_SCHEMA_VERSION = 2


class IncompatibleSaveError(Exception):
    """Raised when a save file's schema version does not match the engine's."""


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
    bloodline_resonance: int = 0
    stats: Dict[str, int] = field(default_factory=lambda: {
        "strength": 10,
        "dexterity": 10,
        "intelligence": 10,
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
    """A significant narrative event recorded for long-term recall."""
    event: str
    choice_made: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class Quest:
    name: str
    description: str
    objectives: List[str]
    completed_objectives: List[int] = field(default_factory=list)
    status: str = "active"  # active | completed | failed
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NPC:
    name: str
    location: str
    disposition: int = 50  # 0 (hostile) .. 100 (loyal)
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
    schema_version: int = CURRENT_SCHEMA_VERSION
    current_location: str = "The Rusty Gear Tavern"
    locations: Dict[str, Location] = field(default_factory=dict)
    quests: Dict[str, Quest] = field(default_factory=dict)
    npcs: Dict[str, NPC] = field(default_factory=dict)
    world: WorldState = field(default_factory=WorldState)
    log: List[str] = field(default_factory=list)
    story_history: List[PlotPoint] = field(default_factory=list)
    session_summary: str = ""
    summary_anchor_turn: int = 0
    turn_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_log(self, message: str):
        self.log.append(message)

    def record_choice(
        self,
        event: str,
        choice: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ):
        self.story_history.append(
            PlotPoint(event=event, choice_made=choice, tags=tags or [])
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, data: str):
        d = json.loads(data)

        version = d.get("schema_version", 1)
        if version != CURRENT_SCHEMA_VERSION:
            raise IncompatibleSaveError(
                f"Save file schema is version {version}, engine expects {CURRENT_SCHEMA_VERSION}. "
                f"Delete savegame.json to start a new chronicle."
            )

        player_data = d.pop("player", {})
        player = Player(**player_data) if player_data else Player(name="Unknown", hp=10, max_hp=10)

        world_data = d.pop("world", {})
        world = WorldState(**world_data) if world_data else WorldState()

        history = [PlotPoint(**hp) for hp in d.pop("story_history", [])]
        quests = {k: Quest(**v) for k, v in d.pop("quests", {}).items()}
        npcs = {k: NPC(**v) for k, v in d.pop("npcs", {}).items()}
        locations = {k: Location(**v) for k, v in d.pop("locations", {}).items()}

        return cls(
            player=player,
            world=world,
            story_history=history,
            quests=quests,
            npcs=npcs,
            locations=locations,
            **d,
        )

    def save_to_file(self, filename: str = "savegame.json"):
        """Atomic save: write to a temp file, then replace the target."""
        tmp = f"{filename}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self.to_json())
        os.replace(tmp, filename)

    @classmethod
    def load_from_file(cls, filename: str = "savegame.json"):
        with open(filename, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())

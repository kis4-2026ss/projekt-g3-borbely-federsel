import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime

CURRENT_SCHEMA_VERSION = 4


class IncompatibleSaveError(Exception):
    """Raised when a save file's schema version does not match the engine's."""


@dataclass
class DiceRoll:
    dice: str                  # "1d20", "2d6"
    rolls: List[int]           # individual die results
    modifier: int              # total modifier added to sum
    total: int
    dc: Optional[int] = None   # difficulty class or AC target
    success: Optional[bool] = None
    label: str = ""
    roll_type: str = "check"   # "check" | "attack" | "damage" | "initiative"


@dataclass
class Weapon:
    name: str
    damage_dice: str           # "1d6", "2d8"
    hit_bonus: int = 0
    damage_type: str = "slashing"
    description: str = ""


@dataclass
class Armor:
    name: str
    ac_bonus: int
    damage_reduction: int = 0   # flat DR: subtracted from every incoming hit
    description: str = ""


@dataclass
class StatusEffect:
    name: str
    duration_turns: int
    roll_modifier: int = 0     # applied to all dice rolls while active
    description: str = ""


@dataclass
class Enemy:
    name: str
    hp: int
    max_hp: int
    ac: int
    attack_bonus: int = 0
    damage_dice: str = "1d6"
    level: int = 1


@dataclass
class ItemDefinition:
    """A structured item with mechanical properties, authored by the LLM via define_item."""
    id: str                           # unique slug, e.g. "solar_crest"
    name: str
    item_type: str                    # "weapon" | "armor" | "consumable" | "quest" | "lore"
    description: str
    value_gold: int = 0
    damage_dice: Optional[str] = None # weapon only — e.g. "1d8"
    hit_bonus: int = 0                # weapon only
    damage_type: str = "slashing"     # weapon only
    ac_bonus: int = 0                 # armor only
    damage_reduction: int = 0         # armor only — flat DR per hit
    heal_amount: int = 0              # consumable only
    tags: List[str] = field(default_factory=list)


@dataclass
class EncounterTemplate:
    """A fully authored encounter: stat block + narrative context + rewards.
    Defined by the LLM when it first introduces a named enemy; instantiated later via spawn_encounter."""
    id: str                           # unique slug, e.g. "weeping_guardian"
    name: str
    description: str                  # what the player sees on approach
    enemy: Enemy                      # full stat block (nested dataclass)
    xp_reward: int
    gold_reward: int = 0
    item_rewards: List[str] = field(default_factory=list)  # item names or ItemDefinition ids
    narrative_flavor: str = ""        # combat-description guidance for the LLM
    defeat_condition: str = "defeat"  # "defeat" | "soothe" | "outwit" | "endure"
    quest_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    is_boss: bool = False
    spawned: bool = False             # True once spawn_encounter has fired


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
        "constitution": 10,
        "wisdom": 10,
        "charisma": 10,
    })
    equipped_weapon: Optional[Weapon] = None
    equipped_armor: Optional[Armor] = None
    gold: int = 0
    experience: int = 0
    status_effects: List[StatusEffect] = field(default_factory=list)

    def stat_mod(self, stat: str) -> int:
        """D&D-style ability modifier: (score - 10) // 2."""
        return (self.stats.get(stat, 10) - 10) // 2

    @property
    def ac(self) -> int:
        dex_bonus = self.stat_mod("dexterity")
        armor_bonus = self.equipped_armor.ac_bonus if self.equipped_armor else 0
        return 10 + dex_bonus + armor_bonus

    @property
    def xp_to_next_level(self) -> int:
        return self.level * 100

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
    in_combat: bool = False
    active_enemies: List[Enemy] = field(default_factory=list)
    combat_log: List[str] = field(default_factory=list)
    encounter_registry: Dict[str, "EncounterTemplate"] = field(default_factory=dict)
    item_registry: Dict[str, "ItemDefinition"] = field(default_factory=dict)
    last_rolls: List[DiceRoll] = field(default_factory=list)  # transient — not saved to disk

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

    def tick_status_effects(self) -> List[str]:
        """Decrement all status effect durations; return list of names that expired."""
        expired = []
        remaining = []
        for effect in self.player.status_effects:
            effect.duration_turns -= 1
            if effect.duration_turns <= 0:
                expired.append(effect.name)
            else:
                remaining.append(effect)
        self.player.status_effects = remaining
        return expired

    def to_json(self) -> str:
        d = asdict(self)
        d.pop("last_rolls", None)  # transient, never persisted
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, data: str):
        d = json.loads(data)

        version = d.get("schema_version", 1)
        if version != CURRENT_SCHEMA_VERSION:
            raise IncompatibleSaveError(
                f"Save file schema is version {version}, engine expects {CURRENT_SCHEMA_VERSION}. "
                f"Delete savegame.json to start a new chronicle."
            )

        # Reconstruct Player with nested objects
        player_data = d.pop("player", {})
        weapon_data = player_data.pop("equipped_weapon", None)
        armor_data = player_data.pop("equipped_armor", None)
        status_data = player_data.pop("status_effects", [])

        player = Player(**player_data) if player_data else Player(name="Unknown", hp=10, max_hp=10)
        player.equipped_weapon = Weapon(**weapon_data) if weapon_data else None
        player.equipped_armor = Armor(**armor_data) if armor_data else None
        player.status_effects = [StatusEffect(**e) for e in (status_data or [])]

        world_data = d.pop("world", {})
        world = WorldState(**world_data) if world_data else WorldState()

        history = [PlotPoint(**pp) for pp in d.pop("story_history", [])]
        quests = {k: Quest(**v) for k, v in d.pop("quests", {}).items()}
        npcs = {k: NPC(**v) for k, v in d.pop("npcs", {}).items()}
        locations = {k: Location(**v) for k, v in d.pop("locations", {}).items()}
        active_enemies = [Enemy(**e) for e in d.pop("active_enemies", [])]

        # Reconstruct EncounterTemplate (contains nested Enemy)
        encounter_registry: Dict[str, EncounterTemplate] = {}
        for eid, edata in d.pop("encounter_registry", {}).items():
            enemy_data = edata.pop("enemy", {})
            enc_enemy = (
                Enemy(**enemy_data) if enemy_data
                else Enemy(name="Unknown", hp=10, max_hp=10, ac=10)
            )
            encounter_registry[eid] = EncounterTemplate(enemy=enc_enemy, **edata)

        # Reconstruct ItemDefinition (flat — no nested dataclasses)
        item_registry: Dict[str, ItemDefinition] = {
            iid: ItemDefinition(**idata)
            for iid, idata in d.pop("item_registry", {}).items()
        }

        d.pop("last_rolls", None)  # not in save files, but guard anyway

        return cls(
            player=player,
            world=world,
            story_history=history,
            quests=quests,
            npcs=npcs,
            locations=locations,
            active_enemies=active_enemies,
            encounter_registry=encounter_registry,
            item_registry=item_registry,
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

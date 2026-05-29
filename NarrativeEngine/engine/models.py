import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime

CURRENT_SCHEMA_VERSION = 6


class IncompatibleSaveError(Exception):
    """Raised when a save file's schema version does not match the engine's."""


@dataclass
class DiceRoll:
    dice: str                  # "1d20", "2d6"
    rolls: List[int]           # individual die results actually kept
    modifier: int              # total modifier added to sum
    total: int
    dc: Optional[int] = None   # difficulty class or AC target
    success: Optional[bool] = None
    label: str = ""
    roll_type: str = "check"   # "check" | "attack" | "damage" | "initiative"
    natural: Optional[int] = None   # the raw d20 face value (single-d20 rolls only)
    is_critical: bool = False       # natural 20 on an attack roll
    is_fumble: bool = False         # natural 1 on an attack roll
    advantage: int = 0              # -1 disadvantage | 0 normal | +1 advantage
    dropped: List[int] = field(default_factory=list)  # dice discarded by (dis)advantage


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
    ac_bonus: int               # D&D 5e: armor only raises AC, never reduces damage
    description: str = ""


@dataclass
class StatusEffect:
    name: str
    duration_turns: int
    roll_modifier: int = 0     # flat bonus/penalty applied to the player's d20 rolls
    advantage: int = 0         # -1 imposes disadvantage | 0 none | +1 grants advantage
    description: str = ""


@dataclass
class Enemy:
    name: str
    hp: int
    max_hp: int
    ac: int
    attack_bonus: int = 0
    damage_dice: str = "1d6"
    damage_bonus: int = 0      # ability modifier added to the enemy's damage rolls
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
    ac_bonus: int = 0                 # armor only — raises AC
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
    looted: bool = False              # True once loot_encounter has distributed its rewards
    defined_at: str = ""              # auto-set to current_location when define_encounter fires


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
    temp_ac_bonus: int = 0        # transient — set by Defend/Iron Body, reset each turn
    archetype: str = ""           # "fighter" | "mage" | "monk" | "rogue" | "" (unset)
    combat_resource: int = 0      # current class resource (Rage/Mana/Ki/Energy)
    max_combat_resource: int = 0  # 0 = this archetype has no resource system

    def stat_mod(self, stat: str) -> int:
        """D&D-style ability modifier: (score - 10) // 2."""
        return (self.stats.get(stat, 10) - 10) // 2

    @property
    def proficiency_bonus(self) -> int:
        """D&D 5e proficiency: +2 at Lv1, +1 per 4 levels thereafter."""
        return 2 + (self.level - 1) // 4

    @property
    def ac(self) -> int:
        dex_bonus = self.stat_mod("dexterity")
        armor_bonus = self.equipped_armor.ac_bonus if self.equipped_armor else 0
        return 10 + dex_bonus + armor_bonus + self.temp_ac_bonus

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
    is_known: bool = False


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
    enemy_attack_adv: int = 0    # transient — -1 dis / 0 normal / +1 adv for enemy; cleared after use
    mana_shield_value: int = 0   # transient — set by Mage's Mana Shield, consumed in resolve_enemy_attack
    location_entered_turn: int = 0       # transient — turn on which the player last moved to a new location
    last_encounter_turn: int = 0         # transient — turn on which any combat last started (random cooldown)
    chronicle_cycle_start_turn: int = 0  # transient — reset to turn_count after each AFTERMATH
    last_narrative_mode: str = ""        # transient — previous mode, for phase-change detection
    phase_entered_turn: int = 0          # transient — turn_count when current narrative_mode began
    current_activity: str = "exploring"  # transient — current activity category
    activity_entered_turn: int = 0       # transient — turn_count when current activity began
    npc_exchanges_this_location: int = 0 # transient — total dialogue exchanges at current location
    npc_exchange_counts: Dict[str, int] = field(default_factory=dict) # transient — per-NPC exchange count
    player_approaching: bool = False     # transient — player signalled intent to engage a threat
    in_aftermath: bool = False           # transient — one-turn aftermath flag after combat ends
    enemies_defeated_since_rest: int = 0  # transient — 2+ = rest available (boss kill counts as 2)
    player_acts_first: bool = True       # transient — False when enemy won initiative on combat start

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
        d.pop("last_rolls", None)                   # transient, never persisted
        d.pop("enemy_attack_adv", None)             # transient
        d.pop("mana_shield_value", None)            # transient
        d.pop("location_entered_turn", None)        # transient
        d.pop("last_encounter_turn", None)          # transient
        d.pop("chronicle_cycle_start_turn", None)   # transient
        d.pop("last_narrative_mode", None)          # transient
        d.pop("phase_entered_turn", None)           # transient
        d.pop("current_activity", None)             # transient
        d.pop("activity_entered_turn", None)        # transient
        d.pop("npc_exchanges_this_location", None)  # transient
        d.pop("npc_exchange_counts", None)           # transient
        d.pop("player_approaching", None)           # transient
        d.pop("in_aftermath", None)                 # transient
        d.pop("enemies_defeated_since_rest", None)  # transient
        d.pop("player_acts_first", None)            # transient
        d.get("player", {}).pop("temp_ac_bonus", None)  # transient
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
        player_data.pop("temp_ac_bonus", None)  # transient — not in saves
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
        # Strip legacy fields removed from NPC (disposition, metadata) so old saves load cleanly.
        npcs = {
            k: NPC(**{fk: fv for fk, fv in v.items() if fk not in ("disposition", "metadata")})
            for k, v in d.pop("npcs", {}).items()
        }
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

        d.pop("last_rolls", None)                   # not in save files, but guard anyway
        d.pop("enemy_attack_adv", None)             # transient — guard for forward compat
        d.pop("mana_shield_value", None)            # transient — guard for forward compat
        d.pop("location_entered_turn", None)        # transient — guard for forward compat
        d.pop("last_encounter_turn", None)          # transient — guard for forward compat
        d.pop("chronicle_cycle_start_turn", None)   # transient — guard for forward compat
        d.pop("last_narrative_mode", None)          # transient — guard for forward compat
        d.pop("phase_entered_turn", None)           # transient — guard for forward compat
        d.pop("current_activity", None)             # transient — guard for forward compat
        d.pop("activity_entered_turn", None)        # transient — guard for forward compat
        d.pop("npc_exchanges_this_location", None)  # transient — guard for forward compat
        d.pop("npc_exchange_counts", None)           # transient — guard for forward compat
        d.pop("player_approaching", None)           # transient — guard for forward compat
        d.pop("in_aftermath", None)                 # transient — guard for forward compat
        d.pop("enemies_defeated_since_rest", None)  # transient — guard for forward compat
        d.pop("player_acts_first", None)            # transient — guard for forward compat
        d.pop("last_rest_turn", None)               # transient — guard for old saves

        state = cls(
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

        # ── Post-load transient field initialisation ──────────────────────
        # Transient clock fields default to 0, but the orchestrator computes
        # scene-pressure deltas as (turn_count - field).  Leaving them at 0
        # would make every delta read as turn_count (e.g. 52), instantly
        # triggering the "5 turns → force encounter" backstop on the very
        # first post-load narrative turn.  Reset them all to turn_count so
        # every delta starts at 0.
        state.location_entered_turn      = state.turn_count
        state.last_encounter_turn        = state.turn_count
        state.phase_entered_turn         = state.turn_count
        state.activity_entered_turn      = state.turn_count
        state.chronicle_cycle_start_turn = state.turn_count

        # Stale combat log: only meaningful during active combat.  Clear it
        # on load when not in combat so the LLM doesn't see old fight lines
        # as current context during exploration.
        if not state.in_combat:
            state.combat_log.clear()

        return state

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

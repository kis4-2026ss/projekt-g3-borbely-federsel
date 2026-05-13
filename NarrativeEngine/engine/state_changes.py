"""Validated state-mutation ops proposed by the LLM.

The LLM returns a list of small `{"op": ..., ...}` dicts in its structured
response. `apply_changes` walks that list and dispatches each entry through a
strict handler. Unknown ops, malformed payloads, and out-of-range values are
dropped (with a description) rather than crashing the turn loop.
"""

from typing import Dict, Any, List

from .models import Armor, Enemy, GameState, Location, NPC, StatusEffect, Weapon
from .combat import CombatManager
from . import dice


def apply_changes(state: GameState, changes: List[Dict[str, Any]]) -> List[str]:
    """Apply LLM-proposed state changes. Returns short human-readable descriptions
    for each change actually applied (or skipped, with a reason)."""
    descriptions: List[str] = []
    if not isinstance(changes, list):
        return ["[state_changes was not a list — ignored]"]

    for change in changes:
        if not isinstance(change, dict):
            descriptions.append("[skipped: change entry is not an object]")
            continue
        op = change.get("op")
        handler = _HANDLERS.get(op)
        if handler is None:
            descriptions.append(f"[skipped: unknown op '{op}']")
            continue
        try:
            desc = handler(state, change)
            if desc:
                descriptions.append(desc)
        except Exception as e:
            descriptions.append(f"[op '{op}' failed: {e}]")
    return descriptions


# ── Player inventory / health ops ──────────────────────────────────────────

def _add_item(state: GameState, change: Dict[str, Any]) -> str:
    item = change.get("value")
    if not isinstance(item, str) or not item.strip():
        raise ValueError("'value' must be a non-empty string")
    state.player.add_item(item.strip())
    return f"+{item.strip()} (inventory)"


def _remove_item(state: GameState, change: Dict[str, Any]) -> str:
    item = change.get("value")
    if not isinstance(item, str):
        raise ValueError("'value' must be a string")
    if item in state.player.inventory:
        state.player.remove_item(item)
        return f"-{item} (inventory)"
    return ""


def _damage_player(state: GameState, change: Dict[str, Any]) -> str:
    amount = _coerce_int(change.get("value"), field="value")
    amount = max(0, min(amount, 999))
    state.player.take_damage(amount)
    return f"player HP -{amount}"


def _heal_player(state: GameState, change: Dict[str, Any]) -> str:
    amount = _coerce_int(change.get("value"), field="value")
    amount = max(0, min(amount, 999))
    state.player.heal(amount)
    return f"player HP +{amount}"


def _adjust_resonance(state: GameState, change: Dict[str, Any]) -> str:
    delta = _coerce_int(change.get("delta"), field="delta")
    state.player.bloodline_resonance += delta
    sign = "+" if delta >= 0 else ""
    return f"resonance {sign}{delta} (now {state.player.bloodline_resonance})"


# ── Equipment ops ──────────────────────────────────────────────────────────

def _equip_weapon(state: GameState, change: Dict[str, Any]) -> str:
    name = change.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' must be a non-empty string")
    damage_dice = change.get("damage_dice", "1d6")
    hit_bonus = _coerce_int(change.get("hit_bonus", 0), field="hit_bonus")
    damage_type = change.get("damage_type", "slashing")
    description = change.get("description", "")
    state.player.equipped_weapon = Weapon(
        name=name.strip(),
        damage_dice=damage_dice if isinstance(damage_dice, str) else "1d6",
        hit_bonus=hit_bonus,
        damage_type=damage_type if isinstance(damage_type, str) else "slashing",
        description=description if isinstance(description, str) else "",
    )
    return f"equipped weapon: {name.strip()} ({damage_dice})"


def _equip_armor(state: GameState, change: Dict[str, Any]) -> str:
    name = change.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' must be a non-empty string")
    ac_bonus = _coerce_int(change.get("ac_bonus", 0), field="ac_bonus")
    description = change.get("description", "")
    state.player.equipped_armor = Armor(
        name=name.strip(),
        ac_bonus=ac_bonus,
        description=description if isinstance(description, str) else "",
    )
    return f"equipped armor: {name.strip()} (+{ac_bonus} AC)"


# ── XP / Gold ops ──────────────────────────────────────────────────────────

def _award_xp(state: GameState, change: Dict[str, Any]) -> str:
    amount = _coerce_int(change.get("amount"), field="amount")
    amount = max(0, amount)
    player = state.player
    player.experience += amount

    leveled_up = False
    while player.experience >= player.xp_to_next_level:
        player.experience -= player.xp_to_next_level
        player.level += 1
        player.max_hp += 5
        player.hp = min(player.hp + 5, player.max_hp)
        leveled_up = True

    if leveled_up:
        return f"+{amount} XP → Level {player.level}! (max HP +5)"
    return f"+{amount} XP ({player.experience}/{player.xp_to_next_level})"


def _add_gold(state: GameState, change: Dict[str, Any]) -> str:
    amount = _coerce_int(change.get("amount"), field="amount")
    amount = max(0, amount)
    state.player.gold += amount
    return f"+{amount} gold (total: {state.player.gold})"


def _remove_gold(state: GameState, change: Dict[str, Any]) -> str:
    amount = _coerce_int(change.get("amount"), field="amount")
    amount = max(0, amount)
    if state.player.gold < amount:
        raise ValueError(f"Insufficient gold (have {state.player.gold}, need {amount})")
    state.player.gold -= amount
    return f"-{amount} gold (remaining: {state.player.gold})"


# ── Status effect ops ──────────────────────────────────────────────────────

def _apply_status(state: GameState, change: Dict[str, Any]) -> str:
    name = change.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' must be a non-empty string")
    duration = _coerce_int(change.get("duration_turns", 3), field="duration_turns")
    roll_modifier = _coerce_int(change.get("roll_modifier", 0), field="roll_modifier")
    description = change.get("description", "")
    # Replace any existing effect with the same name
    state.player.status_effects = [
        e for e in state.player.status_effects if e.name.lower() != name.strip().lower()
    ]
    state.player.status_effects.append(StatusEffect(
        name=name.strip(),
        duration_turns=max(1, duration),
        roll_modifier=roll_modifier,
        description=description if isinstance(description, str) else "",
    ))
    return f"status applied: {name.strip()} ({duration} turns)"


def _remove_status(state: GameState, change: Dict[str, Any]) -> str:
    name = change.get("name")
    if not isinstance(name, str):
        raise ValueError("'name' must be a string")
    before = len(state.player.status_effects)
    state.player.status_effects = [
        e for e in state.player.status_effects if e.name.lower() != name.strip().lower()
    ]
    if len(state.player.status_effects) == before:
        return ""
    return f"status removed: {name.strip()}"


# ── Stat ops ───────────────────────────────────────────────────────────────

_VALID_STATS = {"strength", "dexterity", "intelligence", "constitution", "wisdom", "charisma"}


def _set_stat(state: GameState, change: Dict[str, Any]) -> str:
    stat = change.get("stat")
    if not isinstance(stat, str):
        raise ValueError("'stat' must be a string")
    stat = stat.strip().lower()
    if stat not in _VALID_STATS:
        raise ValueError(f"'{stat}' not valid. Choose from: {', '.join(sorted(_VALID_STATS))}")
    value = _coerce_int(change.get("value"), field="value")
    value = max(1, min(30, value))
    state.player.stats[stat] = value
    mod = state.player.stat_mod(stat)
    sign = "+" if mod >= 0 else ""
    return f"{stat} set to {value} ({sign}{mod})"


# ── Combat ops ─────────────────────────────────────────────────────────────

def _start_combat(state: GameState, change: Dict[str, Any]) -> str:
    enemy_name = change.get("enemy_name")
    if not isinstance(enemy_name, str) or not enemy_name.strip():
        raise ValueError("'enemy_name' must be a non-empty string")
    hp = max(1, _coerce_int(change.get("enemy_hp", 10), field="enemy_hp"))
    ac = max(1, _coerce_int(change.get("enemy_ac", 10), field="enemy_ac"))
    attack_bonus = _coerce_int(change.get("enemy_attack_bonus", 0), field="enemy_attack_bonus")
    damage_dice = change.get("enemy_damage_dice", "1d6")
    level = max(1, _coerce_int(change.get("enemy_level", 1), field="enemy_level"))

    enemy = Enemy(
        name=enemy_name.strip(),
        hp=hp,
        max_hp=hp,
        ac=ac,
        attack_bonus=attack_bonus,
        damage_dice=damage_dice if isinstance(damage_dice, str) else "1d6",
        level=level,
    )
    state.active_enemies.append(enemy)
    state.in_combat = True
    state.combat_log.clear()

    cm = CombatManager(state)
    p_init, e_init = cm.roll_initiative()
    state.last_rolls.extend([p_init, e_init])

    player_first = p_init.total >= e_init.total
    order = "You act first." if player_first else f"{enemy_name.strip()} acts first."
    state.combat_log.append(f"Combat started. {order}")

    return (
        f"combat started: {enemy_name.strip()} (HP:{hp} AC:{ac} ATK:+{attack_bonus})"
        f" | {order}"
    )


def _end_combat(state: GameState, change: Dict[str, Any]) -> str:
    outcome = change.get("outcome", "ended")
    state.active_enemies.clear()
    state.in_combat = False
    return f"combat ended ({outcome})"


def _roll_attack(state: GameState, change: Dict[str, Any]) -> str:
    if not state.in_combat or not state.active_enemies:
        raise ValueError("No active combat — cannot roll_attack")
    target_name = (change.get("target") or "").strip().lower()
    if target_name:
        enemy = next(
            (e for e in state.active_enemies if e.name.lower() == target_name),
            state.active_enemies[0],
        )
    else:
        enemy = state.active_enemies[0]

    cm = CombatManager(state)
    rolls = cm.resolve_full_round(enemy)
    state.last_rolls.extend(rolls)

    parts = []
    for r in rolls:
        if r.roll_type == "attack":
            hit = "HIT" if r.success else "MISS"
            parts.append(f"attack {r.total} vs AC {r.dc} → {hit}")
        elif r.roll_type == "damage":
            parts.append(f"dmg {r.total}")
    if enemy.hp <= 0:
        parts.append(f"{enemy.name} defeated!")
    return " | ".join(parts) if parts else "attack resolved"


def _roll_skill_check(state: GameState, change: Dict[str, Any]) -> str:
    stat = (change.get("stat") or "strength").strip().lower()
    if stat not in _VALID_STATS:
        stat = "strength"
    dc = _coerce_int(change.get("dc", 10), field="dc")
    label = change.get("label") or f"{stat.capitalize()} Check"

    modifier = state.player.stat_mod(stat)
    modifier += sum(e.roll_modifier for e in state.player.status_effects)

    result = dice.roll("1d20", modifier=modifier, dc=dc, label=label, roll_type="check")
    state.last_rolls.append(result)

    outcome = "success" if result.success else "failure"
    return f"{label}: {result.total} vs DC {dc} → {outcome}"


# ── World / location ops ───────────────────────────────────────────────────

def _move_to(state: GameState, change: Dict[str, Any]) -> str:
    name = change.get("value")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'value' must be a non-empty location name")
    name = name.strip()
    if name not in state.locations:
        state.locations[name] = Location(name=name, description="(Newly travelled-to area.)")
    state.current_location = name
    return f"moved to {name}"


def _discover_location(state: GameState, change: Dict[str, Any]) -> str:
    name = change.get("name")
    description = change.get("description", "")
    connections = change.get("connections", [])
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' must be a non-empty string")
    if not isinstance(connections, list):
        connections = []
    name = name.strip()
    if name in state.locations:
        loc = state.locations[name]
        if description and (not loc.description or loc.description.startswith("(Newly")):
            loc.description = description
        for c in connections:
            if isinstance(c, str) and c not in loc.connections:
                loc.connections.append(c)
        return ""
    state.locations[name] = Location(
        name=name,
        description=description if isinstance(description, str) else "",
        connections=[c for c in connections if isinstance(c, str)],
    )
    return f"discovered: {name}"


def _set_time_of_day(state: GameState, change: Dict[str, Any]) -> str:
    value = change.get("value")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("'value' must be a non-empty string")
    state.world.time_of_day = value.strip()
    return f"time → {value.strip()}"


def _set_weather(state: GameState, change: Dict[str, Any]) -> str:
    value = change.get("value")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("'value' must be a non-empty string")
    state.world.weather = value.strip()
    return f"weather → {value.strip()}"


def _set_world_flag(state: GameState, change: Dict[str, Any]) -> str:
    key = change.get("key")
    value = change.get("value")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("'key' must be a non-empty string")
    state.world.flags[key.strip()] = bool(value)
    return f"flag {key} = {bool(value)}"


# ── Quest ops ──────────────────────────────────────────────────────────────

def _advance_quest(state: GameState, change: Dict[str, Any]) -> str:
    quest_id = change.get("quest_id")
    objective_index = _coerce_int(change.get("objective_index"), field="objective_index")
    if quest_id not in state.quests:
        raise ValueError(f"unknown quest_id '{quest_id}'")
    quest = state.quests[quest_id]
    if objective_index < 0 or objective_index >= len(quest.objectives):
        raise ValueError(f"objective_index {objective_index} out of range")
    if objective_index in quest.completed_objectives:
        return ""
    quest.completed_objectives.append(objective_index)
    return f"quest '{quest.name}': objective {objective_index} done"


def _complete_quest(state: GameState, change: Dict[str, Any]) -> str:
    quest_id = change.get("quest_id")
    if quest_id not in state.quests:
        raise ValueError(f"unknown quest_id '{quest_id}'")
    quest = state.quests[quest_id]
    if quest.status == "completed":
        return ""
    quest.status = "completed"
    return f"quest completed: {quest.name}"


def _fail_quest(state: GameState, change: Dict[str, Any]) -> str:
    quest_id = change.get("quest_id")
    if quest_id not in state.quests:
        raise ValueError(f"unknown quest_id '{quest_id}'")
    quest = state.quests[quest_id]
    if quest.status == "failed":
        return ""
    quest.status = "failed"
    return f"quest failed: {quest.name}"


# ── NPC ops ────────────────────────────────────────────────────────────────

def _npc_key(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _add_npc(state: GameState, change: Dict[str, Any]) -> str:
    name = change.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' must be a non-empty string")
    location = change.get("location")
    if not isinstance(location, str) or not location.strip():
        location = state.current_location
    disposition = _coerce_int(change.get("disposition", 50), field="disposition")
    disposition = max(0, min(100, disposition))
    key = _npc_key(name)
    if key in state.npcs:
        state.npcs[key].is_known = True
        return ""
    state.npcs[key] = NPC(
        name=name.strip(),
        location=location.strip(),
        disposition=disposition,
        is_known=True,
    )
    return f"NPC met: {name.strip()}"


def _update_npc_disposition(state: GameState, change: Dict[str, Any]) -> str:
    name = change.get("name")
    if not isinstance(name, str):
        raise ValueError("'name' must be a string")
    delta = _coerce_int(change.get("delta"), field="delta")
    key = _npc_key(name)
    if key not in state.npcs:
        raise ValueError(f"unknown NPC '{name}'")
    npc = state.npcs[key]
    npc.disposition = max(0, min(100, npc.disposition + delta))
    sign = "+" if delta >= 0 else ""
    return f"{npc.name}: disposition {sign}{delta} → {npc.disposition}"


# ── Helpers ────────────────────────────────────────────────────────────────

def _coerce_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"'{field}' must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as e:
            raise ValueError(f"'{field}' is not a valid integer: {value!r}") from e
    raise ValueError(f"'{field}' must be an integer (got {type(value).__name__})")


# ── Dispatch table ─────────────────────────────────────────────────────────

_HANDLERS = {
    # inventory / health
    "add_item": _add_item,
    "remove_item": _remove_item,
    "damage_player": _damage_player,
    "heal_player": _heal_player,
    "adjust_resonance": _adjust_resonance,
    # equipment
    "equip_weapon": _equip_weapon,
    "equip_armor": _equip_armor,
    # progression
    "award_xp": _award_xp,
    "add_gold": _add_gold,
    "remove_gold": _remove_gold,
    # status effects
    "apply_status": _apply_status,
    "remove_status": _remove_status,
    # stats
    "set_stat": _set_stat,
    # combat
    "start_combat": _start_combat,
    "end_combat": _end_combat,
    "roll_attack": _roll_attack,
    "roll_skill_check": _roll_skill_check,
    # world / location
    "move_to": _move_to,
    "discover_location": _discover_location,
    "set_time_of_day": _set_time_of_day,
    "set_weather": _set_weather,
    "set_world_flag": _set_world_flag,
    # quests
    "advance_quest": _advance_quest,
    "complete_quest": _complete_quest,
    "fail_quest": _fail_quest,
    # npcs
    "add_npc": _add_npc,
    "update_npc_disposition": _update_npc_disposition,
}


OP_REFERENCE = """Available state_changes ops (use the exact 'op' string):

INVENTORY / HEALTH
- {"op":"add_item","value":"<item>"}
- {"op":"remove_item","value":"<item>"}
- {"op":"damage_player","value":<int>}
- {"op":"heal_player","value":<int>}
- {"op":"adjust_resonance","delta":<int>}

EQUIPMENT
- {"op":"equip_weapon","name":"<name>","damage_dice":"1d8","hit_bonus":<int>,"damage_type":"slashing|piercing|bludgeoning"}
- {"op":"equip_armor","name":"<name>","ac_bonus":<int>}

PROGRESSION
- {"op":"award_xp","amount":<int>}
- {"op":"add_gold","amount":<int>}
- {"op":"remove_gold","amount":<int>}

STATUS EFFECTS
- {"op":"apply_status","name":"<name>","duration_turns":<int>,"roll_modifier":<int>}
- {"op":"remove_status","name":"<name>"}

STATS (score 1–30)
- {"op":"set_stat","stat":"strength|dexterity|intelligence|constitution|wisdom|charisma","value":<int>}

COMBAT
- {"op":"start_combat","enemy_name":"<name>","enemy_hp":<int>,"enemy_ac":<int>,"enemy_attack_bonus":<int>,"enemy_damage_dice":"1d6","enemy_level":<int>}
- {"op":"roll_attack","target":"<enemy name>"}
- {"op":"end_combat","outcome":"victory|fled|defeat"}
- {"op":"roll_skill_check","stat":"strength|dexterity|...","dc":<int>,"label":"<description>"}

WORLD / LOCATION
- {"op":"move_to","value":"<location name>"}
- {"op":"discover_location","name":"<name>","description":"<text>","connections":["<other location>"]}
- {"op":"set_time_of_day","value":"Morning|Midday|Evening|Night|..."}
- {"op":"set_weather","value":"<text>"}
- {"op":"set_world_flag","key":"<key>","value":<true|false>}

QUESTS
- {"op":"advance_quest","quest_id":"<id>","objective_index":<int>}
- {"op":"complete_quest","quest_id":"<id>"}
- {"op":"fail_quest","quest_id":"<id>"}

NPCS
- {"op":"add_npc","name":"<name>","location":"<location>","disposition":<0-100>}
- {"op":"update_npc_disposition","name":"<NPC name>","delta":<int>}

Only emit changes the narrative explicitly justifies. Return [] if nothing changed."""

"""Validated state-mutation ops proposed by the LLM.

The LLM returns a list of small `{"op": ..., ...}` dicts in its structured
response. `apply_changes` walks that list and dispatches each entry through a
strict handler. Unknown ops, malformed payloads, and out-of-range values are
dropped (with a description) rather than crashing the turn loop.
"""

from typing import Dict, Any, List

from .models import GameState, NPC, Location


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


# ---- Player ops -----------------------------------------------------------

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


# ---- World / location ops -------------------------------------------------

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
    return f"time -> {value.strip()}"


def _set_weather(state: GameState, change: Dict[str, Any]) -> str:
    value = change.get("value")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("'value' must be a non-empty string")
    state.world.weather = value.strip()
    return f"weather -> {value.strip()}"


def _set_world_flag(state: GameState, change: Dict[str, Any]) -> str:
    key = change.get("key")
    value = change.get("value")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("'key' must be a non-empty string")
    state.world.flags[key.strip()] = bool(value)
    return f"flag {key} = {bool(value)}"


# ---- Quest ops ------------------------------------------------------------

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


# ---- NPC ops --------------------------------------------------------------

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
    return f"{npc.name}: disposition {sign}{delta} -> {npc.disposition}"


# ---- helpers --------------------------------------------------------------

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


_HANDLERS = {
    "add_item": _add_item,
    "remove_item": _remove_item,
    "damage_player": _damage_player,
    "heal_player": _heal_player,
    "adjust_resonance": _adjust_resonance,
    "move_to": _move_to,
    "discover_location": _discover_location,
    "set_time_of_day": _set_time_of_day,
    "set_weather": _set_weather,
    "set_world_flag": _set_world_flag,
    "advance_quest": _advance_quest,
    "complete_quest": _complete_quest,
    "fail_quest": _fail_quest,
    "add_npc": _add_npc,
    "update_npc_disposition": _update_npc_disposition,
}


OP_REFERENCE = """Available state_changes ops (use the exact 'op' string):
- {"op":"add_item","value":"<item>"}
- {"op":"remove_item","value":"<item>"}
- {"op":"damage_player","value":<int>}
- {"op":"heal_player","value":<int>}
- {"op":"adjust_resonance","delta":<int>}
- {"op":"move_to","value":"<location name>"}
- {"op":"discover_location","name":"<name>","description":"<text>","connections":["<other location>"]}
- {"op":"set_time_of_day","value":"Morning|Midday|Evening|Night|..."}
- {"op":"set_weather","value":"<text>"}
- {"op":"set_world_flag","key":"<key>","value":<true|false>}
- {"op":"advance_quest","quest_id":"<id>","objective_index":<int>}
- {"op":"complete_quest","quest_id":"<id>"}
- {"op":"fail_quest","quest_id":"<id>"}
- {"op":"add_npc","name":"<name>","location":"<location>","disposition":<0-100>}
- {"op":"update_npc_disposition","name":"<NPC name>","delta":<int>}
Only emit changes the narrative explicitly justifies. Return [] if nothing changed."""

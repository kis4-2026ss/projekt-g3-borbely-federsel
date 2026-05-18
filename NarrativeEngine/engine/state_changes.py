"""Validated state-mutation ops proposed by the LLM.

The LLM returns a list of small `{"op": ..., ...}` dicts in its structured
response. `apply_changes` walks that list and dispatches each entry through a
strict handler. Unknown ops, malformed payloads, and out-of-range values are
dropped (with a description) rather than crashing the turn loop.
"""

from typing import Dict, Any, List, Tuple

from .models import (
    Armor, Enemy, EncounterTemplate, GameState, ItemDefinition,
    Location, NPC, Quest, StatusEffect, Weapon,
)
from .combat import CombatManager
from . import dice


def apply_changes(state: GameState, changes: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """Apply LLM-proposed state changes. Returns (op_name, description) tuples
    for each change actually applied (or skipped, with a reason).

    The op_name lets the UI classify and style each record without parsing the
    description text. Skip and global-validation failures use synthetic op names
    prefixed with '_' ('_invalid', '_skipped'); per-op failures keep their real
    op name so they're still grouped, but their description is bracketed."""
    records: List[Tuple[str, str]] = []
    if not isinstance(changes, list):
        return [("_invalid", "[state_changes was not a list — ignored]")]

    for change in changes:
        if not isinstance(change, dict):
            records.append(("_skipped", "[skipped: change entry is not an object]"))
            continue
        op = change.get("op")
        handler = _HANDLERS.get(op)
        if handler is None:
            records.append(("_skipped", f"[skipped: unknown op '{op}']"))
            continue
        try:
            desc = handler(state, change)
            if desc:
                records.append((op, desc))
        except Exception as e:
            records.append((op, f"[op '{op}' failed: {e}]"))
    return records


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


def _unequip_weapon(state: GameState, change: Dict[str, Any]) -> str:
    if state.player.equipped_weapon is None:
        return ""
    name = state.player.equipped_weapon.name
    state.player.equipped_weapon = None
    return f"unequipped weapon: {name}"


def _unequip_armor(state: GameState, change: Dict[str, Any]) -> str:
    if state.player.equipped_armor is None:
        return ""
    name = state.player.equipped_armor.name
    state.player.equipped_armor = None
    return f"unequipped armor: {name}"


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


# ── Encounter registry ops ────────────────────────────────────────────────

def _define_encounter(state: GameState, change: Dict[str, Any]) -> str:
    enc_id = change.get("id")
    if not isinstance(enc_id, str) or not enc_id.strip():
        raise ValueError("'id' must be a non-empty string")
    enc_id = enc_id.strip().lower().replace(" ", "_")

    name = change.get("name", enc_id)
    description = change.get("description", "")
    xp_reward = max(0, _coerce_int(change.get("xp_reward", 50), field="xp_reward"))
    gold_reward = max(0, _coerce_int(change.get("gold_reward", 0), field="gold_reward"))
    item_rewards = change.get("item_rewards", [])
    if not isinstance(item_rewards, list):
        item_rewards = []
    item_rewards = [r for r in item_rewards if isinstance(r, str)]

    enemy_name = change.get("enemy_name", name)
    enemy_hp = max(1, _coerce_int(change.get("enemy_hp", 10), field="enemy_hp"))
    enemy_ac = max(1, _coerce_int(change.get("enemy_ac", 10), field="enemy_ac"))
    enemy_atk = _coerce_int(change.get("enemy_attack_bonus", 0), field="enemy_attack_bonus")
    enemy_dmg = change.get("enemy_damage_dice", "1d6")
    enemy_lvl = max(1, _coerce_int(change.get("enemy_level", 1), field="enemy_level"))

    enemy = Enemy(
        name=enemy_name if isinstance(enemy_name, str) else name,
        hp=enemy_hp,
        max_hp=enemy_hp,
        ac=enemy_ac,
        attack_bonus=enemy_atk,
        damage_dice=enemy_dmg if isinstance(enemy_dmg, str) else "1d6",
        level=enemy_lvl,
    )
    template = EncounterTemplate(
        id=enc_id,
        name=name if isinstance(name, str) else enc_id,
        description=description if isinstance(description, str) else "",
        enemy=enemy,
        xp_reward=xp_reward,
        gold_reward=gold_reward,
        item_rewards=item_rewards,
        narrative_flavor=change.get("narrative_flavor", "") if isinstance(change.get("narrative_flavor"), str) else "",
        defeat_condition=change.get("defeat_condition", "defeat") if isinstance(change.get("defeat_condition"), str) else "defeat",
        quest_id=change.get("quest_id") if isinstance(change.get("quest_id"), str) else None,
        tags=[t for t in (change.get("tags") or []) if isinstance(t, str)],
        is_boss=bool(change.get("is_boss", False)),
    )
    state.encounter_registry[enc_id] = template
    boss_tag = " [BOSS]" if template.is_boss else ""
    return f"encounter defined: {template.name}{boss_tag} (id: {enc_id})"


def _spawn_encounter(state: GameState, change: Dict[str, Any]) -> str:
    enc_id = change.get("id")
    if not isinstance(enc_id, str):
        raise ValueError("'id' must be a string")
    enc_id = enc_id.strip().lower().replace(" ", "_")
    template = state.encounter_registry.get(enc_id)
    if template is None:
        raise ValueError(f"No encounter defined with id '{enc_id}'. Use define_encounter first.")

    src = template.enemy
    enemy = Enemy(
        name=src.name,
        hp=src.max_hp,
        max_hp=src.max_hp,
        ac=src.ac,
        attack_bonus=src.attack_bonus,
        damage_dice=src.damage_dice,
        level=src.level,
    )
    state.active_enemies.append(enemy)
    state.in_combat = True
    state.combat_log.clear()
    template.spawned = True

    cm = CombatManager(state)
    p_init, e_init = cm.roll_initiative()
    state.last_rolls.extend([p_init, e_init])

    player_first = p_init.total >= e_init.total
    order = "You act first." if player_first else f"{enemy.name} acts first."
    state.combat_log.append(f"Combat started. {order}")
    return f"encounter spawned: {template.name} (HP:{enemy.hp} AC:{enemy.ac}) | {order}"


def _loot_encounter(state: GameState, change: Dict[str, Any]) -> str:
    enc_id = change.get("id")
    if not isinstance(enc_id, str):
        raise ValueError("'id' must be a string")
    enc_id = enc_id.strip().lower().replace(" ", "_")
    template = state.encounter_registry.get(enc_id)
    if template is None:
        raise ValueError(f"No encounter defined with id '{enc_id}'")

    parts: List[str] = []
    if template.xp_reward > 0:
        desc = _award_xp(state, {"op": "award_xp", "amount": template.xp_reward})
        if desc:
            parts.append(desc)
    if template.gold_reward > 0:
        state.player.gold += template.gold_reward
        parts.append(f"+{template.gold_reward} gold")
    for item_name in template.item_rewards:
        defined = next(
            (it for it in state.item_registry.values() if it.name == item_name or it.id == item_name),
            None,
        )
        if defined:
            _give_defined_item(state, {"op": "give_defined_item", "id": defined.id})
            parts.append(f"received: {defined.name}")
        else:
            state.player.add_item(item_name)
            parts.append(f"received: {item_name}")
    return "loot: " + " | ".join(parts) if parts else "no loot"


# ── Item registry ops ──────────────────────────────────────────────────────

def _define_item(state: GameState, change: Dict[str, Any]) -> str:
    item_id = change.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("'id' must be a non-empty string")
    item_id = item_id.strip().lower().replace(" ", "_")

    name = change.get("name", item_id)
    item_type = change.get("item_type", "lore")
    valid_types = {"weapon", "armor", "consumable", "quest", "lore"}
    if item_type not in valid_types:
        item_type = "lore"
    description = change.get("description", "")

    item = ItemDefinition(
        id=item_id,
        name=name if isinstance(name, str) else item_id,
        item_type=item_type,
        description=description if isinstance(description, str) else "",
        value_gold=max(0, _coerce_int(change.get("value_gold", 0), field="value_gold")),
        damage_dice=change.get("damage_dice") if isinstance(change.get("damage_dice"), str) else None,
        hit_bonus=_coerce_int(change.get("hit_bonus", 0), field="hit_bonus"),
        damage_type=change.get("damage_type", "slashing") if isinstance(change.get("damage_type"), str) else "slashing",
        ac_bonus=_coerce_int(change.get("ac_bonus", 0), field="ac_bonus"),
        heal_amount=max(0, _coerce_int(change.get("heal_amount", 0), field="heal_amount")),
        tags=[t for t in (change.get("tags") or []) if isinstance(t, str)],
    )
    state.item_registry[item_id] = item
    return f"item defined: {item.name} ({item.item_type}, id: {item_id})"


def _give_defined_item(state: GameState, change: Dict[str, Any]) -> str:
    item_id = change.get("id")
    if not isinstance(item_id, str):
        raise ValueError("'id' must be a string")
    item_id = item_id.strip().lower().replace(" ", "_")
    item = state.item_registry.get(item_id)
    if item is None:
        raise ValueError(f"No item defined with id '{item_id}'. Use define_item first.")

    state.player.add_item(item.name)
    extra = ""
    if item.item_type == "weapon" and item.damage_dice:
        state.player.equipped_weapon = Weapon(
            name=item.name,
            damage_dice=item.damage_dice,
            hit_bonus=item.hit_bonus,
            damage_type=item.damage_type,
            description=item.description,
        )
        extra = " (equipped)"
    elif item.item_type == "armor":
        state.player.equipped_armor = Armor(
            name=item.name,
            ac_bonus=item.ac_bonus,
            description=item.description,
        )
        extra = " (equipped)"
    return f"received: {item.name}{extra}"


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

def _define_quest(state: GameState, change: Dict[str, Any]) -> str:
    quest_id = change.get("quest_id")
    if not isinstance(quest_id, str) or not quest_id.strip():
        raise ValueError("'quest_id' must be a non-empty string")
    quest_id = quest_id.strip().lower().replace(" ", "_")
    name = change.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' must be a non-empty string")
    description = change.get("description", "")
    objectives = change.get("objectives", [])
    if not isinstance(objectives, list):
        objectives = []
    objectives = [o for o in objectives if isinstance(o, str) and o.strip()]
    guidelines = change.get("guidelines", "")

    state.quests[quest_id] = Quest(
        name=name.strip(),
        description=description if isinstance(description, str) else "",
        objectives=objectives,
        metadata={"guidelines": guidelines if isinstance(guidelines, str) else ""},
    )
    return f"quest defined: {name.strip()} ({len(objectives)} objectives)"


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
    "unequip_weapon": _unequip_weapon,
    "unequip_armor": _unequip_armor,
    # progression
    "award_xp": _award_xp,
    "add_gold": _add_gold,
    "remove_gold": _remove_gold,
    # status effects
    "apply_status": _apply_status,
    "remove_status": _remove_status,
    # stats
    "set_stat": _set_stat,
    # encounter registry
    "define_encounter": _define_encounter,
    "spawn_encounter": _spawn_encounter,
    "loot_encounter": _loot_encounter,
    # item registry
    "define_item": _define_item,
    "give_defined_item": _give_defined_item,
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
    "define_quest": _define_quest,
    "advance_quest": _advance_quest,
    "complete_quest": _complete_quest,
    "fail_quest": _fail_quest,
    # npcs
    "add_npc": _add_npc,
    "update_npc_disposition": _update_npc_disposition,
}


OP_REFERENCE = """Available state_changes ops (use the exact 'op' string).
See ENCOUNTER & ITEM RULES (below) for when to use define_encounter vs start_combat.



INVENTORY / HEALTH
- {"op":"add_item","value":"<item>"}
- {"op":"remove_item","value":"<item>"}
- {"op":"damage_player","value":<int>}
- {"op":"heal_player","value":<int>}
- {"op":"adjust_resonance","delta":<int>}

EQUIPMENT
- {"op":"equip_weapon","name":"<name>","damage_dice":"1d8","hit_bonus":<int>,"damage_type":"slashing|piercing|bludgeoning"}
- {"op":"equip_armor","name":"<name>","ac_bonus":<int>}
- {"op":"unequip_weapon"}
- {"op":"unequip_armor"}

PROGRESSION
- {"op":"award_xp","amount":<int>}
- {"op":"add_gold","amount":<int>}
- {"op":"remove_gold","amount":<int>}

STATUS EFFECTS
- {"op":"apply_status","name":"<name>","duration_turns":<int>,"roll_modifier":<int>}
- {"op":"remove_status","name":"<name>"}

STATS (score 1–30)
- {"op":"set_stat","stat":"strength|dexterity|intelligence|constitution|wisdom|charisma","value":<int>}

ENCOUNTER REGISTRY (preferred for named enemies — see ENCOUNTER & ITEM RULES)
- {"op":"define_encounter","id":"<slug>","name":"<name>","description":"<approach text>","enemy_name":"<name>","enemy_hp":<int>,"enemy_ac":<int>,"enemy_attack_bonus":<int>,"enemy_damage_dice":"1d6","enemy_level":<int>,"xp_reward":<int>,"gold_reward":<int>,"item_rewards":["<name>"],"narrative_flavor":"<combat prose guidance>","defeat_condition":"defeat|soothe|outwit|endure","quest_id":"<id>|null","tags":["<keyword>"],"is_boss":<bool>}
- {"op":"spawn_encounter","id":"<slug>"}
- {"op":"loot_encounter","id":"<slug>"}

ITEM REGISTRY (for weapons, armor, and quest items with mechanical properties)
- {"op":"define_item","id":"<slug>","name":"<name>","item_type":"weapon|armor|consumable|quest|lore","description":"<text>","value_gold":<int>,"damage_dice":"1d8","hit_bonus":<int>,"damage_type":"slashing|piercing|bludgeoning","ac_bonus":<int>,"heal_amount":<int>,"tags":["<keyword>"]}
- {"op":"give_defined_item","id":"<slug>"}

COMBAT (use start_combat for ad-hoc/unnamed enemies; use spawn_encounter for defined templates)
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
- {"op":"define_quest","quest_id":"<slug>","name":"<name>","description":"<text>","objectives":["<obj1>","<obj2>"],"guidelines":"<narrative hints for future turns>"}
- {"op":"advance_quest","quest_id":"<id>","objective_index":<int>}
- {"op":"complete_quest","quest_id":"<id>"}
- {"op":"fail_quest","quest_id":"<id>"}

NPCS
- {"op":"add_npc","name":"<name>","location":"<location>","disposition":<0-100>}
- {"op":"update_npc_disposition","name":"<NPC name>","delta":<int>}

Only emit changes the narrative explicitly justifies. Return [] if nothing changed."""

"""Builds the LLM payload from current GameState using a layered memory model:

  1. Hot  — the last 6 raw log lines (verbatim recent dialogue)
  2. Warm — `state.session_summary`, a rolling LLM-generated paragraph
  3. Cold — `story_history` PlotPoints, with the last 3 always included plus
            tag-fuzzy-matched older points relevant to the current input

Additionally injects:
  - A scene-type hint (exploration / dialogue / combat) derived from game state
  - A one-shot campaign-start director's note on turn 0 only
"""

import json
import os
import re
from dataclasses import asdict
from typing import Any, Dict, List

from engine.models import GameState
from engine.state_changes import OP_REFERENCE


_RECENT_LOG_LINES = 6
_BACKGROUND_PLOT_POINTS = 3
_MAX_DEEP_RECALL = 5

_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "at", "with", "for", "and",
    "or", "is", "are", "was", "were", "be", "by", "as", "i", "you", "he",
    "she", "it", "they", "we", "my", "your", "this", "that", "these", "those",
}
_WORD_RE = re.compile(r"[a-zA-Z]+")


class PromptOrchestrator:
    def __init__(self, prompt_dir: str = "prompts"):
        self.prompt_dir = prompt_dir
        self.system_config = self._load_json("system_prompt.json")
        self.campaign_start = self._load_json("campaign_start.json")
        self.encounter_rules = self._load_json("encounter_rules.json")

    def _load_json(self, filename: str) -> Dict[str, Any]:
        path = os.path.join(self.prompt_dir, filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    # ── Activity and narrative mode ────────────────────────────────────────

    def _compute_activity(self, state: GameState) -> str:
        """Determine what the player is doing right now.

        Returns one of five activity categories:
          combat    — active combat (state.in_combat True)
          aftermath — one turn immediately after combat ends (state.in_aftermath True)
          approach  — player has explicitly signalled intent to engage a threat
          dialogue  — a known NPC is present at the current location
          exploring — default; player is moving or looking around
        """
        if state.in_combat:
            return "combat"
        if state.in_aftermath:
            return "aftermath"
        if state.player_approaching:
            return "approach"
        local_known_npcs = [
            npc for npc in state.npcs.values()
            if npc.location == state.current_location and npc.is_known
        ]
        if local_known_npcs:
            return "dialogue"
        return "exploring"

    def _get_narrative_mode(self, state: GameState, activity: str) -> str:
        """Derive the narrative mode from the current activity.

        chronicle  — default exploration / dialogue; slow clock (4-5 turns/zone)
        encounter  — player declared approach OR 5-turn zone backstop fires
        combat     — in_combat is True; CombatScreen modal is active
        aftermath  — one-turn mode immediately after combat ends

        Critical change from the old implementation: encounter mode no longer
        fires just because an unspawned encounter exists nearby. Only an explicit
        player approach (set_player_approaching op) or the 5-turn backstop trigger it.
        """
        if activity in ("combat", "aftermath"):
            return activity
        if activity == "approach":
            return "encounter"
        # Hard backstop: 5 turns in a zone with pending encounters → force escalation
        turns_at_loc = state.turn_count - state.location_entered_turn
        pending_enc = [t for t in state.encounter_registry.values() if not t.spawned]
        if pending_enc and turns_at_loc >= 5:
            return "encounter"
        return "chronicle"

    # ── Context assembly ───────────────────────────────────────────────────

    def _get_lean_state(
        self,
        state: GameState,
        narrative_mode: str = "",
        phase_turn: int = 0,
        activity: str = "exploring",
        turns_in_activity: int = 0,
    ) -> Dict[str, Any]:
        active_quests = {
            qid: {
                "name": q.name,
                "description": q.description,
                "objectives": q.objectives,
                "completed_objectives": q.completed_objectives,
                "guidelines": q.metadata.get("guidelines", ""),
            }
            for qid, q in state.quests.items()
            if q.status == "active"
        }
        local_npcs = {
            k: asdict(npc)
            for k, npc in state.npcs.items()
            if npc.location == state.current_location
        }
        loc = state.locations.get(state.current_location)
        location_info = (
            asdict(loc) if loc else {"name": state.current_location, "description": "(unknown)"}
        )

        from engine.archetypes import get_class_loot_profile

        p = state.player
        player_info = {
            "name": p.name,
            "archetype": p.archetype or "unknown",
            "hp": p.hp,
            "max_hp": p.max_hp,
            "level": p.level,
            "ac": p.ac,
            "inventory": p.inventory,
            "stats": p.stats,
            "lineage": p.lineage,
            "bloodline_resonance": p.bloodline_resonance,
            "gold": p.gold,
            "experience": p.experience,
            "xp_to_next_level": p.xp_to_next_level,
            "equipped_weapon": (
                {
                    "name": p.equipped_weapon.name,
                    "damage_dice": p.equipped_weapon.damage_dice,
                    "hit_bonus": p.equipped_weapon.hit_bonus,
                }
                if p.equipped_weapon else None
            ),
            "equipped_armor": (
                {"name": p.equipped_armor.name, "ac_bonus": p.equipped_armor.ac_bonus}
                if p.equipped_armor else None
            ),
            "status_effects": [
                {
                    "name": e.name,
                    "duration_turns": e.duration_turns,
                    "roll_modifier": e.roll_modifier,
                }
                for e in p.status_effects
            ],
            "loot_profile": get_class_loot_profile(p.archetype or "", p.level),
        }

        combat_info = None
        if state.in_combat and state.active_enemies:
            combat_info = {
                "active": True,
                "enemies": [
                    {
                        "name": e.name,
                        "hp": e.hp,
                        "max_hp": e.max_hp,
                        "ac": e.ac,
                        "attack_bonus": e.attack_bonus,
                        "damage_dice": e.damage_dice,
                    }
                    for e in state.active_enemies
                ],
            }

        # Compact encounter registry summary — LLM can reference defined encounters by id
        active_enemy_names = {e.name.lower() for e in state.active_enemies}
        encounter_summary = [
            {
                "id": t.id,
                "name": t.name,
                "is_boss": t.is_boss,
                "spawned": t.spawned,
                "defeat_condition": t.defeat_condition,
                "tags": t.tags,
                "quest_id": t.quest_id,
            }
            for t in state.encounter_registry.values()
            # Omit encounters whose enemy is currently active (already shown in combat block)
            if t.enemy.name.lower() not in active_enemy_names
        ]

        last_round_rolls = [
            {
                "label": r.label,
                "type": r.roll_type,
                "dice": r.dice,
                "total": r.total,
                "modifier": r.modifier,
                "dc": r.dc,
                "success": r.success,
            }
            for r in state.last_rolls
        ] or None

        combat_log_tail = state.combat_log[-4:] if state.combat_log else None

        # DM meta — explicit phase clocks so the LLM never has to guess.
        # narrative_mode, phase_turn, activity, and turns_in_activity are pre-computed
        # by build_payload. Fall back to computing here only when _get_lean_state is
        # called directly (e.g. in tests).
        _activity = activity or self._compute_activity(state)
        _mode = narrative_mode or self._get_narrative_mode(state, _activity)
        _phase_turn = phase_turn  # 0 when called without build_payload context

        turns_at_location = state.turn_count - state.location_entered_turn
        turns_since_last_enc = state.turn_count - state.last_encounter_turn
        active_quest_ids = set(state.quests.keys())
        pending_encounter_ids = [
            t.id for t in state.encounter_registry.values() if not t.spawned
        ]
        pending_quest_encounters = [
            t.id for t in state.encounter_registry.values()
            if not t.spawned and t.quest_id in active_quest_ids
        ]

        dm_meta = {
            "narrative_mode": _mode,                        # "chronicle"|"encounter"|"combat"|"aftermath"
            "activity": _activity,                          # "exploring"|"dialogue"|"approach"|"combat"|"aftermath"
            "turns_in_activity": turns_in_activity,         # turns spent in the current activity
            "npc_exchanges_this_location": state.npc_exchanges_this_location,
            "npc_exchanges_remaining": max(0, 4 - state.npc_exchanges_this_location),
            "phase_turn": _phase_turn,                      # turns spent in current narrative_mode
            "turns_at_current_location": turns_at_location,
            "turns_since_last_encounter": turns_since_last_enc,
            "pending_encounter_ids": pending_encounter_ids,
            "pending_quest_encounter_ids": pending_quest_encounters,
        }

        # Merchants present at current location — shows the LLM what shop stock exists
        local_merchants = {
            k: {
                "name": m.name,
                "greeting": m.greeting,
                "stock": [
                    {
                        "item_name": s.item_name,
                        "price": s.price,
                        "quantity": "unlimited" if s.quantity < 0 else s.quantity,
                        "description": s.description,
                    }
                    for s in m.stock
                    if s.quantity != 0   # exclude sold-out items
                ],
            }
            for k, m in state.merchants.items()
            if m.location == state.current_location
        }

        return {
            "player": player_info,
            "location": location_info,
            "active_quests": active_quests,
            "npcs_present": local_npcs,
            "merchants_present": local_merchants if local_merchants else None,
            "world": asdict(state.world),
            "turn_count": state.turn_count,
            "combat": combat_info,
            "encounter_registry": encounter_summary,
            "last_round_rolls": last_round_rolls,
            "combat_log_tail": combat_log_tail,
            "dm_meta": dm_meta,
        }

    def _get_relevant_history(self, state: GameState, user_input: str) -> Dict[str, Any]:
        history = state.story_history
        background = (
            history[-_BACKGROUND_PLOT_POINTS:]
            if len(history) > _BACKGROUND_PLOT_POINTS
            else list(history)
        )

        terms = _tokenize(user_input) | _tokenize(state.current_location)

        deep_recall: List[Dict[str, Any]] = []
        if terms:
            # Iterate newest-first so the most recent matching events fill the
            # cap before older ones with the same tags crowd them out.
            for pp in reversed(history):
                if pp in background:
                    continue
                pp_terms: set = set()
                for t in pp.tags:
                    pp_terms |= _tokenize(t)
                pp_terms |= _tokenize(pp.event)
                if terms & pp_terms:
                    deep_recall.append(
                        {
                            "event": pp.event,
                            "choice": pp.choice_made,
                            "tags": pp.tags,
                            "timestamp": pp.timestamp,
                        }
                    )
                    if len(deep_recall) >= _MAX_DEEP_RECALL:
                        break

        return {
            "recent_plot_points": [
                {"event": pp.event, "choice": pp.choice_made, "tags": pp.tags}
                for pp in background
            ],
            "deep_recall": deep_recall,
        }

    # ── System message assembly ────────────────────────────────────────────

    def _build_system_message(self, narrative_mode: str) -> str:
        cfg = self.system_config
        # Mode-specific instruction block (chronicle_mode / encounter_mode / combat_mode / aftermath_mode)
        mode_guidance = cfg.get(f"{narrative_mode}_mode", "")

        er = self.encounter_rules
        encounter_rules_text = "\n\n".join(
            v for v in [
                er.get("story_attachment_rule", ""),
                er.get("stat_scaling", ""),
                er.get("enemy_archetypes", ""),
                er.get("defeat_conditions", ""),
                er.get("loot_principles", ""),
                er.get("item_generation", ""),
            ] if v
        )

        parts = [
            cfg.get("system_role", ""),
            "",
            f"WORLD SETTING:\n{cfg.get('world_setting', '')}",
            "",
            f"PROTAGONIST LORE:\n{cfg.get('protagonist_lore', '')}",
            "",
            f"PROSE RULES (apply in every mode):\n{cfg.get('prose_rules', '')}",
            "",
            f"CURRENT NARRATIVE MODE: {narrative_mode.upper()}",
            f"MODE INSTRUCTIONS:\n{mode_guidance}",
            "",
            f"STATE RULES:\n{cfg.get('state_instruction', '')}",
            "",
            f"OUTPUT FORMAT:\n{cfg.get('output_format', '')}",
            "",
            OP_REFERENCE,
            "",
            f"ENCOUNTER & ITEM RULES:\n{encounter_rules_text}",
        ]
        return "\n".join(parts)

    def _build_campaign_start_message(self) -> str:
        """One-shot director's note injected only on turn 0."""
        cs = self.campaign_start
        if not cs:
            return ""
        parts = [
            f"CAMPAIGN START DIRECTIVE — {cs.get('scene_title', 'Opening Scene')}",
            "",
            cs.get("director_note", ""),
            "",
            f"SCENE SETUP:\n{cs.get('scene_setup', '')}",
            "",
            f"REQUIRED STATE CHANGES:\n{cs.get('required_state_changes', '')}",
            "",
            f"OPENING HOOK:\n{cs.get('opening_hook', '')}",
            "",
            f"LOCKET NOTE:\n{cs.get('locket_note', '')}",
            "",
            f"TONE:\n{cs.get('tone_note', '')}",
        ]
        return "\n".join(parts)

    # ── Public API ─────────────────────────────────────────────────────────

    def build_payload(self, state: GameState, user_input: str) -> List[Dict[str, str]]:
        # ── Activity tracking ──────────────────────────────────────────────
        activity = self._compute_activity(state)

        # Per-activity turn clock: reset when the activity category changes.
        if activity != state.current_activity:
            state.activity_entered_turn = state.turn_count
            state.current_activity = activity

        # Dialogue exchange counter: increment every turn spent talking to an NPC.
        if activity == "dialogue":
            state.npc_exchanges_this_location += 1

        turns_in_activity = state.turn_count - state.activity_entered_turn

        # ── Narrative mode ────────────────────────────────────────────────
        narrative_mode = self._get_narrative_mode(state, activity)

        # Phase-change detection: reset the per-phase clock whenever the narrative
        # mode transitions (chronicle→encounter, encounter→combat, etc.).
        # last_narrative_mode="" on the first turn — no reset needed there.
        if state.last_narrative_mode and narrative_mode != state.last_narrative_mode:
            state.phase_entered_turn = state.turn_count
        state.last_narrative_mode = narrative_mode
        phase_turn = state.turn_count - state.phase_entered_turn

        # ── Aftermath clearing ────────────────────────────────────────────
        # Clear the aftermath flag AFTER this turn's lean_state is built so the
        # LLM still receives aftermath mode for this turn, then resets to exploring.
        aftermath_this_turn = state.in_aftermath and activity == "aftermath"

        context_payload = {
            "session_summary": state.session_summary or "(no session summary yet)",
            "current_state": self._get_lean_state(
                state, narrative_mode, phase_turn, activity, turns_in_activity
            ),
            "history": self._get_relevant_history(state, user_input),
            "recent_dialogue": state.log[-_RECENT_LOG_LINES:],
        }

        # Clear aftermath flag after context is assembled (one turn only).
        if aftermath_this_turn:
            state.in_aftermath = False
        context_msg = (
            "CURRENT CONTEXT (JSON — read carefully before composing the next turn):\n"
            f"{json.dumps(context_payload, separators=(',', ':'), default=str)}"
        )

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self._build_system_message(narrative_mode)},
            {"role": "system", "content": context_msg},
        ]

        # Inject the campaign-start director's note only on the very first turn
        if state.turn_count == 0 and self.campaign_start:
            messages.append({
                "role": "system",
                "content": self._build_campaign_start_message(),
            })

        messages.append({"role": "user", "content": f"PLAYER ACTION: {user_input}"})
        return messages


def _tokenize(text: str) -> set:
    if not text:
        return set()
    return {
        w for w in (m.lower() for m in _WORD_RE.findall(text))
        if len(w) > 2 and w not in _STOPWORDS
    }

"""Builds the LLM payload from current GameState using a layered memory model:

  1. Hot — the last 6 raw log lines (verbatim recent dialogue)
  2. Warm — `state.session_summary`, a rolling LLM-generated paragraph
  3. Cold — `story_history` PlotPoints, with the last 3 always included plus
            tag-fuzzy-matched older points relevant to the current input
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

    def _load_json(self, filename: str) -> Dict[str, Any]:
        path = os.path.join(self.prompt_dir, filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    # ---- context assembly -------------------------------------------------

    def _get_lean_state(self, state: GameState) -> Dict[str, Any]:
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
        return {
            "player": {
                "name": state.player.name,
                "hp": state.player.hp,
                "max_hp": state.player.max_hp,
                "inventory": state.player.inventory,
                "stats": state.player.stats,
                "lineage": state.player.lineage,
                "bloodline_resonance": state.player.bloodline_resonance,
            },
            "location": location_info,
            "active_quests": active_quests,
            "npcs_present": local_npcs,
            "world": asdict(state.world),
            "turn_count": state.turn_count,
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
            for pp in history:
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

    # ---- public API -------------------------------------------------------

    def build_payload(self, state: GameState, user_input: str) -> List[Dict[str, str]]:
        cfg = self.system_config

        system_msg = (
            f"{cfg.get('system_role', '')}\n\n"
            f"WORLD SETTING: {cfg.get('world_setting', '')}\n"
            f"PROTAGONIST LORE: {cfg.get('protagonist_lore', '')}\n"
            f"NARRATIVE STYLE: {cfg.get('narrative_style', '')}\n"
            f"STATE RULE: {cfg.get('state_instruction', '')}\n\n"
            f"OUTPUT FORMAT:\n{cfg.get('output_format', '')}\n\n"
            f"{OP_REFERENCE}"
        )

        context_payload = {
            "session_summary": state.session_summary or "(no session summary yet)",
            "current_state": self._get_lean_state(state),
            "history": self._get_relevant_history(state, user_input),
            "recent_dialogue": state.log[-_RECENT_LOG_LINES:],
        }
        context_msg = (
            "CURRENT CONTEXT (JSON — read carefully before composing the next turn):\n"
            f"{json.dumps(context_payload, indent=2, default=str)}"
        )

        user_msg = f"PLAYER ACTION: {user_input}"

        return [
            {"role": "system", "content": system_msg},
            {"role": "system", "content": context_msg},
            {"role": "user", "content": user_msg},
        ]


def _tokenize(text: str) -> set:
    if not text:
        return set()
    return {
        w for w in (m.lower() for m in _WORD_RE.findall(text))
        if len(w) > 2 and w not in _STOPWORDS
    }

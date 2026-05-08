import json
import os
from dataclasses import asdict
from typing import Dict, Any, List
from engine.models import GameState, PlotPoint

class PromptOrchestrator:
    """Bridges GameState with LLM Prompts using a State-Driven Context Strategy."""
    
    def __init__(self, prompt_dir: str = "prompts"):
        self.prompt_dir = prompt_dir
        self.system_config = self._load_json("system_prompt.json")

    def _load_json(self, filename: str) -> Dict[str, Any]:
        path = os.path.join(self.prompt_dir, filename)
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
        return {}

    def _get_lean_state(self, state: GameState) -> Dict[str, Any]:
        """Filters the GameState to only include context relevant to the current turn."""
        # Active Quests
        active_quests = {k: v for k, v in state.quests.items() if v.status == "active"}
        
        # Local NPCs
        local_npcs = {k: v for k, v in state.npcs.items() if v.location == state.current_location}
        
        # Current Location Details
        location_info = state.locations.get(state.current_location, "Unknown Location")

        return {
            "player": {
                "hp": state.player.hp,
                "max_hp": state.player.max_hp,
                "inventory": state.player.inventory,
                "stats": state.player.stats
            },
            "location": location_info,
            "active_quests": active_quests,
            "npcs_present": local_npcs,
            "world": state.world,
            "turn_count": state.turn_count
        }

    def _get_relevant_history(self, state: GameState, user_input: str) -> List[Dict[str, Any]]:
        """
        Implements Dynamic Deep Recall.
        Pulls detailed context for past events related to the current situation.
        """
        relevant = []
        # Condensed Background: Always include the last 3 plot points as a summary
        background_limit = 3
        if len(state.story_history) > background_limit:
            background = state.story_history[-background_limit:]
        else:
            background = state.story_history

        # Dynamic Recall: Search for keywords in user input or current location
        search_terms = set(user_input.lower().split())
        search_terms.add(state.current_location.lower())
        
        for pp in state.story_history:
            # If the plot point tags match current search terms, include it as 'extensive' context
            pp_tags = [t.lower() for t in pp.tags]
            if any(term in pp_tags for term in search_terms):
                if pp not in background:
                    relevant.append({"type": "extensive_recall", "event": pp.event, "choice": pp.choice_made})

        # Add the condensed background
        for pp in background:
            relevant.append({"type": "recent_history", "event": pp.event, "choice": pp.choice_made})
            
        return relevant

    def build_payload(self, state: GameState, user_input: str) -> List[Dict[str, str]]:
        """Constructs the full message list for the LLM API."""
        
        # 1. System Message (Instructions + World Rules)
        system_msg = (
            f"{self.system_config.get('system_role')}\n"
            f"WORLD SETTING: {self.system_config.get('world_setting')}\n"
            f"STYLE: {self.system_config.get('narrative_style')}\n"
            f"TECHNICAL RULE: {self.system_config.get('state_instruction')}"
        )

        # 2. Context Message (Lean State + Relevant History)
        lean_state = self._get_lean_state(state)
        relevant_history = self._get_relevant_history(state, user_input)
        
        # Ensure all nested dataclasses in lean_state are converted to dicts for JSON serialization
        def serialize_nested(obj):
            if hasattr(obj, "__dataclass_fields__"):
                return asdict(obj)
            if isinstance(obj, dict):
                return {k: serialize_nested(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [serialize_nested(i) for i in obj]
            return obj

        context_data = {
            "current_state": serialize_nested(lean_state),
            "relevant_history": serialize_nested(relevant_history),
            "recent_logs": state.log[-5:]  # Last 5 turns for flow
        }
        
        context_msg = f"CURRENT CONTEXT (JSON):\n{json.dumps(context_data, indent=2)}"

        # 3. User Message
        user_msg = f"PLAYER ACTION: {user_input}"

        return [
            {"role": "system", "content": system_msg},
            {"role": "system", "content": context_msg},
            {"role": "user", "content": user_msg}
        ]

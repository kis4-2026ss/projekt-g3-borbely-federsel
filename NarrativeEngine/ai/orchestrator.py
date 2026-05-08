import json
import os
from typing import Dict, Any, List
from engine.models import GameState

class PromptOrchestrator:
    """Bridges GameState with LLM Prompts."""
    
    def __init__(self, prompt_dir: str = "prompts"):
        self.prompt_dir = prompt_dir
        self.system_config = self._load_json("system_prompt.json")

    def _load_json(self, filename: str) -> Dict[str, Any]:
        path = os.path.join(self.prompt_dir, filename)
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
        return {}

    def build_payload(self, state: GameState, user_input: str) -> List[Dict[str, str]]:
        """Constructs the full message list for the LLM API."""
        
        # 1. System Message (Instructions + World Rules)
        system_msg = (
            f"{self.system_config.get('system_role')}\n"
            f"WORLD SETTING: {self.system_config.get('world_setting')}\n"
            f"STYLE: {self.system_config.get('narrative_style')}\n"
            f"TECHNICAL RULE: {self.system_config.get('state_instruction')}"
        )

        # 2. Context Message (Current Serialized State)
        # We only send relevant parts to save tokens if needed, 
        # but for now, we send the whole snapshot.
        state_snapshot = state.to_json()
        context_msg = f"CURRENT GAME STATE (JSON):\n{state_snapshot}"

        # 3. User Message (The Action)
        user_msg = f"PLAYER ACTION: {user_input}"

        return [
            {"role": "system", "content": system_msg},
            {"role": "system", "content": context_msg},
            {"role": "user", "content": user_msg}
        ]

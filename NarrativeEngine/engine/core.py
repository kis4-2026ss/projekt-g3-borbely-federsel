import json
import os
from typing import Optional, Dict, Any
from .models import GameState, Player, Location, Quest, NPC, WorldState

class GameEngine:
    """
    The central coordinator for game logic.
    Holds the ACTIVE GameState and manages data-driven content loading.
    """
    
    def __init__(self, initial_state: Optional[GameState] = None):
        self.state = initial_state or self._create_default_state()

    def _create_default_state(self) -> GameState:
        player = Player(name="Aurelian Exile", hp=30, max_hp=30)
        
        # Initial Location
        start_loc = Location(
            name="The Overgrown Outpost",
            description="A cluster of simple stone huts huddled in the shadow of a massive, cracked crystal spire.",
            connections=["The Shattered Plaza"]
        )
        
        return GameState(
            player=player,
            current_location=start_loc.name,
            locations={start_loc.name: start_loc},
            world=WorldState(weather="Ethereal Mist")
        )

    def initialize_campaign(self):
        """Kicks off the story and loads the first quest."""
        self.state.add_log("SYSTEM: Initializing Elowen Chronicles...")
        self.state.add_log("A millennium has passed since the Golden Age turned to ash. The name 'Aurelia' is now a whisper feared by the superstitious.")
        self.state.add_log("You are but a scavenger in the dirt, clutching a rusted locket—the only proof of a lineage long since forgotten by the world.")
        self.state.add_log("As you gaze at the cracked crystal spire above, your blood begins to tingle—a faint, ancient rhythm drumming beneath your skin.")
        
        # Load the tutorial quest
        self.load_quest("tutorial_boss")
        
        # Record the initial plot point
        self.state.record_choice(
            event="Awakening", 
            choice="Recognized the call of the Aurelian Spire",
            tags=["bloodline", "awakening", "elowen"]
        )

    def load_quest(self, quest_id: str):
        """Loads a quest from a JSON file and adds it to the game state."""
        path = f"data/quests/{quest_id}.json"
        if os.path.exists(path):
            with open(path, 'r') as f:
                data = json.load(f)
                quest = Quest(
                    name=data['name'],
                    description=data['description'],
                    objectives=data['objectives'],
                    metadata={"guidelines": data['narrative_guidelines']}
                )
                self.state.quests[quest_id] = quest
                self.state.add_log(f"NEW QUEST: {quest.name}")

    def save_game(self):
        self.state.save_to_file()

    def load_game(self):
        self.state = GameState.load_from_file()

    def use_potion(self):
        if "Health Potion" in self.state.player.inventory:
            self.state.player.remove_item("Health Potion")
            self.state.player.heal(15)
            self.state.add_log("You consume a vial of glowing blue liquid. The mana soothes your aching bones.")
        else:
            self.state.add_log("You reach for a potion, but find only empty glass.")

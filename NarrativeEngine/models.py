import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional

@dataclass
class Item:
    name: str
    description: str
    item_type: str  # e.g., 'weapon', 'consumable', 'key_item'
    value: int      # Damage for weapons, heal amount for consumables

@dataclass
class Inventory:
    items: List[Item] = field(default_factory=list)
    capacity: int = 20

    def add_item(self, item: Item) -> bool:
        """Returns True if item was added, False if inventory is full."""
        if len(self.items) < self.capacity:
            self.items.append(item)
            return True
        return False

    def remove_item(self, item: Item):
        if item in self.items:
            self.items.remove(item)

@dataclass
class Entity:
    name: str
    max_hp: int
    hp: int

    def take_damage(self, amount: int):
        self.hp = max(0, self.hp - amount)

    def heal(self, amount: int):
        self.hp = min(self.max_hp, self.hp + amount)

    def is_alive(self) -> bool:
        return self.hp > 0

@dataclass
class Player(Entity):
    max_mana: int = 50
    mana: int = 50
    strength: int = 10
    agility: int = 10
    inventory: Inventory = field(default_factory=Inventory)

    def use_mana(self, amount: int) -> bool:
        if self.mana >= amount:
            self.mana -= amount
            return True
        return False

    def get_state_dict(self) -> dict:
        """Returns the player state as a dictionary for LLM context."""
        return asdict(self)

    def get_state_json(self) -> str:
        """Serializes the player state to a JSON string."""
        return json.dumps(self.get_state_dict())

@dataclass
class Enemy(Entity):
    base_damage: int = 5
    description: str = ""

    def get_state_dict(self) -> dict:
        """Returns the enemy state as a dictionary for LLM context."""
        return asdict(self)
    
    def get_state_json(self) -> str:
        """Serializes the enemy state to a JSON string."""
        return json.dumps(self.get_state_dict())

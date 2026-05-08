import pytest
from models import Player, Enemy, Item, Inventory
from combat_system import CombatSystem
from unittest.mock import patch

def test_entity_health_bounds():
    """Chaos test: Ensure health never drops below zero or exceeds max."""
    enemy = Enemy(name="Goblin", max_hp=20, hp=20)
    
    # Over-damage
    enemy.take_damage(500)
    assert enemy.hp == 0
    assert not enemy.is_alive()

    # Over-heal
    enemy.heal(1000)
    assert enemy.hp == 20

def test_inventory_capacity():
    """Chaos test: Ensure inventory respects limits."""
    inv = Inventory(capacity=2)
    potion = Item(name="Potion", description="Heals", item_type="consumable", value=10)
    
    assert inv.add_item(potion) is True
    assert inv.add_item(potion) is True
    assert inv.add_item(potion) is False  # Capacity reached
    assert len(inv.items) == 2

@patch('combat_system.CombatSystem.roll_dice')
def test_player_attack_critical_hit(mock_roll):
    """Test deterministic math: Critical hit doubles strength damage and bypasses armor."""
    mock_roll.return_value = 20  # Force a natural 20
    
    combat = CombatSystem()
    player = Player(name="Hero", max_hp=50, hp=50, strength=10)
    enemy = Enemy(name="Orc", max_hp=30, hp=30)
    
    result = combat.resolve_player_attack(player, enemy)
    
    assert result["hit"] is True
    assert result["critical"] is True
    assert result["damage_dealt"] == 20  # Strength (10) * 2
    assert enemy.hp == 10
    assert result["target_hp_remaining"] == 10

@patch('combat_system.CombatSystem.roll_dice')
def test_player_attack_miss(mock_roll):
    """Test deterministic math: Low roll misses."""
    mock_roll.return_value = 1  # Force a natural 1
    
    combat = CombatSystem()
    player = Player(name="Hero", max_hp=50, hp=50, strength=10) # modifier is +5
    enemy = Enemy(name="Orc", max_hp=30, hp=30) # default armor is 10
    
    # Total attack will be 1 + 5 = 6 < 10
    result = combat.resolve_player_attack(player, enemy)
    
    assert result["hit"] is False
    assert result["damage_dealt"] == 0
    assert enemy.hp == 30

@patch('combat_system.CombatSystem.roll_dice')
def test_enemy_attack_kills_player(mock_roll):
    """Test deterministic state change: Player dies."""
    mock_roll.return_value = 18
    
    combat = CombatSystem()
    player = Player(name="Hero", max_hp=50, hp=2, agility=10) # armor is 15
    enemy = Enemy(name="Dragon", max_hp=100, hp=100, base_damage=50)
    
    result = combat.resolve_enemy_attack(enemy, player)
    
    assert result["hit"] is True
    assert result["damage_dealt"] == 50
    assert player.hp == 0
    assert not player.is_alive()
    assert result["target_killed"] is True

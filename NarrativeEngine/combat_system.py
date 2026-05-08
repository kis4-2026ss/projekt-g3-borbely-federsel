import random
from models import Player, Enemy

class CombatSystem:
    """
    Handles the deterministic logic for combat, including dice rolls,
    stat calculations, and damage application.
    """
    
    @staticmethod
    def roll_dice(sides: int = 20) -> int:
        """Rolls a virtual die with the given number of sides."""
        return random.randint(1, sides)

    def resolve_player_attack(self, player: Player, enemy: Enemy) -> dict:
        """
        Resolves a player attacking an enemy.
        Returns a dictionary summarizing the combat event for the LLM.
        """
        # Deterministic math: Attack roll (1d20) + player strength modifier
        attack_roll = self.roll_dice(20)
        str_modifier = player.strength // 2
        total_attack = attack_roll + str_modifier

        # Temporary static enemy armor for calculation
        enemy_armor = 10

        event_summary = {
            "actor": player.name,
            "target": enemy.name,
            "action": "attack",
            "roll": attack_roll,
            "modifier": str_modifier,
            "total_attack": total_attack,
            "hit": False,
            "critical": False,
            "damage_dealt": 0,
            "target_hp_remaining": enemy.hp,
            "target_killed": False
        }

        # Determine hit and damage
        if attack_roll == 20:
            # Critical Hit: auto hit, double damage
            event_summary["critical"] = True
            event_summary["hit"] = True
            damage = (player.strength * 2) 
        elif total_attack >= enemy_armor:
            # Regular Hit
            event_summary["hit"] = True
            damage = player.strength
        else:
            # Miss
            damage = 0

        # Apply damage if it was a hit
        if event_summary["hit"]:
            enemy.take_damage(damage)
            event_summary["damage_dealt"] = damage
            event_summary["target_hp_remaining"] = enemy.hp
            if not enemy.is_alive():
                event_summary["target_killed"] = True

        return event_summary

    def resolve_enemy_attack(self, enemy: Enemy, player: Player) -> dict:
        """
        Resolves an enemy attacking the player.
        Returns a dictionary summarizing the combat event for the LLM.
        """
        attack_roll = self.roll_dice(20)
        
        # Player armor scaling based on agility
        player_armor = 10 + (player.agility // 2)

        event_summary = {
            "actor": enemy.name,
            "target": player.name,
            "action": "attack",
            "roll": attack_roll,
            "total_attack": attack_roll, # Enemies might get their own modifiers later
            "hit": False,
            "critical": False,
            "damage_dealt": 0,
            "target_hp_remaining": player.hp,
            "target_killed": False
        }

        # Determine hit and damage
        if attack_roll == 20:
            event_summary["critical"] = True
            event_summary["hit"] = True
            damage = enemy.base_damage * 2
        elif attack_roll >= player_armor:
            event_summary["hit"] = True
            damage = enemy.base_damage
        else:
            damage = 0

        # Apply damage if it was a hit
        if event_summary["hit"]:
            player.take_damage(damage)
            event_summary["damage_dealt"] = damage
            event_summary["target_hp_remaining"] = player.hp
            if not player.is_alive():
                event_summary["target_killed"] = True

        return event_summary

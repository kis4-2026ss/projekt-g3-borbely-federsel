"""Slice B: Mathematical resolution of combat rounds.

The LLM proposes combat ops (start_combat, roll_attack, end_combat).
CombatManager performs all dice math and mutates GameState accordingly.
"""

from typing import List, Tuple

from .models import DiceRoll, Enemy, GameState
from . import dice


class CombatManager:
    def __init__(self, state: GameState):
        self.state = state

    def roll_initiative(self) -> Tuple[DiceRoll, DiceRoll]:
        """Roll initiative for player and first enemy. Returns (player_roll, enemy_roll)."""
        dex_mod = self.state.player.stat_mod("dexterity")
        player_init = dice.roll(
            "1d20",
            modifier=dex_mod,
            label="Player Initiative",
            roll_type="initiative",
        )
        enemy_init = dice.roll(
            "1d20",
            label="Enemy Initiative",
            roll_type="initiative",
        )
        return player_init, enemy_init

    def resolve_player_attack(self, enemy: Enemy) -> List[DiceRoll]:
        """Player attacks enemy. Returns all DiceRolls made (attack + optional damage)."""
        player = self.state.player
        rolls: List[DiceRoll] = []

        str_mod = player.stat_mod("strength")
        status_mod = sum(e.roll_modifier for e in player.status_effects)
        weapon = player.equipped_weapon
        hit_bonus = str_mod + status_mod + (weapon.hit_bonus if weapon else 0)

        attack_roll = dice.roll(
            "1d20",
            modifier=hit_bonus,
            dc=enemy.ac,
            label=f"Attack vs {enemy.name}",
            roll_type="attack",
        )
        rolls.append(attack_roll)

        if attack_roll.success:
            damage_notation = weapon.damage_dice if weapon else "1d4"
            damage_roll = dice.roll(
                damage_notation,
                modifier=str_mod,
                label=f"Damage to {enemy.name}",
                roll_type="damage",
            )
            rolls.append(damage_roll)
            enemy.hp = max(0, enemy.hp - damage_roll.total)
            self.state.combat_log.append(
                f"Player hits {enemy.name} for {damage_roll.total} dmg"
                f" (roll {attack_roll.total} vs AC {enemy.ac})"
            )
        else:
            self.state.combat_log.append(
                f"Player misses {enemy.name} (roll {attack_roll.total} vs AC {enemy.ac})"
            )

        return rolls

    def resolve_enemy_attack(self, enemy: Enemy) -> List[DiceRoll]:
        """Enemy counter-attacks player. Returns all DiceRolls made."""
        player = self.state.player
        rolls: List[DiceRoll] = []

        attack_roll = dice.roll(
            "1d20",
            modifier=enemy.attack_bonus,
            dc=player.ac,
            label=f"{enemy.name} attacks",
            roll_type="attack",
        )
        rolls.append(attack_roll)

        if attack_roll.success:
            damage_roll = dice.roll(
                enemy.damage_dice,
                label=f"{enemy.name} damage",
                roll_type="damage",
            )
            rolls.append(damage_roll)
            player.take_damage(damage_roll.total)
            self.state.combat_log.append(
                f"{enemy.name} hits player for {damage_roll.total} dmg"
                f" (roll {attack_roll.total} vs AC {player.ac})"
            )
        else:
            self.state.combat_log.append(
                f"{enemy.name} misses player (roll {attack_roll.total} vs AC {player.ac})"
            )

        return rolls

    def resolve_full_round(self, enemy: Enemy) -> List[DiceRoll]:
        """One full combat round: player attacks, then enemy counter-attacks if still alive.

        Removes the enemy from active_enemies and clears in_combat if HP reaches 0.
        """
        all_rolls: List[DiceRoll] = []

        all_rolls.extend(self.resolve_player_attack(enemy))

        if enemy.hp > 0:
            all_rolls.extend(self.resolve_enemy_attack(enemy))

        if enemy.hp <= 0:
            self.state.active_enemies = [
                e for e in self.state.active_enemies if e is not enemy
            ]
            if not self.state.active_enemies:
                self.state.in_combat = False
            self.state.add_log(f"COMBAT: {enemy.name} has been defeated.")

        return all_rolls

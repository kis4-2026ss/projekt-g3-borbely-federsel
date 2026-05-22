"""Slice B: Mathematical resolution of combat rounds."""

from typing import List, Tuple

from .models import DiceRoll, Enemy, GameState
from . import dice


def _award_xp_for_kill(state: GameState, enemy: Enemy) -> int:
    """Award XP for defeating an enemy (25 + level * 25).
    Handles level-up cascade. Returns the raw XP amount awarded."""
    xp = 25 + (enemy.level * 25)
    player = state.player
    player.experience += xp
    while player.experience >= player.xp_to_next_level:
        player.experience -= player.xp_to_next_level
        player.level += 1
        player.max_hp += 5
        player.hp = min(player.hp + 5, player.max_hp)
        state.combat_log.append(
            f"Level up! Now level {player.level}. Max HP +5."
        )
    return xp


class CombatManager:
    def __init__(self, state: GameState):
        self.state = state

    def roll_initiative(self) -> Tuple[DiceRoll, DiceRoll]:
        dex_mod = self.state.player.stat_mod("dexterity")
        player_init = dice.roll(
            "1d20", modifier=dex_mod,
            label="Player Initiative", roll_type="initiative",
        )
        enemy_init = dice.roll(
            "1d20", label="Enemy Initiative", roll_type="initiative",
        )
        return player_init, enemy_init

    def resolve_player_attack(self, enemy: Enemy) -> List[DiceRoll]:
        """Player attacks enemy. Returns all DiceRolls made."""
        player = self.state.player
        rolls: List[DiceRoll] = []

        str_mod = player.stat_mod("strength")
        status_mod = sum(e.roll_modifier for e in player.status_effects)
        weapon = player.equipped_weapon
        hit_bonus = str_mod + status_mod + (weapon.hit_bonus if weapon else 0)

        attack_roll = dice.roll(
            "1d20", modifier=hit_bonus, dc=enemy.ac,
            label=f"Attack vs {enemy.name}", roll_type="attack",
        )
        rolls.append(attack_roll)

        if attack_roll.success:
            damage_notation = weapon.damage_dice if weapon else "1d4"
            damage_roll = dice.roll(
                damage_notation, modifier=str_mod,
                label=f"Damage to {enemy.name}", roll_type="damage",
            )
            rolls.append(damage_roll)
            # A hit always deals at least 1 damage regardless of low stats
            actual_dmg = max(1, damage_roll.total)
            enemy.hp = max(0, enemy.hp - actual_dmg)
            self.state.combat_log.append(
                f"Player hits {enemy.name} for {actual_dmg} dmg"
                f" (roll {attack_roll.total} vs AC {enemy.ac})"
            )
        else:
            self.state.combat_log.append(
                f"Player misses {enemy.name}"
                f" (roll {attack_roll.total} vs AC {enemy.ac})"
            )

        return rolls

    def resolve_enemy_attack(self, enemy: Enemy) -> List[DiceRoll]:
        """Enemy counter-attacks player. Damage is reduced by armor DR."""
        player = self.state.player
        rolls: List[DiceRoll] = []

        attack_roll = dice.roll(
            "1d20", modifier=enemy.attack_bonus, dc=player.ac,
            label=f"{enemy.name} attacks", roll_type="attack",
        )
        rolls.append(attack_roll)

        if attack_roll.success:
            damage_roll = dice.roll(
                enemy.damage_dice,
                label=f"{enemy.name} damage", roll_type="damage",
            )
            rolls.append(damage_roll)

            raw_damage = damage_roll.total
            dr = player.equipped_armor.damage_reduction if player.equipped_armor else 0
            actual_damage = max(0, raw_damage - dr)
            player.take_damage(actual_damage)

            dr_note = f" (-{dr} armor)" if dr > 0 else ""
            self.state.combat_log.append(
                f"{enemy.name} hits player for {actual_damage} dmg{dr_note}"
                f" (roll {attack_roll.total} vs AC {player.ac})"
            )
        else:
            self.state.combat_log.append(
                f"{enemy.name} misses player"
                f" (roll {attack_roll.total} vs AC {player.ac})"
            )

        return rolls

    def resolve_full_round(self, enemy: Enemy) -> List[DiceRoll]:
        """One full combat round: player attacks, then enemy counter-attacks.

        When the enemy is killed:
          - Removes it from active_enemies.
          - Clears in_combat if no enemies remain.
          - Auto-awards XP (25 + level * 25).
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

            xp_gained = _award_xp_for_kill(self.state, enemy)
            self.state.add_log(
                f"COMBAT: {enemy.name} has been defeated! Gained {xp_gained} XP."
            )
            self.state.combat_log.append(
                f"{enemy.name} defeated! +{xp_gained} XP"
            )

        return all_rolls

"""Slice B: Mathematical resolution of combat rounds."""

from typing import Iterable, List, Tuple

from .models import DiceRoll, Enemy, GameState, StatusEffect
from . import dice


def net_advantage(effects: Iterable[StatusEffect]) -> int:
    """Collapse all status-effect advantage sources into a single state.

    D&D 5e rule: advantage and disadvantage never stack -- any number of each
    just counts as one. If a creature has both, they cancel to a normal roll.
    Returns +1 (advantage), 0 (normal) or -1 (disadvantage).
    """
    has_adv = any(e.advantage > 0 for e in effects)
    has_dis = any(e.advantage < 0 for e in effects)
    if has_adv and not has_dis:
        return 1
    if has_dis and not has_adv:
        return -1
    return 0


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
        """Player attacks enemy. Returns all DiceRolls made.

        Applies D&D 5e rules: advantage/disadvantage from status effects,
        natural-20 critical hits (damage dice rolled twice) and natural-1
        automatic misses.
        """
        player = self.state.player
        rolls: List[DiceRoll] = []

        str_mod = player.stat_mod("strength")
        status_mod = sum(e.roll_modifier for e in player.status_effects)
        weapon = player.equipped_weapon
        hit_bonus = str_mod + status_mod + (weapon.hit_bonus if weapon else 0)
        advantage = net_advantage(player.status_effects)

        attack_roll = dice.roll(
            "1d20", modifier=hit_bonus, dc=enemy.ac,
            label=f"Attack vs {enemy.name}", roll_type="attack",
            advantage=advantage,
        )
        rolls.append(attack_roll)

        if attack_roll.success:
            damage_notation = weapon.damage_dice if weapon else "1d4"
            damage_roll = dice.roll(
                damage_notation, modifier=str_mod,
                label=f"Damage to {enemy.name}", roll_type="damage",
                crit=attack_roll.is_critical,
            )
            rolls.append(damage_roll)
            # A hit always deals at least 1 damage regardless of low stats
            actual_dmg = max(1, damage_roll.total)
            enemy.hp = max(0, enemy.hp - actual_dmg)
            crit_note = " CRITICAL HIT!" if attack_roll.is_critical else ""
            self.state.combat_log.append(
                f"Player hits {enemy.name} for {actual_dmg} dmg{crit_note}"
                f" (roll {attack_roll.total} vs AC {enemy.ac})"
            )
        else:
            fumble_note = " (natural 1 - fumble)" if attack_roll.is_fumble else ""
            self.state.combat_log.append(
                f"Player misses {enemy.name}{fumble_note}"
                f" (roll {attack_roll.total} vs AC {enemy.ac})"
            )

        return rolls

    def resolve_enemy_attack(self, enemy: Enemy) -> List[DiceRoll]:
        """Enemy counter-attacks player.

        D&D 5e: armor only affects the player's AC (chance to be hit) -- it
        does not reduce damage. A natural 20 is a critical hit (damage dice
        doubled), a natural 1 is an automatic miss.
        """
        player = self.state.player
        rolls: List[DiceRoll] = []

        attack_roll = dice.roll(
            "1d20", modifier=enemy.attack_bonus, dc=player.ac,
            label=f"{enemy.name} attacks", roll_type="attack",
        )
        rolls.append(attack_roll)

        if attack_roll.success:
            damage_roll = dice.roll(
                enemy.damage_dice, modifier=enemy.damage_bonus,
                label=f"{enemy.name} damage", roll_type="damage",
                crit=attack_roll.is_critical,
            )
            rolls.append(damage_roll)

            actual_damage = max(0, damage_roll.total)
            player.take_damage(actual_damage)

            crit_note = " CRITICAL HIT!" if attack_roll.is_critical else ""
            self.state.combat_log.append(
                f"{enemy.name} hits player for {actual_damage} dmg{crit_note}"
                f" (roll {attack_roll.total} vs AC {player.ac})"
            )
        else:
            fumble_note = " (natural 1 - fumble)" if attack_roll.is_fumble else ""
            self.state.combat_log.append(
                f"{enemy.name} misses player{fumble_note}"
                f" (roll {attack_roll.total} vs AC {player.ac})"
            )

        return rolls

    def resolve_full_round(self, enemy: Enemy) -> List[DiceRoll]:
        """One full combat round, ordered by initiative.

        D&D 5e: both combatants roll initiative (1d20 + Dex mod) and the
        higher result acts first; the player wins ties. Each combatant takes
        one attack, and a combatant that drops to 0 HP does not get to act.

        When the enemy is killed:
          - Removes it from active_enemies.
          - Clears in_combat if no enemies remain.
          - Auto-awards XP (25 + level * 25).
        """
        all_rolls: List[DiceRoll] = []

        player_init, enemy_init = self.roll_initiative()
        all_rolls.extend([player_init, enemy_init])

        player_first = player_init.total >= enemy_init.total  # ties -> player
        winner = "Player" if player_first else enemy.name
        self.state.combat_log.append(
            f"Initiative: Player {player_init.total} vs {enemy.name}"
            f" {enemy_init.total} - {winner} acts first."
        )

        order = (
            (self.resolve_player_attack, self.resolve_enemy_attack)
            if player_first
            else (self.resolve_enemy_attack, self.resolve_player_attack)
        )
        for resolve in order:
            if enemy.hp <= 0 or self.state.player.hp <= 0:
                break
            all_rolls.extend(resolve(enemy))

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

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
        prof = player.proficiency_bonus
        status_mod = sum(e.roll_modifier for e in player.status_effects)
        weapon = player.equipped_weapon
        hit_bonus = str_mod + prof + status_mod + (weapon.hit_bonus if weapon else 0)
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

        # Consume the advantage modifier set by Evade — reset immediately after use
        adv = self.state.enemy_attack_adv
        self.state.enemy_attack_adv = 0

        attack_roll = dice.roll(
            "1d20", modifier=enemy.attack_bonus, dc=player.ac,
            label=f"{enemy.name} attacks", roll_type="attack",
            advantage=adv,
        )
        rolls.append(attack_roll)

        if attack_roll.success:
            damage_roll = dice.roll(
                enemy.damage_dice, modifier=enemy.damage_bonus,
                label=f"{enemy.name} damage", roll_type="damage",
                crit=attack_roll.is_critical,
            )
            rolls.append(damage_roll)

            actual_damage = max(1, damage_roll.total)
            # Mage's Mana Shield: absorb incoming damage before it lands
            if self.state.mana_shield_value > 0:
                absorbed = min(self.state.mana_shield_value, actual_damage)
                actual_damage = max(0, actual_damage - absorbed)
                self.state.mana_shield_value = 0
                self.state.combat_log.append(
                    f"Mana Shield absorbs {absorbed} damage!"
                )
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

        NOTE: Used by the roll_attack LLM op (state_changes.py) only.
        The combat UI calls resolve_player_attack / resolve_enemy_attack directly
        so it can check mid-round death and show initiative separately.

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

    # ── Player combat actions ─────────────────────────────────────────────

    def resolve_power_strike(self, enemy: Enemy) -> List[DiceRoll]:
        """Power Strike: attack with disadvantage, but damage dice are doubled on a hit.

        High risk / high reward — trades accuracy for burst damage. Uses the
        same proficiency + STR + weapon hit_bonus formula as a standard strike,
        but with advantage=-1 (disadvantage). On hit, crit=True doubles all
        damage dice (identical to the critical-hit mechanic).
        """
        player = self.state.player
        rolls: List[DiceRoll] = []

        str_mod = player.stat_mod("strength")
        prof = player.proficiency_bonus
        status_mod = sum(e.roll_modifier for e in player.status_effects)
        weapon = player.equipped_weapon
        hit_bonus = str_mod + prof + status_mod + (weapon.hit_bonus if weapon else 0)

        attack_roll = dice.roll(
            "1d20", modifier=hit_bonus, dc=enemy.ac,
            label=f"Power Strike vs {enemy.name}", roll_type="attack",
            advantage=-1,  # always disadvantage
        )
        rolls.append(attack_roll)

        if attack_roll.success:
            damage_notation = weapon.damage_dice if weapon else "1d4"
            damage_roll = dice.roll(
                damage_notation, modifier=str_mod,
                label=f"Power Damage to {enemy.name}", roll_type="damage",
                crit=True,  # always doubles dice regardless of whether it's a nat-20
            )
            rolls.append(damage_roll)
            actual_dmg = max(1, damage_roll.total)
            enemy.hp = max(0, enemy.hp - actual_dmg)
            self.state.combat_log.append(
                f"Player power-strikes {enemy.name} for {actual_dmg} dmg"
                f" (doubled dice, roll {attack_roll.total} vs AC {enemy.ac})"
            )
        else:
            fumble_note = " (fumble!)" if attack_roll.is_fumble else ""
            self.state.combat_log.append(
                f"Player's power strike misses {enemy.name}{fumble_note}"
                f" (roll {attack_roll.total} vs AC {enemy.ac})"
            )

        return rolls

    def resolve_evade(self) -> Tuple[bool, DiceRoll]:
        """Evade: DEX check vs DC 12.

        SUCCESS — player still attacks this round AND enemy counter-attacks at
        disadvantage. The UI must call resolve_player_attack after this on success.
        Sets state.enemy_attack_adv = -1.

        FAILURE — player is caught off-balance: no player attack, enemy gets
        advantage on their counter. Sets state.enemy_attack_adv = +1.

        Returns (success, dex_roll).
        """
        player = self.state.player
        dex_mod = player.stat_mod("dexterity")
        evade_roll = dice.roll(
            "1d20", modifier=dex_mod, dc=12,
            label="Evade (DEX check)", roll_type="check",
        )
        if evade_roll.success:
            self.state.enemy_attack_adv = -1   # enemy attacks at disadvantage
            self.state.combat_log.append(
                f"Player evades! Strikes back — enemy counter at disadvantage."
                f" (roll {evade_roll.total} vs DC 12)"
            )
        else:
            self.state.enemy_attack_adv = 1    # enemy gets advantage
            self.state.combat_log.append(
                f"Evade failed — caught off-balance, enemy strikes with advantage."
                f" (roll {evade_roll.total} vs DC 12)"
            )
        return evade_roll.success, evade_roll

    def resolve_defend(self) -> int:
        """Defend: no player attack this round, AC raised by 2 + CON modifier.

        Returns the ac_bonus applied so the UI can display it.
        Sets player.temp_ac_bonus; the UI must reset this to 0 at the start
        of the next action so the bonus doesn't carry into future rounds.

        On a miss the UI should trigger resolve_player_attack as a riposte —
        resolve_defend itself does not handle that; it only sets the AC.
        """
        player = self.state.player
        con_mod = player.stat_mod("constitution")
        ac_bonus = 2 + max(0, con_mod)
        player.temp_ac_bonus = ac_bonus
        self.state.combat_log.append(
            f"Player takes a defensive stance (+{ac_bonus} AC this round)."
        )
        return ac_bonus

    # ── Fighter abilities ─────────────────────────────────────────────────

    def resolve_cleave(self, enemies: List[Enemy]) -> List[DiceRoll]:
        """Fighter — Cleave: attack all active enemies at disadvantage.
        One damage roll per successful hit; each enemy checked independently."""
        player = self.state.player
        str_mod = player.stat_mod("strength")
        prof = player.proficiency_bonus
        weapon = player.equipped_weapon
        hit_bonus = str_mod + prof + (weapon.hit_bonus if weapon else 0)
        damage_notation = weapon.damage_dice if weapon else "1d4"
        rolls: List[DiceRoll] = []

        for enemy in enemies:
            atk = dice.roll(
                "1d20", modifier=hit_bonus, dc=enemy.ac,
                label=f"Cleave vs {enemy.name}", roll_type="attack", advantage=-1,
            )
            rolls.append(atk)
            if atk.success:
                dmg = dice.roll(
                    damage_notation, modifier=str_mod,
                    label=f"Cleave dmg → {enemy.name}", roll_type="damage",
                    crit=atk.is_critical,
                )
                rolls.append(dmg)
                actual = max(1, dmg.total)
                enemy.hp = max(0, enemy.hp - actual)
                self.state.combat_log.append(
                    f"Cleave hits {enemy.name} for {actual} dmg"
                    f" (roll {atk.total} vs AC {enemy.ac})"
                )
            else:
                self.state.combat_log.append(
                    f"Cleave misses {enemy.name} (roll {atk.total} vs AC {enemy.ac})"
                )

        return rolls

    def resolve_second_wind(self) -> List[DiceRoll]:
        """Fighter — Second Wind: self-heal 1d10 + CON_mod HP."""
        player = self.state.player
        con_mod = player.stat_mod("constitution")
        heal_roll = dice.roll(
            "1d10", modifier=con_mod, label="Second Wind", roll_type="damage",
        )
        amount = max(1, heal_roll.total)
        player.heal(amount)
        self.state.combat_log.append(f"Second Wind restores {amount} HP.")
        return [heal_roll]

    # ── Mage abilities ────────────────────────────────────────────────────

    def resolve_arcane_bolt(self, enemy: Enemy) -> List[DiceRoll]:
        """Mage — Arcane Bolt: INT-based ranged attack, 2d6 + INT_mod damage.
        Uses INT modifier for both attack and damage rolls instead of STR."""
        player = self.state.player
        int_mod = player.stat_mod("intelligence")
        prof = player.proficiency_bonus
        rolls: List[DiceRoll] = []

        atk = dice.roll(
            "1d20", modifier=int_mod + prof, dc=enemy.ac,
            label=f"Arcane Bolt vs {enemy.name}", roll_type="attack",
        )
        rolls.append(atk)

        if atk.success:
            dmg = dice.roll(
                "2d6", modifier=int_mod,
                label=f"Arcane dmg → {enemy.name}", roll_type="damage",
                crit=atk.is_critical,
            )
            rolls.append(dmg)
            actual = max(1, dmg.total)
            enemy.hp = max(0, enemy.hp - actual)
            crit_note = " CRITICAL HIT!" if atk.is_critical else ""
            self.state.combat_log.append(
                f"Arcane Bolt strikes {enemy.name} for {actual} dmg{crit_note}"
                f" (roll {atk.total} vs AC {enemy.ac})"
            )
        else:
            self.state.combat_log.append(
                f"Arcane Bolt fizzles (roll {atk.total} vs AC {enemy.ac})"
            )

        return rolls

    def resolve_mana_shield(self) -> List[DiceRoll]:
        """Mage — Mana Shield: roll 1d6 + INT_mod and store result as
        state.mana_shield_value. resolve_enemy_attack consumes this to
        absorb incoming damage before it is applied."""
        player = self.state.player
        int_mod = player.stat_mod("intelligence")
        shield_roll = dice.roll(
            "1d6", modifier=int_mod, label="Mana Shield", roll_type="check",
        )
        self.state.mana_shield_value = max(1, shield_roll.total)
        self.state.combat_log.append(
            f"Mana Shield charged — absorbs up to {self.state.mana_shield_value} dmg."
        )
        return [shield_roll]

    # ── Monk abilities ────────────────────────────────────────────────────

    def resolve_flurry(self, enemy: Enemy) -> List[DiceRoll]:
        """Monk — Flurry of Blows: two quick unarmed strikes, 1d4 + DEX_mod each.
        Uses DEX instead of STR for both attack and damage."""
        player = self.state.player
        dex_mod = player.stat_mod("dexterity")
        prof = player.proficiency_bonus
        hit_bonus = dex_mod + prof
        rolls: List[DiceRoll] = []

        for i in range(2):
            atk = dice.roll(
                "1d20", modifier=hit_bonus, dc=enemy.ac,
                label=f"Flurry {i + 1} vs {enemy.name}", roll_type="attack",
            )
            rolls.append(atk)
            if atk.success:
                dmg = dice.roll(
                    "1d4", modifier=dex_mod,
                    label=f"Flurry {i + 1} dmg", roll_type="damage",
                    crit=atk.is_critical,
                )
                rolls.append(dmg)
                actual = max(1, dmg.total)
                enemy.hp = max(0, enemy.hp - actual)
                self.state.combat_log.append(f"Flurry {i + 1} hits for {actual} dmg.")
            else:
                self.state.combat_log.append(
                    f"Flurry {i + 1} misses (roll {atk.total} vs AC {enemy.ac})."
                )
            if enemy.hp <= 0:
                break

        return rolls

    def resolve_iron_body(self) -> int:
        """Monk — Iron Body: raise AC by 2 + CON_mod; gain temp HP equal to WIS_mod.
        Returns the AC bonus applied so the UI can display it."""
        player = self.state.player
        con_mod = player.stat_mod("constitution")
        wis_mod = player.stat_mod("wisdom")
        ac_bonus = 2 + max(0, con_mod)
        player.temp_ac_bonus = ac_bonus
        temp_hp = max(0, wis_mod)
        if temp_hp > 0:
            player.hp = min(player.max_hp, player.hp + temp_hp)
        self.state.combat_log.append(
            f"Iron Body: +{ac_bonus} AC this round"
            + (f", +{temp_hp} HP from focus." if temp_hp else ".")
        )
        return ac_bonus

    def resolve_meditate(self) -> DiceRoll:
        """Monk — Meditate: skip player attack; enemy gets disadvantage this round;
        player gains the Focused status effect (advantage on next attack)."""
        from .models import StatusEffect

        self.state.enemy_attack_adv = -1
        focused = StatusEffect(
            name="Focused", duration_turns=1, advantage=1,
            description="Centred — advantage on the next attack roll.",
        )
        # Replace any existing Focused effect
        self.state.player.status_effects = [
            e for e in self.state.player.status_effects if e.name != "Focused"
        ]
        self.state.player.status_effects.append(focused)
        self.state.combat_log.append(
            "Player meditates — focused for next strike, enemy off-balance."
        )
        # Return a minimal roll so the UI has something to display
        return dice.roll("1d1", label="Meditate", roll_type="check")

    # ── Rogue abilities ───────────────────────────────────────────────────

    def resolve_backstab(self, enemy: Enemy) -> List[DiceRoll]:
        """Rogue — Backstab: attack with automatic advantage + 1d6 sneak damage on hit.
        Uses DEX modifier for attack and weapon damage."""
        player = self.state.player
        dex_mod = player.stat_mod("dexterity")
        prof = player.proficiency_bonus
        weapon = player.equipped_weapon
        hit_bonus = dex_mod + prof + (weapon.hit_bonus if weapon else 0)
        damage_notation = weapon.damage_dice if weapon else "1d4"
        rolls: List[DiceRoll] = []

        atk = dice.roll(
            "1d20", modifier=hit_bonus, dc=enemy.ac,
            label=f"Backstab vs {enemy.name}", roll_type="attack", advantage=1,
        )
        rolls.append(atk)

        if atk.success:
            dmg = dice.roll(
                damage_notation, modifier=dex_mod,
                label="Backstab dmg", roll_type="damage", crit=atk.is_critical,
            )
            sneak = dice.roll("1d6", label="Sneak Attack", roll_type="damage")
            rolls.extend([dmg, sneak])
            actual = max(1, dmg.total + sneak.total)
            enemy.hp = max(0, enemy.hp - actual)
            self.state.combat_log.append(
                f"Backstab hits {enemy.name} for {actual} dmg"
                f" (weapon {dmg.total} + sneak {sneak.total})."
            )
        else:
            self.state.combat_log.append(
                f"Backstab missed {enemy.name} (roll {atk.total} vs AC {enemy.ac})."
            )

        return rolls

    def resolve_smoke_screen(self) -> None:
        """Rogue — Smoke Screen: no player attack this round.
        Enemy gets disadvantage; player gains Smoke Cover (+1 advantage) for the
        next attack roll (duration 1 turn)."""
        from .models import StatusEffect

        self.state.enemy_attack_adv = -1
        buff = StatusEffect(
            name="Smoke Cover", duration_turns=1, advantage=1,
            description="Hidden in smoke — advantage on the next attack.",
        )
        self.state.player.status_effects = [
            e for e in self.state.player.status_effects if e.name != "Smoke Cover"
        ]
        self.state.player.status_effects.append(buff)
        self.state.combat_log.append(
            "Smoke fills the air — enemy blinded, player concealed."
        )

    def resolve_poison_strike(self, enemy: Enemy) -> List[DiceRoll]:
        """Rogue — Poison Strike: normal DEX-based attack + 1d4 poison damage on hit."""
        player = self.state.player
        dex_mod = player.stat_mod("dexterity")
        prof = player.proficiency_bonus
        weapon = player.equipped_weapon
        hit_bonus = dex_mod + prof + (weapon.hit_bonus if weapon else 0)
        damage_notation = weapon.damage_dice if weapon else "1d4"
        rolls: List[DiceRoll] = []

        atk = dice.roll(
            "1d20", modifier=hit_bonus, dc=enemy.ac,
            label=f"Poison Strike vs {enemy.name}", roll_type="attack",
        )
        rolls.append(atk)

        if atk.success:
            dmg = dice.roll(
                damage_notation, modifier=dex_mod,
                label="Weapon dmg", roll_type="damage", crit=atk.is_critical,
            )
            poison = dice.roll("1d4", label="Poison", roll_type="damage")
            rolls.extend([dmg, poison])
            actual = max(1, dmg.total + poison.total)
            enemy.hp = max(0, enemy.hp - actual)
            self.state.combat_log.append(
                f"Poison Strike: {actual} dmg ({dmg.total} weapon + {poison.total} poison)."
            )
        else:
            self.state.combat_log.append(
                f"Poison Strike misses (roll {atk.total} vs AC {enemy.ac})."
            )

        return rolls

    def resolve_thrown_weapon(self, enemy: Enemy, damage_dice: str) -> List[DiceRoll]:
        """Combat item — Thrown Weapon: DEX-based attack using the item's damage_dice."""
        player = self.state.player
        dex_mod = player.stat_mod("dexterity")
        prof = player.proficiency_bonus
        rolls: List[DiceRoll] = []

        atk = dice.roll(
            "1d20", modifier=dex_mod + prof, dc=enemy.ac,
            label=f"Thrown vs {enemy.name}", roll_type="attack",
        )
        rolls.append(atk)

        if atk.success:
            dmg = dice.roll(
                damage_dice, modifier=dex_mod,
                label="Thrown dmg", roll_type="damage", crit=atk.is_critical,
            )
            rolls.append(dmg)
            actual = max(1, dmg.total)
            enemy.hp = max(0, enemy.hp - actual)
            self.state.combat_log.append(
                f"Thrown weapon hits {enemy.name} for {actual} dmg."
            )
        else:
            self.state.combat_log.append(
                f"Thrown weapon misses (roll {atk.total} vs AC {enemy.ac})."
            )

        return rolls

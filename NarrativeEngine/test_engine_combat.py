"""Tests for the D&D 5e combat rules in the live engine.

Covers engine/dice.py and engine/combat.py:
  * natural-20 critical hits (auto-hit, damage dice doubled)
  * natural-1 automatic misses (fumbles)
  * advantage / disadvantage (roll 2d20, keep higher / lower)
  * initiative-driven turn order in resolve_full_round
  * enemy damage modifiers
  * armor affecting AC only (no flat damage reduction)

Randomness is made deterministic by patching ``random.randint`` with a fixed
``side_effect`` sequence — the values are consumed in the order the engine
rolls dice.

Run from the repository root:  python -m pytest test_engine_combat.py -v
"""

import pytest
from unittest.mock import patch

from engine.models import Armor, Enemy, GameState, Player, StatusEffect, Weapon
from engine.combat import CombatManager, net_advantage
from engine.dice import roll


# ── Builders ──────────────────────────────────────────────────────────────

def make_player(hp=30, strength=10, dexterity=10, weapon="1d6", armor_ac=None):
    p = Player(name="Hero", hp=hp, max_hp=30)
    p.stats["strength"] = strength
    p.stats["dexterity"] = dexterity
    if weapon is not None:
        p.equipped_weapon = Weapon(name="Blade", damage_dice=weapon)
    if armor_ac is not None:
        p.equipped_armor = Armor(name="Mail", ac_bonus=armor_ac)
    return p


def make_enemy(hp=30, ac=10, attack_bonus=0, damage_dice="1d6", damage_bonus=0, level=1):
    return Enemy(
        name="Goblin", hp=hp, max_hp=hp, ac=ac,
        attack_bonus=attack_bonus, damage_dice=damage_dice,
        damage_bonus=damage_bonus, level=level,
    )


def make_state(player=None):
    return GameState(player=player or make_player())


# ══ engine/dice.py ════════════════════════════════════════════════════════

def test_natural_20_is_critical_and_auto_hits():
    """A natural 20 on an attack always hits, even against an absurd AC."""
    with patch("random.randint", side_effect=[20]):
        r = roll("1d20", modifier=0, dc=99, roll_type="attack")
    assert r.natural == 20
    assert r.is_critical is True
    assert r.is_fumble is False
    assert r.success is True          # auto-hit despite DC 99


def test_natural_1_is_fumble_and_auto_misses():
    """A natural 1 always misses, even with a huge modifier against a low DC."""
    with patch("random.randint", side_effect=[1]):
        r = roll("1d20", modifier=50, dc=1, roll_type="attack")
    assert r.natural == 1
    assert r.is_fumble is True
    assert r.is_critical is False
    assert r.success is False         # auto-miss despite total of 51 vs DC 1


def test_natural_20_on_a_check_is_not_a_critical():
    """Crit / fumble rules apply only to attack rolls, not ability checks."""
    with patch("random.randint", side_effect=[20]):
        r = roll("1d20", dc=99, roll_type="check")
    assert r.is_critical is False
    assert r.success is False          # 20 < DC 99 — a check just compares totals


def test_advantage_keeps_the_higher_die():
    with patch("random.randint", side_effect=[8, 15]):
        r = roll("1d20", advantage=1)
    assert r.rolls == [15]
    assert r.dropped == [8]
    assert r.total == 15
    assert r.advantage == 1


def test_disadvantage_keeps_the_lower_die():
    with patch("random.randint", side_effect=[8, 15]):
        r = roll("1d20", advantage=-1)
    assert r.rolls == [8]
    assert r.dropped == [15]
    assert r.total == 8
    assert r.advantage == -1


def test_critical_doubles_the_damage_dice():
    """crit=True doubles the dice count (D&D 5e crit damage)."""
    with patch("random.randint", side_effect=[1, 2, 3, 4]):
        r = roll("2d6", modifier=2, crit=True, roll_type="damage")
    assert len(r.rolls) == 4           # 2d6 rolled twice
    assert r.total == 1 + 2 + 3 + 4 + 2


def test_advantage_rejects_multi_die_notation():
    with pytest.raises(ValueError):
        roll("2d6", advantage=1)


def test_invalid_advantage_value_rejected():
    with pytest.raises(ValueError):
        roll("1d20", advantage=2)


# ══ net_advantage helper ══════════════════════════════════════════════════

def test_net_advantage_single_sources():
    assert net_advantage([StatusEffect("Blessed", 3, advantage=1)]) == 1
    assert net_advantage([StatusEffect("Poisoned", 3, advantage=-1)]) == -1
    assert net_advantage([]) == 0


def test_net_advantage_does_not_stack():
    """Two advantage sources still count as a single advantage."""
    effects = [
        StatusEffect("Blessed", 3, advantage=1),
        StatusEffect("Hasted", 3, advantage=1),
    ]
    assert net_advantage(effects) == 1


def test_net_advantage_cancels_when_both_present():
    """Advantage + disadvantage cancel out to a normal roll."""
    effects = [
        StatusEffect("Blessed", 3, advantage=1),
        StatusEffect("Poisoned", 3, advantage=-1),
    ]
    assert net_advantage(effects) == 0


# ══ engine/combat.py — player attacks ═════════════════════════════════════

def test_player_critical_hit_doubles_damage():
    """Natural 20: auto-hit and the weapon damage dice are rolled twice."""
    state = make_state(make_player(strength=10, weapon="1d6"))   # str mod +0
    enemy = make_enemy(hp=30, ac=10)
    cm = CombatManager(state)
    # attack d20=20, then crit damage 1d6 doubled -> two dice [3, 4]
    with patch("random.randint", side_effect=[20, 3, 4]):
        rolls = cm.resolve_player_attack(enemy)
    attack, damage = rolls
    assert attack.is_critical is True
    assert len(damage.rolls) == 2          # damage dice doubled
    assert enemy.hp == 30 - (3 + 4)        # 7 damage dealt
    assert "CRITICAL" in state.combat_log[-1]


def test_player_natural_1_misses_even_with_huge_bonus():
    """A natural 1 misses outright, no damage roll happens."""
    state = make_state(make_player(strength=30, weapon="1d6"))   # str mod +10
    state.player.equipped_weapon.hit_bonus = 5
    enemy = make_enemy(hp=30, ac=10)
    cm = CombatManager(state)
    with patch("random.randint", side_effect=[1]):              # only the attack die
        rolls = cm.resolve_player_attack(enemy)
    assert len(rolls) == 1                  # no damage roll
    assert rolls[0].is_fumble is True
    assert rolls[0].success is False
    assert enemy.hp == 30                   # untouched


def test_player_attack_uses_advantage_from_status_effect():
    state = make_state(make_player(weapon="1d6"))
    state.player.status_effects.append(StatusEffect("Blessed", 3, advantage=1))
    enemy = make_enemy(hp=30, ac=10)
    cm = CombatManager(state)
    # advantage attack rolls two d20s [4, 17] -> keep 17, then damage 1d6 [5]
    with patch("random.randint", side_effect=[4, 17, 5]):
        rolls = cm.resolve_player_attack(enemy)
    attack = rolls[0]
    assert attack.advantage == 1
    assert attack.rolls == [17]
    assert attack.dropped == [4]
    assert attack.success is True


def test_player_attack_uses_disadvantage_from_status_effect():
    state = make_state(make_player(weapon="1d6"))
    state.player.status_effects.append(StatusEffect("Poisoned", 3, advantage=-1))
    enemy = make_enemy(hp=30, ac=10)
    cm = CombatManager(state)
    # disadvantage rolls [4, 17] -> keep 4 -> misses AC 10
    with patch("random.randint", side_effect=[4, 17]):
        rolls = cm.resolve_player_attack(enemy)
    attack = rolls[0]
    assert attack.advantage == -1
    assert attack.rolls == [4]
    assert attack.success is False
    assert enemy.hp == 30


# ══ engine/combat.py — enemy attacks ══════════════════════════════════════

def test_enemy_damage_bonus_is_added():
    """An enemy with a damage modifier adds it to its damage roll."""
    state = make_state(make_player(hp=30, dexterity=10))          # AC 10
    enemy = make_enemy(ac=10, attack_bonus=0, damage_dice="1d6", damage_bonus=3)
    cm = CombatManager(state)
    # attack d20=16 (hits AC 10), damage 1d6=4 -> 4 + 3 bonus = 7
    with patch("random.randint", side_effect=[16, 4]):
        cm.resolve_enemy_attack(enemy)
    assert state.player.hp == 30 - 7


def test_armor_does_not_reduce_damage_only_ac():
    """D&D 5e: armor raises AC but never subtracts from damage taken."""
    armored = make_player(hp=30, dexterity=10, armor_ac=5)        # AC = 10+0+5 = 15
    state = make_state(armored)
    enemy = make_enemy(ac=10, attack_bonus=0, damage_dice="1d6", damage_bonus=0)
    cm = CombatManager(state)
    # attack d20=16 beats AC 15 -> hit; damage 1d6=5 applies in full
    with patch("random.randint", side_effect=[16, 5]):
        cm.resolve_enemy_attack(enemy)
    assert state.player.hp == 30 - 5         # full 5, no reduction


def test_armor_raises_ac():
    assert make_player(dexterity=10, armor_ac=0).ac == 10
    assert make_player(dexterity=10, armor_ac=5).ac == 15
    assert make_player(dexterity=14, armor_ac=5).ac == 17   # +2 Dex mod


def test_enemy_natural_20_is_a_critical_hit():
    state = make_state(make_player(hp=30, dexterity=10))
    enemy = make_enemy(ac=10, damage_dice="1d6", damage_bonus=0)
    cm = CombatManager(state)
    # attack d20=20 -> crit; damage 1d6 doubled [2, 3] = 5
    with patch("random.randint", side_effect=[20, 2, 3]):
        rolls = cm.resolve_enemy_attack(enemy)
    assert rolls[0].is_critical is True
    assert len(rolls[1].rolls) == 2
    assert state.player.hp == 30 - 5


# ══ engine/combat.py — initiative turn order ══════════════════════════════

def test_full_round_rolls_initiative_first():
    state = make_state(make_player(weapon="1d6"))
    state.in_combat = True
    enemy = make_enemy(hp=30, ac=99)        # AC 99 so nobody connects
    state.active_enemies = [enemy]
    cm = CombatManager(state)
    # 2 initiative dice, then two attack dice that both whiff vs AC/AC
    with patch("random.randint", side_effect=[10, 5, 8, 8]):
        rolls = cm.resolve_full_round(enemy)
    assert rolls[0].roll_type == "initiative"
    assert rolls[1].roll_type == "initiative"


def test_initiative_player_wins_and_kills_before_enemy_acts():
    state = make_state(make_player(hp=30, weapon="1d6"))
    state.in_combat = True
    enemy = make_enemy(hp=3, ac=10, level=1)
    state.active_enemies = [enemy]
    cm = CombatManager(state)
    # player init 19 > enemy init 2 -> player first.
    # player attack d20=15 hits AC 10, damage 1d6=5 -> enemy 3 HP -> dead.
    with patch("random.randint", side_effect=[19, 2, 15, 5]):
        cm.resolve_full_round(enemy)
    assert enemy.hp == 0
    assert state.player.hp == 30             # enemy never got to swing
    assert state.in_combat is False
    assert state.player.experience == 50     # 25 + level*25


def test_initiative_enemy_wins_and_kills_before_player_acts():
    state = make_state(make_player(hp=1, weapon="1d6"))   # AC 10, 1 HP
    state.in_combat = True
    enemy = make_enemy(hp=30, ac=10, attack_bonus=0, damage_dice="1d6")
    state.active_enemies = [enemy]
    cm = CombatManager(state)
    # player init 2 < enemy init 19 -> enemy first.
    # enemy attack d20=15 hits AC 10, damage 1d6=6 -> player 1 HP -> dead.
    with patch("random.randint", side_effect=[2, 19, 15, 6]):
        cm.resolve_full_round(enemy)
    assert state.player.hp == 0
    assert enemy.hp == 30                    # player never got to swing


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

"""Dedicated D&D-style combat UI — three-column layout.

Left   : Action panel — all available actions with descriptions, loaded from
         engine/combat_actions.json. Active action is highlighted live.
Centre : Combat log — append-only round-by-round narration.
Right  : Stats panel — enemy HP/AC, player HP/AC/XP.

Opened automatically when in_combat becomes True (from app.py).
The player picks one action per turn from a numbered menu:

  Universal (all archetypes):
    1  — Strike       standard attack (STR-based)
    ^U — Use Item     consume first usable item in inventory
    R  — Flee         end combat, small HP penalty
    Esc — close overlay (combat state preserved unless victory/flee)

  Slots [2]–[4] are archetype-specific:
    Fighter  — Cleave / Second Wind / Defend
    Mage     — Arcane Bolt / Mana Shield / Evade
    Monk     — Flurry / Iron Body / Meditate
    Rogue    — Backstab / Smoke Screen / Poison Strike
    (none)   — Power Strike / Evade / Defend  (fallback)
"""

import asyncio
import json
import os
from typing import Dict, List, Optional, Tuple

# Pacing delays (seconds) — snappy but readable
_PACE_ANNOUNCE = 0.35   # after the "Round N ⚔ Action!" header line
_PACE_BETWEEN  = 0.45   # pause between player turn and enemy counter
_PACE_ROLL     = 0.10   # between individual roll lines

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static, RichLog

# Type alias for one action entry loaded from the JSON
_ActionEntry = Dict[str, str]

# Inline fallback if combat_actions.json is missing or unreadable
_FALLBACK_ACTIONS: Dict[str, List[_ActionEntry]] = {
    "universal": [
        {"slot": "1", "key": "1", "id": "strike",   "name": "Strike",    "short_desc": "STR attack vs AC.", "type": "attack"},
        {"slot": "u", "key": "^U","id": "use_item", "name": "Use Item",  "short_desc": "Use first consumable.", "type": "utility"},
        {"slot": "r", "key": "R", "id": "flee",     "name": "Flee",      "short_desc": "Escape (10% HP).", "type": "escape"},
    ],
    "fighter":  [
        {"slot": "2", "key": "2", "id": "cleave",       "name": "Cleave",       "short_desc": "All enemies, disadv.", "type": "attack"},
        {"slot": "3", "key": "3", "id": "second_wind",  "name": "Second Wind",  "short_desc": "Heal 1d10+CON.", "type": "heal"},
        {"slot": "4", "key": "4", "id": "defend",       "name": "Defend",       "short_desc": "+AC, riposte.", "type": "defense"},
    ],
    "mage":  [
        {"slot": "2", "key": "2", "id": "arcane_bolt",  "name": "Arcane Bolt",  "short_desc": "INT ranged attack.", "type": "attack"},
        {"slot": "3", "key": "3", "id": "mana_shield",  "name": "Mana Shield",  "short_desc": "Damage barrier.", "type": "defense"},
        {"slot": "4", "key": "4", "id": "evade",        "name": "Evade",        "short_desc": "DEX DC12, counter.", "type": "mobility"},
    ],
    "monk":  [
        {"slot": "2", "key": "2", "id": "flurry",       "name": "Flurry",       "short_desc": "Two 1d4+DEX hits.", "type": "attack"},
        {"slot": "3", "key": "3", "id": "iron_body",    "name": "Iron Body",    "short_desc": "+AC+WIS temp HP.", "type": "defense"},
        {"slot": "4", "key": "4", "id": "meditate",     "name": "Meditate",     "short_desc": "Foe at disadv.", "type": "utility"},
    ],
    "rogue":  [
        {"slot": "2", "key": "2", "id": "backstab",     "name": "Backstab",     "short_desc": "Adv + 1d6 sneak.", "type": "attack"},
        {"slot": "3", "key": "3", "id": "smoke_screen", "name": "Smoke Screen", "short_desc": "Blind foe.", "type": "utility"},
        {"slot": "4", "key": "4", "id": "poison_strike","name": "Poison Strike","short_desc": "Attack + 1d4 poison.", "type": "attack"},
    ],
    "fallback":  [
        {"slot": "2", "key": "2", "id": "power_strike", "name": "Power Strike", "short_desc": "Disadv, double dmg.", "type": "attack"},
        {"slot": "3", "key": "3", "id": "evade",        "name": "Evade",        "short_desc": "DEX DC12, counter.", "type": "mobility"},
        {"slot": "4", "key": "4", "id": "defend",       "name": "Defend",       "short_desc": "+AC, riposte.", "type": "defense"},
    ],
}

# Type-to-colour mapping for action type badge
_TYPE_COLOR: Dict[str, str] = {
    "attack":   "red",
    "heal":     "green",
    "defense":  "blue",
    "mobility": "cyan",
    "utility":  "yellow",
    "escape":   "dim",
}


class CombatScreen(ModalScreen[None]):
    """Full-screen modal that surfaces the active combat encounter."""

    CSS = """
    CombatScreen {
        align: center middle;
    }

    #combat-panel {
        width: 96%;
        height: 96%;
        min-width: 80;
        max-width: 160;
        background: #0d0d0d;
        border: double red;
        padding: 0;
    }

    #combat-title {
        text-align: center;
        text-style: bold;
        color: red;
        height: 1;
        padding: 0 2;
        background: #1a0000;
    }

    #combat-main {
        height: 1fr;
        layout: horizontal;
    }

    #actions-col {
        width: 30;
        background: #0a0a1a;
        border-right: tall $accent;
        padding: 0 1;
        overflow-y: auto;
    }

    #log-col {
        width: 1fr;
        padding: 0 1;
    }

    #stats-col {
        width: 30;
        background: #1a0a0a;
        border-left: tall $error;
        padding: 0 1;
        overflow-y: auto;
    }

    #combat-log {
        height: 1fr;
        background: #000000;
        scrollbar-gutter: stable;
    }

    #combat-status {
        height: 1;
        text-align: center;
        background: #1a1a1a;
        padding: 0 2;
    }
    """

    BINDINGS = [
        Binding("1",       "strike",         "1 Strike",    priority=True, show=False),
        Binding("2",       "slot2",          "2 Action",    priority=True, show=False),
        Binding("3",       "slot3",          "3 Action",    priority=True, show=False),
        Binding("4",       "slot4",          "4 Action",    priority=True, show=False),
        Binding("5",       "slot5",          "5 Action",    priority=True, show=False),
        Binding("ctrl+u",  "use_item",       "^U Use Item", priority=True, show=False),
        Binding("r",       "flee",           "R Flee",      priority=True, show=False),
        Binding("escape",  "dismiss_combat", "Esc Close",   show=False),
    ]

    def __init__(self, engine, refresh_parent):
        super().__init__()
        self.engine = engine
        self.refresh_parent = refresh_parent
        self.round = 0
        self._active_slot: str = ""           # slot key being executed right now
        self._action_db: Dict = {}            # raw JSON data
        self._actions: List[_ActionEntry] = []  # merged universal + archetype list
        self._round_in_progress: bool = False # prevents double-fire during async delays
        self._load_actions()

    # ── Action registry ───────────────────────────────────────────────────

    def _load_actions(self) -> None:
        """Load engine/combat_actions.json and merge universal + archetype entries."""
        try:
            json_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "engine", "combat_actions.json"
            )
            with open(json_path, encoding="utf-8") as f:
                self._action_db = json.load(f)
        except Exception:
            self._action_db = _FALLBACK_ACTIONS

        arch = self.engine.state.player.archetype or ""
        arch_key = arch if arch in self._action_db else "fallback"
        universal = self._action_db.get("universal", _FALLBACK_ACTIONS["universal"])
        specific  = self._action_db.get(arch_key,   _FALLBACK_ACTIONS.get(arch_key, []))
        self._actions = list(universal) + list(specific)

    # ── Compose ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="combat-panel"):
            yield Static("⚔  C O M B A T  ⚔", id="combat-title", markup=True)
            with Horizontal(id="combat-main"):
                with VerticalScroll(id="actions-col"):
                    yield Static("", id="actions-content", markup=True)
                with Vertical(id="log-col"):
                    yield RichLog(id="combat-log", wrap=True, markup=True)
                with VerticalScroll(id="stats-col"):
                    yield Static("", id="stats-content", markup=True)
            yield Static("", id="combat-status", markup=True)

    def on_mount(self) -> None:
        """Populate the combat log with existing entries and do an initial panel render."""
        # Reset class resource to full at the start of each new combat encounter
        if self.round == 0:
            p = self.engine.state.player
            if p.max_combat_resource > 0:
                p.combat_resource = p.max_combat_resource
        log = self.query_one("#combat-log", RichLog)
        state = self.engine.state
        for line in state.combat_log[-8:]:
            log.write(f"[dim]{line}[/]")
        if not state.combat_log:
            log.write("[italic dim]The battle begins…[/]")
        self._refresh_panels()

    # ── Rendering ─────────────────────────────────────────────────────────

    def _render_actions_panel(self) -> str:
        """Build Rich markup for the left actions column."""
        state = self.engine.state
        p = state.player
        lines: List[str] = ["[bold underline]ACTIONS[/]\n"]

        # Resource bar header (shown above action list when class has a resource)
        if p.max_combat_resource > 0:
            from engine.archetypes import ARCHETYPES
            res_info = ARCHETYPES.get(p.archetype, {}).get("resource", {})
            res_name  = res_info.get("name", "")
            res_color = res_info.get("color", "yellow")
            ability_costs = res_info.get("ability_costs", {})
            res_ratio  = p.combat_resource / max(p.max_combat_resource, 1)
            res_filled = max(0, min(12, int(res_ratio * 12)))
            res_bar = (
                f"[{res_color}]{'█' * res_filled}[/]"
                + f"[dim]{'░' * (12 - res_filled)}[/]"
            )
            lines.append(f"[bold]{res_name}[/] {res_bar} {p.combat_resource}/{p.max_combat_resource}\n")
        else:
            ability_costs = {}
            res_name = ""
            res_color = "yellow"

        # Collect first consumable name for Use Item dynamic label
        consumable_name = ""
        for name in state.player.inventory:
            item = next(
                (it for it in state.item_registry.values() if it.name == name),
                None,
            )
            if item and (
                (item.item_type == "consumable" and item.heal_amount > 0)
                or item.item_type == "combat"
            ):
                consumable_name = name
                break

        player_level = state.player.level

        for entry in self._actions:
            slot      = entry.get("slot", "")
            key       = entry.get("key", slot)
            name      = entry.get("name", "")
            raw_desc  = entry.get("short_desc", "")
            etype     = entry.get("type", "attack")
            color     = _TYPE_COLOR.get(etype, "white")
            active    = slot == self._active_slot

            # Level-gated slot-5 abilities: show lock badge when below required level
            level_unlock = entry.get("level_unlock")
            if level_unlock and player_level < level_unlock:
                lines.append(
                    f"[dim]\\[{key}] 🔒 {name}  [italic](Lv.{level_unlock})[/][/]"
                )
                for desc_line in raw_desc.split("\n"):
                    lines.append(f"  [dim]{desc_line}[/]")
                lines.append("")
                continue

            # Dynamic use-item description
            if entry.get("id") == "use_item":
                if consumable_name:
                    raw_desc = f"{consumable_name}"
                else:
                    raw_desc = "(nothing usable)"

            # Highlight active action
            if active:
                key_markup  = f"[bold yellow]\\[{key}][/]"
                name_markup = f"[bold yellow]▶ {name}[/]"
            else:
                key_markup  = f"[dim]\\[{key}][/]"
                name_markup = f"[{color}]{name}[/]"

            lines.append(f"{key_markup} {name_markup}")

            # Description lines — indent, split on \n
            for desc_line in raw_desc.split("\n"):
                if active:
                    lines.append(f"  [yellow]{desc_line}[/]")
                else:
                    lines.append(f"  [dim]{desc_line}[/]")

            # Resource cost indicator
            cost = ability_costs.get(entry.get("id", ""), 0)
            if cost > 0 and res_name:
                if active:
                    lines.append(f"  [bold yellow]{cost} {res_name}[/]")
                elif p.combat_resource < cost:
                    lines.append(f"  [bold red]{cost} {res_name} (insufficient)[/]")
                else:
                    lines.append(f"  [dim {res_color}]{cost} {res_name}[/]")

            lines.append("")

        return "\n".join(lines)

    def _render_stats_panel(self) -> str:
        """Build Rich markup for the right stats column (enemy + player)."""
        state = self.engine.state
        p = state.player
        lines: List[str] = []

        # ── Enemy block ───────────────────────────────────────────────────
        if state.active_enemies:
            lines.append("[bold underline]ENEMY[/]\n")
            for i, enemy in enumerate(state.active_enemies):
                marker = "[bold yellow]▶[/] " if i == 0 else "  "
                lines.append(
                    f"{marker}[bold red]{enemy.name}[/]"
                )
                lines.append(
                    f"  [dim]Lv.{enemy.level}  AC {enemy.ac}"
                    f"  ATK +{enemy.attack_bonus}[/]"
                )
                lines.append(
                    f"  [dim]DMG {enemy.damage_dice}"
                    + (f"+{enemy.damage_bonus}" if enemy.damage_bonus else "")
                    + "[/]"
                )
                lines.append(_hp_bar(enemy.hp, enemy.max_hp))
                lines.append("")
        else:
            lines.append("[dim](no enemies)[/]\n")

        # ── Divider ───────────────────────────────────────────────────────
        lines.append("[dim]─────────────────────[/]")
        lines.append("")

        # ── Player block ──────────────────────────────────────────────────
        lines.append("[bold underline]YOU[/]\n")
        arch_label = f"[dim]{p.archetype.title()}[/]  " if p.archetype else ""
        lines.append(f"  {arch_label}[bold green]{p.name}[/]  [dim]Lv.{p.level}[/]")

        # AC with temp bonus indicator
        if p.temp_ac_bonus:
            label = "Iron Body" if p.archetype == "monk" else "Defending"
            lines.append(f"  AC [bold cyan]{p.ac}[/] [dim](+{p.temp_ac_bonus} {label})[/]")
        else:
            lines.append(f"  AC [bold cyan]{p.ac}[/]  [dim]Prof +{p.proficiency_bonus}[/]")

        lines.append(_hp_bar(p.hp, p.max_hp))

        # Class resource bar
        if p.max_combat_resource > 0:
            from engine.archetypes import ARCHETYPES
            res_info  = ARCHETYPES.get(p.archetype, {}).get("resource", {})
            res_name  = res_info.get("name", "")
            res_color = res_info.get("color", "yellow")
            res_ratio  = p.combat_resource / max(p.max_combat_resource, 1)
            res_filled = max(0, min(16, int(res_ratio * 16)))
            res_bar = (
                f"[{res_color}]{'█' * res_filled}[/]"
                + f"[dim]{'░' * (16 - res_filled)}[/]"
            )
            lines.append(f"  {res_name} {res_bar} {p.combat_resource}/{p.max_combat_resource}")

        # XP bar
        xp_ratio = min(p.experience / max(p.xp_to_next_level, 1), 1.0)
        xp_filled = max(0, min(16, int(xp_ratio * 16)))
        xp_bar = (
            "[blue]" + "█" * xp_filled + "[/]"
            + "[dim]" + "░" * (16 - xp_filled) + "[/]"
        )
        lines.append(f"  XP {xp_bar}")
        lines.append(f"  [dim]{p.experience}/{p.xp_to_next_level}  Gold: {p.gold}[/]")

        # Status effects
        if p.status_effects:
            lines.append("")
            lines.append("  [bold]STATUS[/]")
            for eff in p.status_effects:
                mod_str = ""
                if eff.roll_modifier != 0:
                    mod_str = f" {'+' if eff.roll_modifier >= 0 else ''}{eff.roll_modifier}"
                adv = getattr(eff, "advantage", 0)
                if adv > 0:
                    mod_str += " [green]adv[/]"
                elif adv < 0:
                    mod_str += " [red]dis[/]"
                color = "red" if eff.roll_modifier < 0 else "green" if eff.roll_modifier > 0 else "yellow"
                lines.append(f"  [{color}]{eff.name}[/]{mod_str} ({eff.duration_turns}t)")

        return "\n".join(lines)

    def _refresh_panels(self) -> None:
        """Re-render the actions column and the stats column."""
        try:
            self.query_one("#actions-content", Static).update(
                self._render_actions_panel()
            )
        except Exception:
            pass
        try:
            self.query_one("#stats-content", Static).update(
                self._render_stats_panel()
            )
        except Exception:
            pass

    def _set_status(self, text: str, color: str = "white") -> None:
        try:
            self.query_one("#combat-status", Static).update(f"[{color}]{text}[/]")
        except Exception:
            pass

    def _log(self, text: str) -> None:
        try:
            self.query_one("#combat-log", RichLog).write(text)
        except Exception:
            pass

    def _set_active(self, slot: str) -> None:
        """Highlight the given slot in the actions panel, then immediately refresh."""
        self._active_slot = slot
        self._refresh_panels()

    def _clear_active(self) -> None:
        """Clear the active-slot highlight."""
        self._active_slot = ""

    def _spend_resource(self, cost: int) -> bool:
        """Deduct `cost` from the player's class resource.
        Returns True if the action can proceed; False (+ shows error) if insufficient."""
        if cost <= 0:
            return True
        p = self.engine.state.player
        if p.max_combat_resource <= 0:
            return True  # archetype has no resource system
        from engine.archetypes import ARCHETYPES
        res_name = ARCHETYPES.get(p.archetype, {}).get("resource", {}).get("name", "resource")
        if p.combat_resource < cost:
            self._set_status(
                f"Not enough {res_name}! ({p.combat_resource}/{p.max_combat_resource})", "red"
            )
            return False
        p.combat_resource -= cost
        self._refresh_panels()
        return True

    # ── Shared action helpers ─────────────────────────────────────────────

    def _guard(self) -> bool:
        """Return True (abort) if the player is dead or there are no enemies."""
        state = self.engine.state
        if state.player.hp <= 0:
            self._log("[bold red]You are already dead.[/]")
            return True
        if not state.in_combat or not state.active_enemies:
            self._set_status("No enemies to fight.", "yellow")
            return True
        return False

    def _start_round(self) -> None:
        """Housekeeping at the top of every player action."""
        self.round += 1
        state = self.engine.state
        p = state.player
        p.temp_ac_bonus = 0
        # Regen class resource (Mana has regen=0, so Mage's pool only depletes)
        if p.max_combat_resource > 0:
            from engine.archetypes import ARCHETYPES
            regen = ARCHETYPES.get(p.archetype, {}).get("resource", {}).get("regen_per_round", 0)
            if regen > 0:
                p.combat_resource = min(p.max_combat_resource, p.combat_resource + regen)

    def _roll_initiative_and_log(self, enemy) -> bool:
        """Roll initiative, log the result, return True if player acts first."""
        from engine.combat import CombatManager
        cm = CombatManager(self.engine.state)
        p_init, e_init = cm.roll_initiative()
        player_first = p_init.total >= e_init.total
        who = "You act first." if player_first else f"{enemy.name} acts first!"
        self._log(
            f"[bold]Round {self.round}[/]  "
            f"🎯 Initiative: You [bold]{p_init.total}[/] vs "
            f"{enemy.name} [bold]{e_init.total}[/] — {who}"
        )
        return player_first

    async def _do_enemy_counter(self, cm, enemy, initial_pause: bool = True) -> bool:
        """Run enemy counter-attack with pacing. Returns True if player died.

        initial_pause=True (default) waits _PACE_BETWEEN before enemy rolls — used
        when the player acted first and we need a dramatic beat before the counter.
        Pass initial_pause=False when the enemy already went first (initiative lost)
        so we don't double-pause after player's counter-strike.
        """
        if initial_pause:
            await asyncio.sleep(_PACE_BETWEEN)
        enemy_rolls = cm.resolve_enemy_attack(enemy)
        for r in enemy_rolls:
            self._log(_format_roll_line(r))
            await asyncio.sleep(_PACE_ROLL)
        if self.engine.state.player.hp <= 0:
            self._log("[bold red]You have fallen in battle.[/]")
            self._clear_active()
            self._refresh_panels()
            self.refresh_parent()
            self.app._handle_player_death()
            self.dismiss()
            return True
        return False

    def _check_enemy_dead(self, enemy) -> bool:
        """Award XP and clean up if enemy died. Returns True if enemy is dead."""
        if enemy.hp > 0:
            return False
        from engine.combat import _award_xp_for_kill
        state = self.engine.state
        xp = _award_xp_for_kill(state, enemy)
        state.active_enemies = [e for e in state.active_enemies if e is not enemy]
        if not state.active_enemies:
            state.in_combat = False
            state.in_aftermath = True   # trigger one aftermath narration turn
        state.add_log(f"COMBAT: {enemy.name} has been defeated! Gained {xp} XP.")
        state.combat_log.append(f"{enemy.name} defeated! +{xp} XP")
        self._log(f"[bold yellow]{enemy.name} has been defeated! +{xp} XP[/]")

        # Advance the rest counter.
        # Boss kill → immediately ready (set to 2); minor kill → +1 (need 2 total).
        is_boss = any(
            t.is_boss and t.enemy.name == enemy.name
            for t in state.encounter_registry.values()
        )
        if is_boss:
            state.enemies_defeated_since_rest = 2
        else:
            state.enemies_defeated_since_rest = min(2, state.enemies_defeated_since_rest + 1)

        return True

    def _finish_round(self, enemy, enemy_died: bool) -> None:
        """Refresh UI and handle end-of-round state."""
        self._clear_active()
        self._refresh_panels()
        self.refresh_parent()
        if enemy_died and not self.engine.state.in_combat:
            self._set_status("Victory! All enemies defeated. Press Esc to continue.", "green")
            self.call_after_refresh(self._show_victory_screen)
        elif not enemy_died:
            state = self.engine.state
            p = state.player
            self._set_status(
                f"Round {self.round}  │  "
                f"{enemy.name}: {enemy.hp}/{enemy.max_hp} HP  │  "
                f"You: {p.hp}/{p.max_hp} HP",
                "white",
            )

    # ── Actions ───────────────────────────────────────────────────────────

    async def action_strike(self) -> None:
        """Standard attack: 1d20 + STR + proficiency vs enemy AC."""
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._set_active("1")
            self._start_round()

            player_first = self._roll_initiative_and_log(enemy)
            await asyncio.sleep(_PACE_ANNOUNCE)

            if not player_first:
                if await self._do_enemy_counter(cm, enemy, initial_pause=False):
                    return
                await asyncio.sleep(_PACE_BETWEEN)

            player_rolls = cm.resolve_player_attack(enemy)
            for r in player_rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)

            enemy_died = self._check_enemy_dead(enemy)
            if enemy_died:
                self._finish_round(enemy, True)
                return

            if player_first:
                if await self._do_enemy_counter(cm, enemy):
                    return

            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    async def action_power_strike(self) -> None:
        """Power Strike: disadvantage to-hit, but double damage dice on hit."""
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            # _active_slot already set by action_slot2 dispatcher
            self._start_round()

            player_first = self._roll_initiative_and_log(enemy)
            await asyncio.sleep(_PACE_ANNOUNCE)

            if not player_first:
                if await self._do_enemy_counter(cm, enemy, initial_pause=False):
                    return
                await asyncio.sleep(_PACE_BETWEEN)

            self._log("[bold magenta]⚡ Power Strike![/]")
            player_rolls = cm.resolve_power_strike(enemy)
            for r in player_rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)

            enemy_died = self._check_enemy_dead(enemy)
            if enemy_died:
                self._finish_round(enemy, True)
                return

            if player_first:
                if await self._do_enemy_counter(cm, enemy):
                    return

            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    async def action_evade(self) -> None:
        """Evade: DEX check vs DC 12."""
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            # _active_slot already set by action_slot3/slot4 dispatcher
            self._start_round()

            self._log(f"[bold cyan]🌀 Evade — Round {self.round}[/]")
            success, evade_roll = cm.resolve_evade()
            self._log(_format_roll_line(evade_roll))
            await asyncio.sleep(_PACE_ANNOUNCE)

            if success:
                self._log("[cyan]You slip the strike — and lunge back![/]")
                player_rolls = cm.resolve_player_attack(enemy)
                for r in player_rolls:
                    self._log(_format_roll_line(r))
                    await asyncio.sleep(_PACE_ROLL)
                enemy_died = self._check_enemy_dead(enemy)
                if enemy_died:
                    self._finish_round(enemy, True)
                    return
                if await self._do_enemy_counter(cm, enemy):
                    return
            else:
                self._log("[red]Caught off-balance — enemy strikes with advantage![/]")
                if await self._do_enemy_counter(cm, enemy, initial_pause=False):
                    return

            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    async def action_defend(self) -> None:
        """Defend: raise AC by 2 + CON mod. If the enemy misses, riposte for free."""
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            # _active_slot already set by action_slot4 dispatcher
            self._start_round()

            ac_bonus = cm.resolve_defend()
            self._log(
                f"[bold blue]🛡 Defend — Round {self.round}  "
                f"(+{ac_bonus} AC  [dim]— miss = riposte[/])[/]"
            )
            await asyncio.sleep(_PACE_ANNOUNCE)
            await asyncio.sleep(_PACE_BETWEEN)

            enemy_rolls = cm.resolve_enemy_attack(enemy)
            for r in enemy_rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)

            if state.player.hp <= 0:
                self._log("[bold red]You have fallen in battle.[/]")
                self._clear_active()
                self._refresh_panels()
                self.refresh_parent()
                self.app._handle_player_death()
                self.dismiss()
                return

            attack_roll = enemy_rolls[0] if enemy_rolls else None
            if attack_roll is not None and attack_roll.success is False:
                self._log("[bold cyan]⚡ Riposte! Their swing found only air.[/]")
                await asyncio.sleep(0.25)
                riposte_rolls = cm.resolve_player_attack(enemy)
                for r in riposte_rolls:
                    self._log(_format_roll_line(r))
                    await asyncio.sleep(_PACE_ROLL)
                enemy_died = self._check_enemy_dead(enemy)
                self._finish_round(enemy, enemy_died)
                return

            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    def action_use_item(self) -> None:
        """Use the first available usable item (consumable or combat type)."""
        if self._round_in_progress or self.engine.state.player.hp <= 0:
            return
        consumables = self._consumable_names()
        if not consumables:
            self._set_status("No usable items in inventory.", "yellow")
            return
        self._set_active("u")
        item_name = consumables[0]
        state = self.engine.state
        item = next(
            (it for it in state.item_registry.values() if it.name == item_name),
            None,
        )
        if item and item.item_type == "combat":
            self._apply_combat_item(item)
            state.player.remove_item(item.name)
        else:
            ok, msg = self.engine.use_consumable(item_name)
            if ok:
                self._log(f"[green]✚ {msg}[/]")
                self._set_status(msg, "green")
            else:
                self._set_status(msg, "red")
        self._clear_active()
        self._refresh_panels()
        self.refresh_parent()

    def _consumable_names(self) -> List[str]:
        state = self.engine.state
        return [name for name in state.player.inventory if self._is_usable(name)]

    def _is_usable(self, item_name: str) -> bool:
        item = next(
            (it for it in self.engine.state.item_registry.values() if it.name == item_name),
            None,
        )
        if item is None:
            return False
        if item.item_type == "consumable" and item.heal_amount > 0:
            return True
        if item.item_type == "combat":
            return True
        return False

    def _apply_combat_item(self, item) -> None:
        """Resolve the mechanical effect of a combat-type item."""
        from engine.models import StatusEffect
        state = self.engine.state
        tags = item.tags if item.tags else []

        if "enemy_disadvantage" in tags:
            state.enemy_attack_adv = -1
            self._log(f"[cyan]💨 {item.name} — smoke fills the air! Enemy attack at disadvantage.[/]")
            self._set_status(f"{item.name} used — enemy at disadvantage.", "cyan")
        elif "player_advantage" in tags:
            buff = StatusEffect(
                name="Item Buff", duration_turns=1, advantage=1,
                description=f"{item.name} effect — advantage on next attack.",
            )
            state.player.status_effects = [
                e for e in state.player.status_effects if e.name != "Item Buff"
            ]
            state.player.status_effects.append(buff)
            self._log(f"[cyan]⚗️ {item.name} surges through you — advantage on next attack.[/]")
            self._set_status(f"{item.name} used — advantage granted.", "cyan")
        elif "thrown_weapon" in tags and item.damage_dice:
            from engine.combat import CombatManager
            if state.active_enemies:
                enemy = state.active_enemies[0]
                cm = CombatManager(state)
                rolls = cm.resolve_thrown_weapon(enemy, item.damage_dice)
                for r in rolls:
                    self._log(_format_roll_line(r))
                self._check_enemy_dead(enemy)
        else:
            self._log(f"[yellow]{item.name} used.[/]")

    # ── Archetype slot dispatchers ────────────────────────────────────────

    async def action_slot2(self) -> None:
        self._set_active("2")
        arch = self.engine.state.player.archetype
        if arch == "fighter":
            await self._do_cleave()
        elif arch == "mage":
            await self._do_arcane_bolt()
        elif arch == "monk":
            await self._do_flurry()
        elif arch == "rogue":
            await self._do_backstab()
        else:
            await self.action_power_strike()

    async def action_slot3(self) -> None:
        self._set_active("3")
        arch = self.engine.state.player.archetype
        if arch == "fighter":
            await self._do_second_wind()
        elif arch == "mage":
            await self._do_mana_shield()
        elif arch == "monk":
            await self._do_iron_body()
        elif arch == "rogue":
            await self._do_smoke_screen()
        else:
            await self.action_evade()

    async def action_slot4(self) -> None:
        self._set_active("4")
        arch = self.engine.state.player.archetype
        if arch == "mage":
            await self.action_evade()
        elif arch == "monk":
            await self._do_meditate()
        elif arch == "rogue":
            await self._do_poison_strike()
        else:
            await self.action_defend()

    async def action_slot5(self) -> None:
        """Dispatch the level-3 archetype unlock ability, or show a locked message."""
        from engine.archetypes import get_level_unlock
        state = self.engine.state
        unlock = get_level_unlock(state.player.archetype)
        if not unlock:
            return
        if state.player.level < unlock.get("level", 99):
            self._set_status(
                f"{unlock['name']} unlocks at Lv.{unlock['level']}.", "dim"
            )
            return
        self._set_active("5")
        arch = state.player.archetype
        if arch == "fighter":
            await self._do_battle_cry()
        elif arch == "mage":
            await self._do_arcane_surge()
        elif arch == "monk":
            await self._do_ki_strike()
        elif arch == "rogue":
            await self._do_shadow_step()

    # ── Fighter actions ───────────────────────────────────────────────────

    async def _do_cleave(self) -> None:
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            cm = CombatManager(state)
            self._set_active("2")
            self._start_round()          # regen fires first
            if not self._spend_resource(1):   # costs 1 Rage — checked after regen
                self._clear_active()
                return
            self._log(f"[bold red]⚔ Cleave! — Round {self.round}[/]")
            await asyncio.sleep(_PACE_ANNOUNCE)

            enemies_snapshot = list(state.active_enemies)
            rolls = cm.resolve_cleave(enemies_snapshot)
            for r in rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)

            all_dead = True
            for enemy in enemies_snapshot:
                if not self._check_enemy_dead(enemy):
                    all_dead = False

            if all_dead or not state.active_enemies:
                self._finish_round(enemies_snapshot[0], True)
                return

            first_survivor = state.active_enemies[0]
            if await self._do_enemy_counter(cm, first_survivor):
                return
            self._finish_round(first_survivor, False)
        finally:
            self._round_in_progress = False

    async def _do_second_wind(self) -> None:
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._set_active("3")
            self._start_round()
            self._log(f"[bold green]💚 Second Wind — Round {self.round}[/]")
            await asyncio.sleep(_PACE_ANNOUNCE)
            rolls = cm.resolve_second_wind()
            for r in rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)
            if await self._do_enemy_counter(cm, enemy):
                return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    # ── Mage actions ──────────────────────────────────────────────────────

    async def _do_arcane_bolt(self) -> None:
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._set_active("2")
            self._start_round()          # regen fires first
            if not self._spend_resource(1):   # costs 1 Mana — checked after regen
                self._clear_active()
                return

            player_first = self._roll_initiative_and_log(enemy)
            await asyncio.sleep(_PACE_ANNOUNCE)

            if not player_first:
                if await self._do_enemy_counter(cm, enemy, initial_pause=False):
                    return
                await asyncio.sleep(_PACE_BETWEEN)

            self._log(f"[bold blue]✨ Arcane Bolt — Round {self.round}[/]")
            rolls = cm.resolve_arcane_bolt(enemy)
            for r in rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)

            enemy_died = self._check_enemy_dead(enemy)
            if enemy_died:
                self._finish_round(enemy, True)
                return
            if player_first:
                if await self._do_enemy_counter(cm, enemy):
                    return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    async def _do_mana_shield(self) -> None:
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._set_active("3")
            self._start_round()          # regen fires first
            if not self._spend_resource(1):   # costs 1 Mana — checked after regen
                self._clear_active()
                return
            self._log(f"[bold blue]🔮 Mana Shield — Round {self.round}[/]")
            await asyncio.sleep(_PACE_ANNOUNCE)
            rolls = cm.resolve_mana_shield()
            for r in rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)
            if await self._do_enemy_counter(cm, enemy):
                return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    # ── Monk actions ──────────────────────────────────────────────────────

    async def _do_flurry(self) -> None:
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._set_active("2")
            self._start_round()          # regen fires first
            if not self._spend_resource(1):   # costs 1 Ki — checked after regen
                self._clear_active()
                return

            player_first = self._roll_initiative_and_log(enemy)
            await asyncio.sleep(_PACE_ANNOUNCE)

            if not player_first:
                if await self._do_enemy_counter(cm, enemy, initial_pause=False):
                    return
                await asyncio.sleep(_PACE_BETWEEN)

            self._log(f"[bold yellow]👊 Flurry of Blows — Round {self.round}[/]")
            rolls = cm.resolve_flurry(enemy)
            for r in rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)

            enemy_died = self._check_enemy_dead(enemy)
            if enemy_died:
                self._finish_round(enemy, True)
                return
            if player_first:
                if await self._do_enemy_counter(cm, enemy):
                    return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    async def _do_iron_body(self) -> None:
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._set_active("3")
            self._start_round()

            ac_bonus = cm.resolve_iron_body()
            self._log(
                f"[bold cyan]🗿 Iron Body — Round {self.round}  "
                f"(+{ac_bonus} AC[dim] — stance holds[/])[/]"
            )
            self._refresh_panels()
            await asyncio.sleep(_PACE_ANNOUNCE)
            if await self._do_enemy_counter(cm, enemy):
                return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    async def _do_meditate(self) -> None:
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._set_active("4")
            self._start_round()
            self._log(f"[bold cyan]🧘 Meditate — Round {self.round}[/]")
            cm.resolve_meditate()
            # Meditate grants +1 bonus Ki (on top of the round's normal regen)
            p = state.player
            if p.max_combat_resource > 0 and p.archetype == "monk":
                p.combat_resource = min(p.max_combat_resource, p.combat_resource + 1)
                self._log(f"[cyan]You centre yourself. Ki restores. Focused — enemy off-balance.[/]")
            else:
                self._log("[cyan]You centre yourself. Focused — enemy off-balance.[/]")
            await asyncio.sleep(_PACE_ANNOUNCE)
            if await self._do_enemy_counter(cm, enemy):
                return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    # ── Rogue actions ─────────────────────────────────────────────────────

    async def _do_backstab(self) -> None:
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._set_active("2")
            self._start_round()          # regen fires first
            if not self._spend_resource(1):   # costs 1 Energy — checked after regen
                self._clear_active()
                return

            player_first = self._roll_initiative_and_log(enemy)
            await asyncio.sleep(_PACE_ANNOUNCE)

            if not player_first:
                if await self._do_enemy_counter(cm, enemy, initial_pause=False):
                    return
                await asyncio.sleep(_PACE_BETWEEN)

            self._log(f"[bold red]🗡 Backstab — Round {self.round}[/]")
            rolls = cm.resolve_backstab(enemy)
            for r in rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)

            enemy_died = self._check_enemy_dead(enemy)
            if enemy_died:
                self._finish_round(enemy, True)
                return
            if player_first:
                if await self._do_enemy_counter(cm, enemy):
                    return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    async def _do_smoke_screen(self) -> None:
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._set_active("3")
            self._start_round()
            self._log(f"[bold yellow]💨 Smoke Screen — Round {self.round}[/]")
            cm.resolve_smoke_screen()
            self._log("[yellow]Smoke fills the gap. You vanish into it.[/]")
            await asyncio.sleep(_PACE_ANNOUNCE)
            if await self._do_enemy_counter(cm, enemy):
                return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    async def _do_poison_strike(self) -> None:
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._set_active("4")
            self._start_round()          # regen fires first
            if not self._spend_resource(1):   # costs 1 Energy — checked after regen
                self._clear_active()
                return

            player_first = self._roll_initiative_and_log(enemy)
            await asyncio.sleep(_PACE_ANNOUNCE)

            if not player_first:
                if await self._do_enemy_counter(cm, enemy, initial_pause=False):
                    return
                await asyncio.sleep(_PACE_BETWEEN)

            self._log(f"[bold green]☠ Poison Strike — Round {self.round}[/]")
            rolls = cm.resolve_poison_strike(enemy)
            for r in rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)

            enemy_died = self._check_enemy_dead(enemy)
            if enemy_died:
                self._finish_round(enemy, True)
                return
            if player_first:
                if await self._do_enemy_counter(cm, enemy):
                    return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    # ── Level-3 unlock abilities ──────────────────────────────────────────

    async def _do_battle_cry(self) -> None:
        """Fighter Lv3 — Battle Cry: buff action, no enemy counter this round."""
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._start_round()          # regen fires first
            if not self._spend_resource(2):   # costs 2 Rage — checked after regen
                self._clear_active()
                return
            self._log(f"[bold yellow]📣 Battle Cry! — Round {self.round}[/]")
            await asyncio.sleep(_PACE_ANNOUNCE)
            cm.resolve_battle_cry()
            self._log(
                "[yellow]A war cry tears through the air. "
                "Your strikes sharpen for the next 2 turns.[/]"
            )
            await asyncio.sleep(_PACE_BETWEEN)
            # Battle Cry intimidates — the enemy braces rather than attacking
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    async def _do_arcane_surge(self) -> None:
        """Mage Lv3 — Arcane Surge: pay HP, deal guaranteed 3d6+INT damage."""
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._start_round()          # regen fires first (Mage regen=0, so no change)
            if not self._spend_resource(3):   # costs 3 Mana — checked after regen
                self._clear_active()
                return
            self._log(f"[bold magenta]⚡ Arcane Surge — Round {self.round}[/]")
            await asyncio.sleep(_PACE_ANNOUNCE)
            rolls = cm.resolve_arcane_surge(enemy)
            for r in rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)

            # Check if the HP cost killed the player
            if state.player.hp <= 0:
                self._log("[bold red]The surge tears you apart. You have fallen.[/]")
                self._clear_active()
                self._refresh_panels()
                self.refresh_parent()
                self.app._handle_player_death()
                self.dismiss()
                return

            enemy_died = self._check_enemy_dead(enemy)
            if enemy_died:
                self._finish_round(enemy, True)
                return
            if await self._do_enemy_counter(cm, enemy):
                return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    async def _do_ki_strike(self) -> None:
        """Monk Lv3 — Ki Strike: guaranteed hit, 1d8+WIS+STR damage."""
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._start_round()          # regen fires first
            if not self._spend_resource(3):   # costs 3 Ki — checked after regen
                self._clear_active()
                return
            self._log(f"[bold cyan]🌀 Ki Strike — Round {self.round}[/]")
            await asyncio.sleep(_PACE_ANNOUNCE)
            rolls = cm.resolve_ki_strike(enemy)
            for r in rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)
            enemy_died = self._check_enemy_dead(enemy)
            if enemy_died:
                self._finish_round(enemy, True)
                return
            if await self._do_enemy_counter(cm, enemy):
                return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    async def _do_shadow_step(self) -> None:
        """Rogue Lv3 — Shadow Step: forced-crit attack with doubled damage dice."""
        if self._round_in_progress or self._guard():
            return
        self._round_in_progress = True
        try:
            from engine.combat import CombatManager
            state = self.engine.state
            enemy = state.active_enemies[0]
            cm = CombatManager(state)
            self._start_round()          # regen fires first
            if not self._spend_resource(3):   # costs 3 Energy — checked after regen
                self._clear_active()
                return
            self._log(f"[bold red]👁 Shadow Step — Round {self.round}[/]")
            await asyncio.sleep(_PACE_ANNOUNCE)
            rolls = cm.resolve_shadow_step(enemy)
            for r in rolls:
                self._log(_format_roll_line(r))
                await asyncio.sleep(_PACE_ROLL)
            enemy_died = self._check_enemy_dead(enemy)
            if enemy_died:
                self._finish_round(enemy, True)
                return
            if await self._do_enemy_counter(cm, enemy):
                return
            self._finish_round(enemy, False)
        finally:
            self._round_in_progress = False

    # ── Flee / dismiss ────────────────────────────────────────────────────

    def action_flee(self) -> None:
        """Attempt to flee: end combat, take a small HP penalty (10% max HP)."""
        if self._round_in_progress or self.engine.state.player.hp <= 0:
            return
        state = self.engine.state
        flee_damage = max(1, state.player.max_hp // 10)
        state.player.take_damage(flee_damage)
        state.player.temp_ac_bonus = 0
        state.in_combat = False
        state.active_enemies.clear()
        state.player_approaching = False
        state.combat_log.append(f"Player fled! Took {flee_damage} damage escaping.")
        state.add_log(f"COMBAT: You fled the battle, taking {flee_damage} damage.")
        self._log(f"[yellow]🏃 You flee! -{flee_damage} HP[/]")
        self.refresh_parent()
        self.dismiss()
        self._restore_input()

    def action_dismiss_combat(self) -> None:
        """Close the combat overlay.

        If the player just won (in_aftermath is True), schedule the aftermath
        narration to run in the narrative log after this screen closes.
        Otherwise re-enable the narrative input directly.
        """
        in_aftermath = self.engine.state.in_aftermath
        self.refresh_parent()
        self.dismiss()
        if in_aftermath:
            self.app.run_worker(
                self.app._auto_aftermath(),
                exclusive=True,
                name="aftermath",
            )
        else:
            self._restore_input()

    def _restore_input(self) -> None:
        """Re-enable and focus the narrative input after this screen closes."""
        try:
            inp = self.app.query_one("#player-input")
            inp.disabled = False
            inp.focus()
        except Exception:
            pass

    def _show_victory_screen(self) -> None:
        """Show the victory summary in the actions column and prompt the player to press Esc."""
        state = self.engine.state
        p = state.player

        victory_lines = [
            "[bold yellow]━━━  V I C T O R Y  ━━━[/]",
            "",
        ]
        for line in state.combat_log[-5:]:
            if any(kw in line for kw in ("XP", "defeated", "Level up")):
                victory_lines.append(f"[dim green]{line}[/]")
        victory_lines += [
            "",
            f"[dim]HP: {p.hp}/{p.max_hp}[/]",
            f"[dim]XP: {p.experience}/{p.xp_to_next_level}[/]",
            f"[dim]Gold: {p.gold}[/]",
            "",
            "[bold green]Press [Esc] to return[/]",
            "[dim]to your chronicle[/]",
        ]

        try:
            self.query_one("#actions-content", Static).update(
                "\n".join(victory_lines)
            )
        except Exception:
            pass

        self._set_status("✓ Victory — press Esc to continue", "bold green")
        self.refresh_parent()


# ── Formatting helpers ─────────────────────────────────────────────────────

def _bar(current: int, maximum: int, width: int = 16) -> str:
    if maximum <= 0:
        return f"[dim]{'░' * width}[/dim]"
    ratio = current / maximum
    filled = max(0, min(width, int(ratio * width)))
    empty = width - filled
    color = "green" if ratio > 0.6 else "yellow" if ratio > 0.3 else "red"
    filled_part = f"[{color}]{'█' * filled}[/{color}]" if filled > 0 else ""
    empty_part = f"[dim]{'░' * empty}[/dim]" if empty > 0 else ""
    return filled_part + empty_part


def _hp_bar(current: int, maximum: int, label: str = "HP") -> str:
    return f"  {label} {_bar(current, maximum)} {current}/{maximum}"


def _format_roll_line(roll) -> str:
    """Compact one-line dice roll summary for the combat log."""
    rolls_str = "+".join(str(r) for r in roll.rolls)
    if len(roll.rolls) > 1:
        rolls_str = f"({rolls_str})"
    if getattr(roll, "advantage", 0):
        adv_tag = "adv" if roll.advantage > 0 else "dis"
        dropped = "/".join(str(d) for d in getattr(roll, "dropped", []))
        rolls_str += f" [dim]({adv_tag}, dropped {dropped})[/]"

    mod_part = ""
    if roll.modifier > 0:
        mod_part = f"[cyan]+{roll.modifier}[/]"
    elif roll.modifier < 0:
        mod_part = f"[red]{roll.modifier}[/]"

    total_color = "white"
    if roll.success is True:
        total_color = "green"
    elif roll.success is False:
        total_color = "red"

    icon = {"attack": "⚔", "damage": "💥", "initiative": "🎯", "check": "⚄"}.get(
        roll.roll_type, "⚄"
    )
    label = roll.label or roll.dice
    result = f"[bold {total_color}]{roll.total}[/]"

    outcome = ""
    if roll.dc is not None:
        if roll.roll_type == "attack":
            if getattr(roll, "is_critical", False):
                outcome = " → [bold green]CRITICAL HIT![/]"
            elif getattr(roll, "is_fumble", False):
                outcome = " → [bold red]MISS (fumble!)[/]"
            elif roll.success:
                outcome = " → [bold green]HIT[/]"
            else:
                outcome = " → [bold red]MISS[/]"
        else:
            outcome = " → [bold green]✓[/]" if roll.success else " → [bold red]✗[/]"

    mod_str = f" {mod_part}" if mod_part else ""
    return f"  {icon} [bold yellow]{label}[/]: {rolls_str}{mod_str} = {result}{outcome}"

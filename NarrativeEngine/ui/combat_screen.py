"""Dedicated D&D-style combat UI.

Opened automatically when in_combat becomes True (from app.py).
The player can attack, use a consumable, or flee — all without going
through the LLM, so dice resolve instantly.

Key bindings:
  A  — attack the first active enemy (full round: player attack → enemy counter)
  U  — use a consumable (cycles through usable items)
  F  — flee (ends combat, small HP penalty)
  Esc — dismiss overlay (combat state preserved; return to narrative input)
"""

from typing import List, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Label, Static, RichLog


class CombatScreen(ModalScreen[None]):
    """Full-screen modal that surfaces the active combat encounter."""

    CSS = """
    CombatScreen {
        align: center middle;
    }

    #combat-panel {
        width: 80;
        height: 36;
        background: #0d0d0d;
        border: double red;
        padding: 1 2;
    }

    #combat-title {
        text-align: center;
        text-style: bold;
        color: red;
        height: 1;
        margin-bottom: 1;
    }

    #enemy-section {
        height: auto;
        background: #1a0000;
        border: tall $error;
        padding: 1;
        margin-bottom: 1;
    }

    #player-section {
        height: auto;
        background: #001a00;
        border: tall $success;
        padding: 1;
        margin-bottom: 1;
    }

    #combat-log {
        height: 10;
        background: #000000;
        border: solid $accent;
        margin-bottom: 1;
    }

    #action-hints {
        height: 1;
        text-align: center;
        color: $text-muted;
    }

    #combat-status {
        height: 1;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("ctrl+a", "attack", "^A Attack", priority=True),
        Binding("ctrl+u", "use_item", "^U Use Item", priority=True),
        Binding("ctrl+f", "flee", "^F Flee", priority=True),
        Binding("escape", "dismiss_combat", "Esc Close", show=True),
    ]

    def __init__(self, engine, refresh_parent):
        super().__init__()
        self.engine = engine
        self.refresh_parent = refresh_parent

    # ── Compose ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="combat-panel"):
            yield Static("⚔  C O M B A T  ⚔", id="combat-title", markup=True)
            yield Static(self._render_enemy_block(), id="enemy-section", markup=True)
            yield Static(self._render_player_block(), id="player-section", markup=True)
            yield RichLog(id="combat-log", wrap=True, markup=True)
            yield Static("", id="combat-status", markup=True)
            yield Static(
                "^A Attack  ^U Use Item  ^F Flee  Esc Close",
                id="action-hints",
                markup=True,
            )

    def on_mount(self) -> None:
        """Populate the combat log with any existing entries on open."""
        log = self.query_one("#combat-log", RichLog)
        state = self.engine.state
        # Show last 8 combat log lines on mount
        for line in state.combat_log[-8:]:
            log.write(f"[dim]{line}[/]")
        if not state.combat_log:
            log.write("[italic dim]The battle begins…[/]")

    # ── Rendering ─────────────────────────────────────────────────────────

    def _render_enemy_block(self) -> str:
        state = self.engine.state
        if not state.active_enemies:
            return "[dim](no enemies)[/]"
        lines = []
        for enemy in state.active_enemies:
            lines.append(f"[bold red]{enemy.name}[/]  "
                         f"[dim]Lv.{enemy.level}  AC {enemy.ac}  ATK +{enemy.attack_bonus}  DMG {enemy.damage_dice}[/]")
            lines.append(_hp_bar(enemy.hp, enemy.max_hp, label="HP"))
        return "\n".join(lines)

    def _render_player_block(self) -> str:
        state = self.engine.state
        p = state.player
        weapon_str = (
            f"{p.equipped_weapon.name} [{p.equipped_weapon.damage_dice}]"
            if p.equipped_weapon else "(no weapon)"
        )
        armor_str = (
            f"{p.equipped_armor.name} [AC+{p.equipped_armor.ac_bonus}"
            + (f" DR{p.equipped_armor.damage_reduction}" if p.equipped_armor.damage_reduction else "")
            + "]"
            if p.equipped_armor else "(no armor)"
        )
        consumables = self._consumable_names()
        inv_str = ", ".join(consumables) if consumables else "(none)"

        lines = [
            f"[bold green]{p.name}[/]  "
            f"[dim]Lv.{p.level}  AC {p.ac}  XP {p.experience}/{p.xp_to_next_level}[/]",
            _hp_bar(p.hp, p.max_hp, label="HP"),
            f"  ⚔ {weapon_str}   🛡 {armor_str}",
            f"  🧪 Usable: {inv_str}",
        ]
        return "\n".join(lines)

    def _consumable_names(self) -> List[str]:
        state = self.engine.state
        return [
            name for name in state.player.inventory
            if self._is_usable(name)
        ]

    def _is_usable(self, item_name: str) -> bool:
        item = next(
            (it for it in self.engine.state.item_registry.values() if it.name == item_name),
            None,
        )
        return item is not None and item.item_type == "consumable" and item.heal_amount > 0

    def _refresh_panels(self) -> None:
        self.query_one("#enemy-section", Static).update(self._render_enemy_block())
        self.query_one("#player-section", Static).update(self._render_player_block())

    def _set_status(self, text: str, color: str = "white") -> None:
        self.query_one("#combat-status", Static).update(f"[{color}]{text}[/]")

    def _log(self, text: str) -> None:
        log = self.query_one("#combat-log", RichLog)
        log.write(text)

    # ── Actions ───────────────────────────────────────────────────────────

    def action_attack(self) -> None:
        state = self.engine.state

        # Guard: already dead — should not happen normally but protect against it
        if state.player.hp <= 0:
            self._log("[bold red]You are already dead.[/]")
            self._set_status("You are dead.", "red")
            return

        if not state.in_combat or not state.active_enemies:
            self._set_status("No enemies to attack.", "yellow")
            return

        from engine.combat import CombatManager
        enemy = state.active_enemies[0]
        cm = CombatManager(state)

        # Run the player's attack first so we can stop before the enemy
        # counter-attacks if that would kill the player.
        player_rolls = cm.resolve_player_attack(enemy)
        for r in player_rolls:
            self._log(_format_roll_line(r))

        if enemy.hp <= 0:
            # Enemy died on the player's swing — award XP, no counter-attack
            from engine.combat import _award_xp_for_kill
            xp = _award_xp_for_kill(state, enemy)
            state.active_enemies = [e for e in state.active_enemies if e is not enemy]
            if not state.active_enemies:
                state.in_combat = False
            state.add_log(f"COMBAT: {enemy.name} has been defeated! Gained {xp} XP.")
            state.combat_log.append(f"{enemy.name} defeated! +{xp} XP")
            self._log(f"[bold yellow]{enemy.name} has been defeated! +{xp} XP[/]")
            self._refresh_panels()
            self.refresh_parent()
            if not state.in_combat:
                self._set_status("Victory! All enemies defeated.", "green")
                self.call_after_refresh(self._close_after_victory)
            return

        # Enemy survived — now it counter-attacks
        enemy_rolls = cm.resolve_enemy_attack(enemy)
        for r in enemy_rolls:
            self._log(_format_roll_line(r))

        # Log the last combat-log line (hit/miss summary from CombatManager)
        if state.combat_log:
            self._log(f"[dim]{state.combat_log[-1]}[/]")

        # Check player death after the counter-attack
        if state.player.hp <= 0:
            self._log("[bold red]You have fallen in battle.[/]")
            self._refresh_panels()
            self.refresh_parent()
            self.app._handle_player_death()
            self.dismiss()
            return

        self._refresh_panels()
        self.refresh_parent()
        self._set_status(
            f"{enemy.name}: {enemy.hp}/{enemy.max_hp} HP  |  "
            f"You: {state.player.hp}/{state.player.max_hp} HP",
            "white",
        )

    def _close_after_victory(self) -> None:
        self.refresh_parent()
        self.dismiss()

    def action_use_item(self) -> None:
        """Use the first available consumable in inventory."""
        if self.engine.state.player.hp <= 0:
            return
        consumables = self._consumable_names()
        if not consumables:
            self._set_status("No usable items in inventory.", "yellow")
            return
        item_name = consumables[0]
        ok, msg = self.engine.use_consumable(item_name)
        if ok:
            self._log(f"[green]✚ {msg}[/]")
            self._set_status(msg, "green")
        else:
            self._set_status(msg, "red")
        self._refresh_panels()
        self.refresh_parent()

    def action_flee(self) -> None:
        """Attempt to flee: end combat, take a small HP penalty."""
        if self.engine.state.player.hp <= 0:
            return
        state = self.engine.state
        flee_damage = max(1, state.player.max_hp // 10)  # 10% max HP penalty
        state.player.take_damage(flee_damage)
        state.in_combat = False
        state.active_enemies.clear()
        state.combat_log.append(f"Player fled! Took {flee_damage} damage escaping.")
        state.add_log(f"COMBAT: You fled the battle, taking {flee_damage} damage.")
        self._log(f"[yellow]🏃 You flee! -{flee_damage} HP[/]")
        self.refresh_parent()
        self.dismiss()

    def action_dismiss_combat(self) -> None:
        """Close overlay without changing combat state."""
        self.refresh_parent()
        self.dismiss()


# ── Formatting helpers ─────────────────────────────────────────────────────

def _bar(current: int, maximum: int, width: int = 20) -> str:
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
            outcome = " → [bold green]HIT[/]" if roll.success else " → [bold red]MISS[/]"
        else:
            outcome = " → [bold green]✓[/]" if roll.success else " → [bold red]✗[/]"

    mod_str = f" {mod_part}" if mod_part else ""
    return f"  {icon} [bold yellow]{label}[/]: {rolls_str}{mod_str} = {result}{outcome}"

from typing import Dict, List

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Label, RichLog, Static

from ai.client import AIClient
from ai.orchestrator import PromptOrchestrator
from ai.parser import parse_response
from engine.core import GameEngine
from engine.models import DiceRoll, Enemy, IncompatibleSaveError, Player
from engine.state_changes import apply_changes


SUMMARY_REFRESH_TURNS = 8
SUMMARY_INPUT_LOG_LINES = 16


class StatDisplay(Static):
    """Sidebar section widget — renders Rich-markup text reactively."""
    renderable = reactive("")

    def watch_renderable(self, new_val: str) -> None:
        self.update(new_val)


class ChronosApp(App):
    TITLE = "ChronosTUI: Narrative Engine"
    SUB_TITLE = "A D&D-Inspired AI RPG"

    CSS = """
    Screen { background: #121212; }

    #main-layout { layout: horizontal; height: 1fr; }

    #sidebar {
        width: 42;
        background: #1e1e1e;
        border-right: tall $accent;
        padding: 1 2;
    }

    #content-area { width: 1fr; layout: vertical; }

    #game-log {
        height: 1fr;
        border: double $primary;
        background: #000000;
        margin: 1;
        padding: 1;
    }

    #input-container { height: 3; margin: 0 1 1 1; }

    Input { border: none; background: #2a2a2a; }

    .stat-header {
        text-style: bold underline;
        color: $accent;
        margin-top: 1;
    }

    .stat-sep {
        color: $accent;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("d", "toggle_dark", "Toggle Dark"),
        ("s", "save_game", "Save"),
        ("p", "use_potion", "Potion"),
        ("a", "quick_attack", "Attack"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.engine = GameEngine()
        self.orchestrator = PromptOrchestrator()
        self.ai_client = AIClient()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main-layout"):
            with VerticalScroll(id="sidebar"):
                yield Label("CHARACTER", classes="stat-header")
                yield StatDisplay(id="character-display")
                yield Label("STATS", classes="stat-header")
                yield StatDisplay(id="stats-display")
                yield Label("EQUIPMENT", classes="stat-header")
                yield StatDisplay(id="equipment-display")
                yield Label("INVENTORY & QUESTS", classes="stat-header")
                yield StatDisplay(id="inventory-display")
            with Vertical(id="content-area"):
                yield RichLog(id="game-log", wrap=True, markup=True)
                with Container(id="input-container"):
                    yield Input(
                        placeholder="What do you do? (e.g., 'examine the gears')",
                        id="player-input",
                    )
        yield Footer()

    async def on_mount(self) -> None:
        self.engine.initialize_campaign()
        self.engine.state.player.add_item("Health Potion")
        self.update_ui()
        self.query_one("#player-input").focus()
        await self.process_narrative("I awaken.")

    # ── Input loop ────────────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        if not command:
            return
        log = self.query_one("#game-log", RichLog)
        log.write(f"\n[yellow]> {command}[/]")
        event.input.value = ""

        self.engine.state.turn_count += 1
        self.engine.state.add_log(f"PLAYER: {command}")

        expired = self.engine.state.tick_status_effects()
        for name in expired:
            log.write(f"[dim yellow]⏱ {name} faded.[/]")

        await self.process_narrative(command)
        self.update_ui()

    async def process_narrative(self, user_input: str) -> None:
        state = self.engine.state
        log_widget = self.query_one("#game-log", RichLog)

        state.last_rolls.clear()
        log_widget.write("[italic dim]The air shimmers as the narrator speaks...[/]")

        prior_location = state.current_location
        prior_quest_status: Dict[str, str] = {
            qid: q.status for qid, q in state.quests.items()
        }

        payload = self.orchestrator.build_payload(state, user_input)
        raw = await self.ai_client.generate_narrative(payload, json_mode=True)
        parsed = parse_response(raw)

        change_descs = apply_changes(state, parsed.state_changes)

        # Display any dice rolls that occurred during state changes
        if state.last_rolls:
            log_widget.write("")
            for r in state.last_rolls:
                log_widget.write(_format_dice_roll(r))

        if parsed.plot_point:
            event_text = parsed.plot_point.get("event")
            if isinstance(event_text, str) and event_text.strip():
                tags = parsed.plot_point.get("tags") or []
                if not isinstance(tags, list):
                    tags = []
                tags = [t for t in tags if isinstance(t, str)]
                choice = parsed.plot_point.get("choice_made")
                if not isinstance(choice, str):
                    choice = user_input
                state.record_choice(
                    event=event_text.strip(), choice=choice, tags=tags
                )

        self._record_heuristic_plot_points(prior_location, prior_quest_status, user_input)

        narrative = parsed.narrative or "(silence)"
        state.add_log(f"NARRATOR: {narrative}")
        log_widget.write("")
        log_widget.write(narrative)
        if change_descs:
            log_widget.write(f"[dim cyan]· {' | '.join(change_descs)}[/]")
        if parsed.parse_warnings:
            log_widget.write(f"[dim yellow]⚠ {'; '.join(parsed.parse_warnings)}[/]")

        if self._should_refresh_summary():
            self.run_worker(
                self._refresh_summary(),
                name="session-summary",
                exclusive=True,
            )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _record_heuristic_plot_points(
        self,
        prior_location: str,
        prior_quest_status: Dict[str, str],
        user_input: str,
    ) -> None:
        state = self.engine.state
        if state.current_location != prior_location:
            state.record_choice(
                event=f"Travelled from {prior_location} to {state.current_location}",
                choice=user_input,
                tags=["travel", _slug(prior_location), _slug(state.current_location)],
            )
        for qid, q in state.quests.items():
            old_status = prior_quest_status.get(qid)
            if old_status and old_status != q.status:
                state.record_choice(
                    event=f"Quest '{q.name}' status: {old_status} → {q.status}",
                    choice=user_input,
                    tags=["quest", q.status, qid],
                )

    def _should_refresh_summary(self) -> bool:
        s = self.engine.state
        return (s.turn_count - s.summary_anchor_turn) >= SUMMARY_REFRESH_TURNS

    async def _refresh_summary(self) -> None:
        state = self.engine.state
        recent = state.log[-SUMMARY_INPUT_LOG_LINES:]
        new_summary = await self.ai_client.summarize(state.session_summary, recent)
        if new_summary and not new_summary.startswith("ARCANE ERROR") and not new_summary.startswith("ERROR:"):
            state.session_summary = new_summary
            state.summary_anchor_turn = state.turn_count

    # ── Actions ───────────────────────────────────────────────────────────

    def action_use_potion(self) -> None:
        self.engine.use_potion()
        self.update_ui()

    def action_save_game(self) -> None:
        log = self.query_one("#game-log", RichLog)
        try:
            self.engine.save_game()
            log.write("[bold green]SYSTEM: Chronicle saved to disk.[/]")
        except Exception as e:
            log.write(f"[bold red]SYSTEM: Save failed: {e}[/]")

    def action_load_game(self) -> None:
        log = self.query_one("#game-log", RichLog)
        try:
            self.engine.load_game()
            self.update_ui()
            log.write("[bold green]SYSTEM: Chronicle restored from disk.[/]")
        except IncompatibleSaveError as e:
            log.write(f"[bold red]SYSTEM: {e}[/]")
        except FileNotFoundError:
            log.write("[bold red]SYSTEM: No save file found.[/]")
        except Exception as e:
            log.write(f"[bold red]SYSTEM: Load failed: {e}[/]")

    async def action_quick_attack(self) -> None:
        """Press 'a' during combat to immediately send an attack command."""
        state = self.engine.state
        if not state.in_combat or not state.active_enemies:
            return
        enemy = state.active_enemies[0]
        command = f"I attack {enemy.name}"
        log = self.query_one("#game-log", RichLog)
        log.write(f"\n[yellow]> {command}[/]")
        state.turn_count += 1
        state.add_log(f"PLAYER: {command}")
        expired = state.tick_status_effects()
        for name in expired:
            log.write(f"[dim yellow]⏱ {name} faded.[/]")
        await self.process_narrative(command)
        self.update_ui()

    # ── UI refresh ────────────────────────────────────────────────────────

    def update_ui(self) -> None:
        state = self.engine.state
        p = state.player

        # ── Character panel ───────────────────────────────────────────────
        char_lines = [
            f"[bold]{p.name}[/]",
            f"[cyan]{state.current_location}[/]",
            f"Turn: {state.turn_count}",
            "",
            _hp_bar(p.hp, p.max_hp),
            _xp_bar(p.experience, p.level),
            f"Gold: [yellow]{p.gold}[/]  AC: [cyan]{p.ac}[/]  Res: {p.bloodline_resonance}",
        ]
        if state.in_combat and state.active_enemies:
            char_lines += ["", "[bold red]⚔ COMBAT[/]"]
            for enemy in state.active_enemies:
                char_lines.append(f"[bold]{enemy.name}[/]  (AC {enemy.ac}  ATK +{enemy.attack_bonus})")
                char_lines.append(_enemy_hp_bar(enemy))
        self.query_one("#character-display", StatDisplay).renderable = "\n".join(char_lines)

        # ── Stats panel ───────────────────────────────────────────────────
        self.query_one("#stats-display", StatDisplay).renderable = _stat_block(p)

        # ── Equipment panel ───────────────────────────────────────────────
        equip_lines: List[str] = []
        if p.equipped_weapon:
            w = p.equipped_weapon
            bonus = f"+{w.hit_bonus}" if w.hit_bonus >= 0 else str(w.hit_bonus)
            equip_lines.append(f"⚔ [bold]{w.name}[/]  [{w.damage_dice} {bonus}]")
        else:
            equip_lines.append("⚔ [dim](no weapon)[/]")
        if p.equipped_armor:
            a = p.equipped_armor
            equip_lines.append(f"🛡 [bold]{a.name}[/]  [AC +{a.ac_bonus}]")
        else:
            equip_lines.append("🛡 [dim](no armor)[/]")
        if p.status_effects:
            equip_lines.append("")
            equip_lines.append("[bold]STATUS[/]")
            for eff in p.status_effects:
                mod_str = ""
                if eff.roll_modifier != 0:
                    mod_str = f" {'+' if eff.roll_modifier >= 0 else ''}{eff.roll_modifier}"
                color = "red" if eff.roll_modifier < 0 else "green" if eff.roll_modifier > 0 else "yellow"
                equip_lines.append(f"[{color}]{eff.name}[/]{mod_str} ({eff.duration_turns}t)")
        self.query_one("#equipment-display", StatDisplay).renderable = "\n".join(equip_lines)

        # ── Inventory & Quests panel ──────────────────────────────────────
        inv_lines: List[str] = ["[bold]INVENTORY[/]"]
        if p.inventory:
            inv_lines.extend(f"• {item}" for item in p.inventory)
        else:
            inv_lines.append("[dim](empty)[/]")
        inv_lines.append("")
        inv_lines.append("[bold]QUESTS[/]")
        for q in state.quests.values():
            if q.status == "completed":
                inv_lines.append(f"[dim green]✓ {q.name}[/]")
            elif q.status == "failed":
                inv_lines.append(f"[dim red]✗ {q.name}[/]")
            else:
                inv_lines.append(f"! {q.name}")
        if not state.quests:
            inv_lines.append("[dim](none)[/]")
        self.query_one("#inventory-display", StatDisplay).renderable = "\n".join(inv_lines)


# ── Module-level helpers ──────────────────────────────────────────────────


def _bar(current: int, maximum: int, width: int = 16) -> str:
    if maximum <= 0:
        return f"[dim]{'░' * width}[/]"
    ratio = current / maximum
    filled = max(0, min(width, int(ratio * width)))
    empty = width - filled
    color = "green" if ratio > 0.6 else "yellow" if ratio > 0.3 else "red"
    return f"[{color}]{'█' * filled}[/][dim]{'░' * empty}[/]"


def _hp_bar(current: int, maximum: int) -> str:
    return f"HP  {_bar(current, maximum)} {current}/{maximum}"


def _xp_bar(experience: int, level: int) -> str:
    threshold = level * 100
    ratio = min(experience / max(threshold, 1), 1.0)
    filled = max(0, min(16, int(ratio * 16)))
    empty = 16 - filled
    bar = f"[blue]{'█' * filled}[/][dim]{'░' * empty}[/]"
    return f"XP  {bar} {experience}/{threshold}  Lv.{level}"


def _enemy_hp_bar(enemy: Enemy) -> str:
    ratio = enemy.hp / max(enemy.max_hp, 1)
    filled = max(0, min(14, int(ratio * 14)))
    empty = 14 - filled
    color = "green" if ratio > 0.6 else "yellow" if ratio > 0.3 else "red"
    bar = f"[{color}]{'█' * filled}[/][dim]{'░' * empty}[/]"
    return f"HP [{bar}] {enemy.hp}/{enemy.max_hp}"


def _stat_block(player: Player) -> str:
    pairs = [
        ("strength", "STR"), ("constitution", "CON"),
        ("dexterity", "DEX"), ("wisdom",       "WIS"),
        ("intelligence", "INT"), ("charisma",  "CHA"),
    ]
    lines = []
    for i in range(0, len(pairs), 2):
        k1, l1 = pairs[i]
        k2, l2 = pairs[i + 1]
        v1, v2 = player.stats.get(k1, 10), player.stats.get(k2, 10)
        m1, m2 = player.stat_mod(k1), player.stat_mod(k2)
        s1 = "+" if m1 >= 0 else ""
        s2 = "+" if m2 >= 0 else ""
        lines.append(f"{l1} {v1:2d} ({s1}{m1})  {l2} {v2:2d} ({s2}{m2})")
    return "\n".join(lines)


def _format_dice_roll(roll: DiceRoll) -> str:
    """Format a DiceRoll as a Rich-markup block for the game log."""
    rolls_str = "+".join(str(r) for r in roll.rolls)
    if len(roll.rolls) > 1:
        rolls_str = f"({rolls_str})"

    mod_part = ""
    if roll.modifier > 0:
        mod_part = f" [cyan]+{roll.modifier}[/]"
    elif roll.modifier < 0:
        mod_part = f" [red]{roll.modifier}[/]"

    total_color = "white"
    if roll.success is True:
        total_color = "green"
    elif roll.success is False:
        total_color = "red"

    icon = {"attack": "⚔", "damage": "💥", "initiative": "🎯", "check": "⚄"}.get(
        roll.roll_type, "⚄"
    )
    label = roll.label or roll.dice
    lines = [
        f"[bold yellow]{icon} {label}[/]",
        f"  {roll.dice}: {rolls_str}{mod_part} = [bold {total_color}]{roll.total}[/]",
    ]
    if roll.dc is not None:
        if roll.roll_type == "attack":
            outcome = "[bold green]HIT[/]" if roll.success else "[bold red]MISS[/]"
            lines.append(f"  vs AC {roll.dc} → {outcome}")
        else:
            outcome = "[bold green]✓ SUCCESS[/]" if roll.success else "[bold red]✗ FAILURE[/]"
            lines.append(f"  vs DC {roll.dc} → {outcome}")
    return "\n".join(lines)


def _slug(text: str) -> str:
    return "_".join(text.lower().split())[:32] if text else "unknown"


if __name__ == "__main__":
    app = ChronosApp()
    app.run()

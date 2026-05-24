from typing import Dict, List

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.binding import Binding
from textual.widgets import Header, Input, Label, RichLog, Static

from ai.client import AIClient
from ai.orchestrator import PromptOrchestrator
from ai.parser import parse_response
from engine.core import GameEngine
from engine.models import DiceRoll, Enemy, IncompatibleSaveError, Player
from engine.state_changes import apply_changes


SUMMARY_REFRESH_TURNS = 8
SUMMARY_INPUT_LOG_LINES = 16


# ── State-change visual classification ─────────────────────────────────────
# Major ops get their own icon + styled line in the game log.
# Anything not listed here collapses into the single dim cyan summary.
_CHANGE_TIERS: Dict[str, tuple] = {
    "add_item":          ("📦", "bold green",   "Picked up"),
    "give_defined_item": ("📦", "bold green",   "Picked up"),
    "remove_item":       ("📤", "yellow",       "Dropped"),
    "equip_weapon":      ("⚔",  "bold yellow",  "Equipped"),
    "equip_armor":       ("🛡", "bold yellow",  "Equipped"),
    "unequip_weapon":    ("⚔",  "yellow",       "Unequipped"),
    "unequip_armor":     ("🛡", "yellow",       "Unequipped"),
    "start_combat":      ("⚔",  "bold red",     "Combat"),
    "spawn_encounter":   ("⚔",  "bold red",     "Combat"),
    "end_combat":        ("⚔",  "bold cyan",    "Combat ends"),
    "loot_encounter":    ("💰", "bold yellow",  "Loot"),
    "move_to":           ("🗺", "bold cyan",    "Travel"),
    "discover_location": ("🗺", "cyan",         "Discovered"),
    "add_npc":           ("👤", "bold",         "Met"),
    "advance_quest":     ("★",  "bold magenta", "Quest"),
    "complete_quest":    ("★",  "bold green",   "Quest complete"),
    "fail_quest":        ("★",  "bold red",     "Quest failed"),
    "define_quest":      ("★",  "magenta",      "New quest"),
    "award_xp":          ("✨", "blue",         "XP"),
    "damage_player":     ("💥", "red",          "Damage"),
    "heal_player":       ("✚",  "green",        "Healed"),
}

_ITEM_TYPE_ICONS: Dict[str, str] = {
    "weapon":     "⚔",
    "armor":      "🛡",
    "consumable": "🧪",
    "combat":     "💥",
    "utility":    "🔧",
    "quest":      "★",
    "lore":       "📜",
}


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
        width: 46;
        background: #1e1e1e;
        border-right: tall $accent;
        padding: 1 1;
        overflow-x: hidden;
        scrollbar-gutter: stable;
    }

    #content-area { width: 1fr; layout: vertical; }

    #game-log {
        height: 1fr;
        border: double $primary;
        background: #000000;
        padding: 1;
        scrollbar-gutter: stable;
    }

    #input-container { height: 3; margin: 0 1 0 1; }

    #key-hints-bar {
        height: 1;
        text-align: center;
        color: #888888;
        background: #1a1a1a;
        margin: 0 1 1 1;
    }

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
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+d", "toggle_dark", "Dark"),
        ("ctrl+s", "save_game", "Save"),
        ("ctrl+l", "load_game", "Load"),
        ("ctrl+u", "use_potion", "Use Item"),
        ("ctrl+a", "quick_attack", "Attack"),
        ("ctrl+e", "open_inventory", "Inventory"),
        ("ctrl+b", "open_combat", "Combat"),
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
                yield Static(
                    "^A Attack  ^B Combat  ^E Inventory  ^U Use Item"
                    "  ^S Save  ^L Load  ^D Dark  ^Q Quit",
                    id="key-hints-bar",
                )
    def on_mount(self) -> None:
        self.run_worker(self._init_game(), exclusive=True, name="init")

    async def _init_game(self) -> None:
        from ui.intro_screen import IntroScreen
        result = await self.push_screen_wait(IntroScreen())
        log = self.query_one("#game-log", RichLog)
        if result == "load":
            try:
                self.engine.load_game()
                # Loaded save may predate the archetype system — prompt if unset
                if not self.engine.state.player.archetype:
                    await self._show_class_select()
                self.update_ui()
                self.query_one("#player-input").focus()
                log.write("[bold green]SYSTEM: Chronicle restored from disk.[/]")
                return
            except IncompatibleSaveError as e:
                log.write(f"[bold red]SYSTEM: {e} — starting new chronicle.[/]")
            except Exception as e:
                log.write(f"[bold red]SYSTEM: Load failed: {e} — starting new chronicle.[/]")
        self.engine.initialize_campaign()
        await self._show_class_select()
        self.update_ui()
        self.query_one("#player-input").focus()
        await self.process_narrative("I awaken.")

    async def _show_class_select(self) -> None:
        """Push the ClassSelectScreen modal and apply the chosen archetype."""
        from ui.class_select_screen import ClassSelectScreen
        from engine.archetypes import apply_archetype
        archetype = await self.push_screen_wait(ClassSelectScreen())
        if archetype:
            apply_archetype(self.engine.state, archetype)
            try:
                self.engine.save_game()
            except Exception:
                pass

    # ── Input loop ────────────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.engine.state.player.hp <= 0:
            return  # dead — input is disabled but guard here too
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

        log_widget.write("[italic dim]The air shimmers as the narrator speaks...[/]")

        prior_location = state.current_location
        prior_in_combat = state.in_combat
        prior_quest_status: Dict[str, str] = {
            qid: q.status for qid, q in state.quests.items()
        }

        # Build payload before clearing last_rolls so the previous turn's dice
        # results are visible to the LLM as last_round_rolls in the context.
        payload = self.orchestrator.build_payload(state, user_input)
        state.last_rolls.clear()
        raw = await self.ai_client.generate_narrative(payload, json_mode=True)
        parsed = parse_response(raw)

        change_records = apply_changes(state, parsed.state_changes)

        # Record LLM-emitted plot point (display happens further down)
        llm_plot_event: str = ""
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
                llm_plot_event = event_text.strip()

        heuristic_plots = self._record_heuristic_plot_points(
            prior_location, prior_quest_status, user_input
        )

        # ── Render order: dice → narrative → state changes → plot points → warnings
        if state.last_rolls:
            log_widget.write("")
            for r in state.last_rolls:
                log_widget.write(_format_dice_roll(r))

        narrative = parsed.narrative or "(silence)"
        state.add_log(f"NARRATOR: {narrative}")
        log_widget.write("")
        log_widget.write(narrative)

        _render_state_changes(log_widget, change_records)

        if llm_plot_event:
            log_widget.write(f"[italic gold1]★ Plot: {llm_plot_event}[/]")
        for event_text in heuristic_plots:
            log_widget.write(f"[italic gold1]★ Plot: {event_text}[/]")

        if parsed.parse_warnings:
            log_widget.write(f"[dim yellow]⚠ {'; '.join(parsed.parse_warnings)}[/]")

        # Player death — lock everything down before proceeding
        if state.player.hp <= 0:
            self._handle_player_death()
            return

        # Auto-open the dedicated combat UI when a new combat starts.
        # For boss encounters, hold off — the narrative may contain the boss's
        # final words; opening immediately would cover them before the player reads them.
        if not prior_in_combat and state.in_combat and state.active_enemies:
            if self._is_boss_combat():
                log_widget.write("")
                log_widget.write(
                    "[bold red]⚔ A boss confronts you.[/]  "
                    "[dim]Read above, then press [bold]^B[/bold] to open the combat screen.[/]"
                )
            else:
                log_widget.write(
                    "[bold red]⚔ Combat started! Press [bold]^B[/bold] to fight  [R] Flee[/]"
                )
                self._open_combat_screen()

        if self._should_refresh_summary():
            self.run_worker(
                self._refresh_summary(),
                name="session-summary",
                exclusive=True,
            )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _is_boss_combat(self) -> bool:
        """Return True if any active enemy matches a boss encounter template."""
        state = self.engine.state
        active_names = {e.name.lower() for e in state.active_enemies}
        return any(
            t.is_boss and t.enemy.name.lower() in active_names
            for t in state.encounter_registry.values()
        )

    def _record_heuristic_plot_points(
        self,
        prior_location: str,
        prior_quest_status: Dict[str, str],
        user_input: str,
    ) -> List[str]:
        """Record automatic plot points for travel and quest-status changes.
        Returns the event texts so the caller can surface them in the log."""
        state = self.engine.state
        events: List[str] = []
        if state.current_location != prior_location:
            event_text = (
                f"Travelled from {prior_location} to {state.current_location}"
            )
            state.record_choice(
                event=event_text,
                choice=user_input,
                tags=["travel", _slug(prior_location), _slug(state.current_location)],
            )
            events.append(event_text)
        for qid, q in state.quests.items():
            old_status = prior_quest_status.get(qid)
            if old_status and old_status != q.status:
                event_text = (
                    f"Quest '{q.name}' status: {old_status} → {q.status}"
                )
                state.record_choice(
                    event=event_text,
                    choice=user_input,
                    tags=["quest", q.status, qid],
                )
                events.append(event_text)
        return events

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

    def _is_dead(self) -> bool:
        return self.engine.state.player.hp <= 0

    def _handle_player_death(self) -> None:
        """Lock the UI on player death. Only Q (quit) remains active."""
        if self.engine.state.player.hp > 0:
            return
        inp = self.query_one("#player-input", Input)
        inp.disabled = True
        inp.placeholder = "You are dead. Press Q to quit."
        log = self.query_one("#game-log", RichLog)
        log.write("")
        log.write("[bold red]" + "─" * 52 + "[/]")
        log.write("[bold red]        YOUR CHRONICLE ENDS HERE        [/]")
        log.write("[bold red]    The ruins of Elowen claim another.   [/]")
        log.write("[bold red]" + "─" * 52 + "[/]")
        log.write("[dim]Press [bold]Q[/bold] to quit.[/]")

    def action_use_potion(self) -> None:
        if self._is_dead():
            return
        self.engine.use_potion()
        self.update_ui()

    def action_save_game(self) -> None:
        if self._is_dead():
            return
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

    def action_open_inventory(self) -> None:
        if self._is_dead():
            return
        from ui.inventory_modal import InventoryModal
        self.push_screen(InventoryModal(self.engine, self.update_ui))

    def action_open_combat(self) -> None:
        if self._is_dead():
            return
        from ui.combat_screen import CombatScreen
        if isinstance(self.screen, CombatScreen):
            return
        if not self.engine.state.in_combat or not self.engine.state.active_enemies:
            return
        self._open_combat_screen()

    def _open_combat_screen(self) -> None:
        """Push the CombatScreen modal and lock the narrative input while it's open."""
        from ui.combat_screen import CombatScreen
        try:
            self.query_one("#player-input", Input).disabled = True
        except Exception:
            pass
        self.push_screen(CombatScreen(self.engine, self.update_ui))

    def on_screen_resume(self) -> None:
        """Fallback: re-enable narrative input when a non-combat modal closes.

        Note: Textual's ScreenResume has bubble=False, so this only fires when
        ChronosApp is used as an explicit Screen (rare). CombatScreen re-enables
        input directly via _restore_input() / run_worker(_auto_aftermath) on
        every dismiss path, so this is just a safety net for other modals.
        """
        try:
            inp = self.query_one("#player-input", Input)
            inp.disabled = False
            inp.focus()
        except Exception:
            pass

    async def _auto_aftermath(self) -> None:
        """Auto-trigger the aftermath narration turn after combat victory.

        Fires a single LLM turn in aftermath mode so the player sees loot,
        XP, and the next hook without having to type anything first.
        """
        log = self.query_one("#game-log", RichLog)
        try:
            self.query_one("#player-input", Input).disabled = True
        except Exception:
            pass

        state = self.engine.state
        state.turn_count += 1
        state.add_log("COMBAT: The enemy has been defeated.")
        expired = state.tick_status_effects()
        for name in expired:
            log.write(f"[dim yellow]⏱ {name} faded.[/]")

        await self.process_narrative("The enemy has been defeated.")
        self.update_ui()

        try:
            inp = self.query_one("#player-input", Input)
            inp.disabled = False
            inp.focus()
        except Exception:
            pass

    async def action_quick_attack(self) -> None:
        """Press 'a' during combat to immediately send an attack command."""
        if self._is_dead():
            return
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
                adv = getattr(eff, "advantage", 0)
                if adv > 0:
                    mod_str += " [green]adv[/]"
                elif adv < 0:
                    mod_str += " [red]dis[/]"
                color = "red" if eff.roll_modifier < 0 else "green" if eff.roll_modifier > 0 else "yellow"
                equip_lines.append(f"[{color}]{eff.name}[/]{mod_str} ({eff.duration_turns}t)")
        self.query_one("#equipment-display", StatDisplay).renderable = "\n".join(equip_lines)

        # ── Inventory & Quests panel ──────────────────────────────────────
        inv_lines: List[str] = ["[bold]INVENTORY[/]"]
        if p.inventory:
            inv_lines.extend(
                _render_inventory_item(state, item, is_equipped=self.engine.is_equipped(item))
                for item in p.inventory
            )
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


def _styled_segment(content: str, tag: str) -> str:
    """Emit a tagged markup span, or '' when content is empty. Avoids
    emitting `[tag][/tag]` empty spans that Textual's markup parser rejects."""
    if not content:
        return ""
    return f"[{tag}]{content}[/{tag}]"


def _bar(current: int, maximum: int, width: int = 16) -> str:
    if maximum <= 0:
        return _styled_segment("░" * width, "dim")
    ratio = current / maximum
    filled = max(0, min(width, int(ratio * width)))
    empty = width - filled
    color = "green" if ratio > 0.6 else "yellow" if ratio > 0.3 else "red"
    return _styled_segment("█" * filled, color) + _styled_segment("░" * empty, "dim")


def _hp_bar(current: int, maximum: int) -> str:
    return f"HP  {_bar(current, maximum)} {current}/{maximum}"


def _xp_bar(experience: int, level: int) -> str:
    threshold = level * 100
    ratio = min(experience / max(threshold, 1), 1.0)
    filled = max(0, min(16, int(ratio * 16)))
    empty = 16 - filled
    bar = _styled_segment("█" * filled, "blue") + _styled_segment("░" * empty, "dim")
    return f"XP  {bar} {experience}/{threshold}  Lv.{level}"


def _enemy_hp_bar(enemy: Enemy) -> str:
    ratio = enemy.hp / max(enemy.max_hp, 1)
    filled = max(0, min(14, int(ratio * 14)))
    empty = 14 - filled
    color = "green" if ratio > 0.6 else "yellow" if ratio > 0.3 else "red"
    bar = _styled_segment("█" * filled, color) + _styled_segment("░" * empty, "dim")
    return f"HP {bar} {enemy.hp}/{enemy.max_hp}"


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
    if getattr(roll, "advantage", 0):
        adv_tag = "advantage" if roll.advantage > 0 else "disadvantage"
        dropped = "/".join(str(d) for d in getattr(roll, "dropped", []))
        rolls_str += f" [dim]({adv_tag}, dropped {dropped})[/]"

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
            if getattr(roll, "is_critical", False):
                outcome = "[bold green]CRITICAL HIT![/]"
            elif getattr(roll, "is_fumble", False):
                outcome = "[bold red]MISS (natural 1!)[/]"
            elif roll.success:
                outcome = "[bold green]HIT[/]"
            else:
                outcome = "[bold red]MISS[/]"
            lines.append(f"  vs AC {roll.dc} → {outcome}")
        else:
            outcome = "[bold green]✓ SUCCESS[/]" if roll.success else "[bold red]✗ FAILURE[/]"
            lines.append(f"  vs DC {roll.dc} → {outcome}")
    return "\n".join(lines)


def _slug(text: str) -> str:
    return "_".join(text.lower().split())[:32] if text else "unknown"


# ── State-change rendering ────────────────────────────────────────────────

def _strip_op_prefix(desc: str, op: str) -> str:
    """Trim redundant op-name prefixes from a description so the tier label
    doesn't double up (e.g. 'Picked up: +Rusted Blade (inventory)')."""
    prefixes = {
        "add_item":          ("+", " (inventory)"),
        "remove_item":       ("-", " (inventory)"),
        "move_to":           ("moved to ", ""),
        "discover_location": ("discovered: ", ""),
        "add_npc":           ("NPC met: ", ""),
        "define_quest":      ("quest defined: ", ""),
        "complete_quest":    ("quest completed: ", ""),
        "fail_quest":        ("quest failed: ", ""),
        "loot_encounter":    ("loot: ", ""),
        "give_defined_item": ("received: ", ""),
        "equip_weapon":      ("equipped weapon: ", ""),
        "equip_armor":       ("equipped armor: ", ""),
        "unequip_weapon":    ("unequipped weapon: ", ""),
        "unequip_armor":     ("unequipped armor: ", ""),
        "award_xp":          ("", ""),
        "damage_player":     ("player HP -", ""),
        "heal_player":       ("player HP +", ""),
    }
    head, tail = prefixes.get(op, ("", ""))
    out = desc
    if head and out.startswith(head):
        out = out[len(head):]
    if tail and out.endswith(tail):
        out = out[: -len(tail)]
    return out


def _render_state_changes(log_widget: RichLog, records: List[tuple]) -> None:
    """Render state-change records into the game log. Major-tier ops get their
    own styled line; everything else collapses into one dim cyan summary."""
    if not records:
        return
    minor: List[str] = []
    for op, desc in records:
        if desc.startswith("["):  # failure / skip / invalid
            log_widget.write(f"[dim red]⚠ {desc}[/]")
            continue
        tier = _CHANGE_TIERS.get(op)
        if tier is None:
            minor.append(desc)
            continue
        icon, style, label = tier
        payload = _strip_op_prefix(desc, op)
        log_widget.write(f"[{style}]{icon} {label}: {payload}[/]")
    for item in minor:
        log_widget.write(f"[dim cyan]· {item}[/]")


# ── Inventory rendering ───────────────────────────────────────────────────

def _lookup_item_def(state, name: str):
    """Find an ItemDefinition by display name. Pattern mirrored from
    engine.state_changes._loot_encounter."""
    return next(
        (it for it in state.item_registry.values() if it.name == name),
        None,
    )


def _render_inventory_item(state, item_name: str, is_equipped: bool = False) -> str:
    """Render one inventory entry. Uses item_registry metadata when available;
    falls back to the plain bullet for ad-hoc add_item entries. When is_equipped
    is True, an '(equipped)' marker is appended."""
    defn = _lookup_item_def(state, item_name)
    icon = "•" if defn is None else _ITEM_TYPE_ICONS.get(defn.item_type, "•")
    type_tag = "" if defn is None else f" [dim]({defn.item_type})[/]"
    equipped_tag = " [bold yellow](equipped)[/]" if is_equipped else ""
    return f"{icon} {item_name}{type_tag}{equipped_tag}"


if __name__ == "__main__":
    app = ChronosApp()
    app.run()

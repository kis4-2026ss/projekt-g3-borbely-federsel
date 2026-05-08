from typing import Dict

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Label, RichLog, Static

from ai.client import AIClient
from ai.orchestrator import PromptOrchestrator
from ai.parser import parse_response
from engine.core import GameEngine
from engine.models import IncompatibleSaveError
from engine.state_changes import apply_changes


SUMMARY_REFRESH_TURNS = 8        # rebuild session_summary every N turns
SUMMARY_INPUT_LOG_LINES = 16     # how many recent log lines feed the summary call


class StatDisplay(Static):
    """Sidebar widget for stats / inventory / quests."""
    renderable = reactive("")

    def watch_renderable(self, new_val: str) -> None:
        self.update(new_val)


class ChronosApp(App):
    TITLE = "ChronosTUI: Narrative Engine"
    SUB_TITLE = "A d&d Inspired AI RPG"

    CSS = """
    Screen { background: #121212; }

    #main-layout { layout: horizontal; height: 1fr; }

    #sidebar {
        width: 35;
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
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("d", "toggle_dark", "Toggle Dark Mode"),
        ("s", "save_game", "Save Game"),
        ("p", "use_potion", "Drink Potion"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.engine = GameEngine()
        self.orchestrator = PromptOrchestrator()
        self.ai_client = AIClient()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main-layout"):
            with Vertical(id="sidebar"):
                yield Label("PLAYER DATA", classes="stat-header")
                yield StatDisplay(id="stats-display")
                yield Label("\nINVENTORY & QUESTS", classes="stat-header")
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

    # ---- input loop -------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        if not command:
            return
        self.query_one("#game-log", RichLog).write(f"\n[yellow]> {command}[/]")
        event.input.value = ""

        self.engine.state.turn_count += 1
        self.engine.state.add_log(f"PLAYER: {command}")

        await self.process_narrative(command)
        self.update_ui()

    async def process_narrative(self, user_input: str) -> None:
        log_widget = self.query_one("#game-log", RichLog)
        log_widget.write("[italic dim]The air shimmers as the narrator speaks...[/]")

        # Snapshot for heuristic plot-point detection
        prior_location = self.engine.state.current_location
        prior_quest_status: Dict[str, str] = {
            qid: q.status for qid, q in self.engine.state.quests.items()
        }

        payload = self.orchestrator.build_payload(self.engine.state, user_input)
        raw = await self.ai_client.generate_narrative(payload, json_mode=True)
        parsed = parse_response(raw)

        # 1) apply LLM-proposed state changes
        change_descs = apply_changes(self.engine.state, parsed.state_changes)

        # 2) record LLM-supplied plot point (preferred)
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
                self.engine.state.record_choice(
                    event=event_text.strip(), choice=choice, tags=tags
                )

        # 3) heuristic backup plot points (in case the LLM missed it)
        self._record_heuristic_plot_points(prior_location, prior_quest_status, user_input)

        # 4) display
        narrative = parsed.narrative or "(silence)"
        self.engine.state.add_log(f"NARRATOR: {narrative}")
        log_widget.write("")
        log_widget.write(narrative)
        if change_descs:
            log_widget.write(f"[dim cyan]· {' | '.join(change_descs)}[/]")
        if parsed.parse_warnings:
            log_widget.write(
                f"[dim yellow]⚠ {'; '.join(parsed.parse_warnings)}[/]"
            )

        # 5) trigger session summary refresh in the background if due
        if self._should_refresh_summary():
            self.run_worker(
                self._refresh_summary(),
                name="session-summary",
                exclusive=True,
            )

    # ---- helpers ----------------------------------------------------------

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
                tags=[
                    "travel",
                    _slug(prior_location),
                    _slug(state.current_location),
                ],
            )
        for qid, q in state.quests.items():
            old_status = prior_quest_status.get(qid)
            if old_status and old_status != q.status:
                state.record_choice(
                    event=f"Quest '{q.name}' status: {old_status} -> {q.status}",
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

    # ---- actions ----------------------------------------------------------

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

    # ---- UI refresh -------------------------------------------------------

    def update_ui(self) -> None:
        p = self.engine.state.player

        stats_text = (
            f"Name: [bold]{p.name}[/]\n"
            f"Location: [cyan]{self.engine.state.current_location}[/]\n"
            f"HP: [green]{p.hp}[/]/[green]{p.max_hp}[/]\n"
            f"Lineage: {p.lineage}\n"
            f"Resonance: {p.bloodline_resonance}\n"
            f"Turn: {self.engine.state.turn_count}"
        )
        self.query_one("#stats-display", StatDisplay).renderable = stats_text

        inv_list = [f"• {item}" for item in p.inventory]
        quest_list = [
            f"! {q.name} ({q.status})" for q in self.engine.state.quests.values()
        ]
        display_text = "[bold]INVENTORY[/]\n" + (
            "\n".join(inv_list) if inv_list else "Empty"
        )
        display_text += "\n\n[bold]ACTIVE QUESTS[/]\n" + (
            "\n".join(quest_list) if quest_list else "None"
        )
        self.query_one("#inventory-display", StatDisplay).renderable = display_text


def _slug(text: str) -> str:
    return "_".join(text.lower().split())[:32] if text else "unknown"


if __name__ == "__main__":
    app = ChronosApp()
    app.run()

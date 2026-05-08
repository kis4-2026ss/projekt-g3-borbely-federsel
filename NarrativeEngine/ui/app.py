from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Log, Static, Label, Input
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from engine.core import GameEngine

class StatDisplay(Static):
    """A widget to display player statistics."""
    renderable = reactive("")

    def watch_renderable(self, new_val: str) -> None:
        self.update(new_val)

class ChronosApp(App):
    """A professional TUI for ChronosTUI."""

    TITLE = "ChronosTUI: Narrative Engine"
    SUB_TITLE = "A d&d Inspired AI RPG"

    CSS = """
    Screen {
        background: #121212;
    }

    #main-layout {
        layout: horizontal;
        height: 1fr;
    }

    #sidebar {
        width: 35;
        background: #1e1e1e;
        border-right: tall $accent;
        padding: 1 2;
    }

    #content-area {
        width: 1fr;
        layout: vertical;
    }

    #game-log {
        height: 1fr;
        border: double $primary;
        background: #000000;
        margin: 1;
        padding: 1;
    }

    #input-container {
        height: 3;
        margin: 0 1 1 1;
    }

    Input {
        border: none;
        background: #2a2a2a;
    }

    .stat-header {
        text-style: bold underline;
        color: $accent;
        margin-bottom: 1;
    }

    .stat-value {
        margin-left: 1;
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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main-layout"):
            with Vertical(id="sidebar"):
                yield Label("PLAYER DATA", classes="stat-header")
                yield StatDisplay(id="stats-display")
                yield Label("\nINVENTORY", classes="stat-header")
                yield StatDisplay(id="inventory-display")
            with Vertical(id="content-area"):
                yield Log(id="game-log")
                with Container(id="input-container"):
                    yield Input(placeholder="What do you do? (e.g., 'examine the gears')", id="player-input")
        yield Footer()

    def on_mount(self) -> None:
        # Initial Setup
        self.engine.state.player.add_item("Health Potion")
        self.engine.state.player.add_item("Rusty Dagger")
        self.engine.state.player.hp = 12
        self.update_ui()
        self.query_one("#game-log", Log).write_line("[bold cyan]Welcome to Aethelgard, traveler.[/]")
        self.query_one("#player-input").focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle player input submissions."""
        command = event.value.strip()
        if command:
            self.query_one("#game-log", Log).write_line(f"\n[yellow]> {command}[/]")
            # Here we will eventually trigger the AI Orchestrator
            self.engine.state.add_log(f"You attempted to: {command}")
            event.input.value = ""
            self.update_ui()

    def action_use_potion(self) -> None:
        self.engine.use_potion()
        self.update_ui()

    def action_save_game(self) -> None:
        self.engine.save_game()
        self.query_one("#game-log", Log).write_line("[bold green]SYSTEM: Game state persisted to disk.[/]")

    def update_ui(self) -> None:
        """Centralized UI refresh logic."""
        p = self.engine.state.player
        
        # Update Stats
        stats_text = (
            f"Name: [bold]{p.name}[/]\n"
            f"HP: [green]{p.hp}[/]/[green]{p.max_hp}[/]\n"
            f"XP: {p.level * 100}"
        )
        self.query_one("#stats-display", StatDisplay).renderable = stats_text
        
        # Update Inventory
        inv_text = "\n".join([f"• {item}" for item in p.inventory]) if p.inventory else "Empty"
        self.query_one("#inventory-display", StatDisplay).renderable = inv_text

        # Synchronize Log
        log_widget = self.query_one("#game-log", Log)
        if self.engine.state.log:
            log_widget.write_line(self.engine.state.log[-1])

if __name__ == "__main__":
    app = ChronosApp()
    app.run()

if __name__ == "__main__":
    app = ChronosApp()
    app.run()

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Log, Static, Label, Input
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from engine.core import GameEngine
from ai.orchestrator import PromptOrchestrator
from ai.client import AIClient

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
                yield Log(id="game-log")
                with Container(id="input-container"):
                    yield Input(placeholder="What do you do? (e.g., 'examine the gears')", id="player-input")
        yield Footer()

    async def on_mount(self) -> None:
        # Initialize campaign logic
        self.engine.initialize_campaign()
        self.engine.state.player.add_item("Health Potion")
        self.update_ui()
        self.query_one("#player-input").focus()
        
        # Initial AI Narration
        await self.process_narrative("I awaken.")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle player input submissions."""
        command = event.value.strip()
        if command:
            self.query_one("#game-log", Log).write_line(f"\n[yellow]> {command}[/]")
            event.input.value = ""
            
            # Record the turn and update state
            self.engine.state.turn_count += 1
            self.engine.state.add_log(f"PLAYER: {command}")
            
            # Process AI Narrative
            await self.process_narrative(command)
            
            self.update_ui()

    async def process_narrative(self, user_input: str) -> None:
        """Triggers the AI orchestration and updates the log."""
        log_widget = self.query_one("#game-log", Log)
        log_widget.write_line("[italic]The air shimmers as the narrator speaks...[/]")
        
        # 1. Build Payload
        payload = self.orchestrator.build_payload(self.engine.state, user_input)
        
        # 2. Call AI
        response = await self.ai_client.generate_narrative(payload)
        
        # 3. Add to Log & State
        self.engine.state.add_log(f"NARRATOR: {response}")
        log_widget.write_line(f"\n{response}")

    def action_use_potion(self) -> None:
        self.engine.use_potion()
        self.update_ui()

    def action_save_game(self) -> None:
        self.engine.save_game()
        self.query_one("#game-log", Log).write_line("[bold green]SYSTEM: Chronicle saved to disk.[/]")

    def update_ui(self) -> None:
        """Centralized UI refresh logic."""
        p = self.engine.state.player
        
        # Update Stats
        stats_text = (
            f"Name: [bold]{p.name}[/]\n"
            f"Location: [cyan]{self.engine.state.current_location}[/]\n"
            f"HP: [green]{p.hp}[/]/[green]{p.max_hp}[/]\n"
            f"Lineage: {p.lineage}\n"
            f"Turn: {self.engine.state.turn_count}"
        )
        self.query_one("#stats-display", StatDisplay).renderable = stats_text
        
        # Update Inventory & Quests
        inv_list = [f"• {item}" for item in p.inventory]
        quest_list = [f"! {q.name} ({q.status})" for q in self.engine.state.quests.values()]
        
        display_text = "[bold]INVENTORY[/]\n" + ("\n".join(inv_list) if inv_list else "Empty")
        display_text += "\n\n[bold]ACTIVE QUESTS[/]\n" + ("\n".join(quest_list) if quest_list else "None")
        
        self.query_one("#inventory-display", StatDisplay).renderable = display_text

if __name__ == "__main__":
    app = ChronosApp()
    app.run()

if __name__ == "__main__":
    app = ChronosApp()
    app.run()

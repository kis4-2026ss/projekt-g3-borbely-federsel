import os

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

_SAVE_FILE = "savegame.json"

_TITLE = """\
[bold gold1] ██████╗██╗  ██╗██████╗  ██████╗ ███╗   ██╗ ██████╗ ███████╗[/]
[bold gold1]██╔════╝██║  ██║██╔══██╗██╔═══██╗████╗  ██║██╔═══██╗██╔════╝[/]
[bold gold1]██║     ███████║██████╔╝██║   ██║██╔██╗ ██║██║   ██║███████╗[/]
[bold gold1]██║     ██╔══██║██╔══██╗██║   ██║██║╚██╗██║██║   ██║╚════██║[/]
[bold gold1]╚██████╗██║  ██║██║  ██║╚██████╔╝██║ ╚████║╚██████╔╝███████║[/]
[bold gold1] ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝ ╚══════╝[/]
[dim]                    T  E  R  M  I  N  A  L    E  N  G  I  N  E[/]"""

_LORE = """\
[italic dim]The Eternal Aurelian Empire has fallen.
Its crystal spires crack and weep with forgotten magic.
You are an exile — last heir of a broken bloodline —
awakening in the rubble of what once was.[/]"""


class IntroScreen(Screen[str]):
    """Static intro screen shown before any LLM call is made.
    Dismisses with 'new' or 'load' so ChronosApp.on_mount can branch."""

    CSS = """
    IntroScreen {
        align: center middle;
        background: #080808;
    }
    #intro-panel {
        width: 74;
        height: auto;
        background: #141414;
        border: double #ffd700;
        padding: 2 3;
        align: center middle;
    }
    #title-art {
        text-align: center;
        margin-bottom: 1;
    }
    #separator {
        text-align: center;
        color: $accent;
        margin-bottom: 1;
    }
    #lore-text {
        text-align: center;
        margin-bottom: 2;
    }
    .menu-item {
        text-align: center;
        height: 1;
        margin-bottom: 1;
    }
    #hints-text {
        text-align: center;
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("n", "new_game", "New Chronicle", show=False),
        Binding("c", "continue_game", "Continue Chronicle", show=False),
        Binding("enter", "new_game", "Start", show=False),
    ]

    def compose(self) -> ComposeResult:
        save_exists = os.path.exists(_SAVE_FILE)
        hint = "[dim]Press [bold white][[N]][/] for a new game"
        if save_exists:
            hint += "  or  [bold white][[C]][/] to continue your chronicle"
        hint += "[/]"

        with Vertical(id="intro-panel"):
            yield Static(_TITLE, id="title-art", markup=True)
            yield Static("─" * 68, id="separator", markup=True)
            yield Static(_LORE, id="lore-text", markup=True)
            yield Static(
                "[bold green][[N]]  Begin New Chronicle[/]",
                classes="menu-item",
                markup=True,
            )
            if save_exists:
                yield Static(
                    "[bold cyan][[C]]  Continue Chronicle[/]",
                    classes="menu-item",
                    markup=True,
                )
            yield Static(hint, id="hints-text", markup=True)

    def action_new_game(self) -> None:
        self.dismiss("new")

    def action_continue_game(self) -> None:
        if os.path.exists(_SAVE_FILE):
            self.dismiss("load")
        else:
            self.dismiss("new")

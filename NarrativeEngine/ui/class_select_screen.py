"""Class-selection modal — shown at the start of every new chronicle.

The player presses 1–4 to pick an archetype. The screen dismisses itself with
the chosen archetype key string (e.g. "fighter"), which app.py forwards to
engine.archetypes.apply_archetype().
"""

import json
import os

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from engine.archetypes import ARCHETYPES


def _build_action_labels() -> dict:
    """Load action id → display name from combat_actions.json (one-time at import)."""
    path = os.path.join(os.path.dirname(__file__), "..", "engine", "combat_actions.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        labels: dict = {}
        for entries in data.values():
            if isinstance(entries, list):
                for entry in entries:
                    if "id" in entry and "name" in entry:
                        labels[entry["id"]] = entry["name"]
        return labels
    except Exception:
        return {}


_ACTION_LABELS = _build_action_labels()

_ARCHETYPE_ORDER = ["fighter", "mage", "monk", "rogue"]


class ClassSelectScreen(ModalScreen[str]):
    """Full-screen modal for archetype selection."""

    CSS = """
    ClassSelectScreen {
        align: center middle;
        background: rgba(0,0,0,0.85);
    }

    #class-panel {
        width: 72;
        height: auto;
        background: #0d0d0d;
        border: double $accent;
        padding: 1 2;
    }

    #class-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        height: 1;
        margin-bottom: 1;
    }

    .class-row {
        height: auto;
        margin-bottom: 1;
        padding: 0 1;
        border-left: thick $accent;
    }

    #class-footer {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
        height: 1;
    }
    """

    BINDINGS = [
        Binding("1", "choose_fighter", "Fighter", priority=True, show=False),
        Binding("2", "choose_mage",    "Mage",    priority=True, show=False),
        Binding("3", "choose_monk",    "Monk",    priority=True, show=False),
        Binding("4", "choose_rogue",   "Rogue",   priority=True, show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="class-panel"):
            yield Static(
                "⚔  C H O O S E  Y O U R  P A T H  ⚔",
                id="class-title",
                markup=True,
            )
            for num, arch_key in enumerate(_ARCHETYPE_ORDER, start=1):
                arch = ARCHETYPES[arch_key]
                action_names = "  /  ".join(
                    _ACTION_LABELS.get(a, a.replace("_", " ").title())
                    for a in arch["actions"]
                )
                yield Static(
                    f"[bold yellow][{num}][/]  [bold]{arch['display_name']}[/]\n"
                    f"     [dim]{arch['description']}[/]\n"
                    f"     [dim cyan]Combat: {action_names}[/]",
                    classes="class-row",
                    markup=True,
                )
            yield Static(
                "[dim]Press [bold]1[/bold]–[bold]4[/bold] to choose your path."
                "  Your choice shapes every fight.[/]",
                id="class-footer",
                markup=True,
            )

    def action_choose_fighter(self) -> None:
        self.dismiss("fighter")

    def action_choose_mage(self) -> None:
        self.dismiss("mage")

    def action_choose_monk(self) -> None:
        self.dismiss("monk")

    def action_choose_rogue(self) -> None:
        self.dismiss("rogue")

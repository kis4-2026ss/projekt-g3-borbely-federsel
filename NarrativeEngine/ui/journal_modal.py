"""Journal modal — press Ctrl+J to open.

Two-panel view:
  Left  — Chronicle of Events: story_history PlotPoints, newest first.
  Right — Known World: every discovered Location with description + connections.
"""

from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class JournalModal(ModalScreen[None]):
    """Read-only journal overlay.  No state mutations happen here."""

    CSS = """
    JournalModal {
        width: 100%;
        height: 100%;
    }

    #journal-panel {
        width: 100%;
        height: 100%;
        background: #1a1a1a;
        border: thick $accent;
        padding: 0;
    }

    #journal-title {
        width: 1fr;
        height: 3;
        content-align: center middle;
        background: #2a1a0a;
        color: gold;
        text-style: bold;
        border-bottom: solid $accent;
    }

    #journal-columns {
        height: 1fr;
        layout: horizontal;
    }

    #events-panel {
        width: 1fr;
        height: 1fr;
        border-right: solid $accent;
        padding: 1 1;
        overflow-y: auto;
    }

    #locations-panel {
        width: 1fr;
        height: 1fr;
        padding: 1 1;
        overflow-y: auto;
    }

    .journal-section-header {
        text-style: bold underline;
        color: $accent;
        margin-bottom: 1;
    }

    .plot-entry {
        margin-bottom: 1;
        color: #cccccc;
        width: 100%;
    }

    .plot-event {
        color: gold;
    }

    .plot-meta {
        color: #888888;
    }

    .location-entry {
        margin-bottom: 1;
        color: #cccccc;
        width: 100%;
    }

    .location-name {
        color: $accent;
        text-style: bold;
    }

    .location-desc {
        color: #aaaaaa;
    }

    .location-conn {
        color: #666666;
    }

    #journal-hints {
        height: 2;
        content-align: center middle;
        background: #111111;
        border-top: solid $accent;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Esc Close", show=True, priority=True),
        Binding("ctrl+j", "close", "^J Close", show=False, priority=True),
    ]

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def compose(self) -> ComposeResult:
        state = self.engine.state
        with Vertical(id="journal-panel"):
            yield Static(
                "✦  CHRONICLE OF ELOWEN  ✦",
                id="journal-title",
                markup=True,
            )
            with Horizontal(id="journal-columns"):
                with VerticalScroll(id="events-panel"):
                    yield Label("EVENTS", classes="journal-section-header")
                    yield from self._build_events(state)
                with VerticalScroll(id="locations-panel"):
                    yield Label("KNOWN WORLD", classes="journal-section-header")
                    yield from self._build_locations(state)
            yield Static(
                "[dim]\\[Esc][/]  Close Journal",
                id="journal-hints",
                markup=True,
            )

    # ── Content builders ──────────────────────────────────────────────────

    def _build_events(self, state):
        if not state.story_history:
            yield Label("[dim](No events recorded yet.)[/]", markup=True)
            return

        seen_events: set = set()
        for pp in reversed(state.story_history):
            # Deduplicate: skip entries whose event text is identical to one
            # already shown (normalised to lowercase, stripped whitespace).
            key = pp.event.strip().lower()
            if key in seen_events:
                continue
            seen_events.add(key)

            # Format timestamp to something readable
            try:
                dt = datetime.fromisoformat(pp.timestamp)
                ts = dt.strftime("%d %b  %H:%M")
            except (ValueError, AttributeError):
                ts = ""

            lines = []
            ts_part = f"[dim]{ts}[/]  " if ts else ""
            lines.append(f"[gold1]★[/] {ts_part}[bold]{pp.event}[/]")

            if pp.choice_made and pp.choice_made.strip():
                lines.append(f"  [dim]›[/] [italic]{pp.choice_made}[/]")

            if pp.tags:
                tag_str = "  ".join(f"[{t}]" for t in pp.tags[:5])
                lines.append(f"  [dim cyan]{tag_str}[/]")

            yield Label("\n".join(lines), markup=True, classes="plot-entry")

    def _build_locations(self, state):
        if not state.locations:
            yield Label("[dim](No locations discovered yet.)[/]", markup=True)
            return

        current = state.current_location
        for loc_name, loc in sorted(state.locations.items()):
            here = " [bold yellow](here)[/]" if loc_name == current else ""
            lines = [f"[bold $accent]📍 {loc_name}[/]{here}"]

            if loc.description and not loc.description.startswith("(Newly"):
                lines.append(f"   [dim]{loc.description}[/]")

            if loc.connections:
                conns = ", ".join(loc.connections[:4])
                if len(loc.connections) > 4:
                    conns += ", …"
                lines.append(f"   [dim cyan]→ {conns}[/]")

            yield Label("\n".join(lines), markup=True, classes="location-entry")

    # ── Actions ───────────────────────────────────────────────────────────

    def action_close(self) -> None:
        self.dismiss()

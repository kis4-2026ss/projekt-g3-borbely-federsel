from typing import List

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static


class InventoryModal(ModalScreen[None]):
    """Modal inventory panel. Opened via Ctrl+I from the main app.

    Selecting an item and pressing E equips it (weapons/armor only).
    Pressing D drops it from inventory; if the item was equipped, the
    slot is also cleared. Escape closes the modal and triggers a parent
    UI refresh so the sidebar reflects any changes."""

    CSS = """
    InventoryModal {
        align: center middle;
    }
    #inventory-panel {
        width: 70;
        height: 26;
        background: #1e1e1e;
        border: thick $accent;
        padding: 1 2;
    }
    #equipped-header {
        height: auto;
        padding-bottom: 1;
        border-bottom: dim $accent;
    }
    #inventory-list {
        height: 1fr;
        margin-top: 1;
        background: #121212;
    }
    #status-line {
        height: 1;
        margin-top: 1;
    }
    #key-hints {
        height: 1;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("e", "equip", "Equip", show=True),
        Binding("d", "drop", "Drop", show=True),
    ]

    def __init__(self, engine, refresh_parent):
        super().__init__()
        self.engine = engine
        self.refresh_parent = refresh_parent
        # Names in the same order as ListView children, so the selected
        # index always maps back to the right inventory entry.
        self._displayed_names: List[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="inventory-panel"):
            yield Static(self._render_equipped(), id="equipped-header", markup=True)
            yield ListView(*self._build_list_items(), id="inventory-list")
            yield Static("", id="status-line", markup=True)
            yield Static(
                "[dim][E] Equip   [D] Drop   [Esc] Close[/]",
                id="key-hints",
                markup=True,
            )

    # ── Rendering ─────────────────────────────────────────────────────────

    def _render_equipped(self) -> str:
        p = self.engine.state.player
        lines = ["[bold]EQUIPPED[/]"]
        if p.equipped_weapon:
            w = p.equipped_weapon
            bonus = f"+{w.hit_bonus}" if w.hit_bonus >= 0 else str(w.hit_bonus)
            lines.append(f"  ⚔ {w.name}  [{w.damage_dice} {bonus}]")
        else:
            lines.append("  ⚔ [dim](no weapon)[/]")
        if p.equipped_armor:
            a = p.equipped_armor
            lines.append(f"  🛡 {a.name}  [+{a.ac_bonus} AC]")
        else:
            lines.append("  🛡 [dim](no armor)[/]")
        return "\n".join(lines)

    def _build_list_items(self) -> List[ListItem]:
        # Late import: ui.app imports this module via action_open_inventory,
        # so importing it here at module top would create a circular dependency.
        from ui.app import _render_inventory_item

        state = self.engine.state
        self._displayed_names = []
        if not state.player.inventory:
            return [ListItem(Label("[dim](empty)[/]", markup=True))]
        items: List[ListItem] = []
        for name in state.player.inventory:
            rendered = _render_inventory_item(
                state, name, is_equipped=self.engine.is_equipped(name)
            )
            items.append(ListItem(Label(rendered, markup=True)))
            self._displayed_names.append(name)
        return items

    def _refresh_list(self) -> None:
        lv = self.query_one("#inventory-list", ListView)
        prior_index = lv.index
        lv.clear()
        for item in self._build_list_items():
            lv.append(item)
        if self._displayed_names:
            if prior_index is not None and prior_index < len(self._displayed_names):
                lv.index = prior_index
            else:
                lv.index = 0
        self.query_one("#equipped-header", Static).update(self._render_equipped())

    def _selected_item_name(self):
        lv = self.query_one("#inventory-list", ListView)
        if lv.index is None or lv.index >= len(self._displayed_names):
            return None
        return self._displayed_names[lv.index]

    def _set_status(self, text: str, success: bool = True) -> None:
        color = "green" if success else "red"
        self.query_one("#status-line", Static).update(f"[{color}]{text}[/]")

    # ── Actions ───────────────────────────────────────────────────────────

    def action_equip(self) -> None:
        name = self._selected_item_name()
        if name is None:
            self._set_status("No item selected.", success=False)
            return
        ok, msg = self.engine.equip_item_from_inventory(name)
        self._set_status(msg, success=ok)
        if ok:
            self._refresh_list()

    def action_drop(self) -> None:
        name = self._selected_item_name()
        if name is None:
            self._set_status("No item selected.", success=False)
            return
        ok, msg = self.engine.drop_item(name)
        self._set_status(msg, success=ok)
        if ok:
            self._refresh_list()

    def action_close(self) -> None:
        self.refresh_parent()
        self.dismiss()

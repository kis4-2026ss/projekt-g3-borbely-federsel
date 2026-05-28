"""Merchant shop modal — opens automatically when the LLM emits open_shop,
or manually via Ctrl+M when a merchant is present at the current location.

Layout:
  Header  — merchant name + greeting + player's current gold
  List    — scrollable item list (name, price, stock, description)
  Footer  — status message + key hints

Buying deducts gold and adds the item to inventory immediately (no LLM call).
If the item has a registered item_id in item_registry, its full mechanical
properties (weapon stats, armor AC, heal_amount) are applied on purchase.
"""

from typing import List, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from engine.models import Armor, Merchant, MerchantItem, Weapon


class MerchantModal(ModalScreen[None]):
    """Shop overlay for a single Merchant."""

    CSS = """
    MerchantModal {
        align: center middle;
    }

    #shop-panel {
        width: 78;
        height: 32;
        background: #1e1510;
        border: thick $accent;
        padding: 0;
    }

    #shop-header {
        height: auto;
        padding: 1 2;
        background: #2a1e10;
        border-bottom: solid $accent;
        color: #dddddd;
    }

    #shop-gold {
        color: yellow;
        text-style: bold;
    }

    #shop-list {
        height: 1fr;
        background: #181210;
        margin: 0 1;
        color: #cccccc;
    }

    #shop-status {
        height: 1;
        padding: 0 2;
        color: #cccccc;
    }

    #shop-hints {
        height: 1;
        text-align: center;
        color: #888888;
        background: #111111;
        border-top: solid $accent;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Esc Close", show=True),
        Binding("ctrl+b", "buy", "^B Buy", show=True, priority=True),
        Binding("enter", "buy", "Enter Buy", show=False, priority=True),
    ]

    def __init__(self, engine, merchant_key: str, refresh_parent):
        super().__init__()
        self.engine = engine
        self.merchant_key = merchant_key
        self.refresh_parent = refresh_parent
        self._item_indices: List[int] = []   # maps list position → stock index

    # ── Property helpers ──────────────────────────────────────────────────

    @property
    def _merchant(self) -> Optional[Merchant]:
        return self.engine.state.merchants.get(self.merchant_key)

    # ── Compose ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        merchant = self._merchant
        if merchant is None:
            # Shouldn't happen, but guard gracefully
            with Vertical(id="shop-panel"):
                yield Static("[bold red]No merchant found.[/]", markup=True)
                yield Static("Esc  Close", id="shop-hints")
            return

        with Vertical(id="shop-panel"):
            yield Static(self._render_header(merchant), id="shop-header", markup=True)
            yield ListView(*self._build_list_items(merchant), id="shop-list")
            yield Static("", id="shop-status", markup=True)
            yield Static(
                "^B / Enter  Buy   Esc  Close",
                id="shop-hints",
            )

    # ── Rendering ─────────────────────────────────────────────────────────

    def _render_header(self, merchant: Merchant) -> str:
        gold = self.engine.state.player.gold
        lines = [
            f"[bold yellow]🏪  {merchant.name.upper()}[/]",
            f'[dim italic]"{merchant.greeting}"[/]',
            f"",
            f"[bold]Your gold:[/]  [yellow]💰 {gold}[/]",
        ]
        return "\n".join(lines)

    def _build_list_items(self, merchant: Merchant) -> List[ListItem]:
        self._item_indices = []
        items: List[ListItem] = []
        player_gold = self.engine.state.player.gold

        for idx, entry in enumerate(merchant.stock):
            if entry.quantity == 0:
                continue   # out of stock — skip entirely
            self._item_indices.append(idx)

            icon = self._item_icon(entry)
            qty_str = "∞" if entry.quantity < 0 else str(entry.quantity)
            affordable = player_gold >= entry.price
            price_color = "green" if affordable else "red"

            desc_part = (
                f"  [dim]{entry.description[:50]}[/]"
                if entry.description else ""
            )
            line = (
                f"{icon} [bold]{entry.item_name}[/]"
                f"  [{price_color}]{entry.price}g[/]"
                f"  [dim cyan]{qty_str}[/]"
                f"{desc_part}"
            )
            items.append(ListItem(Label(line, markup=True)))

        if not items:
            items.append(ListItem(Label("[dim](No items available.)[/]", markup=True)))
            self._item_indices = []

        return items

    def _item_icon(self, entry: MerchantItem) -> str:
        state = self.engine.state
        # Check item_registry for item type
        if entry.item_id:
            defn = state.item_registry.get(entry.item_id.lower().replace(" ", "_"))
            if defn:
                return {
                    "weapon": "⚔",
                    "armor": "🛡",
                    "consumable": "🧪",
                    "quest": "★",
                    "lore": "📜",
                }.get(defn.item_type, "•")
        return "•"

    def _refresh_list(self) -> None:
        merchant = self._merchant
        if merchant is None:
            return
        lv = self.query_one("#shop-list", ListView)
        prior_index = lv.index
        lv.clear()
        for item in self._build_list_items(merchant):
            lv.append(item)
        if self._item_indices and prior_index is not None:
            new_idx = min(prior_index, len(self._item_indices) - 1)
            if new_idx >= 0:
                lv.index = new_idx
        # Also refresh the gold line in the header
        self.query_one("#shop-header", Static).update(self._render_header(merchant))

    def _selected_stock_index(self) -> Optional[int]:
        lv = self.query_one("#shop-list", ListView)
        if lv.index is None or lv.index >= len(self._item_indices):
            return None
        return self._item_indices[lv.index]

    def _set_status(self, text: str, success: bool = True) -> None:
        color = "bold green" if success else "bold red"
        self.query_one("#shop-status", Static).update(f"[{color}]{text}[/]")

    # ── Buy logic ─────────────────────────────────────────────────────────

    def action_buy(self) -> None:
        merchant = self._merchant
        if merchant is None:
            return

        stock_idx = self._selected_stock_index()
        if stock_idx is None:
            self._set_status("Select an item first.", success=False)
            return

        entry = merchant.stock[stock_idx]
        state = self.engine.state
        player = state.player

        if player.gold < entry.price:
            self._set_status(
                f"Not enough gold! Need {entry.price}g, have {player.gold}g.",
                success=False,
            )
            return

        # Deduct gold
        player.gold -= entry.price

        # Add item — use item_registry properties if available
        item_id = entry.item_id.strip().lower().replace(" ", "_") if entry.item_id else ""
        defn = state.item_registry.get(item_id) if item_id else None

        if defn:
            # Full mechanical item — mirror give_defined_item logic
            if defn.item_type in ("lore", "quest") and defn.name in player.inventory:
                player.gold += entry.price   # refund — can't buy a second unique
                self._set_status("You already possess that item.", success=False)
                return
            player.add_item(defn.name)
            if defn.item_type == "weapon" and defn.damage_dice:
                player.equipped_weapon = Weapon(
                    name=defn.name,
                    damage_dice=defn.damage_dice,
                    hit_bonus=defn.hit_bonus,
                    damage_type=defn.damage_type,
                    description=defn.description,
                )
                self._set_status(f"Bought {defn.name} and equipped it! (-{entry.price}g)")
            elif defn.item_type == "armor":
                player.equipped_armor = Armor(
                    name=defn.name,
                    ac_bonus=defn.ac_bonus,
                    description=defn.description,
                )
                self._set_status(f"Bought {defn.name} and equipped it! (-{entry.price}g)")
            else:
                self._set_status(f"Bought {defn.name}!  (-{entry.price}g)")
        else:
            # Ad-hoc item — just add by name
            player.add_item(entry.item_name)
            self._set_status(f"Bought {entry.item_name}!  (-{entry.price}g)")

        # Decrement stock if limited
        if entry.quantity > 0:
            entry.quantity -= 1

        self._refresh_list()
        self.refresh_parent()

    # ── Actions ───────────────────────────────────────────────────────────

    def action_close(self) -> None:
        self.refresh_parent()
        self.dismiss()

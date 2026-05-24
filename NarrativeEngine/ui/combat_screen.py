"""Dedicated D&D-style combat UI.

Opened automatically when in_combat becomes True (from app.py).
The player picks one action per turn from a numbered menu:

  Universal (all archetypes):
    1  — Strike       standard attack (STR-based)
    ^U — Use Item     consume first usable item in inventory
    R  — Flee         end combat, small HP penalty
    Esc — close overlay (combat state preserved)

  Slots [2]–[4] are archetype-specific:
    Fighter  — Cleave / Second Wind / Defend
    Mage     — Arcane Bolt / Mana Shield / Evade
    Monk     — Flurry / Iron Body / Meditate
    Rogue    — Backstab / Smoke Screen / Poison Strike
    (none)   — Power Strike / Evade / Defend  (fallback)
"""

from typing import Dict, List, Optional, Tuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static, RichLog

# Maps archetype key → (slot2_label, slot3_label, slot4_label) shown in the hints bar
_ARCHETYPE_LABELS: Dict[str, Tuple[str, str, str]] = {
    "fighter": ("[2] Cleave",       "[3] Second Wind", "[4] Defend"),
    "mage":    ("[2] Arcane Bolt",  "[3] Mana Shield", "[4] Evade"),
    "monk":    ("[2] Flurry",       "[3] Iron Body",   "[4] Meditate"),
    "rogue":   ("[2] Backstab",     "[3] Smoke Screen","[4] Poison Strike"),
    "":        ("[2] Power Strike", "[3] Evade",       "[4] Defend"),  # fallback
}


class CombatScreen(ModalScreen[None]):
    """Full-screen modal that surfaces the active combat encounter."""

    CSS = """
    CombatScreen {
        align: center middle;
    }

    #combat-panel {
        width: 92%;
        height: 92%;
        min-width: 60;
        max-width: 110;
        background: #0d0d0d;
        border: double red;
        padding: 1 2;
    }

    #combat-title {
        text-align: center;
        text-style: bold;
        color: red;
        height: 1;
        margin-bottom: 1;
    }

    #enemy-section {
        height: auto;
        background: #1a0000;
        border: tall $error;
        padding: 0 1;
        margin-bottom: 1;
    }

    #player-section {
        height: auto;
        background: #001a00;
        border: tall $success;
        padding: 0 1;
        margin-bottom: 1;
    }

    #combat-log {
        height: 1fr;
        background: #000000;
        border: solid $accent;
        margin-bottom: 1;
        scrollbar-gutter: stable;
    }

    #action-hints {
        height: 2;
        text-align: center;
        color: $text-muted;
    }

    #combat-status {
        height: 1;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("1",       "strike",         "1 Strike",   priority=True, show=False),
        Binding("2",       "slot2",          "2 Action",   priority=True, show=False),
        Binding("3",       "slot3",          "3 Action",   priority=True, show=False),
        Binding("4",       "slot4",          "4 Action",   priority=True, show=False),
        Binding("ctrl+u",  "use_item",       "^U Use Item",priority=True, show=False),
        Binding("r",       "flee",           "R Flee",     priority=True, show=False),
        Binding("escape",  "dismiss_combat", "Esc Close",  show=False),
    ]

    def __init__(self, engine, refresh_parent):
        super().__init__()
        self.engine = engine
        self.refresh_parent = refresh_parent
        self.round = 0

    # ── Compose ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="combat-panel"):
            yield Static("⚔  C O M B A T  ⚔", id="combat-title", markup=True)
            yield Static(self._render_enemy_block(), id="enemy-section", markup=True)
            yield Static(self._render_player_block(), id="player-section", markup=True)
            yield RichLog(id="combat-log", wrap=True, markup=True)
            yield Static("", id="combat-status", markup=True)
            yield Static(
                "[dim][1] Strike  [2] Power Strike  [3] Evade  [4] Defend[/]\n"
                "[dim][^U] Use Item  [R] Flee  [Esc] Close[/]",
                id="action-hints",
                markup=True,
            )

    def on_mount(self) -> None:
        """Populate the combat log with existing entries and set the action guide."""
        log = self.query_one("#combat-log", RichLog)
        state = self.engine.state
        for line in state.combat_log[-8:]:
            log.write(f"[dim]{line}[/]")
        if not state.combat_log:
            log.write("[italic dim]The battle begins…[/]")
        # Write archetype-aware guide to the log
        arch = state.player.archetype
        labels = _ARCHETYPE_LABELS.get(arch, _ARCHETYPE_LABELS[""])
        l0, l1, l2 = labels
        log.write(
            f"[dim cyan]━━  [1] Strike  │  {l0}  │  {l1}  │  {l2}  │  [^U] Item  ━━[/]"
        )
        # Initialise hints bar to match the archetype
        self._update_hints()

    # ── Rendering ─────────────────────────────────────────────────────────

    def _render_enemy_block(self) -> str:
        state = self.engine.state
        if not state.active_enemies:
            return "[dim](no enemies)[/]"
        lines = []
        for i, enemy in enumerate(state.active_enemies):
            marker = "[bold yellow]▶[/] " if i == 0 else "  "
            lines.append(
                f"{marker}[bold red]{enemy.name}[/]  "
                f"[dim]Lv.{enemy.level}  AC {enemy.ac}  ATK +{enemy.attack_bonus}"
                f"  DMG {enemy.damage_dice}+{enemy.damage_bonus}[/]"
            )
            lines.append(_hp_bar(enemy.hp, enemy.max_hp, label="HP"))
        return "\n".join(lines)

    def _render_player_block(self) -> str:
        state = self.engine.state
        p = state.player
        weapon_str = (
            f"{p.equipped_weapon.name} [{p.equipped_weapon.damage_dice}]"
            if p.equipped_weapon else "(no weapon)"
        )
        armor_str = (
            f"{p.equipped_armor.name} [AC+{p.equipped_armor.ac_bonus}]"
            if p.equipped_armor else "(no armor)"
        )
        consumables = self._consumable_names()
        inv_str = ", ".join(consumables) if consumables else "(none)"
        if p.temp_ac_bonus:
            bonus_label = "Iron Body" if p.archetype == "monk" else "Defending"
            ac_str = f"{p.ac} [bold green](+{p.temp_ac_bonus} {bonus_label})[/]"
        else:
            ac_str = str(p.ac)
        arch_label = f"  [{p.archetype.title()}]" if p.archetype else ""

        lines = [
            f"[bold green]{p.name}[/]{arch_label}  "
            f"[dim]Lv.{p.level}  AC {ac_str}  Prof +{p.proficiency_bonus}"
            f"  XP {p.experience}/{p.xp_to_next_level}[/]",
            _hp_bar(p.hp, p.max_hp, label="HP"),
            f"  ⚔ {weapon_str}   🛡 {armor_str}",
            f"  🧪 Usable: {inv_str}",
        ]
        return "\n".join(lines)

    def _consumable_names(self) -> List[str]:
        state = self.engine.state
        return [name for name in state.player.inventory if self._is_usable(name)]

    def _is_usable(self, item_name: str) -> bool:
        item = next(
            (it for it in self.engine.state.item_registry.values() if it.name == item_name),
            None,
        )
        if item is None:
            return False
        if item.item_type == "consumable" and item.heal_amount > 0:
            return True
        if item.item_type == "combat":
            return True
        return False

    def _update_hints(self) -> None:
        """Refresh the action-hints bar to match the current archetype."""
        arch = self.engine.state.player.archetype
        labels = _ARCHETYPE_LABELS.get(arch, _ARCHETYPE_LABELS[""])
        l0, l1, l2 = labels
        line1 = f"[dim][1] Strike  {l0}  {l1}  {l2}[/]"
        line2 = "[dim][^U] Use Item  [R] Flee  [Esc] Close[/]"
        self.query_one("#action-hints", Static).update(f"{line1}\n{line2}")

    def _refresh_panels(self) -> None:
        self.query_one("#enemy-section", Static).update(self._render_enemy_block())
        self.query_one("#player-section", Static).update(self._render_player_block())
        self._update_hints()

    def _set_status(self, text: str, color: str = "white") -> None:
        self.query_one("#combat-status", Static).update(f"[{color}]{text}[/]")

    def _log(self, text: str) -> None:
        self.query_one("#combat-log", RichLog).write(text)

    # ── Shared action helpers ─────────────────────────────────────────────

    def _guard(self) -> bool:
        """Return True (abort) if the player is dead or there are no enemies."""
        state = self.engine.state
        if state.player.hp <= 0:
            self._log("[bold red]You are already dead.[/]")
            return True
        if not state.in_combat or not state.active_enemies:
            self._set_status("No enemies to fight.", "yellow")
            return True
        return False

    def _start_round(self) -> None:
        """Housekeeping at the top of every player action."""
        self.round += 1
        # Clear the previous turn's Defend bonus before the new action resolves
        self.engine.state.player.temp_ac_bonus = 0

    def _roll_initiative_and_log(self, enemy) -> bool:
        """Roll initiative, log the result, return True if player acts first."""
        from engine.combat import CombatManager
        cm = CombatManager(self.engine.state)
        p_init, e_init = cm.roll_initiative()
        player_first = p_init.total >= e_init.total
        who = "You act first." if player_first else f"{enemy.name} acts first!"
        self._log(
            f"[bold]Round {self.round}[/]  "
            f"🎯 Initiative: You [bold]{p_init.total}[/] vs "
            f"{enemy.name} [bold]{e_init.total}[/] — {who}"
        )
        return player_first

    def _do_enemy_counter(self, cm, enemy) -> bool:
        """Run enemy counter-attack. Returns True if player died."""
        from engine.combat import _award_xp_for_kill  # noqa: F401 — unused here but imported for clarity
        enemy_rolls = cm.resolve_enemy_attack(enemy)
        for r in enemy_rolls:
            self._log(_format_roll_line(r))
        if self.engine.state.player.hp <= 0:
            self._log("[bold red]You have fallen in battle.[/]")
            self._refresh_panels()
            self.refresh_parent()
            self.app._handle_player_death()
            self.dismiss()
            return True
        return False

    def _check_enemy_dead(self, enemy) -> bool:
        """Award XP and clean up if enemy died. Returns True if enemy is dead."""
        if enemy.hp > 0:
            return False
        from engine.combat import _award_xp_for_kill
        state = self.engine.state
        xp = _award_xp_for_kill(state, enemy)
        state.active_enemies = [e for e in state.active_enemies if e is not enemy]
        if not state.active_enemies:
            state.in_combat = False
            state.in_aftermath = True   # trigger one aftermath narration turn
        state.add_log(f"COMBAT: {enemy.name} has been defeated! Gained {xp} XP.")
        state.combat_log.append(f"{enemy.name} defeated! +{xp} XP")
        self._log(f"[bold yellow]{enemy.name} has been defeated! +{xp} XP[/]")
        return True

    def _finish_round(self, enemy, enemy_died: bool) -> None:
        """Refresh UI and handle end-of-round state."""
        self._refresh_panels()
        self.refresh_parent()
        if enemy_died and not self.engine.state.in_combat:
            self._set_status("Victory! All enemies defeated.", "green")
            self.call_after_refresh(self._close_after_victory)
        elif not enemy_died:
            state = self.engine.state
            p = state.player
            self._set_status(
                f"Round {self.round}  │  "
                f"{enemy.name}: {enemy.hp}/{enemy.max_hp} HP  │  "
                f"You: {p.hp}/{p.max_hp} HP",
                "white",
            )

    # ── Actions ───────────────────────────────────────────────────────────

    def action_strike(self) -> None:
        """Standard attack: 1d20 + STR + proficiency vs enemy AC."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()

        player_first = self._roll_initiative_and_log(enemy)

        if not player_first:
            # Enemy strikes first
            if self._do_enemy_counter(cm, enemy):
                return

        # Player attacks
        player_rolls = cm.resolve_player_attack(enemy)
        for r in player_rolls:
            self._log(_format_roll_line(r))

        enemy_died = self._check_enemy_dead(enemy)
        if enemy_died:
            self._finish_round(enemy, True)
            return

        if player_first:
            # Enemy counter-attack
            if self._do_enemy_counter(cm, enemy):
                return

        self._finish_round(enemy, False)

    def action_power_strike(self) -> None:
        """Power Strike: disadvantage to-hit, but double damage dice on hit."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()

        player_first = self._roll_initiative_and_log(enemy)

        if not player_first:
            if self._do_enemy_counter(cm, enemy):
                return

        # Player power-strikes
        self._log("[bold magenta]⚡ Power Strike![/]")
        player_rolls = cm.resolve_power_strike(enemy)
        for r in player_rolls:
            self._log(_format_roll_line(r))

        enemy_died = self._check_enemy_dead(enemy)
        if enemy_died:
            self._finish_round(enemy, True)
            return

        if player_first:
            if self._do_enemy_counter(cm, enemy):
                return

        self._finish_round(enemy, False)

    def action_evade(self) -> None:
        """Evade: DEX check vs DC 12.
        Success → player strikes AND enemy counter at disadvantage.
        Failure → player is off-balance, enemy counter at advantage.
        """
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()

        self._log(f"[bold cyan]🌀 Evade — Round {self.round}[/]")
        success, evade_roll = cm.resolve_evade()
        self._log(_format_roll_line(evade_roll))

        if success:
            self._log("[cyan]You slip the strike — and lunge back![/]")
            # Player attacks in the same round
            player_rolls = cm.resolve_player_attack(enemy)
            for r in player_rolls:
                self._log(_format_roll_line(r))
            enemy_died = self._check_enemy_dead(enemy)
            if enemy_died:
                self._finish_round(enemy, True)
                return
            # Enemy counter at disadvantage (flag already set by resolve_evade)
            if self._do_enemy_counter(cm, enemy):
                return
        else:
            self._log("[red]Caught off-balance — enemy strikes with advantage![/]")
            # No player attack; enemy has advantage (flag set by resolve_evade)
            if self._do_enemy_counter(cm, enemy):
                return

        self._finish_round(enemy, False)

    def action_defend(self) -> None:
        """Defend: raise AC by 2 + CON mod. If the enemy misses, riposte for free."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()

        ac_bonus = cm.resolve_defend()
        self._log(
            f"[bold blue]🛡 Defend — Round {self.round}  "
            f"(+{ac_bonus} AC  [dim]— miss = riposte[/])[/]"
        )

        # Run enemy attack inline so we can inspect the roll for riposte
        enemy_rolls = cm.resolve_enemy_attack(enemy)
        for r in enemy_rolls:
            self._log(_format_roll_line(r))

        if state.player.hp <= 0:
            self._log("[bold red]You have fallen in battle.[/]")
            self._refresh_panels()
            self.refresh_parent()
            self.app._handle_player_death()
            self.dismiss()
            return

        # Riposte: enemy's attack roll missed → free counter-attack
        attack_roll = enemy_rolls[0] if enemy_rolls else None
        if attack_roll is not None and attack_roll.success is False:
            self._log("[bold cyan]⚡ Riposte! Their swing found only air.[/]")
            riposte_rolls = cm.resolve_player_attack(enemy)
            for r in riposte_rolls:
                self._log(_format_roll_line(r))
            enemy_died = self._check_enemy_dead(enemy)
            self._finish_round(enemy, enemy_died)
            return

        self._finish_round(enemy, False)

    def action_use_item(self) -> None:
        """Use the first available usable item (consumable or combat type)."""
        if self.engine.state.player.hp <= 0:
            return
        consumables = self._consumable_names()
        if not consumables:
            self._set_status("No usable items in inventory.", "yellow")
            return
        item_name = consumables[0]
        state = self.engine.state
        item = next(
            (it for it in state.item_registry.values() if it.name == item_name),
            None,
        )
        if item and item.item_type == "combat":
            self._apply_combat_item(item)
            state.player.remove_item(item.name)
        else:
            ok, msg = self.engine.use_consumable(item_name)
            if ok:
                self._log(f"[green]✚ {msg}[/]")
                self._set_status(msg, "green")
            else:
                self._set_status(msg, "red")
        self._refresh_panels()
        self.refresh_parent()

    def _apply_combat_item(self, item) -> None:
        """Resolve the mechanical effect of a combat-type item."""
        from engine.models import StatusEffect
        state = self.engine.state
        tags = item.tags if item.tags else []

        if "enemy_disadvantage" in tags:
            state.enemy_attack_adv = -1
            self._log(f"[cyan]💨 {item.name} — smoke fills the air! Enemy attack at disadvantage.[/]")
            self._set_status(f"{item.name} used — enemy at disadvantage.", "cyan")
        elif "player_advantage" in tags:
            buff = StatusEffect(
                name="Item Buff", duration_turns=1, advantage=1,
                description=f"{item.name} effect — advantage on next attack.",
            )
            state.player.status_effects = [
                e for e in state.player.status_effects if e.name != "Item Buff"
            ]
            state.player.status_effects.append(buff)
            self._log(f"[cyan]⚗️ {item.name} surges through you — advantage on next attack.[/]")
            self._set_status(f"{item.name} used — advantage granted.", "cyan")
        elif "thrown_weapon" in tags and item.damage_dice:
            from engine.combat import CombatManager
            if state.active_enemies:
                enemy = state.active_enemies[0]
                cm = CombatManager(state)
                rolls = cm.resolve_thrown_weapon(enemy, item.damage_dice)
                for r in rolls:
                    self._log(_format_roll_line(r))
                self._check_enemy_dead(enemy)
        else:
            self._log(f"[yellow]{item.name} used.[/]")

    # ── Archetype slot dispatchers ────────────────────────────────────────

    def action_slot2(self) -> None:
        """Dispatch [2] to the archetype-appropriate action."""
        arch = self.engine.state.player.archetype
        if arch == "fighter":
            self._do_cleave()
        elif arch == "mage":
            self._do_arcane_bolt()
        elif arch == "monk":
            self._do_flurry()
        elif arch == "rogue":
            self._do_backstab()
        else:
            self.action_power_strike()

    def action_slot3(self) -> None:
        """Dispatch [3] to the archetype-appropriate action."""
        arch = self.engine.state.player.archetype
        if arch == "fighter":
            self._do_second_wind()
        elif arch == "mage":
            self._do_mana_shield()
        elif arch == "monk":
            self._do_iron_body()
        elif arch == "rogue":
            self._do_smoke_screen()
        else:
            self.action_evade()

    def action_slot4(self) -> None:
        """Dispatch [4] to the archetype-appropriate action."""
        arch = self.engine.state.player.archetype
        if arch == "mage":
            self.action_evade()
        elif arch == "monk":
            self._do_meditate()
        elif arch == "rogue":
            self._do_poison_strike()
        else:
            self.action_defend()  # fighter + fallback

    # ── Fighter actions ───────────────────────────────────────────────────

    def _do_cleave(self) -> None:
        """Fighter — Cleave: attack all enemies at disadvantage."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        cm = CombatManager(state)
        self._start_round()
        self._log(f"[bold red]⚔ Cleave! — Round {self.round}[/]")

        enemies_snapshot = list(state.active_enemies)
        rolls = cm.resolve_cleave(enemies_snapshot)
        for r in rolls:
            self._log(_format_roll_line(r))

        # Check each enemy for death; track whether any survive
        all_dead = True
        for enemy in enemies_snapshot:
            if not self._check_enemy_dead(enemy):
                all_dead = False

        if all_dead or not state.active_enemies:
            self._finish_round(enemies_snapshot[0], True)
            return

        # Counter-attack from the first surviving enemy
        first_survivor = state.active_enemies[0]
        if self._do_enemy_counter(cm, first_survivor):
            return
        self._finish_round(first_survivor, False)

    def _do_second_wind(self) -> None:
        """Fighter — Second Wind: self-heal 1d10 + CON_mod; enemy still attacks."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()
        self._log(f"[bold green]💚 Second Wind — Round {self.round}[/]")
        rolls = cm.resolve_second_wind()
        for r in rolls:
            self._log(_format_roll_line(r))
        if self._do_enemy_counter(cm, enemy):
            return
        self._finish_round(enemy, False)

    # ── Mage actions ──────────────────────────────────────────────────────

    def _do_arcane_bolt(self) -> None:
        """Mage — Arcane Bolt: INT-based ranged attack."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()

        player_first = self._roll_initiative_and_log(enemy)
        if not player_first:
            if self._do_enemy_counter(cm, enemy):
                return

        self._log(f"[bold blue]✨ Arcane Bolt — Round {self.round}[/]")
        rolls = cm.resolve_arcane_bolt(enemy)
        for r in rolls:
            self._log(_format_roll_line(r))

        enemy_died = self._check_enemy_dead(enemy)
        if enemy_died:
            self._finish_round(enemy, True)
            return
        if player_first:
            if self._do_enemy_counter(cm, enemy):
                return
        self._finish_round(enemy, False)

    def _do_mana_shield(self) -> None:
        """Mage — Mana Shield: charge absorption; enemy still attacks."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()
        self._log(f"[bold blue]🔮 Mana Shield — Round {self.round}[/]")
        rolls = cm.resolve_mana_shield()
        for r in rolls:
            self._log(_format_roll_line(r))
        if self._do_enemy_counter(cm, enemy):
            return
        self._finish_round(enemy, False)

    # ── Monk actions ──────────────────────────────────────────────────────

    def _do_flurry(self) -> None:
        """Monk — Flurry of Blows: two quick 1d4+DEX attacks."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()

        player_first = self._roll_initiative_and_log(enemy)
        if not player_first:
            if self._do_enemy_counter(cm, enemy):
                return

        self._log(f"[bold yellow]👊 Flurry of Blows — Round {self.round}[/]")
        rolls = cm.resolve_flurry(enemy)
        for r in rolls:
            self._log(_format_roll_line(r))

        enemy_died = self._check_enemy_dead(enemy)
        if enemy_died:
            self._finish_round(enemy, True)
            return
        if player_first:
            if self._do_enemy_counter(cm, enemy):
                return
        self._finish_round(enemy, False)

    def _do_iron_body(self) -> None:
        """Monk — Iron Body: AC bonus + WIS temp HP; enemy still attacks."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()

        ac_bonus = cm.resolve_iron_body()
        self._log(
            f"[bold cyan]🗿 Iron Body — Round {self.round}  "
            f"(+{ac_bonus} AC[dim] — stance holds[/])[/]"
        )
        self._refresh_panels()  # show updated AC before enemy attacks
        if self._do_enemy_counter(cm, enemy):
            return
        self._finish_round(enemy, False)

    def _do_meditate(self) -> None:
        """Monk — Meditate: enemy at disadvantage; gain Focused for next round."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()
        self._log(f"[bold cyan]🧘 Meditate — Round {self.round}[/]")
        cm.resolve_meditate()
        self._log("[cyan]You centre yourself. Focused — enemy off-balance.[/]")
        if self._do_enemy_counter(cm, enemy):
            return
        self._finish_round(enemy, False)

    # ── Rogue actions ─────────────────────────────────────────────────────

    def _do_backstab(self) -> None:
        """Rogue — Backstab: auto-advantage + 1d6 sneak damage on hit."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()

        player_first = self._roll_initiative_and_log(enemy)
        if not player_first:
            if self._do_enemy_counter(cm, enemy):
                return

        self._log(f"[bold red]🗡 Backstab — Round {self.round}[/]")
        rolls = cm.resolve_backstab(enemy)
        for r in rolls:
            self._log(_format_roll_line(r))

        enemy_died = self._check_enemy_dead(enemy)
        if enemy_died:
            self._finish_round(enemy, True)
            return
        if player_first:
            if self._do_enemy_counter(cm, enemy):
                return
        self._finish_round(enemy, False)

    def _do_smoke_screen(self) -> None:
        """Rogue — Smoke Screen: no attack; enemy blind, player concealed."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()
        self._log(f"[bold yellow]💨 Smoke Screen — Round {self.round}[/]")
        cm.resolve_smoke_screen()
        self._log("[yellow]Smoke fills the gap. You vanish into it.[/]")
        if self._do_enemy_counter(cm, enemy):
            return
        self._finish_round(enemy, False)

    def _do_poison_strike(self) -> None:
        """Rogue — Poison Strike: normal attack + 1d4 poison bonus on hit."""
        if self._guard():
            return
        from engine.combat import CombatManager
        state = self.engine.state
        enemy = state.active_enemies[0]
        cm = CombatManager(state)
        self._start_round()

        player_first = self._roll_initiative_and_log(enemy)
        if not player_first:
            if self._do_enemy_counter(cm, enemy):
                return

        self._log(f"[bold green]☠ Poison Strike — Round {self.round}[/]")
        rolls = cm.resolve_poison_strike(enemy)
        for r in rolls:
            self._log(_format_roll_line(r))

        enemy_died = self._check_enemy_dead(enemy)
        if enemy_died:
            self._finish_round(enemy, True)
            return
        if player_first:
            if self._do_enemy_counter(cm, enemy):
                return
        self._finish_round(enemy, False)

    # ── Flee / dismiss ────────────────────────────────────────────────────

    def action_flee(self) -> None:
        """Attempt to flee: end combat, take a small HP penalty (10% max HP)."""
        if self.engine.state.player.hp <= 0:
            return
        state = self.engine.state
        flee_damage = max(1, state.player.max_hp // 10)
        state.player.take_damage(flee_damage)
        state.player.temp_ac_bonus = 0
        state.in_combat = False
        state.active_enemies.clear()
        state.player_approaching = False  # cancel approach so encounter mode doesn't re-trigger
        state.combat_log.append(f"Player fled! Took {flee_damage} damage escaping.")
        state.add_log(f"COMBAT: You fled the battle, taking {flee_damage} damage.")
        self._log(f"[yellow]🏃 You flee! -{flee_damage} HP[/]")
        self.refresh_parent()
        self.dismiss()

    def action_dismiss_combat(self) -> None:
        """Close overlay without changing combat state."""
        self.refresh_parent()
        self.dismiss()

    def _close_after_victory(self) -> None:
        """Called after a brief delay so the player can read the victory message."""
        self._write_victory_summary()
        self.refresh_parent()
        self.dismiss()

    def _write_victory_summary(self) -> None:
        """Write XP / loot / level-up summary to the combat log before closing."""
        state = self.engine.state
        p = state.player
        self._log("[bold yellow]━━━━━━━━━  V I C T O R Y  ━━━━━━━━━[/]")
        # Level-up is handled inside _award_xp_for_kill; surface any logged line
        for line in state.combat_log[-5:]:
            if "Level up" in line or "XP" in line or "defeated" in line:
                self._log(f"[dim green]{line}[/]")
        self._log(
            f"[dim]HP: {p.hp}/{p.max_hp}  │  XP: {p.experience}/{p.xp_to_next_level}"
            f"  │  Gold: {p.gold}[/]"
        )


# ── Formatting helpers ─────────────────────────────────────────────────────

def _bar(current: int, maximum: int, width: int = 20) -> str:
    if maximum <= 0:
        return f"[dim]{'░' * width}[/dim]"
    ratio = current / maximum
    filled = max(0, min(width, int(ratio * width)))
    empty = width - filled
    color = "green" if ratio > 0.6 else "yellow" if ratio > 0.3 else "red"
    filled_part = f"[{color}]{'█' * filled}[/{color}]" if filled > 0 else ""
    empty_part = f"[dim]{'░' * empty}[/dim]" if empty > 0 else ""
    return filled_part + empty_part


def _hp_bar(current: int, maximum: int, label: str = "HP") -> str:
    return f"  {label} {_bar(current, maximum)} {current}/{maximum}"


def _format_roll_line(roll) -> str:
    """Compact one-line dice roll summary for the combat log."""
    rolls_str = "+".join(str(r) for r in roll.rolls)
    if len(roll.rolls) > 1:
        rolls_str = f"({rolls_str})"
    if getattr(roll, "advantage", 0):
        adv_tag = "adv" if roll.advantage > 0 else "dis"
        dropped = "/".join(str(d) for d in getattr(roll, "dropped", []))
        rolls_str += f" [dim]({adv_tag}, dropped {dropped})[/]"

    mod_part = ""
    if roll.modifier > 0:
        mod_part = f"[cyan]+{roll.modifier}[/]"
    elif roll.modifier < 0:
        mod_part = f"[red]{roll.modifier}[/]"

    total_color = "white"
    if roll.success is True:
        total_color = "green"
    elif roll.success is False:
        total_color = "red"

    icon = {"attack": "⚔", "damage": "💥", "initiative": "🎯", "check": "⚄"}.get(
        roll.roll_type, "⚄"
    )
    label = roll.label or roll.dice
    result = f"[bold {total_color}]{roll.total}[/]"

    outcome = ""
    if roll.dc is not None:
        if roll.roll_type == "attack":
            if getattr(roll, "is_critical", False):
                outcome = " → [bold green]CRITICAL HIT![/]"
            elif getattr(roll, "is_fumble", False):
                outcome = " → [bold red]MISS (fumble!)[/]"
            elif roll.success:
                outcome = " → [bold green]HIT[/]"
            else:
                outcome = " → [bold red]MISS[/]"
        else:
            outcome = " → [bold green]✓[/]" if roll.success else " → [bold red]✗[/]"

    mod_str = f" {mod_part}" if mod_part else ""
    return f"  {icon} [bold yellow]{label}[/]: {rolls_str}{mod_str} = {result}{outcome}"

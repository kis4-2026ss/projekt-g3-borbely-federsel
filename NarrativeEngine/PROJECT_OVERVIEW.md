# ChronosTUI — Project Overview

## What Is It?

**ChronosTUI** is a terminal-based RPG that fuses two things: a deterministic game engine (HP, inventory, quests, dice) and a live LLM narrator that generates every story beat in response to what the player types. The player writes in natural language — "I approach the merchant" or "I attack the guardian" — and the AI responds with prose *plus* a structured list of game-state mutations the engine applies immediately.

The world is **Elowen**, a dark fantasy setting built on the ruins of the Eternal Aurelian Empire. The tone is grim, atmospheric, and reactive to player choices.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.x |
| Terminal UI | [Textual](https://textual.textualize.io/) (async TUI framework) |
| AI Backend | GitHub Models (`https://models.inference.ai.azure.com`) — default model `gpt-4o-mini` |
| HTTP client | `httpx` (async) |
| Config | `.env` via `python-dotenv` |

---

## How to Run

```bash
pip install -r requirements.txt
# Create a .env file:
# GITHUB_TOKEN=<your_github_token>
# LLM_MODEL=gpt-4o-mini   (optional)

python main.py
```

---

## Player-Facing Features

### Character Classes (Archetypes)
At the start of every new chronicle, the player picks one of four classes. Each has unique stats, starting gear, combat actions, a class resource, and level-up bonuses defined in `engine/classes.json`:

| Class | Flavour | Resource | Signature |
|---|---|---|---|
| **Fighter** | Iron will, strong arm | Rage (3 max) | Cleave, Second Wind, Battle Cry (Lv3) |
| **Mage** | Arcane glass cannon | Mana (5 max) | Fireball, Arcane Bolt, Mana Shield (Lv3) |
| **Monk** | Swift unarmed striker | Ki (4 max) | Flurry, Iron Body, Quivering Palm (Lv3) |
| **Rogue** | Evasive precision killer | Energy (4 max) | Backstab, Smoke Bomb, Death Mark (Lv3) |

### Combat
Combat is modal — the player enters a dedicated `CombatScreen` when a fight starts. Each turn the player chooses from class-specific actions (attack, use ability, defend, flee, use item). Dice rolls follow D&D 5e conventions:

- **Attack:** `1d20 + hit_bonus + STR/DEX mod` vs. enemy AC
- **Damage:** weapon dice + STR mod on a hit; crits (natural 20) double the damage dice
- **Initiative:** both sides roll `1d20 + DEX mod`; higher roll acts first
- **Advantage/Disadvantage:** rolls two d20s, keeps the higher/lower respectively
- **AC:** `10 + DEX mod + armor bonus`

Status effects (Poisoned, Blessed, Burning…) attach roll modifiers and advantage flags that the engine tracks and ticks down each turn.

### Progression
- **XP:** auto-awarded on enemy kill (`25 + level × 25`)
- **Level-ups:** cascade immediately; each class has per-level HP and stat boosts defined in JSON
- **Gold:** earned from loot; displayed in the sidebar
- **Loot profiles:** the orchestrator feeds the LLM a per-class, per-level loot table so boss drops are mechanically appropriate

### Keyboard Shortcuts (in-game)
| Key | Action |
|---|---|
| `Ctrl+S` | Save game (atomic write) |
| `Ctrl+L` | Load game |
| `Ctrl+Q` | Quit |
| `Ctrl+A` | Quick attack |
| `Ctrl+U` | Use potion |
| `Ctrl+E` | Open inventory |
| `Ctrl+B` | Open combat screen |
| `Ctrl+R` | Rest (after defeating 2+ enemies) |

---

## Architecture

### Module Map

```
NarrativeEngine/
├── main.py                  Entry point
├── engine/
│   ├── models.py            All dataclasses (GameState, Player, Quest, NPC, Enemy…)
│   ├── state_changes.py     LLM op dispatcher + all op handlers
│   ├── core.py              GameEngine — holds active state, save/load
│   ├── archetypes.py        Class system loader (reads classes.json)
│   ├── combat.py            CombatManager — dice math, round resolution
│   ├── dice.py              Core dice roller (advantage, crits, fumbles)
│   ├── exploration.py       Slice A helpers (mostly superseded by move_to op)
│   ├── classes.json         Single source of truth for all four classes
│   └── combat_actions.json  Combat action definitions
├── ai/
│   ├── client.py            Async httpx client → GitHub Models API
│   ├── parser.py            JSON response parser with malformed-output fallback
│   └── orchestrator.py      Three-tier memory assembly + system prompt builder
├── ui/
│   ├── app.py               ChronosApp (Textual) — main turn loop, sidebar, log
│   ├── class_select_screen.py  Archetype selection modal at new-game start
│   ├── intro_screen.py      Opening title screen
│   ├── combat_screen.py     Modal combat UI (actions, dice display)
│   └── inventory_modal.py   Inventory viewer
└── prompts/
    ├── system_prompt.json   Narrator persona, world rules, mode instructions
    ├── campaign_start.json  One-shot opening scene director's note (turn 0 only)
    └── encounter_rules.json Enemy scaling, loot, and defeat-condition rules
```

---

## The Turn Loop

Every player input goes through this exact pipeline:

```
Player types → TUI captures input
            → turn_count++, "PLAYER:" appended to log
            → Snapshot location + quest status (for heuristic plot-point detection)
            → PromptOrchestrator.build_payload()  ← assembles 3-tier memory + system prompt
            → AIClient.generate_narrative()        ← posts to GitHub Models (JSON mode)
            → parser.parse_response()              ← parse + fallback on malformed output
            → apply_changes()                      ← dispatches each op through typed handler
            → Plot-point recording (LLM + heuristic backups)
            → UI render: narrative prose + dim state-change summary + warnings
            → Background summary worker (every 8 turns)
```

---

## Three-Tier Memory Model

The LLM context is assembled from three layers every turn:

| Tier | Source | Content | Refresh rate |
|---|---|---|---|
| **Hot** | `state.log[-6:]` | Last 6 PLAYER/NARRATOR lines verbatim | Every turn (free) |
| **Warm** | `state.session_summary` | One-paragraph rolling summary of the session | Background LLM call every 8 turns |
| **Cold** | `state.story_history` | Tagged `PlotPoint` events (significant choices, boss fights, discoveries) | Appended by LLM or heuristic triggers |

Cold recall is keyword-matched (not embeddings): the orchestrator tokenizes the current input + location, strips stopwords, and fuzzy-matches against PlotPoint tags and event text. Always includes the 3 most recent points; up to 5 older ones per turn via tag match.

---

## LLM Contract: Structured Output

The system prompt requires the LLM to return a single JSON object every turn:

```json
{
  "narrative": "<player-facing prose — the ONLY text the player sees>",
  "state_changes": [
    {"op": "move_to", "value": "Shattered Plaza"},
    {"op": "award_xp", "amount": 75},
    {"op": "define_encounter", "id": "weeping_guardian", "is_boss": true, ...}
  ],
  "plot_point": {
    "event": "Player entered the Shattered Plaza and saw the Weeping Guardian",
    "choice_made": "Approached the plaza",
    "tags": ["weeping_guardian", "shattered_plaza", "boss"]
  }
}
```

`state_changes` is a list of typed ops. The engine validates and applies them deterministically — it never trusts the LLM blindly. Unknown ops, type errors, and out-of-range values are silently dropped with a warning rather than crashing the turn.

### Full Op Reference

| Category | Ops |
|---|---|
| Inventory / Health | `add_item`, `remove_item`, `damage_player`, `heal_player`, `adjust_resonance` |
| Equipment | `equip_weapon`, `equip_armor`, `unequip_weapon`, `unequip_armor` |
| Progression | `award_xp`, `add_gold`, `remove_gold` |
| Status Effects | `apply_status`, `remove_status` |
| Stats | `set_stat` |
| Encounter Registry | `define_encounter`, `spawn_encounter`, `loot_encounter` |
| Item Registry | `define_item`, `give_defined_item` |
| Combat | `start_combat`, `roll_attack`, `end_combat`, `roll_skill_check` |
| World / Location | `move_to`, `discover_location`, `set_time_of_day`, `set_weather`, `set_world_flag` |
| Quests | `define_quest`, `advance_quest`, `complete_quest`, `fail_quest` |
| NPCs | `add_npc` |
| Activity | `set_player_approaching` |

---

## Narrative Modes

The orchestrator feeds the LLM a `dm_meta` block each turn telling it which **narrative mode** it's in. This controls the system prompt instructions and pacing:

| Mode | Trigger | Behaviour |
|---|---|---|
| `chronicle` | Default exploration or NPC dialogue | Slow-burn storytelling; 4–5 turns per zone |
| `encounter` | Player approaches a threat (`set_player_approaching`) OR 5-turn backstop | Tension escalation, enemy arrival beat |
| `combat` | `state.in_combat == True` | CombatScreen active; combat UI takes over |
| `aftermath` | One turn after combat ends | Loot resolution, narrative wind-down |

---

## Save System

- Saves to `savegame.json` via an **atomic write**: writes to `savegame.json.tmp` first, then `os.replace()` swaps it in — no half-written saves on crash.
- Schema version is checked on load (`CURRENT_SCHEMA_VERSION = 6`). A version mismatch raises `IncompatibleSaveError` with a clear message.
- Transient fields (dice rolls, combat clocks, approach flags) are stripped before saving and re-initialised to safe defaults on load.

---

## Encounter & Item Registries

Two in-memory registries persist in `GameState` and are saved to disk:

**`encounter_registry`** — named enemy templates authored by the LLM via `define_encounter`. Stores full stat blocks, XP/gold/item rewards, narrative flavor, and a `defeat_condition` (`defeat | soothe | outwit | endure`). A template is spawned once with `spawn_encounter`; the `spawned` flag prevents double-spawning.

**`item_registry`** — structured item definitions authored via `define_item`. Types: `weapon`, `armor`, `consumable`, `quest`, `lore`. Quest and lore items are permanent — the LLM cannot remove them. Weapons and armor are auto-equipped when given to the player.

---

## Design Constraints & Principles

- **UI/Logic separation:** Textual widgets render and react to events only. All game logic lives in `GameEngine`, state-change handlers, or combat managers.
- **Async everywhere:** All LLM calls and heavy work are `async/await`. Background summary runs as a `Textual` worker so it never blocks the main turn.
- **Typed domain objects:** Dataclasses with explicit `typing` annotations across all module boundaries. Untyped dicts only exist at the LLM response boundary, where they are immediately validated.
- **Defensive parsing only at the boundary:** The rest of the engine trusts its own data and uses plain dataclass calls.
- **No blind LLM trust:** Every op handler validates types, ranges, and preconditions. A bad LLM output degrades gracefully — the turn continues, and the player sees a dim warning rather than a crash.

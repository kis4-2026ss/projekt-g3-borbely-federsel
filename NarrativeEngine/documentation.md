# ChronosTUI — Architecture & Design

This document is the human-facing companion to `CLAUDE.md`. CLAUDE.md is the
terse operational reference that future AI assistants and contributors check
when they need *what* a thing is. This document explains *why* the project is
structured the way it is, and the reasoning behind the bigger design calls.

---

## 1. What we're building

A terminal-based RPG narrative engine that combines a **deterministic game
engine** with an **LLM narrator**. The player explores Elowen — a dark-fantasy
world built on the ruins of the fallen Aurelian Empire — by typing free-form
actions ("examine the crystal," "talk to the smith"). The LLM generates the
narrative response *and* proposes mechanical state changes; the engine validates
and applies them. The TUI is built with [Textual](https://textual.textualize.io/) + Rich.

The interesting question this project is trying to answer is:

> How do you run a coherent multi-hour RPG when the storyteller is a stateless
> LLM that fits ~2000 tokens of context per turn?

Everything below follows from that question.

---

## 2. Design philosophy

Three convictions shape the codebase.

### 2.1 The engine owns truth; the LLM is a content provider

The LLM is a writer, not a database. Game state lives in `GameState` and is
mutated only through registered, validated operations. The LLM can *propose*
changes (add an item, move the player, advance a quest) but cannot directly
write to the state — the engine inspects the proposal, type-checks it, clamps
ranges, and either applies or drops it.

This matters because LLMs hallucinate. Without this boundary, a narrator
saying "you find a Solar Crest" leaves the player's inventory empty — or
worse, the next turn's narrator forgets the crest exists. With this
boundary, narrative and mechanics stay in lockstep.

### 2.2 Memory is layered by cost, not by recency alone

The naive approach — "stuff the last N turns into the prompt" — runs out of
context after ~30 turns and forgets earlier story beats permanently. We use
three tiers, each tuned for a different cost/value ratio:

| Tier | Cost per turn | Value | What it stores |
|---|---|---|---|
| Hot | free | high (immediate flow) | Last 6 raw log lines |
| Warm | one LLM call every 8 turns | medium (mid-range coherence) | One-paragraph rolling summary |
| Cold | tag-fuzzy match on recall | situational (deep callback) | All `PlotPoint`s, last 3 always + tag-matched older ones |

The cold tier is the one that lets the narrator say, fifty turns later,
*"You remember the crystal humming when you first touched the spire"* — only
when the current input or location semantically references it. Everything
else is forgotten by design, because including it would crowd out the recent
context.

### 2.3 Defensive parsing only at the LLM boundary

Internal callers (engine ↔ UI, engine ↔ engine) trust each other and use
plain dataclass calls. The LLM boundary is the *only* place we tolerate
malformed input, and it's where all the parsing/validation lives:
`ai/parser.py` (JSON shape), `engine/state_changes.py` (op semantics).

This keeps the bulk of the code clean. We don't sprinkle `if x is None`
checks through the engine; we trust the dataclasses. We're paranoid only
where reality is actually uncertain — when the LLM speaks.

---

## 3. The turn loop

Every player input flows through this pipeline. It's the load-bearing
control flow of the entire app.

```
Player types "I touch the spire"
         │
         ▼
┌────────────────────────┐
│   ui/app.py            │   ← input capture, turn_count++, log append
└──────────┬─────────────┘
           │ snapshot prior_location, prior_quest_status
           ▼
┌────────────────────────┐
│ ai/orchestrator.py     │   ← build_payload:
│   build_payload()      │     · compute activity + narrative_mode
│                        │     · update phase/activity clocks
│                        │     · system msg (rules + op reference)
│                        │     · context msg (3-tier memory + lean state)
│                        │     · [optional] spawn-override injection
│                        │     · user msg (the player's action)
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│ ai/client.py           │   ← POST to OpenRouter (or any OpenAI-compat
│   generate_narrative() │     endpoint) with response_format=json_object
└──────────┬─────────────┘
           │ raw JSON string (or error / malformed text)
           ▼
┌────────────────────────┐
│ ai/parser.py           │   ← parse_response:
│   parse_response()     │     · tries strict JSON, then {…} substring
│                        │     · falls back to "treat as narrative"
│                        │     · returns NarrativeResponse + warnings
└──────────┬─────────────┘
           │
           ├──────────────────────────────────────────────┐
           ▼                                              ▼
┌────────────────────────┐               ┌────────────────────────┐
│ engine/state_changes   │               │ state.record_choice    │
│   apply_changes()      │               │ (LLM-supplied PlotPoint │
│   per-op handlers      │               │  + heuristic backups for │
│   (validate + clamp)   │               │  location / quest moves) │
└──────────┬─────────────┘               └──────────┬─────────────┘
           │                                        │
           │ engine-side encounter enforcement      │
           │ (force spawn if LLM missed it)         │
           └────────────────────┬───────────────────┘
                                ▼
                     ┌────────────────────────┐
                     │   ui/app.py            │   ← write dice, narrative,
                     │   render               │     state-change footer,
                     └──────────┬─────────────┘     parse warnings
                                │
                                ├── session_logger.log_turn(...)
                                │
                                ├── [if TTS enabled] ai/tts.speak(narrative)
                                │
                                ▼
                     ┌────────────────────────┐
                     │ Textual worker         │   ← if turn_count -
                     │ _refresh_summary()     │     summary_anchor_turn ≥ 8
                     │ (background, async)    │     run summary in parallel
                     └────────────────────────┘
```

The summary worker doesn't block the next turn — by the time the player
types again, the new summary is usually ready, or the next turn proceeds
with the prior one. Either way the UI never waits.

---

## 4. Memory model in depth

### 4.1 The three tiers

**Hot (verbatim recent log)** — `state.log[-6:]` is appended to every
prompt. This is just the last few `PLAYER:` and `NARRATOR:` lines, raw.
Cheap, high signal for immediate continuity ("the player just asked
about the gears").

**Warm (rolling session summary)** — `state.session_summary` is a single
paragraph regenerated every 8 turns by a separate LLM call. It ingests
the *previous* summary plus the last 16 log lines and produces a new
paragraph. The result: a continuously updated précis of the session
that costs us one extra LLM call every eight turns but covers the
mid-range gap (turns ~10 ago that have fallen out of the hot window
but aren't yet old enough to be meaningful as PlotPoints).

**Cold (tagged plot history)** — `state.story_history` is a list of
`PlotPoint(event, choice_made, tags, timestamp)` objects. Two things
go in this list:

1. The LLM's explicitly-flagged plot points (when its `plot_point`
   field in the structured response is non-null).
2. Heuristic backups recorded by the engine when it detects a major
   transition the LLM missed (location change, quest status change).
   The heuristics use boring tags like `["travel", <slug-of-old>,
   <slug-of-new>]`.

When building a prompt, we always include the last 3 plot points
(the "recent significant events" baseline). On top of that, we run a
cold-recall pass: tokenize the player's input + current location, then
walk the older plot points and pull any whose tags or event-text
overlap. Capped at 5 per turn.

### 4.2 Why tags rather than embeddings

Tags are cheap, deterministic, and debuggable. You can read a save file
and immediately see *why* a plot point would or wouldn't be recalled.
Embeddings are smarter (they handle synonyms — "tower" finds "spire")
but require an embedding API call per recall, plus storage of vectors,
plus an explanation when retrieval feels wrong. For a project of this
scale, tags-with-tokenization is the right tradeoff. Switching to
embeddings later means swapping the recall function in
`orchestrator._get_relevant_history`; the rest of the pipeline doesn't
care how recall works internally.

---

## 5. The state-mutation contract

`engine/state_changes.py` is the single chokepoint where the LLM is
allowed to change the world. It exposes:

- `apply_changes(state, changes) -> List[Tuple[str, str]]` — the dispatcher,
  returns `(op_name, description)` tuples for each change applied or skipped.
  The op_name lets the UI tier and style each record without parsing text.
- `OP_REFERENCE` — a string injected into the system prompt so the LLM
  knows the full schema it's targeting.
- A private `_HANDLERS` dict mapping op names to handler functions.

Each handler:
1. Pulls fields off the change dict with type checks.
2. Validates ranges (HP ≥ 0, objective_index in bounds, etc.).
3. Mutates `state` through the proper model methods.
4. Returns a short description string, or raises `ValueError` on bad input.

The dispatcher catches per-op exceptions and turns them into log lines
like `[op 'advance_quest' failed: unknown quest_id 'xyz']`. **A single
bad op never kills the turn.** The narrative still renders, the player
still acts, and the dev sees the diagnostic in the log.

### 5.1 Adding a new op

This is intentionally cheap. To add (say) `apply_status_effect`:

1. Write a handler in `state_changes.py` that takes `(state, change)`
   and mutates the right model.
2. Register it in `_HANDLERS`.
3. Append a one-line schema to `OP_REFERENCE`.

That's it. The next turn, the LLM will see the new op in its system
prompt and start using it where the narrative justifies.

---

## 6. Narrative mode state machine

The orchestrator derives a `narrative_mode` each turn from the player's
activity. This mode selects the mode-specific instruction block sent to the
LLM, and drives pacing, urgency, and encounter spawning.

```
chronicle ──── player approaches ────▶ encounter
    │                                      │
    │  zone clock (8 turns exploring       │ phase_turn 0: arrival beat
    │  with pending encounter)             │ phase_turn ≥ 1: spawn MUST fire
    ▼                                      ▼
encounter ◀──────────────────────── spawn_encounter
                                          │
                                     state.in_combat = True
                                          │
                                          ▼
                                       combat
                                          │
                                    end_combat op
                                          │
                                          ▼
                                       aftermath ──▶ (returns to chronicle)
```

**Activity** is computed first (combat → aftermath → approach → dialogue →
exploring), then mapped to a mode. Key behaviours:

- **chronicle** — default; exploration, discovery, NPC dialogue. The zone
  clock ticks only while *exploring*; it is paused during dialogue so a
  fight cannot break out mid-conversation.
- **encounter** — triggered by `set_player_approaching` or the 8-turn zone
  backstop. The LLM gets one arrival beat (`phase_turn 0`) for the entity to
  react, then `spawn_encounter` is mandatory at `phase_turn ≥ 1`. This rule
  is enforced three ways: the OP_REFERENCE clarifies the rule, the
  orchestrator injects a hard override message, and the engine
  deterministically forces the spawn if the LLM still ignores it.
- **combat** — `CombatScreen` modal is active; the main narrative screen
  is frozen.
- **aftermath** — one-turn window after combat ends; the LLM narrates the
  resolution, distributes loot, and advances relevant quests.

**Phase turn tracking** — `state.phase_entered_turn` resets whenever the
mode changes. `phase_turn = turn_count - phase_entered_turn`. Both the
orchestrator and the LLM context expose this number so timing rules
(arrival beat, zone clock) are unambiguous.

---

## 7. Combat system

`engine/combat.py` implements full D&D 5e-inspired turn-based combat via
`CombatManager`. It is called exclusively by `ui/combat_screen.py` (direct
player actions) and by the `roll_attack` LLM op (for script-driven rounds).

### 7.1 Core mechanics

- **Initiative** — `1d20 + DEX mod` for both sides; player wins ties.
  `state.player_acts_first` is set on `spawn_encounter` and read by
  `CombatScreen` on mount.
- **Attack rolls** — `1d20 + STR + proficiency + hit_bonus vs enemy AC`.
  Natural 20 = critical hit (damage dice rolled twice). Natural 1 = fumble.
- **Advantage / disadvantage** — collapse-to-single rule: any number of
  sources counts as one. Net result: `net_advantage()` returns +1, 0, or -1.
  Status effects carry the flag.
- **Damage floor** — every hit deals at least 1 damage regardless of
  reductions (mitigation is applied before the `max(1, …)` clamp).
- **XP + level-up** — `_award_xp_for_kill` runs a cascade: `25 + level * 25`
  XP per kill. Level-up applies class-specific HP and stat bonuses from
  `engine/archetypes.py` and logs the event to the combat log.

### 7.2 Archetypes and class abilities

Four playable classes, defined in `engine/classes.json` and loaded by
`engine/archetypes.py`:

| Class | Resource | Slots 2–4 | Level-3 unlock |
|---|---|---|---|
| **Fighter** | Rage (max 3) | Cleave / Second Wind / Defend | Battle Cry |
| **Mage** | Mana (max 5) | Arcane Bolt / Mana Shield / Evade | Arcane Surge |
| **Monk** | Ki (max 4) | Flurry of Blows / Iron Body / Meditate | Ki Strike |
| **Rogue** | Energy (max 3) | Backstab / Smoke Screen / Poison Strike | Shadow Step |

Slot 1 is always **Strike** (standard STR attack). `^U` uses a consumable
item. `R` flees (10% HP penalty).

Each class has a data-driven loot profile (`loot_profile` in `classes.json`)
that the orchestrator injects into the LLM context so boss drops are
calibrated to the player's class and level.

### 7.3 Reactions

Every class has a defensive reaction triggered when the enemy hits:

| Class | Reaction | Effect |
|---|---|---|
| Fighter | **Guard** | Reduce damage by STR mod + 2 (min 1) |
| Mage | **Mana Intercept** | Pre-load a `(INT*2+4)` Mana Shield; Shield drains before HP |
| Monk | **Ki Redirect** | Take half damage (rounded down) |
| Rogue | **Shadow Slip** | DEX check vs attack total; success = 0 damage |

Reactions are presented in `CombatScreen` after a hit lands but before damage
is applied.

### 7.4 CombatScreen

`ui/combat_screen.py` is a Textual `ModalScreen` with a three-column layout:

- **Left** — action panel (loaded from `engine/combat_actions.json`; inline
  fallback if file is missing). Shows slot key, name, type badge, description.
  Active selection is highlighted live.
- **Centre** — combat log (append-only round-by-round narration).
- **Right** — stats panel (enemy HP/AC, player HP/AC/XP/resource).

The screen opens automatically when combat starts (main screen shows a
prompt to press `^B`). It closes on victory, defeat, or when the player flees.

---

## 8. NPC dialogue system

### 8.1 Dual soft-cap

Two independent counters prevent NPCs from being infinitely questioned:

| Counter | Tracks | Soft cap | Resets on |
|---|---|---|---|
| `npc_exchanges_this_location` | All dialogue turns at current location | ~8 | `move_to` |
| `npc_exchange_counts[npc_key]` | Per-NPC exchange count | ~6 | `move_to` |

Both counters are exposed in `dm_meta` every turn so the LLM can see the
pressure building. The system prompt defines a graduated withdrawal arc:
answers naturally shorten, then the NPC shows character-appropriate impatience
or fear, then they stop engaging entirely — using *their own voice*, not
canned phrases. The caps are soft: no code ever ejects the player from
dialogue; the LLM writes the organic wind-down.

### 8.2 Zone clock and dialogue

The 8-turn zone backstop (which can force encounter mode) is explicitly
*paused during dialogue*. The orchestrator only checks `turns_at_location`
for the backstop when `activity != "dialogue"`. This prevents a combat
from exploding mid-conversation simply because the player asked too many
questions before walking to the plaza.

---

## 9. LLM provider

The AI client (`ai/client.py`) targets any OpenAI-compatible
`/chat/completions` endpoint. Configuration via `.env`:

```
LLM_API_URL=https://openrouter.ai/api/v1/chat/completions   # or any compat endpoint
LLM_API_KEY=sk-or-...                                        # or GITHUB_TOKEN as fallback
LLM_MODEL=openai/gpt-4.1-mini                                # default
```

The client maintains a persistent `httpx.AsyncClient` with a 15-second
keepalive expiry (below the Azure/OpenRouter idle-close threshold). On
`TimeoutException` or `RemoteProtocolError`, it retries once on a fresh
connection before returning an error narrative — transparent to the player.

**Model choice** — `openai/gpt-4.1-mini` via OpenRouter is the current
default. It produces strong instruction-following for the dense structured
output spec and handles JSON mode reliably. Gemini and other models can be
configured but tend to produce shorter responses and have weaker JSON-mode
enforcement against complex system prompts.

---

## 10. TTS

`ai/tts.py` integrates ElevenLabs for optional voice narration.

- **Toggle** — `^T` in the main screen.
- **Config** — `ELEVEN_API_KEY`, `TTS_VOICE_ID`, `TTS_MODEL` in `.env`.
- **Cancellation** — a generation counter ensures stale audio is discarded
  if a new turn fires before the previous audio finishes streaming.
- **Playback** — `pygame.mixer` plays the generated MP3 from a temp file.
  Non-blocking: the event loop continues while audio plays.

If `ELEVEN_API_KEY` is not set, the toggle is silently unavailable.

---

## 11. Session logging

`engine/session_logger.py` writes a JSONL file per play session to
`logs/session_YYYY-MM-DD_HH-MM-SS.jsonl`. Each line is either a system
event (`{"type":"system", "msg":"..."}`) or a full turn record
(`{"type":"turn", "t":N, "loc":"...", "hp":N, "player":"...",
"narrative":"...", "changes":[...], "warnings":[]}`).

The file is opened with `buffering=1` (line-buffered), so every turn flushes
to disk immediately. No turns are lost on crash. Logging errors are swallowed
silently — they never surface to the player.

---

## 12. Persistence

`GameState.save_to_file` writes to `<filename>.tmp` and then uses
`os.replace` to atomically swap. If the process is killed mid-write,
the on-disk save is either the old version or the new one — never half
of each.

`GameState.from_json` checks the embedded `schema_version` (currently
**7**) and raises `IncompatibleSaveError` on mismatch. The error message
tells the user to delete the save file. Saves from earlier schema versions
are refused cleanly rather than silently producing broken state.

Old NPC saves that contain the now-removed `disposition` or `metadata`
fields are forward-compatible: `from_json` strips those keys before
constructing the `NPC` dataclass, so they load without error.

When the schema needs to evolve again, the choice is:
- Bump `CURRENT_SCHEMA_VERSION` and reject old saves (current posture —
  fine while the game is in heavy churn).
- Or add an `if version == N: ... migrate ...` branch in `from_json`
  before the equality check. Worth doing once players have saves they
  care about.

---

## 13. UI ↔ logic separation

Textual widgets handle composition, input events, and rendering. They
do **not** contain game logic. The contract:

- `ChronosApp` owns one `GameEngine`, one `PromptOrchestrator`, one
  `AIClient`, one `SessionLogger`. It calls into them; it doesn't know
  how they work internally.
- All non-trivial state mutation goes through engine methods or the
  state-change dispatcher. The UI's only direct mutations are
  `state.turn_count += 1` and the `add_log` calls in the input handler —
  both of which are bookkeeping, not game logic.
- Long-running work (LLM calls, TTS, summaries) goes through `async`
  methods or `App.run_worker`. The Textual event loop is never blocked.
- Modal screens (`CombatScreen`, `InventoryModal`, `JournalModal`,
  `IntroScreen`, `ClassSelectScreen`) each own a focused slice of the
  interaction. They read state and call back into the engine; they do not
  own state themselves.

---

## 14. Module map

```
NarrativeEngine/
├── main.py                     entry point: ChronosApp().run()
├── engine/
│   ├── models.py               dataclasses + JSON serialization + atomic save
│   │                           (schema_version=7; Player, Enemy, GameState, etc.)
│   ├── state_changes.py        LLM op contract: handlers + dispatcher + OP_REFERENCE
│   ├── core.py                 GameEngine: state holder, quest loader, save/load
│   ├── combat.py               CombatManager: D&D combat math, class abilities,
│   │                           reactions, XP/level-up cascade
│   ├── archetypes.py           Archetype system: loads classes.json, exposes
│   │                           apply_archetype, get_level_bonus, get_class_loot_profile
│   ├── classes.json            Data file: stats, HP, resources, starting items,
│   │                           level bonuses, loot profiles for all 4 classes
│   ├── dice.py                 Dice notation parser + roller (1d20, 2d6, etc.);
│   │                           supports advantage/disadvantage and critical hits
│   └── session_logger.py       Append-only JSONL session log (one file per session)
├── ai/
│   ├── client.py               Async httpx client; generate_narrative + summarize
│   │                           (OpenAI-compat; default: OpenRouter/gpt-4.1-mini)
│   ├── parser.py               parse_response: strict → fuzzy → plain-text fallback
│   ├── orchestrator.py         build_payload: 3-tier memory + lean state + narrative
│   │                           mode state machine + spawn-override injection
│   └── tts.py                  ElevenLabs TTS: speak(text), stop(); pygame playback
├── ui/
│   ├── app.py                  ChronosApp: main TUI turn loop, render, workers,
│   │                           engine-side encounter enforcement, key bindings
│   ├── combat_screen.py        3-column combat modal: actions / log / stats;
│   │                           handles all class abilities and reactions
│   ├── inventory_modal.py      Modal inventory panel (equip / use / drop)
│   ├── journal_modal.py        Two-panel read-only journal (PlotPoints + locations)
│   ├── intro_screen.py         Splash/start screen with ASCII title art
│   └── class_select_screen.py  Archetype selection (shown on new game or missing archetype)
├── prompts/
│   ├── system_prompt.json      Narrator persona, world rules, mode instructions,
│   │                           output format spec, NPC dialogue caps
│   ├── campaign_start.json     One-shot opening director's note (turn 0 only)
│   └── encounter_rules.json    Enemy stat scaling tables, defeat conditions, loot rules
├── data/
│   └── quests/                 Data-driven quest definitions
├── logs/                       JSONL session logs (runtime artefact, gitignored)
├── CLAUDE.md                   Operational reference for AI agents / contributors
├── documentation.md            This file
└── requirements.txt            textual, rich, httpx, elevenlabs, pygame, python-dotenv
```

---

## 15. What's built and what isn't

### Working today

- Deterministic engine with full `GameState` model (schema v7)
- 4 playable archetypes: Fighter, Mage, Monk, Rogue — each with starting
  gear, class resource, 3 combat abilities, and a level-3 unlock
- Full D&D 5e combat: initiative, advantage/disadvantage, crits/fumbles,
  status effects, reactions, XP + level-up cascade
- 3-column combat modal with real-time round narration
- Encounter registry: `define_encounter` → `spawn_encounter` two-step with
  arrival beat, zone clock, and 3-layer enforcement to prevent LLM ignoring
  the spawn rule
- Structured LLM output with ~25 validated state-mutation ops
- Three-tier memory (hot log / warm summary / cold tagged PlotPoints)
- Dual soft-cap NPC dialogue system (per-NPC + per-location, organic withdrawal)
- Narrative mode state machine (chronicle / encounter / combat / aftermath)
  with phase-turn tracking
- Atomic save with schema versioning and forward-compat NPC field stripping
- Robust JSON parsing with malformed-output fallback
- Textual TUI: sidebar stats, Rich-rendered narrative log, modal screens
- Session logging to JSONL (line-buffered, crash-safe)
- Optional TTS via ElevenLabs (`^T` toggle)
- Inventory modal with equip / use / drop
- Journal modal with PlotPoints + discovered locations
- Intro splash screen; class selection screen

### Known gaps / deferred work

- **No automated tests.** The end-to-end check is hand-run. A test suite
  should target the state-changes dispatcher (unit) and a full turn loop
  with a mocked AI client (integration).
- **Tag-based recall, not semantic.** Migration path is well-defined: swap
  `_get_relevant_history` in `orchestrator.py`. Everything else is
  indifferent to how recall works.
- **Save slots.** Single save file only. Slots are a one-line change to
  the save/load paths once the game stabilises.
- **Multi-enemy combat.** The `active_enemies` list supports it and
  `Cleave` already operates on the full list, but `CombatScreen` currently
  targets the first enemy only. Selecting a target is the missing piece.
- **`combat_actions.json` duplication.** Action definitions exist both in
  that file and as inline fallbacks in `combat_screen.py`. Should converge
  to one source of truth.

---

## 16. Working in this codebase

Conventions worth keeping:

- All new domain types are dataclasses with explicit `typing` hints.
  `Dict[str, Any]` only crosses the LLM boundary; everywhere else uses
  proper types.
- All LLM-touching code is `async`. Anything that would otherwise block
  the event loop goes into `App.run_worker`.
- New ops register in both `_HANDLERS` and `OP_REFERENCE`. Forgetting
  the latter means the LLM never learns the op exists.
- New archetype data lives in `engine/classes.json`. `archetypes.py` is
  a thin loader — adding a class is a data change, not a code change
  (as long as the new abilities are routed through the existing
  `CombatManager` dispatch pattern).
- Transient `GameState` fields (clocks, flags that reset per-session)
  must be stripped in `to_json` and guarded (defaulted) in `from_json`.
  See the block comment around `npc_exchange_counts` and `last_rolls`
  for the pattern.
- Comments explain *why*, not *what*. Most of this codebase is
  comment-free because the names carry their meaning; comments appear
  only where a non-obvious constraint or rationale lives.

**Environment variables** (`.env`, gitignored):

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `LLM_API_KEY` | yes (or `GITHUB_TOKEN`) | — | Auth for the LLM endpoint |
| `LLM_API_URL` | no | OpenRouter completions URL | Swap LLM provider |
| `LLM_MODEL` | no | `openai/gpt-4.1-mini` | Model identifier |
| `ELEVEN_API_KEY` | no (TTS optional) | — | ElevenLabs auth |
| `TTS_VOICE_ID` | no | Adam voice ID | ElevenLabs voice |
| `TTS_MODEL` | no | `eleven_multilingual_v2` | ElevenLabs model |
| `TTS_ENABLED` | no | `false` | Start with TTS on |

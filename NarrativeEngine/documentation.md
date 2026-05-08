# ChronosTUI — Architecture & Design

This document is the human-facing companion to `CLAUDE.md`. CLAUDE.md is the
terse operational reference that future AI assistants and contributors check
when they need *what* a thing is. This document explains *why* the project is
structured the way it is, and the reasoning behind the bigger design calls.

---

## 1. What we're building

A terminal-based RPG narrative engine that combines a **deterministic game
engine** with an **LLM narrator**. The player explores Elowen — a dark-fantasy
world built on the ruins of the fallen Aurelian Empire — by typing
free-form actions ("examine the crystal," "talk to the smith"). The LLM
generates the narrative response *and* proposes mechanical state changes; the
engine validates and applies them. The TUI is built with [Textual](https://textual.textualize.io/) + Rich.

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
│   build_payload()      │     · system msg (rules + op reference)
│                        │     · context msg (3-tier memory + lean state)
│                        │     · user msg (the player's action)
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│ ai/client.py           │   ← POST to GitHub Models with
│   generate_narrative() │     response_format=json_object
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
           └────────────────────┬───────────────────┘
                                ▼
                     ┌────────────────────────┐
                     │   ui/app.py            │   ← write narrative,
                     │   render               │     state-change footer,
                     └──────────┬─────────────┘     parse warnings
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
plus an explanation when retrieval feels wrong. For a school project of
this scale, tags-with-tokenization is the right tradeoff. Switching to
embeddings later means swapping the recall function in
`orchestrator._get_relevant_history`; the rest of the pipeline doesn't
care how recall works internally.

### 4.3 Worked example

Imagine turn 1: the player touches the spire. The LLM returns a
plot_point with `tags=["spire","aurelian","awakening","resonance"]`.
That goes to cold storage.

Turns 2–8: minor exploration, dialogue, no significant plot points.
At turn 8, the warm-summary worker kicks off and produces something
like *"After awakening at the spire and feeling the bloodline
resonance, the exile descended into the village, met the wary
blacksmith, and learned of the Weeping Guardian in the ruined plaza."*

Turn 12, the player types: *"I want to go back to the spire."* The
orchestrator tokenizes the input → `{back, spire}`. It matches the
turn-1 plot point (tag `spire`) → pulls it into cold-recall. The
prompt now contains:
- Hot: last 6 log lines (current dialogue)
- Warm: the summary above
- Cold (recent 3): the last three plot points (some travel/quest noise)
- Cold (deep recall): "Touched the spire, felt resonance" with full tags

The narrator now has both the immediate context *and* the original
mystical-awakening beat to call back to.

---

## 5. The state-mutation contract

`engine/state_changes.py` is the single chokepoint where the LLM is
allowed to change the world. It exposes:

- `apply_changes(state, changes) -> List[str]` — the dispatcher, returns
  human-readable descriptions of what actually changed.
- `OP_REFERENCE` — a string injected into the system prompt so the LLM
  knows the schema it's targeting.
- A private `_HANDLERS` dict mapping op names to handler functions.

Each handler:
1. Pulls fields off the change dict with type checks.
2. Validates ranges (HP ≥ 0, disposition 0..100, objective_index in
   bounds, etc.).
3. Mutates `state` through the proper model methods.
4. Returns a short description string, or raises `ValueError` on bad input.

The dispatcher catches per-op exceptions and turns them into log lines
like `[op 'advance_quest' failed: unknown quest_id 'xyz']`. **A single
bad op never kills the turn.** The narrative still renders, the player
still acts, and the dev sees the diagnostic in the log.

### 5.1 Why this beats free-form mutation

Three concrete wins:

1. **No silent state drift.** If the narrator says "you draw your blade,"
   the engine will only reflect that if an `add_item` op was actually
   emitted. Either the inventory updates and the prose matches, or
   neither happens — no in-between.
2. **Validation at the boundary.** The LLM can hallucinate
   `"objective_index": 99`. The engine refuses; the player is unaffected.
3. **Auditability.** Every state change is logged with a description.
   When debugging a "the quest didn't progress" complaint, you can read
   the log and see exactly which ops fired.

### 5.2 Adding a new op

This is intentionally cheap. To add (say) `give_status_effect`:

1. Write a handler in `state_changes.py` that takes `(state, change)`
   and mutates the right model.
2. Register it in `_HANDLERS`.
3. Append a one-line schema to `OP_REFERENCE`.

That's it. The next turn, the LLM will see the new op in its system
prompt and start using it where the narrative justifies.

---

## 6. Persistence

`GameState.save_to_file` writes to `<filename>.tmp` and then uses
`os.replace` to atomically swap. If the process is killed mid-write,
the on-disk save is either the old version or the new one — never half
of each.

`GameState.from_json` checks the embedded `schema_version` (currently
**2**) and raises `IncompatibleSaveError` on mismatch. The error message
tells the user to delete the save file. Any pre-v2 file (including the
one originally shipped in the repo from earlier development) is refused
cleanly rather than silently producing broken state.

When the schema needs to evolve again, the choice is:
- Bump `CURRENT_SCHEMA_VERSION` and reject old saves (the current
  posture — fine while the game is in heavy churn).
- Or add an `if version == 1: ... migrate ...` branch in `from_json`
  before the equality check. Worth doing once players are accumulating
  saves they care about.

We deliberately did *not* split the save into multiple files. The
single-file approach keeps atomicity simple, the on-disk size
manageable (well under 1 MB even after many sessions), and the LLM
context cost is unaffected (it's controlled by the orchestrator, not
the disk layout).

---

## 7. UI ↔ logic separation

Textual widgets handle composition, input events, and rendering. They
do **not** contain game logic. The contract:

- `ChronosApp` owns one `GameEngine`, one `PromptOrchestrator`, one
  `AIClient`. It calls into them; it doesn't know how they work
  internally.
- All non-trivial state mutation goes through engine methods or the
  state-change dispatcher. The UI's only direct mutations are
  `state.turn_count += 1` and the `add_log` calls in the input handler
  — both of which are book-keeping, not game logic.
- Long-running work (LLM calls, summaries) goes through `async`
  methods or `App.run_worker`. The Textual event loop is never blocked.

If you find yourself adding a third dependency to a UI method, ask
whether the logic belongs in the engine instead.

---

## 8. Module map

(For the operational/agent-focused version, see `CLAUDE.md`.)

```
NarrativeEngine/
├── main.py                 entry point: ChronosApp().run()
├── engine/
│   ├── models.py           dataclasses + JSON serialization + atomic save
│   ├── state_changes.py    LLM op contract: handlers + dispatcher + schema
│   ├── core.py             GameEngine: state holder, quest loader, save/load
│   ├── exploration.py      Slice A helpers (mostly superseded by ops)
│   └── combat.py           Slice B (currently a stub)
├── ai/
│   ├── client.py           async httpx client; generate_narrative + summarize
│   ├── parser.py           parse_response: strict → fuzzy → fallback
│   └── orchestrator.py     build_payload: 3-tier memory + lean state
├── ui/
│   └── app.py              Textual TUI: turn loop, render, workers, actions
├── prompts/
│   └── system_prompt.json  narrator persona + structured-output spec
├── data/
│   └── quests/
│       └── tutorial_boss.json
├── CLAUDE.md               operational reference for AI agents
├── documentation.md        this file
├── requirements.txt        textual, rich, httpx, pydantic, python-dotenv
└── savegame.json           runtime artefact (gitignored)
```

---

## 9. What's built and what isn't

### Working today
- Deterministic engine with full GameState model
- Structured LLM output with 15 validated state-mutation ops
- Three-tier memory (hot logs / warm summary / cold tagged history)
- Auto-recording PlotPoints (LLM-supplied + heuristic backups)
- Atomic save with schema versioning
- Robust JSON parsing with malformed-output fallback
- Textual TUI with live stats sidebar and Rich-rendered narrative log

### Stubbed or unbuilt
- `engine/combat.py` is a 5-line stub. When Slice B starts, combat
  ought to extend the op system (`start_encounter`, `enemy_turn`,
  `end_encounter`) rather than introduce a parallel mutation path.
- No automated tests. The end-to-end smoke check we run by hand is a
  reasonable starting point if a test suite is wanted.
- The save action is bound to `s` but `action_load_game` exists with
  no key binding. One-line fix when the game grows enough to want
  load support.
- Tag-based recall, not semantic recall. Migration path is well-defined
  (swap `_get_relevant_history`).

### Decisions deliberately deferred
- **Save slots.** Considered and dropped for now — premature for a
  game that's still defining its own mechanics.
- **Append-only PlotPoint log.** Same reason; current write volume is
  trivial.
- **Embeddings for recall.** Adds an API dependency and storage layout
  considerations; the keyword approach hasn't yet shown its limits.

---

## 10. Working in this codebase

Conventions worth keeping:
- All new domain types are dataclasses with explicit `typing` hints.
  `Dict[str, Any]` only crosses the LLM boundary; everywhere else uses
  proper types.
- All LLM-touching code is `async`. Anything that would otherwise
  block the event loop goes into `App.run_worker`.
- New ops register in both `_HANDLERS` and `OP_REFERENCE`. Forgetting
  the latter means the LLM never learns the op exists.
- Comments explain *why*, not *what*. Most of this codebase is
  comment-free because the names carry their meaning; comments appear
  only where a non-obvious constraint or rationale lives.
- Environment-specific config is in `.env` (gitignored). Currently
  `GITHUB_TOKEN` (required) and `LLM_MODEL` (optional, default
  `gpt-4o-mini`).

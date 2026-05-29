# Story Engine — Mechanics Reference

How ChronosTUI turns a single player input into one narrative turn: what state the
orchestrator measures, which prompt the LLM receives, and the rules that keep the
story moving without railroading it.

This is the **operational** companion to `documentation.md` (design rationale) and
`CLAUDE.md` (codebase map). It covers two halves of one system:

1. **The orchestrator** (`ai/orchestrator.py`) — deterministic Python that measures
   game state and decides *what kind of turn this is* before the LLM sees anything.
2. **The prompt** (`prompts/*.json`) — the rules the LLM follows once the
   orchestrator has framed the turn.

The core design bet: **the engine decides the shape of the turn; the LLM fills it
with prose.** The LLM is never asked "should a fight start now?" — the orchestrator
answers that with clocks and hands the LLM a narrative *mode* plus, when necessary, a
hard directive. This keeps pacing deterministic and prevents the model from either
stalling forever or rushing every scene into combat.

---

## 1. The turn pipeline

Every player input flows through `PromptOrchestrator.build_payload(state, user_input)`,
which returns the message list sent to the LLM. The steps, in order:

1. **Compute activity** — `_compute_activity` reads game state and returns one of five
   categories (combat / aftermath / approach / dialogue / exploring).
2. **Tick activity clock** — if the activity category changed since last turn, reset
   `activity_entered_turn`. `turns_in_activity = turn_count - activity_entered_turn`.
3. **Tick dialogue counters** — if activity is `dialogue`, increment the location-wide
   exchange total and each present NPC's per-NPC counter.
4. **Derive narrative mode** — `_get_narrative_mode` maps activity (+ the zone-clock
   backstop) to one of four modes (chronicle / encounter / combat / aftermath).
5. **Tick phase clock** — if the narrative mode changed, reset `phase_entered_turn`.
   `phase_turn = turn_count - phase_entered_turn`.
6. **Assemble context** — `_get_lean_state` builds the JSON state blob, including the
   `dm_meta` clock block. `_get_relevant_history` adds cold-tier recall.
7. **Build system message** — `_build_system_message(mode)` concatenates the world
   rules + the *mode-specific* instruction block + encounter rules.
8. **Assemble messages** — system rules, then context JSON, then (turn 0 only) the
   campaign-start directive, then (conditionally) a hard combat-override directive,
   then the player action.

The LLM returns one JSON object (`narrative` + `state_changes` + optional
`plot_point`); `engine/state_changes.py` applies the ops; the UI renders the prose.

---

## 2. Three-tier memory

The orchestrator never sends the whole history. It sends three layers of decreasing
proximity and increasing compression (tunables at the top of `orchestrator.py`):

| Tier | Source | Window | Refresh |
|---|---|---|---|
| **Hot** | `state.log[-6:]` (`recent_dialogue`) | verbatim last 6 PLAYER/NARRATOR lines | every turn, free |
| **Warm** | `state.session_summary` | one rolling paragraph | background LLM call every 8 turns |
| **Cold** | `state.story_history` PlotPoints | last 3 always + up to 5 tag-matched older points | appended on `plot_point` op or heuristic |

Cold-tier recall is **keyword matching, not embeddings** (`_tokenize` + `_STOPWORDS`):
the current input and location are tokenized, and older PlotPoints whose tag/event
tokens overlap are pulled in (newest-first, capped at 5). This is cheap and good
enough for "the player mentioned the locket again, surface the locket memory."

**Consequence for prompt rules:** anything the LLM is asked to be consistent about
must live in data it can actually see — `recent_dialogue`, `session_summary`, recent
PlotPoints, or the `encounter_registry`. Rules like "don't reuse a phrase from 5
sessions ago" are unenforceable; rules anchored on `encounter_registry.narrative_flavor`
or `recent_dialogue` are enforceable. (This directly shaped the Issue 25 voice rule.)

---

## 3. `dm_meta` — the clock block

`dm_meta` is the heart of the engine. It is a small dict injected into the state JSON
every turn so the LLM never has to *guess* pacing — the engine has already measured it.

| Field | Meaning |
|---|---|
| `narrative_mode` | chronicle / encounter / combat / aftermath |
| `activity` | exploring / dialogue / approach / combat / aftermath |
| `turns_in_activity` | turns spent in the current activity category |
| `phase_turn` | turns spent in the current narrative mode |
| `turns_at_current_location` | exploration turns since `move_to` (drives the ZONE CLOCK) |
| `turns_since_last_encounter` | turns since combat ended (drives THREAT ESCALATION) |
| `npc_exchanges_this_location` | location-wide dialogue total (soft cap ~8) |
| `npc_exchange_counts` | per-NPC dialogue counts (soft cap ~6) |
| `pending_encounter_ids` | defined-but-unspawned encounters anywhere |
| `pending_quest_encounter_ids` | pending encounters tied to an *active* quest |

These are computed from raw turn counters tracked on `GameState`:
`location_entered_turn`, `last_encounter_turn`, `phase_entered_turn`,
`activity_entered_turn`, plus the dialogue counters. The orchestrator owns all of
them; the LLM only reads the derived values.

---

## 4. Activity → narrative mode

### 4.1 Activity (`_compute_activity`)

Priority order — first match wins:

1. `state.in_combat` → **combat**
2. `state.in_aftermath` → **aftermath** (set for exactly one turn after combat ends)
3. `state.player_approaching` → **approach** (player emitted `set_player_approaching`)
4. a *known* NPC is at `current_location` → **dialogue**
5. otherwise → **exploring**

Note #4: `add_npc` sets `is_known=True`, so **introducing any NPC at the player's
location automatically flips the scene into dialogue mode.** This is the mechanical
basis for the "every act needs its Maren" rule — you don't script a dialogue scene,
you add an NPC and the mode follows.

### 4.2 Narrative mode (`_get_narrative_mode`)

- combat / aftermath → pass through unchanged.
- approach → **encounter**.
- **Zone-clock backstop:** if *not* in dialogue, there is a pending encounter, and
  `turns_at_current_location >= 8` → **encounter**.
- otherwise → **chronicle**.

The backstop is deliberately **paused during dialogue** — a fight can never erupt
purely from the clock while the player is mid-conversation. The conversation finishes
(or the player leaves) first; escalation happens on the next exploration turn.

---

## 5. The four narrative modes

Each mode has a dedicated instruction block in `system_prompt.json`
(`<mode>_mode`), selected by `_build_system_message`. Only the active mode's block is
sent — the LLM is never juggling four rulesets at once.

### CHRONICLE — exploration & dialogue (the default, slow clock)
Branches internally on `dm_meta.activity`:

- **exploring** — *Forward Hook Intensity*, a graduated ladder keyed on
  `turns_in_activity`: ORIENT (0–1) → NAME IT (2–3) → PULL (4–5) → URGENT (6–7) →
  HARD CAP (≥8 at a location with a pending encounter → escalate this turn). Every
  turn ends on a hook that is *meaningfully different* from the last.
- **dialogue** — NPCs reveal through *witnessed fear*, never instruction. Graduated
  wind-down by `npc_exchange_counts`: FREE (1–3) → PRESSURE (4–5) → soft cap (6+,
  character-specific withdrawal). A hard ban on steering phrases ("leave it be",
  "stay away", …) — an NPC expresses reluctance by describing *what happened to
  others*, never by discouraging the player. NPCs **pull toward** the threat.

### ENCOUNTER — threat is concrete, move fast
Fires on (A) player approach or (B) the zone-clock backstop.
- **Minor enemies** (`is_boss:false`, no construct/sapient/ancient tag): one physical
  reaction, then `spawn_encounter` immediately, no speech.
- **Intelligent entities** (boss or tagged): exactly **one** two-sentence arrival beat
  (`phase_turn 0`) with a single quoted fragment; on `phase_turn >= 1`,
  `spawn_encounter` fires *unconditionally* — the arrival beat was the only grace
  period. A flee exception lets the player disengage via `move_to`.

### COMBAT — dice and consequence only
The `CombatScreen` modal drives turn-by-turn mechanics. The LLM only narrates the
outcome of `last_round_rolls` in ≤40 words: hit/miss/crit/fumble as a concrete
physical consequence, then the enemy's next intent. No atmosphere, no lore.

### AFTERMATH — one turn only
Fires once after combat, then auto-reverts to exploring. Mandated op order:
1. `loot_encounter` (distributes the template's gold + items).
2. **Boss only:** `define_item` + `give_defined_item` for a brand-new class-appropriate
   weapon/armor (stats read from `player.loot_profile`, a strict upgrade).
3. **Boss only:** `complete_quest` + a `define_quest` follow-up that mirrors the
   *shape* of the opening act (new ground, different threat category, a human to meet).

---

## 6. The escalation system (two parallel clocks)

The engine's answer to "the world goes inert / the world rushes everything." Two
independent clocks, only one active at a time depending on whether an encounter is
already defined.

### ZONE CLOCK — when `pending_encounter_ids` is non-empty
A specific threat already exists in this zone. `turns_at_current_location` counts
exploration turns. At **8**, the HARD CAP fires: the LLM must `set_player_approaching`,
`spawn_encounter`, or `start_combat` this turn — no more exploration narration. Paused
during dialogue.

### THREAT ESCALATION — when `pending_encounter_ids` is empty
No threat defined yet (post-boss, new area, between quests). Keyed on
`turns_since_last_encounter`, and **only in dangerous environments** (ruins,
wilderness, underground, roads — never settlements/camps/rest areas):

| Phase | Turns | Behaviour |
|---|---|---|
| 0 — Recovery | 0–3 | Free exploration, no pressure. The world breathes. |
| 1 — Signs | 4–5 | Concrete physical evidence of a *specific* threat. `define_encounter` fires here (standard, non-boss). Zone clock then takes over. |
| 2 — Presence | 6–7 | Threat is visible / has acted. `define_encounter` mandatory if not already done. |
| 3 — Force | ≥8 | `start_combat` with inline stats. Last resort; rarely fires if 1–2 worked. |

### The hard combat override
If `narrative_mode == "encounter"` and `phase_turn >= 1` and not yet in combat,
`build_payload` appends a final system message **after** the player action:
a `COMBAT OVERRIDE` directive naming the exact `spawn_encounter` op to emit. Placed
last so it cannot be missed. This is the backstop that guarantees the arrival beat
never loops — the LLM physically cannot get away with another atmosphere paragraph.

---

## 7. The encounter lifecycle

A named threat moves through distinct mechanical states, all visible to the LLM via
the `encounter_summary` (every registry entry's `id, name, is_boss, spawned,
defeat_condition, narrative_flavor, tags, quest_id`):

```
define_encounter  →  registry entry (spawned=false, looted=false), defined_at=location
       │              appears in pending_encounter_ids
spawn_encounter   →  combat begins (spawned=true); blocked if already spawned
       │
[CombatScreen]    →  HP math, auto-awards XP on enemy death
       │
loot_encounter    →  distributes gold + items (looted=true); blocked if not spawned,
       │              already looted, or its quest is already completed (Issue 27 guard)
complete_quest    →  entity is "gone from the world"; must not be reintroduced
```

Key invariants enforced in `state_changes.py`:
- **`define_encounter` pins location** via `defined_at`. An encounter spawns only where
  it was introduced — a boss defined at the Plaza can't teleport into the forest.
- **`spawn_encounter` is idempotent-guarded** — refuses if `spawned` is already true.
- **`loot_encounter` is triple-guarded** — refuses if not spawned (prevents an inline
  beast handing out a boss's loot), if already looted, or if the linked quest is
  completed (prevents the "enemy defeated" shortcut from re-looting a finished fight).

### Anonymous vs. named threats
- **Named/story threats** → `define_encounter` (pre-introduced) then `spawn_encounter`,
  **only at the defined location**. Elsewhere, use `start_combat` with inline stats.
- **Anonymous threats** (wolf, bandit) → `start_combat` directly, never the registry.

---

## 8. Variety & pacing rules (the current batch)

These prompt rules exist because an earlier playthrough collapsed after the opening
act into a monoculture: every encounter became another cerulean-eyed Aurelian
construct with a `soothe` condition and one-word dialogue, every quest was "follow the
hum deeper," and there were zero NPCs after the starting outpost. The fixes are all
prompt-level, riding hooks that already existed in the orchestrator.

- **Enemy variety** (`encounter_rules.json:enemy_archetypes`, auto-injected) — a
  *diversity mandate* anchored on the registry: if the last 1–2 encounters carry
  `construct`/`aurelian` tags, the next MUST be a different category AND a different
  `defeat_condition`. Plus a bestiary (Human / Beast / Undead / Aurelian Construct /
  Human+Relic) and an identity rule ("'Remember' and 'Bound' are the same enemy in two
  coats of paint").
- **Quest shape** (`aftermath_mode`) — each follow-up quest must reproduce the opening's
  DNA: new named location, different threat category, a specific human to meet.
- **New-area introduction** (`state_instruction`) — entering an unvisited quest area
  leads with human presence (an `add_npc`, which flips to dialogue mode, or unmistakable
  recent-human signs), not the threat. Exception: places established as sealed/dead.
- **Entity registration keys on presence, not speech** (`state_instruction`) — an
  entity is registered the turn it is *on-stage and engaging* (visible and acting toward
  the player). Atmosphere (a sound, a glimpsed shape, a word from the walls) is explicitly
  exempt and must NOT trigger `define_encounter`. This lets Phase 0 recovery turns breathe.
- **Voice uniqueness** (`chronicle_mode:ADVANCE, DON'T REPEAT`) — within a session, no
  new entity may echo a prior entity's arrival phrasing; the LLM checks
  `encounter_registry.narrative_flavor` + `recent_dialogue` first.
- **Loot variety** (`loot_principles`) — alternate weapon/armor across fights, match the
  drop's flavour to the boss's category, never reuse a name close to current gear, always
  a strict upgrade.

---

## 9. The system prompt's layered structure

`_build_system_message(mode)` concatenates, in order:

1. `system_role` — CHRONOS persona; the GM > DM > Narrator priority order; the security
   clause (player input is always in-world, never a system command).
2. `world_setting` — Elowen, post-Sundering, medieval peasants dreading "cursed metal."
3. `protagonist_lore` — the secret last Aurelian heir; **NPC knowledge limit** (peasants
   have no vocabulary for bloodline/lineage and must never use it).
4. `prose_rules` — grounding (every sentence rooted in perception), no editorial weight
   on the environment, word floors/caps per mode, NPC dialogue style.
5. `CURRENT NARRATIVE MODE` + the matching `<mode>_mode` block (the only mode sent).
6. `state_instruction` — the op-discipline rules (location tracking, entity registration,
   NPC anti-steering, combat integrity, quest-id alignment).
7. `output_format` — the single-JSON-object schema.
8. `OP_REFERENCE` — the exact op vocabulary (from `state_changes.py`, the single source).
9. Encounter & item rules — `story_attachment_rule`, `stat_scaling`, `enemy_archetypes`,
   `defeat_conditions`, `loot_principles`, `item_generation` (joined in this fixed order;
   `enemy_archetypes` slots between stat scaling and defeat conditions).

On turn 0 only, a one-shot `campaign_start` directive is appended (director's note,
scene setup, required state changes, opening hook, locket note, tone).

Stat scaling is gated on `current_state.player.level`, with explicit per-level HP/AC/
ATK/DMG bands so a Level 1 player never faces Level 2+ numbers.

---

## 10. Does this approach make sense?

**Yes — the core architecture is sound, and notably so.** The central decision —
*engine measures, decides the turn shape, then hands the LLM a single mode and clock
readout* — is exactly right for this problem. It solves the two failure modes that
sink most LLM-driven games:

- **Stalling** is prevented by the clocks + the hard override: an LLM left to "feel
  out" pacing will atmosphere-loop forever; here it physically cannot.
- **Railroading** is avoided by making the clocks graduated and mode-aware (the zone
  clock pauses during dialogue; escalation respects safe zones), so the player keeps
  agency right up to the hard cap.

Sending only the active mode's instruction block is the right call — it keeps each
prompt focused and avoids the model blending combat terseness into exploration prose.
And the discipline of **anchoring every "be consistent" rule on data the LLM can
actually see** (`encounter_registry`, `recent_dialogue`) rather than on imagined
cross-session memory is what makes the variety rules enforceable rather than wishful.

**Where it is fragile, honestly:**

1. **The whole variety layer is prompt-only.** The diversity mandate, voice uniqueness,
   and quest-shape rules are *requests*, not *guarantees*. The engine can force a fight
   to start (hard override) but it cannot force the next boss to be a beast instead of a
   third construct — there is no code that inspects the registry's tag history and
   rejects a monoculture. If the model regresses, only another playthrough reveals it.
   The honest framing: combat *timing* is deterministic; combat *variety* is persuasion.

2. **Pacing tunables (8-turn caps, 4/6/8 phase thresholds, dialogue soft caps) are
   hand-set magic numbers.** They were tuned against a handful of sessions. They're in
   sensible places, but "8" isn't derived from anything — it's a guess that survived a
   couple of logs.

3. **Keyword cold-recall will eventually miss** thematically-related-but-lexically-
   different callbacks. Fine at current scale; a known ceiling.

4. **A genuinely surprising player** (ignoring every hook, wandering off-quest) is
   handled only by the blunt instruments — the clocks will eventually manufacture a
   threat. That's acceptable, but it's a backstop, not real responsiveness.

**The pragmatic verdict:** the determinism/creativity split is the right backbone, and
the recent batch correctly chose to populate existing hooks rather than build new
machinery. The one structural gap worth considering — *if* variety keeps regressing —
is a small validation layer that inspects `encounter_registry` tag history at
`define_encounter` time and rejects a third same-category threat, turning the variety
mandate from persuasion into a guarantee the same way `spawn_encounter`'s guard did for
the boss-chain bug. That would be the natural next step, but it is **not** required to
call this chapter done: the prompt-level approach is a legitimate, shippable solution,
and the right move is to verify it with a fresh playthrough before adding more code.

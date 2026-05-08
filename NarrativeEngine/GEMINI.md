# ChronosTUI: Project Guidelines

## Project Goal
Develop a hybrid RPG narrative engine bridging deterministic state management (mathematical game rules) with non-deterministic storytelling (LLM API generation).

## Technical Stack
- **Language:** Python 3.11+
- **Frontend UI:** `Textual` and `Rich`.
- **Backend Logic:** Standard Python libraries (`json`, `dataclasses`, `random`).
- **AI Integration:** External LLM APIs (OpenAI or Anthropic) using asynchronous requests.

## Core Architecture & State Flow
1. **State Update:** Deterministic backend processes inputs.
2. **Serialization:** State serialized to JSON payload.
3. **Prompt Orchestration:** JSON state injected into system prompt.
4. **LLM Generation:** Async call to LLM for narrative text.
5. **UI Rendering:** `Textual` app updates widgets without blocking.

## Team Division
- **Slice A (Exploration Engine):** World-state, NPCs, dialogue UI, narrative prompts.
- **Slice B (Combat Engine):** Math resolution (HP, damage, dice), combat UI, combat prompt engineering.

## Strict Coding Directives
- **UI/Logic Separation:** `Textual` widgets handle rendering/events only. Logic in independent classes.
- **Asynchrony:** All LLM and heavy processing must be `async`/`await`.
- **Type Safety:** Rigorous type hinting required.
- **Testability:** Backend functions must be unit-testable in isolation.

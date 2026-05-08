# ChronosTUI: Project Guidelines

## Project Goal
Develop a hybrid RPG narrative engine bridging deterministic state management (mathematical game rules) with non-deterministic storytelling (LLM API generation).

## Technical Stack
- **Language:** Python 3.11+
- **Frontend UI:** `Textual` and `Rich`.
- **Backend Logic:** Standard Python libraries (`json`, `dataclasses`, `random`).
- **AI Integration:** OpenRouter API (Accessing Claude, GPT, etc.) using asynchronous requests.

## Core Architecture & State Flow
1. **State Update:** Deterministic backend (`GameEngine`) processes inputs and updates the `GameState`.
2. **Serialization:** `GameState` is serialized to a JSON payload, including player stats, world state, and history.
3. **Prompt Orchestration:** `PromptOrchestrator` injects filtered state into the system prompt.
4. **LLM Generation:** Async call to OpenRouter for narrative text using OpenAI-compatible formatting.
5. **UI Rendering:** `Textual` app updates widgets (logs, stats, inventory) without blocking.

## Implementation Details

### State Tracking & Persistence
- **GameState (`engine/models.py`):** The single source of truth. Contains `Player`, `WorldState`, `Location` maps, `Quest` tracking, and `story_history`.
- **Persistence:** Automatic JSON serialization. Supports saving/loading from `savegame.json`.
- **Logs:** Maintains a `log` of all actions and narrative responses for UI display and short-term context.

### AI Client & OpenRouter (`ai/client.py`)
- **Provider:** OpenRouter is used as a gateway to multiple LLMs (defaulting to Claude 3 Haiku).
- **Environment Variables:**
    - `OPENROUTER_API_KEY`: Required for authentication.
    - `LLM_MODEL`: (Optional) The model ID to use (e.g., `anthropic/claude-3.5-sonnet`).
- **Standardization:** Uses the standard OpenAI message format `{"role": "...", "content": "..."}`.

### Context Loading Strategy (Prompt Engineering)
- **Lean State Strategy:** To optimize token usage, the orchestrator filters the `GameState` to only include data relevant to the current turn (e.g., local NPCs, active quests, immediate player stats).
- **System Prompt:** Loaded from `prompts/system_prompt.json`, defining role, world setting, and narrative style.

### Story Tracking & Dynamic Recall
- **PlotPoints:** Significant narrative events are recorded as `PlotPoint` objects in `story_history`.
- **Dynamic Deep Recall:** 
    - **Condensed Background:** Always includes the last 3 plot points in the prompt context.
    - **Keyword Matching:** The orchestrator searches all past `PlotPoints` for tags matching the current location or user input. Matching events are injected as "extensive recall" context.

## Team Division
- **Slice A (Exploration Engine):** World-state, NPCs, dialogue UI, narrative prompts.
- **Slice B (Combat Engine):** Math resolution (HP, damage, dice), combat UI, combat prompt engineering.

## Strict Coding Directives
- **UI/Logic Separation:** `Textual` widgets handle rendering/events only. Logic in independent classes (`GameEngine`, `ExplorationManager`).
- **Asynchrony:** All LLM and heavy processing must be `async`/`await`.
- **Type Safety:** Rigorous type hinting required using Python's `typing` and `dataclasses`.
- **Testability:** Backend functions must be unit-testable in isolation.

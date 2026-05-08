# ChronosTUI Memory

## Project Status
- Project initialized with Textual/Rich TUI.
- Core directory structure established: `engine/`, `ui/`, `ai/`.
- `GameState` and `Player` models defined in `engine/models.py`.
- `ChronosApp` skeleton implemented in `ui/app.py`.
- Placeholders for Slice A (Exploration) and Slice B (Combat) created.

## Next Steps
- Implement JSON serialization for `GameState` to prepare for LLM context injection.
- Expand `ExplorationManager` with basic location-based event logic.
- Expand `CombatManager` with a turn-based resolution loop.
- Integrate `AIClient` into the TUI to handle narrative generation asynchronously.

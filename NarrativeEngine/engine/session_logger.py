"""Append-only JSONL session log — one file per play session.

Format — one JSON object per line:

  System event:
    {"ts": "2026-05-28T10:30:00.123", "type": "system", "msg": "New game — Mage — turn 0"}

  Turn:
    {"ts": "...", "type": "turn", "t": 1, "loc": "The Overgrown Outpost", "hp": 15,
     "player": "look around", "narrative": "...", "changes": [...], "warnings": [...]}

Files land in  <project_root>/logs/session_YYYY-MM-DD_HH-MM-SS.jsonl
The file is line-buffered so every turn flushes immediately; no data is lost on crash.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# Anchor to project root regardless of the current working directory.
_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_LOG_DIR = _PROJECT_ROOT / "logs"


class SessionLogger:
    """Write a JSONL log file for one play session.

    Usage::

        logger = SessionLogger()          # opens logs/session_<stamp>.jsonl
        logger.log_system("New game — Mage — turn 0")
        logger.log_turn(turn_count=1, player_input="look around",
                        narrative="...", state_changes=[...],
                        location="Outpost", hp=15)
        logger.close()                    # called in on_unmount
    """

    def __init__(self, log_dir: Optional[Path | str] = None) -> None:
        log_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.path: Path = log_dir / f"session_{stamp}.jsonl"

        # buffering=1  → line-buffered; each write flushes when '\n' is written
        self._file = open(self.path, "w", encoding="utf-8", buffering=1)

    # ── Public API ────────────────────────────────────────────────────────────

    def log_system(self, message: str) -> None:
        """Record a system-level event (game start, load, save, crash)."""
        self._write({"ts": _ts(), "type": "system", "msg": message})

    def log_turn(
        self,
        turn_count: int,
        player_input: str,
        narrative: str,
        state_changes: List[Dict[str, Any]],
        location: str,
        hp: int,
        parse_warnings: Optional[List[str]] = None,
    ) -> None:
        """Record a complete narrative turn."""
        obj: Dict[str, Any] = {
            "ts":        _ts(),
            "type":      "turn",
            "t":         turn_count,
            "loc":       location,
            "hp":        hp,
            "player":    player_input,
            "narrative": narrative,
            "changes":   state_changes,
        }
        if parse_warnings:
            obj["warnings"] = parse_warnings
        self._write(obj)

    def close(self) -> None:
        """Flush and close the log file."""
        try:
            self._file.close()
        except Exception:
            pass

    # ── Internal ──────────────────────────────────────────────────────────────

    def _write(self, obj: Dict[str, Any]) -> None:
        try:
            self._file.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except Exception:
            pass  # never let logging errors surface to the player


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().isoformat(timespec="milliseconds")

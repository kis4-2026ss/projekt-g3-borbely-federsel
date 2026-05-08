"""Parse the LLM's structured JSON response into a typed result.

If the model returns malformed JSON, we fall back to treating the whole text as
narrative so the player still sees something. Parse failures are surfaced via
`parse_warnings` so the UI can show a quiet diagnostic.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class NarrativeResponse:
    narrative: str
    state_changes: List[Dict[str, Any]] = field(default_factory=list)
    plot_point: Optional[Dict[str, Any]] = None
    parse_warnings: List[str] = field(default_factory=list)


_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def parse_response(raw: str) -> NarrativeResponse:
    if not raw or not raw.strip():
        return NarrativeResponse(
            narrative="(The narrator's voice trails into silence...)",
            parse_warnings=["empty response"],
        )

    raw = raw.strip()

    # 1) try the whole string as JSON
    parsed = _try_json(raw)

    # 2) try the first {...} block
    if parsed is None:
        match = _JSON_BLOCK.search(raw)
        if match:
            parsed = _try_json(match.group(0))

    if not isinstance(parsed, dict):
        return NarrativeResponse(
            narrative=raw,
            parse_warnings=["LLM did not return JSON; treating response as plain narrative"],
        )

    warnings: List[str] = []

    narrative = parsed.get("narrative")
    if not isinstance(narrative, str) or not narrative.strip():
        warnings.append("missing or empty 'narrative' field; using raw text")
        narrative = raw
    else:
        narrative = narrative.strip()

    state_changes = parsed.get("state_changes", [])
    if not isinstance(state_changes, list):
        warnings.append("'state_changes' was not a list; ignored")
        state_changes = []

    plot_point = parsed.get("plot_point")
    if plot_point is not None and not isinstance(plot_point, dict):
        warnings.append("'plot_point' was not an object; ignored")
        plot_point = None

    return NarrativeResponse(
        narrative=narrative,
        state_changes=state_changes,
        plot_point=plot_point,
        parse_warnings=warnings,
    )


def _try_json(s: str) -> Optional[Any]:
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None

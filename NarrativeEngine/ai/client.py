import os
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()


class AIClient:
    """Async client for any OpenAI-compatible chat completions endpoint.

    Defaults to GitHub Models. To switch provider, set in .env:
      LLM_API_URL=https://api.openai.com/v1/chat/completions
      LLM_API_KEY=sk-...          (falls back to GITHUB_TOKEN if not set)
      LLM_MODEL=gpt-4o-mini
    """

    DEFAULT_URL = "https://models.inference.ai.azure.com/chat/completions"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("GITHUB_TOKEN")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4.1-mini")
        self.api_url = os.getenv("LLM_API_URL", self.DEFAULT_URL)
        # Persistent client — reuses the TCP+TLS connection across calls.
        # keepalive_expiry=15s: drop idle connections before the Azure server
        # does, preventing stale-connection ReadTimeouts on the reused socket.
        self._http_client = self._make_client()

    @staticmethod
    def _make_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=1,
                keepalive_expiry=15,   # seconds; Azure closes ~20s, so we drop first
            ),
        )

    async def aclose(self) -> None:
        """Close the persistent HTTP connection pool. Call on app shutdown."""
        await self._http_client.aclose()

    async def generate_narrative(
        self,
        payload: List[Dict[str, str]],
        json_mode: bool = True,
    ) -> str:
        """Send the chat payload and return the assistant's content as a string.
        With json_mode=True the model is asked to return a single JSON object."""
        if not self.api_key:
            return "ERROR: No GITHUB_TOKEN found. Please check your .env file."

        data: Dict[str, Any] = {
            "messages": payload,
            "model": self.model,
            "temperature": 0.8,
            "max_tokens": 1800,
            "top_p": 1,
        }
        if json_mode:
            data["response_format"] = {"type": "json_object"}

        return await self._post(data)

    async def summarize(self, prior_summary: str, recent_log: List[str]) -> str:
        """Roll the prior summary forward with new events. Plain prose, no JSON."""
        if not self.api_key:
            return prior_summary or ""

        system = (
            "You are summarizing an ongoing dark-fantasy RPG session for later recall. "
            "Produce a single concise paragraph (max 5 sentences) capturing the key facts, "
            "discoveries, decisions, and unresolved tensions. Preserve names, locations, "
            "and cause-effect links. Do not invent events. Plain prose only — no JSON, "
            "no headings, no bullet points."
        )
        prior = prior_summary.strip() or "(no prior summary)"
        recent = "\n".join(recent_log) if recent_log else "(no recent log entries)"
        user = (
            f"PRIOR SESSION SUMMARY:\n{prior}\n\n"
            f"NEW EVENTS SINCE PRIOR SUMMARY:\n{recent}\n\n"
            "Updated summary:"
        )

        data: Dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "model": self.model,
            "temperature": 0.3,
            "max_tokens": 350,
            "top_p": 1,
        }
        result = await self._post(data)
        if not result or result.startswith("ARCANE ERROR") or result.startswith("ERROR:"):
            return prior_summary
        return result.strip()

    async def _post(self, data: Dict[str, Any]) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/projekt-g3-borbely-federsel",  # any URL
            "X-Title": "ChronosTUI",
        }
        #headers = {
        #    "Content-Type": "application/json",
        #    "Authorization": f"Bearer {self.api_key}",
        #}
        # Two attempts: first uses the persistent connection (fast path).
        # On any connection/timeout error, recreate the client and retry once
        # on a fresh socket — covers both stale-connection and transient failures.
        for attempt in range(2):
            try:
                response = await self._http_client.post(
                    self.api_url, headers=headers, json=data, timeout=45.0
                )
                if response.status_code != 200:
                    return (
                        f"ARCANE ERROR: The ley lines are unstable (HTTP {response.status_code}). "
                        f"Details: {response.text[:200]}"
                    )
                result = response.json()
                if "choices" in result and result["choices"]:
                    content = result["choices"][0].get("message", {}).get("content")
                    if content:
                        return content.strip()
                return "The ancient winds remain silent... (No response from AI)"
            except (httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                if attempt == 0:
                    # Likely a stale connection — drop it and try once more fresh.
                    try:
                        await self._http_client.aclose()
                    except Exception:
                        pass
                    self._http_client = self._make_client()
                    continue
                detail = str(e).strip() or type(e).__name__
                return f"ARCANE ERROR: Connection lost in the mists. ({detail})"
            except httpx.HTTPError as e:
                detail = str(e).strip() or type(e).__name__
                return f"ARCANE ERROR: Connection lost in the mists. ({detail})"
            except Exception as e:
                detail = str(e).strip() or type(e).__name__
                return f"ARCANE ERROR: {detail}"
        return "ARCANE ERROR: All connection attempts failed."

import httpx
import os
import json
from typing import Dict, Any, List
from dotenv import load_dotenv

load_dotenv()


class AIClient:
    """Handles asynchronous calls to the GitHub Models API."""

    def __init__(self, api_key: str = None, model: str = None):
        # Pull the GITHUB_TOKEN from environment
        self.api_key = api_key or os.getenv("GITHUB_TOKEN")

        # GitHub Models use standard names like 'gpt-4o-mini' or 'meta-llama-3.1-405b-instruct'
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")

        # The base URL for GitHub Models inference
        self.api_url = "https://models.inference.ai.azure.com/chat/completions"

    async def generate_narrative(self, payload: List[Dict[str, str]]) -> str:
        """
        Sends the prompt payload to GitHub Models and returns the generated text.
        GitHub Models uses the standard OpenAI-style chat completion format.
        """
        if not self.api_key:
            return "ERROR: No GITHUB_TOKEN found. Please check your .env file."

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        data = {
            "messages": payload,
            "model": self.model,
            "temperature": 0.7,
            "max_tokens": 1000,
            "top_p": 1
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.api_url,
                    headers=headers,
                    json=data,
                    timeout=45.0
                )

                if response.status_code != 200:
                    return (f"ARCANE ERROR: The ley lines are unstable (HTTP {response.status_code}). "
                            f"Details: {response.text[:100]}")

                result = response.json()

                # Extract text using the OpenAI-standard response path
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0].get("message", {}).get("content")
                    if content:
                        return content.strip()

                return "The ancient winds remain silent... (No response from AI)"

            except httpx.HTTPError as e:
                return f"ARCANE ERROR: Connection lost in the mists. ({str(e)})"
            except Exception as e:
                return f"ARCANE ERROR: {str(e)}"
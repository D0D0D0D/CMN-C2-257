"""AgentCore Platform v1.0 — LLM injection seam (CMN-C2-257)."""

# Nodes never construct concrete LLM clients themselves; they receive a
# BaseLLM via constructor injection (tests inject fakes) or fall back to
# build_default_llm(), which sources the API key from the per-agent secret
# provider (ctx.secrets) — never os.environ (S-3 secret isolation).

from __future__ import annotations

import json
import re

from shared.services.llm.base_llm import BaseLLM


class LLMServiceError(RuntimeError):
    """LLM call failed or returned an unusable payload."""


def build_default_llm(config: dict | None = None) -> BaseLLM:
    """Build the production default LLM client (Anthropic).

    The API key is read from the bound secret provider. langchain_anthropic
    is imported lazily so test environments without the optional dependency
    can still import this module.
    """
    cfg = dict(config or {})
    try:
        from shared.secrets.context import current_secrets

        api_key = current_secrets().get("ANTHROPIC_API_KEY")
    except Exception as e:  # provider not bound (e.g. bare test invoke)
        raise LLMServiceError(f"secret provider unavailable: {e}") from e
    if not api_key:
        raise LLMServiceError(
            "ANTHROPIC_API_KEY is not provisioned. Declare it in agent.yaml "
            "requires.secrets and provision via the platform secret provider."
        )
    from shared.services.llm.anthropic_client import AnthropicClient

    client_cfg = {"api_key": api_key}
    for key in ("model", "temperature", "max_tokens", "timeout"):
        if key in cfg:
            client_cfg[key] = cfg[key]
    return AnthropicClient(client_cfg)


def complete_text(llm: BaseLLM, system: str, user: str) -> str:
    """Single completion returning the text content."""
    response = llm.complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    content = response.get("content", "") if isinstance(response, dict) else ""
    if not isinstance(content, str) or not content.strip():
        raise LLMServiceError("LLM returned empty content")
    return content


def parse_json_payload(text: str) -> dict:
    """Parse a JSON object from an LLM response, tolerating ```json fences."""
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", candidate, re.DOTALL)
        if brace:
            candidate = brace.group(0)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise LLMServiceError(f"LLM response is not valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise LLMServiceError("LLM JSON payload is not an object")
    return parsed

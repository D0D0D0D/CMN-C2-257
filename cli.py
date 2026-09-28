"""AGENTIC STAR Marketplace container entry adapter for CMN-C2-257."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from framework.secrets.base import SecretProvider
from framework.utils.config_loader import load_agent_config
from shared.bootstrap import run_agent_marketplace
from shared.secrets.env_provider import EnvProvider

from src.graph.graph import SalesProposalDraftAgent
from src.services.azure_llm import AzureLLM

_NAMESPACE = "cmn"
_AGENT_NAME = "CMN-C2-257"
_PROVIDER_AGENT_NAME = "cmn_c2_257"

_AZURE_OPENAI_SECRET_KEYS = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
)
_REQUIRED_SECRET_KEYS = (*_AZURE_OPENAI_SECRET_KEYS, "SALES_KB_EMBEDDING_API_KEY")
_ALLOWED_SECRET_KEYS = frozenset(_REQUIRED_SECRET_KEYS)


class _AllowlistedEnvironmentProvider(EnvProvider):  # type: ignore[misc]
    """Read only this agent's declared secrets from the process environment."""

    def __init__(self) -> None:
        super().__init__(namespace=_NAMESPACE, agent_name=_PROVIDER_AGENT_NAME)

    def get(self, key: str, default: str | None = None) -> str | None:
        if key not in _ALLOWED_SECRET_KEYS:
            return default
        value = super().get(key)
        return value if value else default


def _marketplace_input_context() -> dict[str, Any]:
    """Build the complete context accepted at the Marketplace boundary."""

    # The domain design makes format hints optional; None means no requested
    # tone or chapter override. Do not accept runner-owned context additions.
    return {"proposal_format": None}


def _require_azure_endpoint(provider: SecretProvider) -> str:
    """Reject ``AZURE_OPENAI_ENDPOINT`` values that are not a bare Azure resource endpoint.

    ``AzureOpenAIClient`` appends its own ``/openai/v1/`` path, so anything
    beyond a scheme and host silently produces a broken request URL, and the
    failure surfaces inside ``complete()``. A node that swallows that exception
    degrades quietly while the invocation still reports ``status: success``,
    which is why this has to be a startup check. The value is reported here,
    never repaired.
    """

    endpoint = provider.require("AZURE_OPENAI_ENDPOINT")
    parts = urlsplit(endpoint)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or port == -1
        or parts.path.strip("/")
        or parts.query
        or parts.fragment
        or any(character.isspace() for character in endpoint)
    ):
        raise ValueError(
            "Marketplace environment variable 'AZURE_OPENAI_ENDPOINT' must be a bare Azure "
            "resource endpoint -- scheme and host only, with no path (such as "
            "'/openai'), query or fragment"
        )
    return endpoint


class MarketplaceGraph(SalesProposalDraftAgent):
    """Marketplace-only boundary hardening and dependency wiring."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        # The runner calls compile() -- and therefore register_nodes() -- before
        # provision_secrets(). register_nodes() reads config["llm"] to build
        # PreProcessNode, so the client has to be in config at construction.
        # Setting it in provision_secrets() is too late: the node keeps llm=None
        # and falls back to build_default_llm(), which requires ANTHROPIC_API_KEY
        # -- a secret Marketplace does not provision.
        merged = dict(config or {})
        # `setdefault` is deliberately NOT used: a config.yaml carrying an
        # explicit `llm: null` already HAS the key, so setdefault would silently
        # no-op and the Pod would run unwired while every test stayed green.
        # Guard on the value, not on key presence.
        if merged.get("llm") is None:
            merged["llm"] = AzureLLM()
        super().__init__(config=merged)

    def invoke(
        self,
        user_input: str,
        session_id: str = "",
        ctx: Any | None = None,
        input_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Marketplace conversation history is untrusted free text and is not
        # covered by this agent's S-2 input gate. Rebuild adapter-owned context
        # from scratch so present and future runner keys cannot enter State.
        del input_context
        return cast(
            dict[str, Any],
            super().invoke(
                user_input,
                session_id=session_id,
                ctx=ctx,
                input_context=_marketplace_input_context(),
            ),
        )

    def provision_secrets(self, provider: SecretProvider) -> None:
        # The Marketplace runner's default provider may include dotenv fallback
        # and unrestricted environment lookup. This adapter deliberately owns a
        # smaller process-environment boundary for this single-agent container.
        del provider
        environment_provider = _AllowlistedEnvironmentProvider()
        for key in _REQUIRED_SECRET_KEYS:
            environment_provider.require(key)

        _require_azure_endpoint(environment_provider)
        super().provision_secrets(environment_provider)


def main() -> None:
    config = load_agent_config(Path(__file__).resolve().parent)
    run_agent_marketplace(
        MarketplaceGraph,
        agent_name=_AGENT_NAME,
        namespace=_NAMESPACE,
        config=config,
    )


if __name__ == "__main__":
    main()

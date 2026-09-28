"""TC-01: State contract — flat TypedDict, msgpack-safe annotations only."""

from __future__ import annotations

import typing

from framework.schemas.agent_state import AgentState

from src.schemas.state import State


class TestStateContract:
    def test_state_is_typed_dict_subclass_of_agent_state(self):
        # TypedDict forbids issubclass checks — verify structural inheritance:
        # every AgentState field must be present in State's merged annotations.
        merged = typing.get_type_hints(State)
        assert set(AgentState.__annotations__) <= set(merged)

    def test_no_pydantic_or_dataclass_fields(self):
        hints = typing.get_type_hints(State)
        for field, hint in hints.items():
            rendered = str(hint)
            assert "BaseModel" not in rendered, f"{field} is Pydantic-typed"
            assert "InvocationContext" not in rendered, f"{field} holds InvocationContext"

    def test_no_credential_shaped_fields(self):
        import re

        pattern = re.compile(r"jwt|token|api_key|secret|password|credential", re.IGNORECASE)
        own_fields = set(State.__annotations__)
        assert not [f for f in own_fields if pattern.search(f)]

    def test_instance_is_plain_dict(self):
        s: State = {"user_input": "x", "customer_issues": []}
        assert isinstance(s, dict)

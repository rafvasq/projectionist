"""A Mellea backend that returns scripted text, for testing @generative functions offline."""

from __future__ import annotations

from mellea.backends.dummy import DummyBackend
from mellea.core.base import GenerateLog
from mellea.stdlib.session import MelleaSession


class ScriptedBackend(DummyBackend):
    """
    DummyBackend refuses structured-output requests and omits the generation log real
    backends attach; this adds both so a @generative call runs against scripted answers.
    """

    async def _generate_from_context(self, action, ctx, *, format=None, model_options=None, tool_calls=False):
        mot, new_ctx = await super()._generate_from_context(
            action, ctx, format=None, model_options=model_options, tool_calls=tool_calls
        )
        mot._generate_log = GenerateLog()
        return mot, new_ctx


def scripted_session(*responses: str) -> tuple[MelleaSession, ScriptedBackend]:
    """A session that answers with the given strings in order, and its backend (`.idx` counts calls)."""
    backend = ScriptedBackend(list(responses))
    return MelleaSession(backend), backend

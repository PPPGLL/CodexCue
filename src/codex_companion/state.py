from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestToken:
    generation: int
    draft: str
    context_revision: int


class SuggestionState:
    """Pure state machine: late model output can never target a newer draft."""

    def __init__(self, debounce_seconds: float = 0.3) -> None:
        self.debounce_seconds = debounce_seconds
        self.draft = ""
        self.context_revision = 0
        self.generation = 0
        self.changed_at = 0.0
        self.started_generation = -1
        self.active: RequestToken | None = None
        self.suggestion = ""

    def observe(self, draft: str, context_revision: int, now: float) -> bool:
        if draft == self.draft and context_revision == self.context_revision:
            return False
        self.draft = draft
        self.context_revision = context_revision
        self.generation += 1
        self.changed_at = now
        self.suggestion = ""
        self.active = None
        return True

    def ready(self, now: float) -> bool:
        return (
            bool(self.draft.strip())
            and self.active is None
            and self.started_generation != self.generation
            and now - self.changed_at >= self.debounce_seconds
        )

    def start(self) -> RequestToken:
        token = RequestToken(self.generation, self.draft, self.context_revision)
        self.started_generation = self.generation
        self.active = token
        return token

    def partial(self, token: RequestToken, text: str) -> bool:
        if token != self.active or token.generation != self.generation:
            return False
        self.suggestion = text
        return True

    def finish(self, token: RequestToken, text: str) -> bool:
        if token != self.active:
            return False
        self.active = None
        if token.generation != self.generation:
            return False
        self.suggestion = text
        return True

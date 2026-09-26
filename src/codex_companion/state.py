from __future__ import annotations

from dataclasses import dataclass

KEYBOARD_QUIET_SECONDS = 0.0


@dataclass(frozen=True)
class RequestToken:
    generation: int
    draft: str
    context_revision: int


@dataclass(frozen=True)
class EditorSnapshot:
    """One eligibility contract shared by requests, display and acceptance."""
    enabled: bool
    inserting: bool
    dirty: bool
    pending_activity: bool
    armed: bool
    composing: bool
    draft: str
    read_matches: bool
    hwnd: int
    foreground: int
    has_bounds: bool
    quiet_seconds: float
    context_allowed: bool

    def rejection(self) -> str:
        if not self.enabled:
            return "paused"
        if self.inserting:
            return "inserting"
        if self.composing:
            return "ime_composing"
        if self.dirty or self.pending_activity:
            return "draft_unconfirmed"
        if not self.draft.strip() or not self.armed:
            return "empty_or_unarmed"
        if not self.hwnd or self.hwnd != self.foreground:
            return "focus_changed"
        if not self.read_matches or not self.has_bounds:
            return "editor_unavailable"
        if not self.context_allowed:
            return "context_unresolved"
        if self.quiet_seconds < KEYBOARD_QUIET_SECONDS:
            return "typing"
        return "ready"


class SuggestionState:
    """Pure state machine: late model output can never target a newer draft."""

    def __init__(self, debounce_seconds: float = KEYBOARD_QUIET_SECONDS) -> None:
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

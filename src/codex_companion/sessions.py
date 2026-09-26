from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    text: str
    kind: Literal["dialogue", "task_summary"] = "dialogue"


@dataclass(frozen=True)
class SessionInfo:
    id: str
    path: Path
    cwd: str
    created_at: str
    last_event_at: str
    preview: str
    recent_texts: tuple[str, ...] = ()
    recent_roles: tuple[Role, ...] = field(default=(), repr=False, compare=False)
    match_texts: tuple[str, ...] = field(default=(), repr=False, compare=False)
    opening_text: str = field(default="", repr=False, compare=False)
    task_title: str = field(default="", repr=False, compare=False)
    title_is_unique: bool = field(default=True, repr=False, compare=False)

_AMBIENT_PREFIXES = ("recommended_plugins", "environment_context", "in-app-browser-context")
_REVIEW_PREFIX = "The following is the Codex agent history "
_HANDOFF_PREFIX = "Another language model started to solve this problem and produced a summary"


def _clean_user_text(text: str) -> str:
    """Keep the typed request, removing known Codex runtime envelopes."""
    value = text.strip()
    if value == "</image>" or re.fullmatch(
            r'<image\s+name=\[Image #\d+\]\s+path="[^"]+">', value):
        return ""  # Attachment markers are not text the user typed.
    if value.startswith((_REVIEW_PREFIX, _HANDOFF_PREFIX)):
        return ""
    while value:
        previous = value
        for tag in _AMBIENT_PREFIXES:
            match = re.match(rf"^<{re.escape(tag)}(?:\s[^>]*)?>", value)
            if match:
                end = value.find(f"</{tag}>", match.end())
                if end < 0:
                    return ""  # Incomplete runtime envelope is not a user turn.
                value = value[end + len(tag) + 3:].strip()
                break
        if value != previous:
            continue
        if value.startswith("# AGENTS.md instructions"):
            end = value.find("</INSTRUCTIONS>")
            if end < 0:
                return ""
            value = value[end + len("</INSTRUCTIONS>"):].strip()
            continue
        break
    if value.startswith("# Files mentioned by the user:"):
        marker = "## My request:"
        if marker not in value:
            return ""
        value = value.split(marker, 1)[1].strip()
    elif value.startswith("## My request:"):
        value = value[len("## My request:"):].strip()
    return value


def _message(payload: dict) -> Message | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "message":
        return None
    role = payload.get("role")
    if role == "assistant" and payload.get("phase") != "final_answer":
        return None
    if role not in ("user", "assistant"):
        return None
    wanted = "input_text" if role == "user" else "output_text"
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    if role == "user":
        # Codex approval review serializes its *entire* agent transcript as a
        # multipart user record. Dropping only the introductory part would
        # leak the following numbered tool calls and results into the model.
        first_text = next((item.get("text") for item in content
                           if isinstance(item, dict) and item.get("type") == "input_text"
                           and isinstance(item.get("text"), str)), "")
        if first_text.strip().startswith((_REVIEW_PREFIX, _HANDOFF_PREFIX)):
            return None
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != wanted:
            continue
        value = item.get("text")
        if not isinstance(value, str):
            continue
        value = _clean_user_text(value) if role == "user" else value.strip()
        if value:
            parts.append(value)
    text = "\n".join(parts)
    return Message(role, text) if text else None


def parse_record(line: str) -> tuple[dict | None, Message | None]:
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, None
    if not isinstance(record, dict):
        return None, None
    if record.get("type") == "response_item":
        return record, _message(record.get("payload", {}))
    return record, None


def _canonical(text: str) -> str:
    return "".join(char.lower() for char in unicodedata.normalize("NFKC", text)
                   if char.isalnum())


def _title_key(text: str) -> str:
    # Preserve punctuation: "A/B" and "AB" need not identify the same task.
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def recent_messages(path: Path, limit: int = 6,
                    max_bytes: int = 16 * 1024 * 1024,
                    end_at: int | None = None) -> list[Message]:
    """Find recent messages backwards without loading a large rollout at once."""
    found: list[Message] = []
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            position = stream.tell()
            if end_at is not None:
                position = min(position, end_at)
            scanned = 0
            remainder = b""
            first_chunk = True
            while position > 0 and len(found) < limit and scanned < max_bytes:
                size = min(262_144, position, max_bytes - scanned)
                position -= size
                scanned += size
                stream.seek(position)
                chunk = stream.read(size) + remainder
                parts = chunk.split(b"\n")
                remainder = parts.pop(0)
                if first_chunk and not chunk.endswith(b"\n"):
                    parts.pop()  # Ignore an incomplete record still being written.
                first_chunk = False
                for raw in reversed(parts):
                    if not raw:
                        continue
                    _, message = parse_record(raw.decode("utf-8", errors="replace"))
                    if message:
                        found.append(message)
                        if len(found) >= limit:
                            break
            if position == 0 and len(found) < limit and remainder:
                _, message = parse_record(remainder.decode("utf-8", errors="replace"))
                if message:
                    found.append(message)
    except OSError:
        return []
    return list(reversed(found[:limit]))


def first_user_message(path: Path, max_bytes: int = 262_144) -> str:
    """Read the opening user request for matching the desktop task title."""
    try:
        with path.open("rb") as stream:
            scanned = 0
            for _ in range(20):
                raw = stream.readline(max_bytes - scanned)
                if not raw:
                    break
                scanned += len(raw)
                if not raw.endswith(b"\n"):
                    break  # Never parse a truncated JSONL record.
                _, message = parse_record(raw.decode("utf-8", errors="replace"))
                if message and message.role == "user":
                    return message.text
                if scanned >= max_bytes:
                    break
    except OSError:
        pass
    return ""


def describe_session(path: Path, with_recent: bool = False) -> SessionInfo | None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            first = json.loads(stream.readline())
        if first.get("type") != "session_meta":
            return None
        meta = first.get("payload", {})
        with path.open("rb") as stream:
            stream.seek(0, 2)
            end = stream.tell()
            stream.seek(max(0, end - 262_144))
            tail = stream.read().decode("utf-8", errors="ignore")
        lines = tail.splitlines()
        if end > 262_144:
            lines = lines[1:]
        preview = ""
        last_at = meta.get("timestamp", "")
        for line in lines:
            record, message = parse_record(line)
            if record:
                last_at = record.get("timestamp", last_at)
            if message and message.role == "user":
                preview = " ".join(message.text.split())[:120]
        recent = recent_messages(path) if with_recent else []
        opening = first_user_message(path) if any(m.role == "user" for m in recent) else ""
        if not preview:
            preview = next((" ".join(m.text.split())[:120]
                            for m in reversed(recent) if m.role == "user"), "")
        return SessionInfo(
            id=str(meta.get("id", "")),
            path=path,
            cwd=str(meta.get("cwd", "")),
            created_at=str(meta.get("timestamp", "")),
            last_event_at=str(last_at),
            preview=preview or "(no user message in recent log)",
            recent_texts=tuple(m.text for m in recent),
            recent_roles=tuple(m.role for m in recent),
            match_texts=tuple(_canonical(m.text) for m in recent),
            opening_text=opening,
        )
    except (OSError, ValueError, UnicodeError):
        return None


def _recent_paths(root: Path, count: int) -> list[tuple[Path, tuple[int, int]]]:
    candidates: list[tuple[int, Path, int]] = []
    for path in root.glob("*/*/*/rollout-*.jsonl"):
        try:
            stat = path.stat()
        except OSError:
            continue
        candidates.append((stat.st_mtime_ns, path, stat.st_size))
    candidates.sort(reverse=True)
    return [(path, (size, mtime)) for mtime, path, size in candidates[:count]]


class SessionIndex:
    """Cache local conversation fingerprints across task switches."""

    def __init__(self, root: Path, limit: int = 80) -> None:
        self.root = root
        self.limit = limit
        self.cache: dict[Path, tuple[tuple[int, int], SessionInfo]] = {}
        self.paths: list[tuple[Path, tuple[int, int]]] = []
        self.paths_at = 0.0
        self.titles: dict[str, str] = {}
        self.title_counts: Counter[str] = Counter()
        self.title_signature: tuple[int, int] | None = None

    def _refresh_titles(self) -> None:
        path = self.root.parent / "session_index.jsonl"
        try:
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            if signature == self.title_signature:
                return
            titles = {}
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue  # The app may be appending a partial record.
                    if not isinstance(row, dict):
                        continue
                    identity, title = row.get("id"), row.get("thread_name")
                    if (isinstance(identity, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity)
                            and isinstance(title, str) and title.strip()):
                        titles[identity] = title  # Latest appended rename wins.
        except (OSError, UnicodeError):
            self.titles = {}
            self.title_counts = Counter()
            self.title_signature = None
            return
        self.titles = titles
        self.title_counts = Counter(_title_key(title) for title in titles.values())
        self.title_signature = signature

    def _with_title(self, info: SessionInfo) -> SessionInfo:
        title = self.titles.get(info.id, "")
        return replace(info, task_title=title,
                       title_is_unique=self.title_counts[_title_key(title)] == 1)

    def list(self, *, force_refresh: bool = False) -> list[SessionInfo]:
        self._refresh_titles()
        if not self.root.exists():
            return []
        now = time.monotonic()
        refreshed = force_refresh or now - self.paths_at >= .5
        if refreshed:
            self.paths = _recent_paths(self.root, self.limit + 20)
        live = {path for path, _ in self.paths}
        for path, _ in self.paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            signature = (stat.st_size, stat.st_mtime_ns)
            cached = self.cache.get(path)
            if cached and cached[0] == signature:
                continue
            info = describe_session(path, with_recent=True)
            if info:
                self.cache[path] = signature, info
        for path in self.cache.keys() - live:
            del self.cache[path]
        if refreshed:
            self.paths_at = time.monotonic()
        return sorted((self._with_title(info) for _, info in self.cache.values()),
                      key=lambda info: info.last_event_at, reverse=True)[:self.limit]

    def candidates(self, visible_texts: list[str], *, force_refresh: bool = False) -> list[SessionInfo]:
        infos = self.list(force_refresh=True) if force_refresh else self.list()
        known = {info.id for info in infos}
        visible = {_title_key(text) for text in visible_texts}
        # An old task opened without a new log event may be outside the recent
        # 80. Resolve its indexed id directly, without parsing every old rollout.
        for identity, title in self.titles.items():
            if identity in known or _title_key(title) not in visible:
                continue
            for path in self.root.glob(f"*/*/*/rollout-*{identity}.jsonl"):
                info = describe_session(path, with_recent=True)
                if info is not None and info.id == identity:
                    infos.append(self._with_title(info))
                    break
        return infos


def _match_task_title(title: str, infos: list[SessionInfo]) -> SessionInfo | None:
    """Use a short desktop title only when it clearly identifies one opening request."""
    value = _canonical(title)
    if not 10 <= len(value) <= 80:
        return None
    grams = {value[i:i + 2] for i in range(len(value) - 1)}
    scores: list[tuple[int, SessionInfo]] = []
    for info in infos:
        if info.recent_roles and "user" not in info.recent_roles:
            continue
        opening = _canonical(info.opening_text)
        if not opening:
            continue
        opening_grams = {opening[i:i + 2] for i in range(len(opening) - 1)}
        overlap = len(grams & opening_grams)
        if overlap:
            scores.append((overlap, info))
    scores.sort(key=lambda item: item[0], reverse=True)
    if not scores or scores[0][0] < 6 or scores[0][0] / len(grams) < .65:
        return None
    if len(scores) > 1 and scores[1][0] > scores[0][0] - 3:
        return None
    return scores[0][1]


def _match_unique_visible_title(visible_texts: list[str],
                                infos: list[SessionInfo]) -> SessionInfo | None:
    """A queued draft can add UI text beside the task title; require one title match."""
    candidate: SessionInfo | None = None
    for text in visible_texts:
        match = _match_task_title(text, infos)
        if match is None:
            continue
        if candidate is not None and candidate.path != match.path:
            return None
        candidate = match
    return candidate


def match_visible_session(visible_texts: list[str], infos: list[SessionInfo]) -> SessionInfo | None:
    """Match visible conversation text to one local log; ambiguous matches are unsafe."""
    visible_titles = {_title_key(text) for text in visible_texts if 2 <= len(text.strip()) <= 200}
    exact_titles = [info for info in infos if info.task_title
                    and _title_key(info.task_title) in visible_titles]
    if exact_titles:
        if (len(exact_titles) == 1 and exact_titles[0].title_is_unique
                and (not exact_titles[0].recent_roles or "user" in exact_titles[0].recent_roles)):
            return exact_titles[0]
        return None  # Never break a known title collision by guessing at its wording.
    fragments: set[str] = set()
    for raw in visible_texts:
        visible = _canonical(raw)
        if len(visible) < 12:
            continue
        width = 12
        if len(visible) <= 48:
            starts = range(0, len(visible) - width + 1, 4)
        else:
            starts = {0, (len(visible) - width) // 4,
                      (len(visible) - width) // 2,
                      3 * (len(visible) - width) // 4,
                      len(visible) - width}
        fragments.update(visible[start:start + width] for start in starts)
    if len(fragments) < 2:
        return _match_unique_visible_title(visible_texts, infos)
    scores: list[tuple[int, SessionInfo]] = []
    for info in infos:
        # Reviewer transcripts can leave final-answer records behind without
        # any real user turn. Such sessions must never become autocomplete context.
        if info.recent_roles and "user" not in info.recent_roles:
            continue
        texts = info.match_texts or tuple(_canonical(value) for value in info.recent_texts)
        score = 12 * sum(any(fragment in text for text in texts)
                         for fragment in fragments)
        if score:
            scores.append((score, info))
    scores.sort(key=lambda item: item[0], reverse=True)
    if not scores or scores[0][0] < 24:
        return _match_unique_visible_title(visible_texts, infos)
    if len(scores) > 1 and scores[1][0] >= scores[0][0] - 12:
        return _match_unique_visible_title(visible_texts, infos)
    return scores[0][1]


CONTEXT_READ_BYTES = 16 * 1024 * 1024
CONTEXT_STORAGE_BYTES = 1024 * 1024


def _bounded_dialogue(message: Message, remaining: int) -> tuple[Message, int]:
    data = message.text.encode("utf-8")
    if len(data) > remaining:
        message = replace(message, text=data[-remaining:].decode("utf-8", errors="ignore"))
    return message, min(len(data), remaining)


def _task_summary(record: dict | None) -> str | None:
    """Only the explicit compaction message, never replacement/developer history."""
    if not record or record.get("type") != "compacted":
        return None
    payload = record.get("payload")
    text = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(text, str) or len(text.encode("utf-8")) > CONTEXT_STORAGE_BYTES:
        return ""
    return text.strip()


def recent_context(path: Path, end_at: int) -> tuple[list[Message], str]:
    """Read recent dialogue and the latest compaction from a bounded log tail.

    The byte bounds protect log I/O and memory, not the model's context budget.
    Token selection happens later using the selected model's own vocabulary.
    """
    found: list[Message] = []
    summary = ""
    size = 0
    seen_compaction = False
    with path.open("rb") as stream:
        stream.seek(0, 2)
        position = min(end_at, stream.tell())
        scanned = 0
        remainder = b""
        first = True
        skip_partial = False
        while position > 0 and scanned < CONTEXT_READ_BYTES:
            width = min(262_144, position, CONTEXT_READ_BYTES - scanned)
            position -= width
            scanned += width
            stream.seek(position)
            chunk = stream.read(width) + remainder
            parts = chunk.split(b"\n")
            remainder = parts.pop(0)
            if first:
                skip_partial = not chunk.endswith(b"\n")
            if skip_partial and parts:
                parts.pop()  # A writer may still be appending this record.
                skip_partial = False
            first = False
            if position == 0 and remainder and not skip_partial:
                parts.insert(0, remainder)
            for raw in reversed(parts):
                record, message = parse_record(raw.decode("utf-8", errors="replace"))
                compacted = _task_summary(record)
                if compacted is not None:
                    if not seen_compaction:
                        seen_compaction = True
                        summary = compacted
                    if compacted:
                        return list(reversed(found)), summary
                if message and size < CONTEXT_STORAGE_BYTES:
                    message, stored = _bounded_dialogue(message, CONTEXT_STORAGE_BYTES - size)
                    found.append(message)
                    size += stored
            # Still scan for the latest summary after the dialogue buffer fills.
    return list(reversed(found)), summary


class SessionTailer:
    """Read dialogue plus compaction summaries off the input thread."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.pending = b""
        self.messages: list[Message] = []
        self.summary = ""
        self.revision = 0
        self.primed = False

    def poll(self) -> bool:
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset = 0
                self.pending = b""
                self.messages.clear()
                self.summary = ""
                self.revision += 1
                self.primed = False
            if not self.primed:
                self.messages, self.summary = recent_context(self.path, size)
                self.pending = b""
                if size:
                    with self.path.open("rb") as stream:
                        start = size
                        tail = b""
                        # A compaction record can span several chunks because
                        # it also contains replacement history. Keep its partial
                        # bytes so completing it later still updates the summary.
                        while start and len(tail) < CONTEXT_READ_BYTES:
                            width = min(start, 262_144, CONTEXT_READ_BYTES - len(tail))
                            start -= width
                            stream.seek(start)
                            tail = stream.read(width) + tail
                            if b"\n" in tail:
                                break
                    if not tail.endswith(b"\n"):
                        partial = tail.rsplit(b"\n", 1)[-1]
                        if b"\n" in tail or start == 0:
                            self.pending = partial
                self.offset = size
                self.primed = True
                if self.messages or self.summary:
                    self.revision += 1
                return bool(self.messages or self.summary)
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read()
                self.offset = stream.tell()
        except OSError:
            return False
        if not chunk:
            return False
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        changed = False
        for raw in lines:
            record, message = parse_record(raw.decode("utf-8", errors="replace"))
            summary = _task_summary(record)
            if summary is not None:
                self.summary = summary
                if summary:
                    self.messages.clear()  # Earlier turns are represented by this snapshot.
                changed = True
            if message:
                self.messages.append(message)
                changed = True
        if changed:
            stored = 0
            for index in range(len(self.messages) - 1, -1, -1):
                self.messages[index], used = _bounded_dialogue(
                    self.messages[index], CONTEXT_STORAGE_BYTES - stored)
                stored += used
                if stored >= CONTEXT_STORAGE_BYTES:
                    del self.messages[:index]
                    break
            self.revision += 1
        return changed

    def context(self) -> list[Message]:
        summary = [Message("assistant", self.summary, "task_summary")] if self.summary else []
        return summary + self.messages[:]

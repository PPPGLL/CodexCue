"""Check versions, prepare a release PR, and resolve its immutable build commit."""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PLAN = ".github/release-plan.json"
VERSION_RE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:b([1-9]\d*))?")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True, encoding="utf-8").strip()


def parts(version: str) -> tuple[int, int, int, int | None]:
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise ValueError(f"Unsupported version: {version!r}; use X.Y.Z or X.Y.ZbN")
    a, b, c, beta = match.groups()
    return int(a), int(b), int(c), int(beta) if beta else None


def next_version(current: str, kind: str, subjects: list[str]) -> str:
    major, minor, patch, beta = parts(current)
    if kind == "auto":
        if beta is not None:
            kind = "beta"
        elif any(re.match(r"\w+(?:\([^)]*\))?!:", s) for s in subjects):
            kind = "major"
        elif any(re.match(r"feat(?:\([^)]*\))?:", s) for s in subjects):
            kind = "minor"
        else:
            kind = "patch"
    if kind == "beta":
        return f"{major}.{minor}.{patch}b{beta + 1}" if beta else f"{major}.{minor + 1}.0b1"
    if kind == "stable":
        if beta is None:
            raise ValueError("The current version is already stable")
        return f"{major}.{minor}.{patch}"
    if beta is not None:
        raise ValueError("Use beta to continue testing or stable to graduate this Beta")
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "major":
        return f"{major + 1}.0.0"
    raise ValueError(f"Unknown release kind: {kind}")


def check(root: Path) -> str:
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    parts(version)
    init = (root / "src/codex_companion/__init__.py").read_text(encoding="utf-8")
    exported = re.findall(r'^__version__ = "([^"\n]+)"$', init, re.M)
    packages = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))["package"]
    locked = [p["version"] for p in packages if p["name"] == "codexcue" and p.get("source") == {"editable": "."}]
    if exported != [version] or locked != [version]:
        raise ValueError("pyproject.toml, __version__, and the local uv.lock package must agree")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## {re.escape(version)}(?:\s|$)", changelog, re.M):
        raise ValueError("CHANGELOG.md has no section for the current version")
    if (root / PLAN).exists():
        plan = json.loads((root / PLAN).read_text(encoding="utf-8"))
        if plan["version"] != version or not re.fullmatch(r"[0-9a-f]{40}", plan["base_commit"]):
            raise ValueError("Release plan does not match this version or has an invalid base commit")
        previous = parts(plan["previous_version"])
        current = parts(version)
        if (*current[:3], current[3] or float("inf")) <= (*previous[:3], previous[3] or float("inf")):
            raise ValueError("Release versions must increase")
        git(root, "merge-base", "--is-ancestor", plan["base_commit"], "HEAD")
        base = tomllib.loads(git(root, "show", f"{plan['base_commit']}:pyproject.toml"))["project"]["version"]
        if base != plan["previous_version"]:
            raise ValueError("Release plan's previous version does not match its base commit")
    return version


def notes_for(root: Path, version: str) -> str:
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}(?:[^\n]*)\n(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    if not match or not match[1].strip():
        raise ValueError(f"No release notes for {version}")
    return match[1].strip()


def prepare(root: Path, kind: str, *, dry_run: bool = False) -> dict:
    current = check(root)
    if git(root, "status", "--porcelain"):
        raise ValueError("Commit or stash your changes before preparing a version")
    base = git(root, "rev-parse", "HEAD")
    boundary = git(root, "log", "-1", "--format=%H", "-G", "^version = ", "--", "pyproject.toml")
    subjects = git(root, "log", "--first-parent", "--format=%s", f"{boundary}..HEAD").splitlines()
    version = next_version(current, kind, subjects)
    if git(root, "tag", "--list", f"v{version}"):
        raise ValueError(f"Tag v{version} already exists; versions and tags are never reused")
    notes = notes_for(root, "Unreleased")
    plan = {"version": version, "previous_version": current, "base_commit": base}
    if not dry_run:
        project = root / "pyproject.toml"
        init = root / "src/codex_companion/__init__.py"
        lock = root / "uv.lock"
        locked = lock.read_text(encoding="utf-8")
        pattern = rf'(\[\[package\]\]\nname = "codexcue"\nversion = "){re.escape(current)}(")'
        updated, count = re.subn(pattern, lambda m: m[1] + version + m[2], locked)
        if count != 1:
            raise ValueError("Cannot locate the local package in uv.lock")
        changelog = root / "CHANGELOG.md"
        updates = {
            project: project.read_text(encoding="utf-8").replace(f'version = "{current}"', f'version = "{version}"', 1),
            init: init.read_text(encoding="utf-8").replace(f'__version__ = "{current}"', f'__version__ = "{version}"', 1),
            lock: updated,
            changelog: changelog.read_text(encoding="utf-8").replace(
                "## Unreleased", f"## Unreleased\n\n## {version} — {date.today().isoformat()}", 1),
            root / PLAN: json.dumps(plan, indent=2) + "\n",
        }
        (root / PLAN).parent.mkdir(parents=True, exist_ok=True)
        for path, content in updates.items():
            path.write_text(content, encoding="utf-8")
        check(root)
    return {**plan, "branch": f"codex/release-{version}", "notes": notes}


def resolve(root: Path) -> dict:
    version = check(root)
    if not (root / PLAN).is_file():
        raise ValueError("No release plan; run Prepare release first")
    commit = git(root, "log", "-1", "--first-parent", "--format=%H", "--", PLAN)
    planned = json.loads(git(root, "show", f"{commit}:{PLAN}"))
    if planned != json.loads((root / PLAN).read_text(encoding="utf-8")):
        raise ValueError("Release plan has uncommitted changes")
    if tomllib.loads(git(root, "show", f"{commit}:pyproject.toml"))["project"]["version"] != version:
        raise ValueError("Release commit and version disagree")
    return {"version": version, "commit": commit, "tag": f"v{version}",
            "prerelease": "true" if parts(version)[3] is not None else "false"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "prepare", "resolve"])
    parser.add_argument("--kind", choices=["auto", "beta", "patch", "minor", "major", "stable"], default="auto")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--notes", type=Path)
    args = parser.parse_args()
    try:
        result = ({"version": check(ROOT)} if args.command == "check" else
                  prepare(ROOT, args.kind, dry_run=args.dry_run) if args.command == "prepare" else resolve(ROOT))
        if args.notes:
            args.notes.write_text(notes_for(ROOT, result["version"]), encoding="utf-8")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                for key in ("version", "branch", "commit", "tag", "prerelease"):
                    if key in result:
                        stream.write(f"{key}={result[key]}\n")
        print(json.dumps(result, ensure_ascii=True))
    except (ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Version check failed: {exc}\n")


if __name__ == "__main__":
    main()

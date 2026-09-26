import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("version_workflow", Path(__file__).parents[1] / "scripts/version.py")
version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(version)


@pytest.mark.parametrize("current,kind,subjects,expected", [
    ("0.1.0b2", "auto", ["feat!: break a format"], "0.1.0b3"),
    ("0.1.0b3", "stable", [], "0.1.0"),
    ("0.1.0", "auto", ["fix: input"], "0.1.1"),
    ("0.1.0", "auto", ["feat(tray): release model"], "0.2.0"),
    ("0.1.0", "auto", ["fix(config)!: change format", "feat: feature"], "1.0.0"),
    ("0.1.0", "beta", [], "0.2.0b1"),
    ("0.1.0", "patch", ["feat: feature"], "0.1.1"),
    ("0.1.0", "minor", [], "0.2.0"),
    ("0.1.0", "major", [], "1.0.0"),
])
def test_next_version(current, kind, subjects, expected):
    assert version.next_version(current, kind, subjects) == expected


@pytest.mark.parametrize("current,kind", [("0.1.0b2", "patch"), ("0.1.0", "stable"),
                                           ("0.1.0b0", "auto"), ("v0.1.0", "auto")])
def test_invalid_or_accidentally_stable_version_is_rejected(current, kind):
    with pytest.raises(ValueError):
        version.next_version(current, kind, [])


def commit(root, message):
    version.git(root, "add", ".")
    version.git(root, "commit", "-m", message)
    return version.git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    version.git(root, "init", "-b", "main")
    version.git(root, "config", "user.name", "Release test")
    version.git(root, "config", "user.email", "test@example.invalid")
    version.git(root, "config", "core.autocrlf", "false")
    (root / "pyproject.toml").write_text('[project]\nname = "codexcue"\nversion = "0.1.0b2"\n')
    init = root / "src/codex_companion/__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text('__version__ = "0.1.0b2"\n')
    (root / "uv.lock").write_text('version = 1\n[[package]]\nname = "codexcue"\nversion = "0.1.0b2"\nsource = { editable = "." }\n')
    (root / "CHANGELOG.md").write_text('# Changelog\n\n## Unreleased\n\n## 0.1.0b2\n\n- Old notes.\n')
    commit(root, "chore(release): 0.1.0b2")
    (root / "CHANGELOG.md").write_text('# Changelog\n\n## Unreleased\n\n- Fix input detection.\n\n## 0.1.0b2\n\n- Old notes.\n')
    commit(root, "fix: input detection")
    return root


def test_prepare_preview_and_release_preserve_old_notes_and_dependencies(repo):
    preview = version.prepare(repo, "auto", dry_run=True)
    assert preview["version"] == "0.1.0b3"
    assert version.git(repo, "status", "--porcelain") == ""
    result = version.prepare(repo, "auto")
    assert result == preview
    assert version.check(repo) == "0.1.0b3"
    assert version.notes_for(repo, "0.1.0b3") == "- Fix input detection."
    assert version.notes_for(repo, "0.1.0b2") == "- Old notes."
    with pytest.raises(ValueError, match="No release notes"):
        version.notes_for(repo, "Unreleased")
    assert json.loads((repo / version.PLAN).read_text())["base_commit"] == preview["base_commit"]


def test_prepare_refuses_dirty_tree_or_existing_tag(repo):
    (repo / "unrelated.txt").write_text("keep this")
    with pytest.raises(ValueError, match="Commit or stash"):
        version.prepare(repo, "auto")
    commit(repo, "docs: fixture")
    version.git(repo, "tag", "v0.1.0b3")
    with pytest.raises(ValueError, match="already exists"):
        version.prepare(repo, "auto")
    assert version.check(repo) == "0.1.0b2"


def test_checks_reject_disagreeing_version(repo):
    (repo / "src/codex_companion/__init__.py").write_text('__version__ = "0.1.0b9"\n')
    with pytest.raises(ValueError, match="must agree"):
        version.check(repo)


def test_prepare_validates_replacements_before_writing(repo):
    lock = repo / "uv.lock"
    lock.write_text(lock.read_text().replace('name = "codexcue"', 'name  = "codexcue"'))
    commit(repo, "chore: equivalent TOML formatting")
    with pytest.raises(ValueError, match="Cannot locate"):
        version.prepare(repo, "auto")
    assert version.git(repo, "status", "--porcelain") == ""
    assert version.check(repo) == "0.1.0b2"


@pytest.mark.parametrize("merge_mode", ["--squash", "--no-ff"])
def test_resolve_builds_release_merge_commit_even_after_main_advances(repo, merge_mode):
    version.git(repo, "switch", "-c", "codex/release-0.1.0b3")
    version.prepare(repo, "auto")
    commit(repo, "chore(release): 0.1.0b3")
    version.git(repo, "switch", "main")
    version.git(repo, "merge", merge_mode, "codex/release-0.1.0b3")
    release_commit = (commit(repo, "chore(release): 0.1.0b3") if merge_mode == "--squash"
                      else version.git(repo, "rev-parse", "HEAD"))
    (repo / "later.txt").write_text("Must not appear in the old version")
    commit(repo, "feat: later feature")
    result = version.resolve(repo)
    assert result["commit"] == release_commit
    assert result["tag"] == "v0.1.0b3"
    assert result["prerelease"] == "true"
    with pytest.raises(subprocess.CalledProcessError):
        version.git(repo, "show", f"{release_commit}:later.txt")


def test_second_release_advances_and_carries_only_new_notes(repo):
    version.prepare(repo, "auto")
    first = commit(repo, "chore(release): 0.1.0b3")
    path = repo / "CHANGELOG.md"
    path.write_text(path.read_text(encoding="utf-8").replace("## Unreleased", "## Unreleased\n\n- Another change."), encoding="utf-8")
    commit(repo, "fix: another change")
    result = version.prepare(repo, "auto")
    assert result["previous_version"] == "0.1.0b3"
    assert result["version"] == "0.1.0b4"
    assert result["notes"] == "- Another change."
    second = commit(repo, "chore(release): 0.1.0b4")
    assert version.resolve(repo)["commit"] == second != first


def test_no_plan_cannot_accidentally_create_release(repo):
    with pytest.raises(ValueError, match="No release plan"):
        version.resolve(repo)


def test_forged_plan_base_version_is_rejected(repo):
    version.prepare(repo, "auto")
    path = repo / version.PLAN
    plan = json.loads(path.read_text())
    plan["previous_version"] = "0.1.0b1"
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="previous version does not match"):
        version.check(repo)

import hashlib
import importlib.util
import json
from pathlib import Path
import tomllib
import zipfile

import pytest

spec = importlib.util.spec_from_file_location("check_release", Path(__file__).parents[1] / "scripts/check_release.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.fixture
def assets(tmp_path):
    files = {"CodexCue.exe": b"fixture executable", "LICENSE": b"MIT",
             "THIRD_PARTY_NOTICES.md": b"notices", "licenses/inventory.json": b"{}",
             "licenses/qt-qtbase/LICENSES/LGPL-3.0-only.txt": b"fixture license",
             "_internal/build-info.json": json.dumps({"version": "1", "commit": "abc", "dirty": False}).encode()}
    source = tmp_path / "CodexCue-1-source.zip"
    source.write_bytes(b"source fixture")
    upstream = []
    for name in ("qtbase", "pyside"):
        path = tmp_path / f"{name}-source.tar.gz"
        path.write_bytes(name.encode())
        upstream.append({"source_file": path.name, "source_sha256": checker.digest(path.read_bytes())})
    manifest = {"files": {n: checker.digest(v) for n, v in files.items()}, "version": "1", "commit": "abc",
                "executable_sha256": checker.digest(files["CodexCue.exe"]), "qt_sources": upstream}
    with zipfile.ZipFile(tmp_path / "CodexCue-1-windows-x64.zip", "w") as archive:
        for name, value in files.items():
            archive.writestr("CodexCue/" + name, value)
        archive.writestr("CodexCue/release.json", json.dumps(manifest))
    (tmp_path / "SHA256SUMS.txt").write_text("".join(f"{checker.digest(p.read_bytes())}  {p.name}\n"
        for p in sorted(tmp_path.iterdir())), encoding="utf-8")
    names = ["unit_tests", "package_dependencies", "download_recovery", "install_lifecycle", "live-model",
             *[f"desktop-{i:02}" for i in range(1, 4)], *[f"startup-{i:02}" for i in range(1, 4)]]
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"status": "PASS", "source_commit": "abc", "checks": [{"name": n,"status": "PASS"} for n in names],
                                  "executable_sha256": manifest["executable_sha256"]}), encoding="utf-8")
    return tmp_path, receipt


def test_complete_release_passes(assets):
    assert checker.check(*assets)["status"] == "PASS"


def test_changed_binary_zip_is_rejected(assets):
    root, receipt = assets
    with (root / "CodexCue-1-windows-x64.zip").open("ab") as stream:
        stream.write(b"altered")
    with pytest.raises(ValueError, match="changed asset"):
        checker.check(root, receipt)


def test_missing_corresponding_source_is_rejected(assets):
    root, receipt = assets
    (root / "pyside-source.tar.gz").unlink()
    with pytest.raises(ValueError, match="Missing or changed asset"):
        checker.check(root, receipt)


@pytest.mark.parametrize("field,value", [("source_commit", "other"), ("executable_sha256", "other"), ("checks", [])])
def test_unrelated_or_incomplete_desktop_receipt_is_rejected(assets, field, value):
    root, receipt = assets
    data = json.loads(receipt.read_text())
    data[field] = value
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        checker.check(root, receipt)


def test_all_version_declarations_agree():
    from codex_companion import __version__

    root = Path(__file__).parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    lock = tomllib.loads((root / "uv.lock").read_text())
    assert project["project"]["version"] == __version__
    assert next(p for p in lock["package"] if p["name"] == "codexcue")["version"] == __version__


@pytest.mark.parametrize("commit,version", [("other", "1"), ("abc", "2")])
def test_ci_artifacts_must_match_the_requested_version(assets, commit, version):
    root, _ = assets
    with pytest.raises(ValueError, match="requested release"):
        checker.check_ci(root, commit, version)


def test_ci_assets_have_a_separate_scope_from_desktop_acceptance(assets):
    root, receipt = assets
    sources = root / "sources"
    sources.mkdir()
    for path in root.glob("*.tar.gz"):
        path.rename(sources / path.name)
    receipt.unlink()
    assert checker.check_ci(root, "abc", "1")["scope"] == "ci_artifacts_only"
    with pytest.raises(FileNotFoundError):
        checker.check(root, receipt)
    (root / "unexpected.txt").write_text("not in the manifest")
    with pytest.raises(ValueError, match="upload list"):
        checker.check_ci(root, "abc", "1")

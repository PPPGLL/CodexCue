"""Reject incomplete, altered, or unverified release assets before publication."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_assets(assets: Path) -> dict:
    sums = {}
    for line in (assets / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in sums:
            raise ValueError("Invalid/duplicate checksum entry")
        matches = [p for p in assets.rglob(match[2]) if p.is_file()]
        if len(matches) != 1 or digest(matches[0].read_bytes()) != match[1]:
            raise ValueError(f"Missing or changed asset: {match[2]}")
        sums[match[2]] = matches[0]
    binary = [p for n, p in sums.items() if n.endswith("-windows-x64.zip")]
    if len(binary) != 1:
        raise ValueError("Expected one Windows ZIP")
    with zipfile.ZipFile(binary[0]) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(".." in PurePosixPath(n).parts or n.startswith("/") for n in names):
            raise ValueError("Unsafe ZIP paths")
        manifest = json.loads(archive.read("CodexCue/release.json"))
        expected = {"CodexCue/" + n for n in manifest["files"]} | {"CodexCue/release.json"}
        actual = {n for n in names if not n.endswith("/")}
        if expected != actual:
            raise ValueError("ZIP file inventory differs from manifest")
        for name, sha in manifest["files"].items():
            path = PurePosixPath(name)
            if ({".local", ".venv", "logs", "sessions"} & set(path.parts)
                    or path.name in {"config.json", ".env"} or path.name.startswith(".env.")
                    or path.suffix in {".log", ".jsonl"}):
                raise ValueError("Private/runtime file in release")
            if digest(archive.read("CodexCue/" + name)) != sha:
                raise ValueError(f"Packaged file changed: {name}")
        if manifest["executable_sha256"] != manifest["files"]["CodexCue.exe"]:
            raise ValueError("Executable identity mismatch")
        build = json.loads(archive.read("CodexCue/_internal/build-info.json"))
        if build["dirty"] or build["commit"] != manifest["commit"] or build["version"] != manifest["version"]:
            raise ValueError("Build provenance mismatch")
        for required in ("LICENSE", "THIRD_PARTY_NOTICES.md", "licenses/inventory.json",
                         "licenses/qt-qtbase/LICENSES/LGPL-3.0-only.txt"):
            if required not in manifest["files"]:
                raise ValueError(f"Missing release license material: {required}")
        if f"CodexCue-{manifest['version']}-source.zip" not in sums:
            raise ValueError("Project source ZIP missing")
        for upstream in manifest["qt_sources"]:
            source = sums.get(upstream["source_file"])
            if source is None or digest(source.read_bytes()) != upstream["source_sha256"]:
                raise ValueError("Corresponding Qt/PySide source missing or altered")
        if len(manifest["qt_sources"]) != 2:
            raise ValueError("Expected both Qt Base and PySide corresponding sources")
    return {"status": "PASS", "version": manifest["version"], "commit": manifest["commit"],
            "executable_sha256": manifest["executable_sha256"], "assets": len(sums)}


def check_ci(assets: Path, commit: str, version: str) -> dict:
    result = check_assets(assets)
    if result["commit"] != commit or result["version"] != version:
        raise ValueError("CI artifacts do not belong to the requested release commit/version")
    uploaded = {p.name for pattern in ("*.zip", "*.txt", "sources/*.tar.gz") for p in assets.glob(pattern)}
    declared = {line.split("  ", 1)[1] for line in (assets / "SHA256SUMS.txt").read_text().splitlines()}
    if uploaded != declared | {"SHA256SUMS.txt"}:
        raise ValueError("CI upload list differs from the checksum manifest")
    return {**result, "scope": "ci_artifacts_only"}


def check(assets: Path, receipt: Path) -> dict:
    manifest = check_assets(assets)
    verification = json.loads(receipt.read_text(encoding="utf-8-sig"))
    if (verification["status"] != "PASS" or verification.get("source_commit") != manifest["commit"]
            or verification["executable_sha256"] != manifest["executable_sha256"]):
        raise ValueError("Desktop receipt does not match the release")
    checks = {x["name"] for x in verification["checks"] if x["status"] == "PASS"}
    required = {"unit_tests", "package_dependencies", "download_recovery", "install_lifecycle", "live-model"}
    if (not required <= checks or sum(n.startswith("desktop-") for n in checks) < 3
            or sum(n.startswith("startup-") for n in checks) < 3):
        raise ValueError("Required release acceptance is incomplete")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.assets.resolve(), args.verification.resolve()), indent=2))

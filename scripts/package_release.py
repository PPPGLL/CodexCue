"""Create a versioned, auditable release from a built Windows directory."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
import zipfile

from collect_licenses import collect

ROOT = Path(__file__).resolve().parents[1]


def is_private_path(path: Path) -> bool:
    return (bool({".local", ".venv", "logs", "sessions"} & set(path.parts))
            or path.name in {"config.json", ".env"} or path.name.startswith(".env.")
            or path.suffix in {".log", ".jsonl"})


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "dist" / "CodexCue")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verification", type=Path)
    args = parser.parse_args()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    if dirty:
        raise SystemExit("Commit the release inputs before packaging; working tree is not clean.")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    bundle = args.bundle.resolve()
    exe = bundle / "CodexCue.exe"
    if not exe.is_file():
        raise SystemExit("Build the Windows executable first.")
    build = json.loads((bundle / "_internal" / "build-info.json").read_text(encoding="utf-8"))
    if build["dirty"] or build["commit"] != commit or build["version"] != version:
        raise SystemExit("Bundle was not built from this clean release commit/version.")
    verification = None
    if args.verification:
        verification = json.loads(args.verification.read_text(encoding="utf-8-sig"))
        if verification["status"] != "PASS" or verification["executable_sha256"] != sha(exe):
            raise SystemExit("Verification receipt does not match this executable.")
        if verification.get("source_commit") != commit:
            raise SystemExit("Verification tests were not run from the release commit.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    staging = output / "staging" / "CodexCue"
    if any(is_private_path(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file()):
        raise SystemExit("App bundle contains private/runtime files; refusing to package.")
    shutil.copytree(bundle, staging)
    for source, target in [("LICENSE", "LICENSE"), ("docs/WINDOWS.md", "README.Windows.md"),
                           ("docs/THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
                           ("CHANGELOG.md", "CHANGELOG.md"), ("scripts/install.ps1", "install.ps1"),
                           ("scripts/install_common.ps1", "install_common.ps1"),
                           ("scripts/uninstall.ps1", "uninstall.ps1")]:
        shutil.copy2(ROOT / source, staging / target)
    inventory = collect(staging / "licenses", output / "sources")
    source_archive = output / f"CodexCue-{version}-source.zip"
    subprocess.run(["git", "archive", "--format=zip", f"--prefix=CodexCue-{version}/",
                    "-o", str(source_archive), commit], cwd=ROOT, check=True)
    files = {p.relative_to(staging).as_posix(): sha(p) for p in sorted(staging.rglob("*")) if p.is_file()}
    manifest = {"application": "CodexCue", "version": version, "commit": commit, "files": files,
                "executable_sha256": sha(exe), "desktop_verified": verification is not None,
                "qt_sources": inventory["upstream"]}
    (staging / "release.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if verification:
        # Keep only public numeric results; local paths/config/logs stay local.
        clean = {"status": "PASS", "source_commit": commit, "executable_sha256": verification["executable_sha256"],
                 "checks": [{k: c[k] for k in ("name", "status", "tests", "checks") if k in c}
                            for c in verification["checks"]]}
        (output / "desktop-verification.json").write_text(json.dumps(clean, indent=2), encoding="utf-8")
    archive = output / f"CodexCue-{version}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                z.write(path, (Path("CodexCue") / path.relative_to(staging)).as_posix())
    assets = [archive, source_archive, *sorted((output / "sources").glob("*.tar.gz"))]
    if (output / "desktop-verification.json").exists():
        assets.append(output / "desktop-verification.json")
    (output / "SHA256SUMS.txt").write_text("".join(f"{sha(p)}  {p.name}\n" for p in assets), encoding="utf-8")
    print(json.dumps({"version": version, "commit": commit, "binary_zip": str(archive),
                      "assets": len(assets), "desktop_verified": verification is not None}))


if __name__ == "__main__":
    main()

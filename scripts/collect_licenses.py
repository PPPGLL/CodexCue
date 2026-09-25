"""Collect installed runtime licenses and exact Qt source/attribution material."""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def runtime_distributions():
    pending = ["codexcue"]
    found = {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in found:
            continue
        dist = metadata.distribution(name)
        found[name] = dist
        for value in dist.requires or []:
            req = Requirement(value)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                pending.append(req.name)
    found["pyinstaller"] = metadata.distribution("pyinstaller")
    # PyInstaller's keyring hooks can include setuptools vendored modules.
    found["setuptools"] = metadata.distribution("setuptools")
    return found


def collect(output: Path, sources: Path) -> dict:
    output.mkdir(parents=True)
    sources.mkdir(parents=True, exist_ok=True)
    packages = []
    for name, dist in sorted(runtime_distributions().items()):
        if name == "codexcue":
            continue
        files = []
        for entry in dist.files or []:
            if ".dist-info/" not in str(entry) or not any(
                    word in str(entry).lower() for word in ("license", "copying", "notice")):
                continue
            source = Path(dist.locate_file(entry))
            if not source.is_file():
                continue
            relative = Path(name) / Path(*PurePosixPath(str(entry)).parts[1:])
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            files.append(relative.as_posix())
        if not files:
            raise RuntimeError(f"No license material found for {name} {dist.version}")
        packages.append({"name": name, "version": dist.version, "files": files,
                         "license": dist.metadata.get("License-Expression") or dist.metadata.get("License", "See files")})
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("Python LICENSE.txt is missing")
    shutil.copy2(python_license, output / "Python-LICENSE.txt")

    qt_version = metadata.version("PySide6")
    upstream = []
    with httpx.Client(timeout=90, follow_redirects=True, headers={"User-Agent": "CodexCue-release"}) as client:
        for repo in ("qt/qtbase", "pyside/pyside-setup"):
            print(f"Collecting {repo} v{qt_version} license and source material...", flush=True)
            response = client.get(f"https://api.github.com/repos/{repo}/commits/v{qt_version}")
            response.raise_for_status()
            commit = response.json()["sha"]
            response = client.get(f"https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1")
            response.raise_for_status()
            tree = response.json()
            if tree.get("truncated"):
                raise RuntimeError("Incomplete upstream license tree")
            # Include attribution files and license/copyright notices throughout
            # the source tree, including bundled third-party implementations.
            selected = [entry for entry in tree["tree"] if entry["type"] == "blob" and (
                entry["path"].startswith("LICENSES/")
                or entry["path"].endswith("qt_attribution.json")
                or any(word in PurePosixPath(entry["path"]).name.lower()
                       for word in ("license", "copying", "copyright", "notice")))]
            prefix = repo.replace("/", "-")

            def fetch(entry):
                remote = f"https://raw.githubusercontent.com/{repo}/{commit}/{entry['path']}"
                data = client.get(remote)
                data.raise_for_status()
                blob = data.content
                digest = hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest()
                if digest != entry["sha"]:
                    raise RuntimeError("Upstream license blob hash mismatch")
                target = output / prefix / entry["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(fetch, selected))
            # Distribute corresponding upstream source beside the binary ZIP.
            # This also provides license files referenced indirectly by an
            # attribution JSON under less conventional filenames.
            source_name = f"{prefix}-{qt_version}-{commit[:12]}-source.tar.gz"
            source_url = f"https://codeload.github.com/{repo}/tar.gz/{commit}"
            source_path = sources / source_name
            with client.stream("GET", source_url) as response:
                response.raise_for_status()
                with source_path.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
            upstream.append({"repository": repo, "version": qt_version, "commit": commit,
                             "source_file": source_name, "source_url": source_url,
                             "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                             "notice_files": len(selected)})
    inventory = {"python": sys.version.split()[0], "packages": packages, "upstream": upstream}
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    return inventory

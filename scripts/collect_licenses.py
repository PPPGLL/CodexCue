"""Collect installed runtime licenses and exact Qt source/attribution material."""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import time

import httpx
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def source_archive(client, repo: str, commit: str, destination: Path) -> str:
    url = f"https://codeload.github.com/{repo}/tar.gz/{commit}"
    cache = Path(__file__).resolve().parents[1] / ".local" / "upstream-sources"
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / destination.name
    checksum = cached.with_suffix(cached.suffix + ".sha256")
    if (cached.is_file() and checksum.is_file()
            and hashlib.sha256(cached.read_bytes()).hexdigest() == checksum.read_text().strip()):
        shutil.copy2(cached, destination)
        return url
    partial = cached.with_suffix(cached.suffix + ".part")
    for attempt in range(3):
        try:
            started = time.monotonic()
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with partial.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        if time.monotonic() - started > 300:
                            raise TimeoutError("Source archive download exceeded five minutes")
                        stream.write(chunk)
            with tarfile.open(partial, "r:gz") as archive:
                first = archive.next()
                if first is None or first.name.split("/")[0] != f"{repo.split('/')[-1]}-{commit}":
                    raise ValueError("Unexpected source archive root")
            partial.replace(cached)
            checksum.write_text(hashlib.sha256(cached.read_bytes()).hexdigest(), encoding="ascii")
            shutil.copy2(cached, destination)
            return url
        except (httpx.HTTPError, OSError, TimeoutError, tarfile.TarError):
            partial.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("Source download did not complete")


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
    with httpx.Client(timeout=httpx.Timeout(45, connect=15), follow_redirects=True,
                      transport=httpx.HTTPTransport(retries=2),
                      headers={"User-Agent": "CodexCue-release"}) as client:
        for repo in ("qt/qtbase", "pyside/pyside-setup"):
            print(f"Collecting {repo} v{qt_version} license and source material...", flush=True)
            response = client.get(f"https://api.github.com/repos/{repo}/commits/v{qt_version}")
            response.raise_for_status()
            commit = response.json()["sha"]
            prefix = repo.replace("/", "-")
            # Distribute corresponding upstream source beside the binary ZIP.
            # This also provides license files referenced indirectly by an
            # attribution JSON under less conventional filenames.
            source_name = f"{prefix}-{qt_version}-{commit[:12]}-source.tar.gz"
            source_path = sources / source_name
            source_url = source_archive(client, repo, commit, source_path)
            count = 0
            with tarfile.open(source_path, "r|gz") as archive:
                for member in archive:
                    parts = PurePosixPath(member.name).parts
                    if not parts or parts[0] != f"{repo.split('/')[-1]}-{commit}" or ".." in parts:
                        raise ValueError("Unsafe upstream archive path")
                    if not member.isfile() or len(parts) < 2:
                        continue
                    relative = PurePosixPath(*parts[1:])
                    if not (relative.parts[0] == "LICENSES" or relative.name == "qt_attribution.json"
                            or any(word in relative.name.lower() for word in ("license", "copying", "copyright", "notice"))):
                        continue
                    target = output / prefix / str(relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(member).read())
                    count += 1
            upstream.append({"repository": repo, "version": qt_version, "commit": commit,
                             "source_file": source_name, "source_url": source_url,
                             "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                             "notice_files": count})
    inventory = {"python": sys.version.split()[0], "packages": packages, "upstream": upstream}
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    return inventory

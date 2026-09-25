# Releasing CodexCue

1. Update the version in `pyproject.toml` and `src/codex_companion/__init__.py`,
   refresh `uv.lock` with `uv lock`, and write `CHANGELOG.md`.
2. Commit all release inputs. `package_release.py` rejects a dirty working tree.
   The Windows workflow runs on a clean hosted VM: setup/bootstrap, unit tests,
   download recovery, installation/rollback/uninstall tests, build, dependency
   loading, license/source collection and maintenance of the packaged tree.
3. Run the Windows workflow manually with `draft_release=true` on the chosen
   commit to create a **draft** prerelease with its source commit/tag, binary ZIP,
   project source ZIP, corresponding Qt/PySide source tarballs and SHA256 sums.
   Repository visibility is unchanged. Nothing publishes automatically.
4. Download that exact candidate ZIP and run desktop acceptance on an unlocked
   Windows desktop with the existing model. No model download is required:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1 -PackagedExe 'C:\candidate\CodexCue\CodexCue.exe' -LiveModel qwen3:4b-instruct
   ```

   The receipt must be PASS and match the candidate EXE SHA256. Keep private logs,
   user paths and configuration local. Only aggregate numeric checks and the EXE
   hash may be attached publicly as `desktop-verification.json`.
5. Before publishing, run `scripts/check_release.py` with the downloaded release
   assets and matching local receipt. It validates the ZIP manifest, source assets
   and checksums. A CI pass alone is not a desktop acceptance result. If a candidate
   changes, rerun acceptance on the changed candidate; don't reuse an old receipt.
6. Publish the existing draft only after explicitly deciding to release it, with
   the exact tested host/Windows/model environment and the known Beta limitations.
   Public availability also requires a separate decision to make this repository
   public. Do not change visibility as a side effect of packaging or validation.

For local release preparation, build a clean committed tree with setup `-Build`,
run desktop acceptance, then run `scripts/package_release.py --output PATH
--verification RECEIPT`. This includes a sanitized public verification summary.
The source checkout, credentials, `.local`, logs and conversation files are never
included in `git archive`. The packager includes only the supplied app bundle,
named public docs, dependency notices and exact upstream source archives.

Qt libraries remain dynamically replaceable. Update the collected source/licensing
inventory whenever dependencies change. Do not omit source tarballs from a
redistributed release. This Beta is unsigned; checksums provide integrity, not
publisher identity. Code signing needs an independently provisioned certificate.

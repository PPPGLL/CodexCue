# Changelog

## Unreleased

- Expand rough revision requests into relevant details and checks while keeping
  incomplete phrases concise and respecting analysis-only instructions.
- Increase generation capacity, preserve complete requirements beyond 120
  characters, and widen the popup for longer suggestions.
- Cover long streamed suggestions, native insertion, and detailed live-model
  requests in automated acceptance.

## 0.1.0b1

First Beta release preparation for Windows x64.

- Continue drafts using an anchored structured response; preserve numbers,
  whitespace and partial words, and keep historical dialogue as quoted context.
- Recognize short task titles and cold-start composers. Automatic mode uses only
  the draft when task identity cannot be verified; strict context mode is optional.
- Cancel stale requests, suppress late output after navigation/submission, show
  the full inserted suffix, and release idle local models.
- Recover native settings, dropdown and suggestion visibility after hidden
  script launches. Add repeated startup and real-model desktop acceptance.
- Preserve corrupt configuration and open recovery settings; allow download
  retries after process-start failure and archive corruption.
- Add verified per-user installation, upgrade rollback, safe uninstall, release
  checksums, version/source provenance and third-party license/source assets.

Known boundaries: VS Code support is experimental; test fixtures do not certify
every host version or IME. The Windows binary is unsigned. Automatic application
updates and startup registration are not included. Models can still produce
unhelpful suggestions; Tab is always optional and never sends a message.

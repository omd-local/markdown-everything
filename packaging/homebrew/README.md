# Homebrew core backend release

Install the backend used by OMD Home, the CLI, and MCP:

```bash
brew install omd-local/omd/omd
```

For an existing installation:

```bash
brew update
brew upgrade omd-local/omd/omd
omd capabilities --json
```

Set OMD Home's executable path to the result of
`echo "$(brew --prefix omd-local/omd/omd)/bin/omd"`. Verify capabilities report
package `0.3.0b4`, protocol `1`, and an `enrich_note` schema version of `1`.
Refresh Home's backend check after upgrading. Home's readiness preflight and
"Nothing was sent" error display require a separate client update.

The `0.3.0b4` formula installs common PDF/Office/Web conversion dependencies,
`yt-dlp`, FFmpeg, Tesseract, and Python 3.12. PDF conversion uses Pillow 13.
It excludes Gradio and the browser UI while the UI dependency constraints
conflict with the Pillow version required by the security audit. `omd-ui --help`
explains the change; launching `omd-ui` exits with status 2 without starting a
server. OMD Home does not require Gradio or the browser UI.

Local transcription remains optional because the MLX model stack is large and
Apple-Silicon-specific. The dependency-free core still supports Python 3.10;
optional document converters require Python 3.11 or newer for Pillow 13.

The installable formula lives in the separate public
`omd-local/homebrew-omd` repository. This directory contains its release
template; keep both copies aligned after every release.

## Publish an immutable release asset

Complete the release checks, dependency audit, and review first. Commit only
release files with a Lore decision record. Do not recreate an existing version
tag with different contents or include unrelated working changes.

From a clean checkout of the reviewed release commit:

```bash
VERSION=0.3.0b4
python -m omd.provider_disclosure
python -m pytest -q
python -m compileall -q omd tests
python -m build --sdist --wheel
```

Verify the installed wheel's capabilities with `OMD_BUILD_REVISION` unset: its
`build_revision` must be the 40-character release commit SHA. The source archive
must preserve that identity when installed outside a Git checkout.

After verification, tag the same commit and upload its artifacts:

```bash
git tag -a "v${VERSION}" -m "OMD ${VERSION} core backend"
git push origin "refs/tags/v${VERSION}"
gh release create "v${VERSION}" "dist/omd-${VERSION}.tar.gz" dist/*.whl \
  --prerelease --verify-tag --title "OMD ${VERSION} core backend" \
  --notes-file release-notes.md
```

The formula uses the uploaded source distribution rather than GitHub's
repository archive. Download it and confirm its SHA-256 matches the local
artifact:

```bash
shasum -a 256 "dist/omd-${VERSION}.tar.gz"
curl -fL "https://github.com/omd-local/markdown-everything/releases/download/v${VERSION}/omd-${VERSION}.tar.gz" \
  -o "/tmp/omd-${VERSION}.tar.gz"
shasum -a 256 "/tmp/omd-${VERSION}.tar.gz"
```

Only then update the `url` and `sha256` fields in `packaging/homebrew/omd.rb`.
Homebrew derives the version from the `omd-X.Y.Z.tar.gz` filename. Copy the same
formula into `Formula/omd.rb` in `omd-local/homebrew-omd`.

## Verify and publish the tap

Verify a fresh source installation, not only an existing development environment:

```bash
brew reinstall --build-from-source omd-local/omd/omd
brew test omd-local/omd/omd
brew audit --strict omd-local/omd/omd
```

`brew test` checks the package version, protocol v1, enrich-note schema v1,
persistent release identity, disclosure schema v1, MCP shutdown, and local HTML
and PDF conversion. It also checks that Gradio is absent, Pillow is version 13,
and `omd-ui` explains its unavailability. No Hosted AI answer request or Vault
content is involved.

Run the dependency audit against a fresh environment with the exact runtime
packages from the formula, including common document extras and the Pillow
security floor. Deferring the UI does not exempt conversion dependencies from
auditing.

After verification, commit the formula in each repository using a Lore decision
record and push the tap's reviewed commit. Users receive it with:

```bash
brew update
brew upgrade omd-local/omd/omd
```

The formula uses `preserve_rpath` because some document conversion wheels carry
valid short `@rpath` IDs; expanding them to long Cellar paths can exceed their
Mach-O load-command headers.

## Restore the browser UI in a later release

Restore UI dependencies only after Gradio accepts a supported Pillow version
that passes the dependency audit. Restore the real `omd-ui` entry point and its
installation smoke test together. Publish a new version and checksum rather
than replacing `0.3.0b4` assets. Audit the hosted demo separately before deploying
any demo update; the core backend release does not update that service.

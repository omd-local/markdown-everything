# Homebrew core backend release

This is the maintainer route for the planned `0.3.0b5` public core release.
The staged files are a release candidate, not evidence that its assets or tap
update have been published. Users receive b5 after both are available:

```bash
brew install omd-local/omd/omd
```

For an existing installation, after the tap update is merged:

```bash
brew update
brew upgrade omd-local/omd/omd
omd capabilities --json
```

Set OMD Home's executable path to the result of
`echo "$(brew --prefix omd-local/omd/omd)/bin/omd"`. For b5, verify capabilities
report package `0.3.0b5`, protocol `1`, `enrich_note` schema version `1`, and the
full public release commit in `build_revision`. Refresh Home's backend check
after upgrading. Home is released separately; installing the backend does not
update the Home plugin.

The core formula installs common PDF/Office/Web conversion dependencies,
`yt-dlp`, FFmpeg, Tesseract, and Python 3.12. PDF conversion uses Pillow 13.
It excludes Gradio and the browser UI while the UI dependency constraints
conflict with the Pillow version required by the security audit. `omd-ui --help`
explains the change; launching `omd-ui` exits with status 2 without starting a
server. OMD Home does not require Gradio or the browser UI.

Local transcription and Ollama remain optional; the MLX transcription stack is
Apple-Silicon-specific. The dependency-free core supports Python 3.10; optional
document converters require Python 3.11 or newer for Pillow 13. DeepSeek remains
unavailable for Hosted AI; do not present the release as enabling it.

The installable formula lives in the separate public
[omd-local/homebrew-omd](https://github.com/omd-local/homebrew-omd) repository.
This directory contains its [release template](omd.rb).

## Prepare the public release change

Start with a clean checkout of the public
[omd-local/markdown-everything](https://github.com/omd-local/markdown-everything)
repository and a branch based on public main. Transfer only reviewed release
files from the development snapshot, inspect every staged path and diff, and
stage an explicit file allowlist. Do not push a private development branch or
its history into the public repository.

Open a protected pull request for the release files. Complete release checks,
provider disclosure review, dependency audit, required CI and review before
merging. Keep commit messages as Lore decision records. Include the b5 version
bump, runtime changes, tests and release documentation; leave unrelated local
changes out of the public PR.

Build only after this PR is merged. The final public release commit, including
the merge or squash commit produced by the PR, is the artifact identity. A
private development SHA or an earlier candidate SHA is not the release identity.

## Build immutable wheel and source assets

Use a fresh, clean public checkout at that final commit. Confirm the version
is b5, record its full 40-character SHA, then run the required checks and build
both artifact types with that identity explicitly stamped:

```bash
VERSION=0.3.0b5
RELEASE_SHA="$(git rev-parse HEAD)"
git status --short
python -m omd.provider_disclosure
python -m pytest -q
python -m compileall -q omd tests
OMD_BUILD_REVISION="$RELEASE_SHA" python -m build --sdist --wheel
```

Stop if the checkout is dirty, the commit is not the reviewed public commit, or
a required check fails. Use a fresh build directory so `dist/` contains only
this release's wheel and source distribution.

Install the wheel and source distribution separately into fresh environments
outside any Git checkout. Leave `OMD_BUILD_REVISION` unset during installation
and verification. Both installations must report package `0.3.0b5`, protocol
v1, enrich-note schema v1, and `build_revision` equal to `RELEASE_SHA` through
`omd capabilities --json`. This verifies that the source archive preserves its
identity when building a new wheel, rather than borrowing another checkout's
SHA. Verify installed entry points, disclosure and representative local
conversion using these environments as well.

Tag the same public commit as `v0.3.0b5` through the reviewed release process.
Upload only the verified wheel, source distribution, SHA-256 manifest and
reviewed notes to the public prerelease. Never replace an existing tag or asset
with different contents; publish a new version instead.

Generate checksums before upload, then re-download both published artifacts
into a separate directory and compare them against the local manifest:

```bash
(cd dist && shasum -a 256 "omd-${VERSION}.tar.gz" "omd-${VERSION}-"*.whl > SHA256SUMS)
DOWNLOAD_DIR="$(mktemp -d)"
curl -fL "https://github.com/omd-local/markdown-everything/releases/download/v${VERSION}/omd-${VERSION}.tar.gz" \
  -o "${DOWNLOAD_DIR}/omd-${VERSION}.tar.gz"
```

Also download the exact wheel filename listed in the local `SHA256SUMS` file
from that release into `DOWNLOAD_DIR`. Copy the local manifest there and run
`shasum -a 256 -c SHA256SUMS` from `DOWNLOAD_DIR`; all entries must match. A
checksum calculated only from the downloaded file is not a comparison.

## Review and verify the tap update

Only after re-download verification, update the source-distribution `url` and
`sha256` in this repository's `packaging/homebrew/omd.rb` and the public tap's
`Formula/omd.rb`. Homebrew derives the version from `omd-0.3.0b5.tar.gz`. Use the
uploaded source distribution, not GitHub's generated repository archive. Keep
both formula copies aligned through reviewed protected PRs; do not rewrite the
release tag to include this later checksum update.

Check out the proposed tap PR revision in the local public tap checkout before
testing, so these commands exercise the candidate formula rather than the
previously published one:

```bash
brew reinstall --build-from-source omd-local/omd/omd
brew test omd-local/omd/omd
brew audit --strict omd-local/omd/omd
```

For a machine without an existing installation, use
`brew install --build-from-source omd-local/omd/omd` first. Confirm the exact b5
version and tagged public commit, and run the installed command outside the
source tree. `brew test` checks package version, protocol v1, enrich-note schema v1,
persistent release identity, disclosure schema v1, MCP shutdown, and local HTML
and PDF conversion. It also checks that Gradio is absent, Pillow is version 13,
and `omd-ui` explains its unavailability. No Hosted AI answer request or Vault
content is involved.

Run the dependency audit against a fresh environment with the exact runtime
packages from the formula, including common document extras and the Pillow
security floor. Deferring the UI does not exempt conversion dependencies from
auditing.

Review the final tap diff, required CI and source-install evidence before
merging the protected PR. After merge, verify `brew update` and installation
from the published tap in a clean machine or environment, including
capabilities and a local conversion.

The formula uses `preserve_rpath` because some document conversion wheels carry
valid short `@rpath` IDs; expanding them to long Cellar paths can exceed their
Mach-O load-command headers.

## Restore the browser UI in a later release

Restore UI dependencies only after Gradio accepts a supported Pillow version
that passes the dependency audit. Restore the real `omd-ui` entry point and its
installation smoke test together. Publish a new version and checksum rather
than replacing b5 assets. Audit the hosted demo separately before deploying
any demo update; the core backend release does not update that service.

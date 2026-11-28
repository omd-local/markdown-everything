# Independent macOS engine build

This is the maintainer build for **OMD Engine.app**, separate from the Obsidian
plugin. The downloaded app contains CPython and the existing OMD engine; the
document profile also contains the existing MarkItDown document converters.
Users do not run Homebrew, pip, Xcode or a compiler to install this artifact.

The app is desktop-only. Use the matching Apple Silicon (`arm64`) or Intel
(`x86_64`) artifact. Its minimum macOS version is derived from the selected
wheels and recorded in both `Info.plist` and `Resources/omd-engine.json`; the
current document wheel sets require macOS 14 or later.

## Inputs and reproducibility

- `runtime-manifest.json` pins the official python-build-standalone release,
  architecture, archive URL and SHA-256. The builder verifies the complete
  archive before extraction or execution.
- `wheels-<architecture>.requirements.txt` and the matching `.lock.json` pin
  every document wheel and its digest. Keep both files aligned. The builder
  verifies all wheels before running the verified pip bootstrap wheel.
- `document-requirements.txt` describes the existing document feature selection
  for maintainers resolving a future update. It is not used for release builds;
  release builds download the checked-in exact, hash-locked inputs.
- Wheel installation is offline, binary-only and without dependency resolution.
  Source distributions, compilation and end-user dependency installation are
  excluded. A missing wheel fails the build rather than falling back to source.
- OMD version, source revision, runtime provenance, wheel inventory, file
  checksums and link targets are recorded beside the generated app. Runtime
  licenses and wheel license files are retained; OMD's license is copied into
  the app's Resources directory.

Build on a native matching macOS runner. The builder uses Python's standard
library and does not add a project dependency:

```sh
python3 packaging/macos/build_bundle.py \
  --runtime-archive /path/to/pinned-runtime.tar.gz \
  --architecture arm64 \
  --profile documents \
  --wheelhouse /path/to/verified-wheels \
  --wheel-lock packaging/macos/wheels-arm64.lock.json \
  --source-root . \
  --revision FULL_SOURCE_COMMIT_SHA \
  --output-dir /path/to/new-output-directory
```

The output directory must not exist. Extraction rejects traversal, external
links, duplicate paths, devices and oversized archives. Build the app on a
filesystem supporting POSIX permissions, such as `/private/tmp`; exFAT app
output is rejected because it cannot retain installation permissions. App and
package staging use `/private/tmp`, normalize directory/file modes and verify
that the requested modes actually apply. Copy compressed ZIP/PKG artifacts to
external volumes afterwards.

Successful output uses an exclusive atomic rename where supported. On a
filesystem without that operation, publishing atomically reserves a fresh
destination and copies into it, writing the completeness manifest last for a
directory. Failed copies remove only that build's reserved output; existing or
concurrently created destinations are preserved.

The explicit `--profile core` is available for diagnostics and packaging tests.
It omits document wheels and must not be described as supporting normal
PDF/Office conversion. The recommended user artifact uses `documents`.

## App and optional CLI installer

The portable app can be moved to `/Applications` or `~/Applications` without
changing its interpreter paths. Its CLI is available at:

```text
/Applications/OMD Engine.app/Contents/MacOS/omd
```

This absolute executable path works without adding anything to shell PATH.
The Obsidian plugin discovers the canonical app locations. A standalone `.app`
drag-install does not by itself create the bare `omd` shell command.

For a conventional independently launched Installer package:

```sh
python3 packaging/macos/build_pkg.py \
  --app '/path/to/new-output-directory/OMD Engine.app' \
  --output /path/to/new-OMD-Engine.pkg
```

This invokes Apple's `pkgbuild` on the maintainer's Mac, never `installer`.
The generated package places the app in `/Applications` and links `omd` and
`omd-mcp` in `/usr/local/bin`. Installer requests administrator authorization;
the app archive remains the installation choice without administrator access.
A custom shell PATH must still include `/usr/local/bin` to use a bare command.

Before installing, the package checks architecture, the recorded minimum macOS
version, the canonical app identifier and existing CLI ownership. It refuses
to replace a Homebrew/user binary or a link to another installation and gives
the app-archive alternative. Upgrades accept only links resolving to the
canonical app executable. The app component is non-relocatable so Installer
cannot silently update an old copy elsewhere and leave broken CLI links.

## Optional capabilities

The base document bundle does not include Ollama, model weights, Tesseract,
all-language OCR data, ffmpeg, yt-dlp, transcription models or Gradio. Existing
tools remain discoverable because launchers prepend the bundle's executables
to the user's inherited PATH. The plugin checks feature readiness and provides
guidance; it does not execute this installer or install executable dependencies.

## Third-party notices and release clearance

The builder copies the exact reviewed runtime and wheel notices into
`Contents/Resources/third-party-licenses/` and records their provenance and
hashes in `inventory.json`. Missing or changed materials reject the build.
For public release builds, pass `--strict-release`; it also rejects unresolved
license blockers. **The current document profile is blocked by cobble 0.1.4's
missing original copyright/license notice.** Review builds retain this blocker
in both the license inventory and `omd-engine.json`; they are not cleared for
public binary release. See [third-party materials](../../docs/third-party-licenses.md)
for sources, coverage and the required resolution.

## Verification and release boundary

`.github/workflows/macos-engine.yml` builds natively for both architectures,
tests hostile archive inputs and install conflicts, exercises document capture,
moves the app to a path containing spaces, runs with a minimal PATH, and builds
and expands the package without installing it. It audits the exact wheel lock
and records the license inventory. Public binary uploads are blocked if either
gate fails; review JSON evidence remains available. Any permitted binary uploads
are **unsigned review artifacts** only; the workflow does not publish a release.

Before a public release, sign nested native code and the app with the project's
Developer ID Application identity, sign the installer with Developer ID
Installer, and notarize/staple the distribution using the project's Apple
credentials. Recompute the distributed checksums and bundle manifest after
signing changes files. Signing and notarization require real credentials and
are not claimed by this builder. Verify Gatekeeper and first use on a fresh
Mac of each architecture with no Homebrew, system Python setup or developer
tools before declaring that release ready.

The owner preparation, credential boundaries and final-artifact acceptance steps
are listed in [macOS signing and notarization](../../docs/macos-signing.md).

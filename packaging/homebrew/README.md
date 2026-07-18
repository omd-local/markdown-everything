# Homebrew tap setup

Goal: end user installs `omd` with a single line:

```bash
brew install omd-local/omd/omd
```

The installable formula lives in the separate public `homebrew-omd` repository
(Homebrew's naming convention for taps). The copy in this directory is the
release template; keep it aligned with the published tap after every release.

## One-time release flow

### 1. Build and publish a release asset

```bash
# from the omd repo root
VERSION=0.3.0b2
git add -A
git commit -m "release: v${VERSION}"
git tag -a "v${VERSION}" -m "OMD ${VERSION}"
git push origin main "refs/tags/v${VERSION}"
python -m build --sdist
gh release create "v${VERSION}" "dist/omd-${VERSION}.tar.gz" \
  --prerelease --verify-tag --title "OMD ${VERSION}"
```

The formula uses the uploaded source distribution rather than GitHub's
auto-generated repository archive:

```
https://github.com/omd-local/markdown-everything/releases/download/v${VERSION}/omd-${VERSION}.tar.gz
```

Do not recreate an existing version tag with different contents. Publish a new
version so downstream checksums remain stable.

### 2. Compute its sha256

```bash
shasum -a 256 "dist/omd-${VERSION}.tar.gz"
curl -fL "https://github.com/omd-local/markdown-everything/releases/download/v${VERSION}/omd-${VERSION}.tar.gz" \
  -o "/tmp/omd-${VERSION}.tar.gz"
shasum -a 256 "/tmp/omd-${VERSION}.tar.gz"
```

The local and public-download digests must match. Copy that digest into the
formula.

### 3. Clone the project tap

The tap lives at `omd-local/homebrew-omd`. The repository name MUST remain
`homebrew-omd` because Homebrew strips the `homebrew-` prefix and uses the rest
as the tap name.

```bash
# locally — adjust the parent path as needed
cd ~/code
gh repo clone omd-local/homebrew-omd
cd homebrew-omd
mkdir -p Formula
cp /path/to/omd/packaging/homebrew/omd.rb Formula/omd.rb
```

### 4. Patch sha256 + url in Formula/omd.rb

Update the `url` and `sha256` fields with the new release asset and digest.
Homebrew derives the version from the `omd-X.Y.Z.tar.gz` filename.

### 5. Push the tap

```bash
git add Formula/omd.rb
git commit -m "omd ${VERSION}"
git push origin main
```

### 6. Verify install

On any Mac:

```bash
brew tap omd-local/omd
brew install omd
omd --help
omd-mcp < /dev/null    # exits 0
brew test omd
brew audit --strict omd-local/omd/omd
```

Or one-shot (no separate tap step):

```bash
brew install omd-local/omd/omd
```

The `v0.3.0b2` public beta includes `omd-ui`, and the formula treats that command
as part of the published contract: installation and `brew test` fail if the UI
or its dependencies are missing. The formula also installs the dependencies for
common PDF/Office/Web conversion. It deliberately leaves `mlx-whisper` out of
the default install because that large stack is Apple-Silicon-specific; OMD
detects it as an optional runtime capability. The base formula still installs
`yt-dlp` for supported public media downloads.

Before publishing a formula change, verify a source reinstall, not only an
already-populated development environment:

```bash
brew reinstall --build-from-source omd-local/omd/omd
brew test omd-local/omd/omd
brew audit --strict omd-local/omd/omd
```

Also convert one generated or non-sensitive PDF and confirm the Markdown text.
The formula uses `preserve_rpath` because several Python extension wheels carry
valid short `@rpath` IDs; expanding those IDs to long Cellar paths can exceed
their Mach-O load-command headers.

## Subsequent releases

For each new version:

1. Bump version in `pyproject.toml`, commit, tag `vX.Y.Z` (or `vX.Y.ZbN` for a
   beta), build the sdist, and upload it to the matching GitHub release.
2. Download the public release asset and confirm its SHA-256 matches the local
   sdist.
3. In the `homebrew-omd` repo, edit `Formula/omd.rb` — bump `url` and
   `sha256`. Commit. Push.
4. Users update via `brew update && brew upgrade omd`.

## Why a separate repo

Homebrew taps are a fixed convention: `<user>/homebrew-<name>` becomes
`brew install <user>/<name>/<formula>`. The formula file lives there, not
in the project repo. This `packaging/homebrew/omd.rb` is the authoritative
release template you sync into the tap on each release. The tap copy is the
authoritative formula users install.

## Optional: GitHub Action to auto-bump the tap

Add `.github/workflows/release.yml` in this repo to push an updated formula
to `homebrew-omd` whenever a tag is cut. See
<https://docs.brew.sh/Formula-Cookbook> "Updating the formula" — wire
`mislav/bump-homebrew-formula-action` for one-shot setup. Skip until manual
release flow gets annoying.

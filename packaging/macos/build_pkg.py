#!/usr/bin/env python3
"""Package a verified engine app with a conflict-safe optional CLI integration.

pkgbuild runs only on the maintainer's Mac. This tool creates an installer; it
does not run it. The app archive remains the no-administrator installation path.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile


spec = importlib.util.spec_from_file_location("omd_bundle_builder", Path(__file__).with_name("build_bundle.py"))
assert spec is not None and spec.loader is not None
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
BundleError = builder.BundleError


def verify_app(app: Path) -> dict:
    if app.name != builder.APP_NAME or not app.is_dir() or app.is_symlink():
        raise BundleError("Expected an OMD Engine.app directory")
    try:
        manifest = json.loads((app.parent / "bundle-manifest.json").read_text())
        provenance = json.loads((app / "Contents/Resources/omd-engine.json").read_text())
        with (app / "Contents/Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise BundleError("App metadata or bundle manifest is unreadable") from exc
    if manifest.get("schema_version") != 1 or manifest.get("bundle") != provenance or info.get("CFBundleIdentifier") != builder.APP_ID:
        raise BundleError("App identity does not match its bundle manifest")
    listed = set()
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise BundleError("Bundle manifest has no files")
    for record in records:
        relative = PurePosixPath(record.get("path", ""))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != app.name or str(relative) in listed:
            raise BundleError("Unsafe or duplicate bundle manifest path")
        path = app.parent.joinpath(*relative.parts)
        listed.add(str(relative))
        if "symlink" in record:
            if not path.is_symlink() or os.readlink(path) != record["symlink"] or not path.resolve().is_relative_to(app.resolve()) or not path.exists():
                raise BundleError("Bundle symlink verification failed")
        else:
            builder.verify_sha256(path, record.get("sha256", ""))
    actual = {path.relative_to(app.parent).as_posix() for path in app.rglob("*") if not builder.is_metadata_sidecar(path.relative_to(app)) and (path.is_file() or path.is_symlink())}
    if actual != listed:
        raise BundleError("App contains unlisted or missing files")
    if provenance.get("architecture") not in builder.ARCHITECTURES:
        raise BundleError("App architecture is invalid")
    return provenance


def preinstall_script(architecture: str, minimum_macos: str = "11.0") -> str:
    if architecture not in builder.ARCHITECTURES:
        raise BundleError("Invalid package architecture")
    if not re.fullmatch(r"\d+\.\d+", minimum_macos):
        raise BundleError("Invalid minimum macOS version")
    minimum_major, minimum_minor = minimum_macos.split(".")
    return '''#!/bin/sh
set -eu
set -f
VOLUME=${3:-/}
VOLUME=${VOLUME%/}
EXPECTED_ARCH=''' + architecture + '''
MINIMUM_MAJOR=''' + minimum_major + '''
MINIMUM_MINOR=''' + minimum_minor + '''
if [ "$(/usr/bin/uname -m)" != "$EXPECTED_ARCH" ]; then
    echo "This OMD Engine installer is for $EXPECTED_ARCH. Download the installer for this Mac's architecture." >&2
    exit 1
fi
CURRENT_VERSION=$(/usr/bin/sw_vers -productVersion)
CURRENT_MAJOR=${CURRENT_VERSION%%.*}
CURRENT_REST=${CURRENT_VERSION#*.}
CURRENT_MINOR=${CURRENT_REST%%.*}
case "$CURRENT_MAJOR:$CURRENT_MINOR" in
    *[!0-9:]*|:*) echo "Could not verify the macOS version; installation was stopped." >&2; exit 1 ;;
esac
if [ "$CURRENT_MAJOR" -lt "$MINIMUM_MAJOR" ] || { [ "$CURRENT_MAJOR" -eq "$MINIMUM_MAJOR" ] && [ "$CURRENT_MINOR" -lt "$MINIMUM_MINOR" ]; }; then
    echo "This OMD Engine package requires macOS $MINIMUM_MAJOR.$MINIMUM_MINOR or later; this Mac has $CURRENT_VERSION." >&2
    exit 1
fi
APP="$VOLUME/Applications/OMD Engine.app"
if [ -e "$APP" ] || [ -L "$APP" ]; then
    if [ -L "$APP" ] || [ ! -d "$APP" ] || [ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist" 2>/dev/null || true)" != 'org.omd-local.engine' ]; then
        echo "Applications already contains an unrelated OMD Engine.app. Keep it unchanged; use the standalone app archive or choose another location." >&2
        exit 1
    fi
fi
normalize_path() {
    old_ifs=$IFS
    IFS=/
    set -- $1
    IFS=$old_ifs
    normalized=''
    for component do
        case "$component" in
            ''|.) ;;
            ..) normalized=${normalized%/*} ;;
            *) normalized="$normalized/$component" ;;
        esac
    done
    printf '%s\\n' "${normalized:-/}"
}
for executable in omd omd-mcp; do
    logical="/usr/local/bin/$executable"
    actual="$VOLUME$logical"
    if [ -e "$actual" ] || [ -L "$actual" ]; then
        if [ ! -L "$actual" ]; then
            echo "$logical already exists and is not an OMD Engine link. It was not changed. Use the standalone app archive and set its executable path in OMD Home." >&2
            exit 1
        fi
        count=0
        while [ -L "$VOLUME$logical" ]; do
            count=$((count + 1))
            if [ "$count" -gt 40 ]; then
                echo "Existing $executable link cannot be resolved safely. It was not changed." >&2
                exit 1
            fi
            link=$(/usr/bin/readlink "$VOLUME$logical")
            case "$link" in
                /*) logical=$(normalize_path "$link") ;;
                *) logical=$(normalize_path "${logical%/*}/$link") ;;
            esac
        done
        if [ "$logical" != "/Applications/OMD Engine.app/Contents/MacOS/$executable" ]; then
            echo "/usr/local/bin/$executable belongs to another installation. It was not changed. Keep using that installation or use the standalone OMD Engine app archive." >&2
            exit 1
        fi
    fi
done
exit 0
'''


def prepare_payload(app: Path, destination: Path) -> tuple[Path, Path, dict]:
    provenance = verify_app(app)
    root = destination / "payload"
    applications = root / "Applications"
    applications.mkdir(parents=True)
    shutil.copytree(app, applications / app.name, symlinks=True, ignore=shutil.ignore_patterns("._*", ".DS_Store", "__MACOSX"))
    manifest = json.loads((app.parent / "bundle-manifest.json").read_text())
    modes = {str(PurePosixPath(record["path"]).relative_to(app.name)): record.get("mode") for record in manifest["files"] if "symlink" not in record}
    builder.normalize_permissions(applications / app.name, modes)
    binaries = root / "usr/local/bin"
    binaries.mkdir(parents=True)
    for name in ("omd", "omd-mcp"):
        (binaries / name).symlink_to(f"/Applications/{app.name}/Contents/MacOS/{name}")
    scripts = destination / "scripts"
    scripts.mkdir()
    builder._write_executable(scripts / "preinstall", preinstall_script(provenance["architecture"], provenance["minimum_macos"]))
    with (destination / "components.plist").open("wb") as handle:
        plistlib.dump(component_configuration(), handle)
    for directory in (root, applications, root / "usr", root / "usr/local", binaries, scripts):
        directory.chmod(0o755)
        if stat.S_IMODE(directory.stat().st_mode) != 0o755:
            raise BundleError("Installer staging did not preserve directory permissions")
    if stat.S_IMODE((scripts / "preinstall").stat().st_mode) != 0o755:
        raise BundleError("Installer preflight script is not executable by the installer")
    return root, scripts, provenance


def component_configuration() -> list[dict]:
    # pkgbuild's default relocation can otherwise update an old app elsewhere
    # while creating a CLI link to a missing canonical /Applications bundle.
    return [{"RootRelativeBundlePath": "Applications/OMD Engine.app", "BundleIsRelocatable": False,
             "BundleHasStrictIdentifier": True, "BundleIsVersionChecked": False, "BundleOverwriteAction": "upgrade"}]


def build_pkg(app: Path, output: Path) -> Path:
    if platform.system() != "Darwin":
        raise BundleError("pkgbuild requires macOS on the build machine")
    if output.exists() or output.is_symlink():
        raise BundleError("Installer output already exists; refusing to overwrite it")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="omd-engine-pkg-", dir="/private/tmp"))
    try:
        root, scripts, provenance = prepare_payload(app, temporary)
        if provenance["architecture"] != platform.machine():
            raise BundleError("Create the installer on the matching native architecture")
        with (app / "Contents/Info.plist").open("rb") as handle:
            version = plistlib.load(handle)["CFBundleVersion"]
        package = temporary / "OMD Engine.pkg"
        subprocess.run(["/usr/bin/pkgbuild", "--root", str(root), "--scripts", str(scripts), "--component-plist", str(temporary / "components.plist"), "--install-location", "/", "--identifier", builder.APP_ID, "--version", version, "--ownership", "recommended", "--filter", r"/\._", "--filter", r"/\.DS_Store$", str(package)], check=True, capture_output=True, text=True, timeout=180)
        builder._atomic_publish(package, output)
        return output
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise BundleError(f"Could not create installer: {getattr(exc, 'stderr', '') or exc}") from exc
    finally:
        shutil.rmtree(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_pkg(args.app, args.output)
    except BundleError as exc:
        parser.exit(1, f"Package rejected: {exc}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

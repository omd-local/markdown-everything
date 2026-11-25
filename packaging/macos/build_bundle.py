#!/usr/bin/env python3
"""Build an independent, relocatable macOS engine from verified offline inputs.

This maintainer tool uses only Python's standard library. It never downloads
anything, modifies an installed application, or invokes Homebrew/Xcode.
"""
from __future__ import annotations

import argparse
import ast
import base64
import configparser
import csv
import errno
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import posixpath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile


HERE = Path(__file__).resolve().parent
ARCHITECTURES = {"arm64", "x86_64"}
PYTHON_MINOR = "3.12"
APP_NAME = "OMD Engine.app"
APP_ID = "org.omd-local.engine"
MAX_RUNTIME_BYTES = 2 * 1024**3
MAX_RUNTIME_MEMBERS = 50_000
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")


class BundleError(ValueError):
    """A build input or verification failed; no output was published."""


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    if not SHA256.fullmatch(expected) or not path.is_file() or path.is_symlink():
        raise BundleError("Expected a regular input file and a SHA-256 digest")
    if digest(path) != expected:
        raise BundleError(f"SHA-256 mismatch for {path.name}")


def load_runtime_manifest() -> dict:
    manifest = json.loads((HERE / "runtime-manifest.json").read_text())
    if manifest.get("schema_version") != 1 or set(manifest.get("runtimes", {})) != ARCHITECTURES:
        raise BundleError("Invalid pinned runtime manifest")
    return manifest


def _archive_path(name: str) -> PurePosixPath:
    if not name or "\\" in name or any(ord(value) < 32 for value in name):
        raise BundleError("Unsafe archive pathname")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "python":
        raise BundleError("Runtime archive must stay inside its python directory")
    return path


def extract_runtime(archive: Path, destination: Path, *, max_bytes: int = MAX_RUNTIME_BYTES) -> None:
    """Validate the complete archive before creating any output or links."""
    if destination.exists() or destination.is_symlink():
        raise BundleError("Runtime extraction destination already exists")
    try:
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
            if len(members) > MAX_RUNTIME_MEMBERS:
                raise BundleError("Runtime archive has too many entries")
            paths: dict[PurePosixPath, tarfile.TarInfo] = {}
            total = 0
            for member in members:
                path = _archive_path(member.name)
                if path in paths or not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                    raise BundleError("Duplicate or unsupported runtime archive entry")
                if member.size < 0:
                    raise BundleError("Invalid runtime entry size")
                total += member.size
                if total > max_bytes:
                    raise BundleError("Runtime archive exceeds its expansion limit")
                paths[path] = member
            links: dict[PurePosixPath, PurePosixPath] = {}
            for path, member in paths.items():
                for parent in path.parents:
                    if parent in paths and not paths[parent].isdir():
                        raise BundleError("Runtime archive entry has a non-directory parent")
                if member.issym() or member.islnk():
                    target = member.linkname
                    if not target or "\\" in target or PurePosixPath(target).is_absolute() or any(ord(v) < 32 for v in target):
                        raise BundleError("Unsafe runtime archive link")
                    normalized = posixpath.normpath(str(path.parent / target) if member.issym() else target)
                    links[path] = _archive_path(normalized)
            for path in links:
                seen = {path}
                target = links[path]
                while target in links:
                    if target in seen:
                        raise BundleError("Runtime archive has a link cycle")
                    seen.add(target)
                    target = links[target]
                if target not in paths or (paths[path].islnk() and not paths[target].isfile()):
                    raise BundleError("Runtime archive has a dangling or invalid link")
            destination.mkdir(parents=True)
            for path, member in paths.items():
                output = destination.joinpath(*path.parts[1:])
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    output.parent.mkdir(parents=True, exist_ok=True)
                    source = handle.extractfile(member)
                    if source is None:
                        raise BundleError("Runtime archive file has no data")
                    with source, output.open("xb") as target:
                        shutil.copyfileobj(source, target)
                    output.chmod(0o755 if member.mode & 0o111 else 0o644)
            for path, target in links.items():
                output = destination.joinpath(*path.parts[1:])
                output.parent.mkdir(parents=True, exist_ok=True)
                if paths[path].issym():
                    output.symlink_to(paths[path].linkname)
                else:
                    while target in links:
                        target = links[target]
                    os.link(destination.joinpath(*target.parts[1:]), output)
    except (tarfile.TarError, OSError) as exc:
        raise BundleError(f"Could not extract verified runtime: {exc}") from exc


def _compatible_wheel(filename: str, architecture: str) -> bool:
    try:
        _, python_tags, abi_tags, platform_tags = filename[:-4].rsplit("-", 3)
    except ValueError:
        return False
    python_ok = any(tag in {"py3", "py312", "cp312"} or ("abi3" in abi_tags.split(".") and re.fullmatch(r"cp3\d+", tag) and int(tag[3:]) <= 12) for tag in python_tags.split("."))
    platform_ok = any(tag == "any" or (tag.startswith("macosx_") and tag.endswith(("_" + architecture, "_universal2"))) for tag in platform_tags.split("."))
    return python_ok and platform_ok


def minimum_macos(wheels: list[Path]) -> str:
    versions = [(11, 0)]
    for wheel in wheels:
        for major, minor in re.findall(r"macosx_(\d+)_(\d+)_", wheel.name):
            versions.append((int(major), int(minor)))
    return ".".join(map(str, max(versions)))


def verify_wheelhouse(directory: Path, lock_path: Path, architecture: str) -> tuple[list[Path], dict]:
    try:
        lock = json.loads(lock_path.read_text())
    except (OSError, ValueError) as exc:
        raise BundleError("Wheelhouse lock is unreadable") from exc
    if (lock.get("schema_version"), lock.get("architecture"), lock.get("python"), lock.get("profile")) != (1, architecture, PYTHON_MINOR, "documents"):
        raise BundleError("Wheelhouse lock does not match this build target")
    records = lock.get("wheels")
    if not isinstance(records, list) or not 1 <= len(records) <= 200:
        raise BundleError("Wheelhouse lock must list every build wheel")
    wheels: list[Path] = []
    names: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise BundleError("Invalid wheel lock entry")
        name, checksum = record.get("filename"), record.get("sha256")
        if not isinstance(name, str) or Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*\.whl", name) or name in names:
            raise BundleError("Unsafe or duplicate wheel filename")
        if not _compatible_wheel(name, architecture):
            raise BundleError(f"Wheel is incompatible with this target: {name}")
        if not isinstance(checksum, str):
            raise BundleError("Wheel is missing a SHA-256 digest")
        path = directory / name
        verify_sha256(path, checksum)
        _verify_wheel_archive(path)
        names.add(name)
        wheels.append(path)
    if {p.name for p in directory.glob("*.whl") if not is_metadata_sidecar(p)} != names:
        raise BundleError("Wheelhouse contains unlisted wheels")
    if not any(p.name.lower().startswith("pip-") for p in wheels):
        raise BundleError("Wheelhouse must contain a verified pip bootstrap wheel")
    if not any(p.name.lower().startswith("markitdown-") for p in wheels):
        raise BundleError("Document profile must include MarkItDown")
    return sorted(wheels), lock


def _verify_wheel_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as handle:
            entries = handle.infolist()
            if len(entries) > MAX_RUNTIME_MEMBERS or sum(entry.file_size for entry in entries) > MAX_RUNTIME_BYTES:
                raise BundleError("Wheel exceeds its extraction limits")
            names = set()
            for entry in entries:
                name = PurePosixPath(entry.filename)
                if name.is_absolute() or ".." in name.parts or "\\" in entry.filename or entry.filename in names or stat.S_ISLNK(entry.external_attr >> 16):
                    raise BundleError("Wheel has an unsafe or duplicate archive entry")
                names.add(entry.filename)
            if not any(name.endswith(".dist-info/METADATA") for name in names):
                raise BundleError("Wheel is missing distribution metadata")
    except (OSError, zipfile.BadZipFile) as exc:
        raise BundleError(f"Wheel is not a valid ZIP archive: {path.name}") from exc


def _environment(contents: Path, revision: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment["PYTHONPATH"] = str(contents / "runtime/lib/python3.12/site-packages")
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONSAFEPATH"] = "1"
    environment["OMD_BUILD_REVISION"] = revision
    environment["PATH"] = str(contents / "runtime/bin") + ":/usr/bin:/bin:/usr/sbin:/sbin"
    return environment


def _run(arguments: list[str], *, environment: dict[str, str], timeout: int = 180) -> str:
    try:
        result = subprocess.run(arguments, check=True, capture_output=True, text=True, env=environment, timeout=timeout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise BundleError(f"Bundled runtime verification failed: {detail[-4000:]}") from exc
    return result.stdout


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.write_text(content)
    path.chmod(0o755)


def _shell_environment(relative_contents: str, revision: str) -> str:
    return f'''#!/bin/sh
set -eu
LAUNCHER=$0
LINK_COUNT=0
while [ -L "$LAUNCHER" ]; do
    LINK_COUNT=$((LINK_COUNT + 1))
    [ "$LINK_COUNT" -le 40 ] || exit 1
    LINK=$(/usr/bin/readlink "$LAUNCHER")
    case "$LINK" in
        /*) LAUNCHER=$LINK ;;
        *) LAUNCHER="$(/usr/bin/dirname -- "$LAUNCHER")/$LINK" ;;
    esac
done
CONTENTS=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$LAUNCHER")/{relative_contents}" && pwd -P)
unset PYTHONHOME
export PYTHONPATH="$CONTENTS/runtime/lib/python3.12/site-packages"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONSAFEPATH=1
export OMD_BUILD_REVISION='{revision}'
export PATH="$CONTENTS/runtime/bin:${{PATH:-/usr/bin:/bin:/usr/sbin:/sbin}}"
'''


def write_launchers(app: Path, revision: str) -> None:
    contents = app / "Contents"
    for executable, module in (("omd", "omd.cli"), ("omd-mcp", "omd.mcp_server")):
        _write_executable(contents / "MacOS" / executable, _shell_environment("..", revision) + f'exec "$CONTENTS/runtime/bin/python3" -s -B -P -m {module} "$@"\n')
    python_link = contents / "MacOS/python3"
    if python_link.exists() or python_link.is_symlink():
        python_link.unlink()
    python_link.symlink_to("../runtime/bin/python3")
    _write_executable(contents / "MacOS/OMD Engine", '''#!/bin/sh
set -eu
/usr/bin/osascript -e 'display dialog "OMD Engine is ready. Open Obsidian, then OMD Home settings and choose Check setup. Keep this app in Applications so the plugin can find it. OCR, transcription and local AI are optional separate components." with title "OMD Engine" buttons {"OK"} default button "OK"'
''')


def _wrap_console_scripts(contents: Path, revision: str) -> None:
    site = contents / "runtime/lib/python3.12/site-packages"
    for entry_file in sorted(site.glob("*.dist-info/entry_points.txt")):
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(entry_file)
        if not parser.has_section("console_scripts"):
            continue
        for name, value in parser.items("console_scripts"):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name) or not re.fullmatch(r"[A-Za-z_][\w.]*(?::[A-Za-z_][\w.]*)?(?:\s*\[[\w., -]+\])?", value):
                raise BundleError("Unsafe wheel console entry point")
            if name in {"python", "python3", "python3.12", "omd", "omd-mcp"}:
                continue
            code = f"from importlib.metadata import EntryPoint; import sys; sys.argv[0]={name!r}; sys.exit(EntryPoint({name!r}, {value!r}, 'console_scripts').load()())"
            quoted = "'" + code.replace("'", "'\"'\"'") + "'"
            _write_executable(contents / "runtime/bin" / name, _shell_environment("../..", revision) + f'exec "$CONTENTS/runtime/bin/python3" -s -B -P -c {quoted} "$@"\n')
    # pip's --target scripts and direct_url metadata contain build-machine
    # paths. The bundle uses the relative launchers above and records input
    # filenames/digests separately in its provenance manifest.
    shutil.rmtree(site / "bin", ignore_errors=True)
    for path in site.glob("*.dist-info/direct_url.json"):
        path.unlink(missing_ok=True)
    _refresh_records(site)


def _refresh_records(site: Path) -> None:
    for record in site.glob("*.dist-info/RECORD"):
        rows = []
        with record.open(newline="") as handle:
            for row in csv.reader(handle):
                if not row:
                    continue
                path = (site / row[0]).resolve()
                if not path.is_relative_to(site.parent.parent) or not path.is_file():
                    continue
                data = path.read_bytes()
                checksum = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                rows.append([row[0], "sha256=" + checksum, str(len(data))])
        rows = [row for row in rows if row[0] != record.relative_to(site).as_posix()]
        rows.append([record.relative_to(site).as_posix(), "", ""])
        with record.open("w", newline="") as handle:
            csv.writer(handle).writerows(rows)


def _source_version(source: Path) -> str:
    tree = ast.parse((source / "omd/__init__.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and re.fullmatch(r"[0-9][A-Za-z0-9.+-]*", value):
                return value
    raise BundleError("Could not read OMD source version")


def clear_site(site: Path) -> None:
    # exFAT may remove an AppleDouble companion when its sibling disappears.
    # A pre-collected directory listing may therefore contain vanished paths.
    for path in list(site.iterdir()):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def is_metadata_sidecar(path: Path) -> bool:
    return any(part.startswith("._") or part in {".DS_Store", "__MACOSX"} for part in path.parts)


def _copy_source(source: Path, site: Path, version: str) -> None:
    package = source / "omd"
    if not package.is_dir():
        raise BundleError("Source root does not contain OMD")
    destination = site / "omd"
    destination.mkdir()
    for path in sorted(package.rglob("*.py")):
        if path.name.startswith("._") or "__pycache__" in path.parts:
            continue
        if path.is_symlink():
            raise BundleError("Source package must not contain symlinks")
        target = destination / path.relative_to(package)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    metadata = site / f"omd-{version}.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(f"Metadata-Version: 2.1\nName: omd\nVersion: {version}\nRequires-Python: >=3.12\nLicense: MIT\n")
    (metadata / "INSTALLER").write_text("omd-macos-bundle\n")
    (metadata / "WHEEL").write_text("Wheel-Version: 1.0\nGenerator: omd-macos-bundle\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
    (metadata / "entry_points.txt").write_text("[console_scripts]\nomd = omd.cli:main\nomd-mcp = omd.mcp_server:main\n")
    with (metadata / "RECORD").open("w", newline="") as handle:
        writer = csv.writer(handle)
        for path in sorted([*destination.rglob("*"), *metadata.iterdir()]):
            if path.is_file() and path.name != "RECORD":
                writer.writerow([path.relative_to(site).as_posix(), "", ""])


def _atomic_publish(source: Path, destination: Path) -> None:
    """Darwin's exclusive rename prevents replacing even an empty destination."""
    function = ctypes.CDLL(None, use_errno=True).renamex_np
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(os.fsencode(source), os.fsencode(destination), 0x00000004) == 0:  # RENAME_EXCL
        return
    error = ctypes.get_errno()
    if error not in {errno.EXDEV, errno.ENOTSUP, errno.EINVAL}:
        raise BundleError(f"Could not publish fresh output: {os.strerror(error)}")
    _reserved_publish(source, destination)


def _reserved_publish(source: Path, destination: Path) -> None:
    """Never overwrite on filesystems lacking an exclusive atomic rename."""
    identity = None
    try:
        if source.is_dir():
            destination.mkdir(mode=0o755)
            identity = destination.stat()
            # Publish the completeness manifest last on a reserved directory.
            for path in sorted(source.iterdir(), key=lambda p: p.name == "bundle-manifest.json"):
                target = destination / path.name
                if path.is_dir():
                    shutil.copytree(path, target, symlinks=True)
                else:
                    shutil.copyfile(path, target)
        else:
            with destination.open("xb") as target:
                identity = os.fstat(target.fileno())
                with source.open("rb") as handle:
                    shutil.copyfileobj(handle, target)
                target.flush()
                os.fsync(target.fileno())
    except OSError as exc:
        if identity is not None:
            try:
                current = destination.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    if destination.is_dir():
                        shutil.rmtree(destination)
                    else:
                        destination.unlink()
            except FileNotFoundError:
                pass
        raise BundleError(f"Could not publish reserved output: {exc}") from exc


def require_posix_output(parent: Path) -> None:
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".omd-mode-check-", dir=parent) as temporary:
        directory = Path(temporary)
        directory.chmod(0o755)
        probe = directory / "mode-check"
        descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        probe.chmod(0o644)
        if stat.S_IMODE(directory.stat().st_mode) != 0o755 or stat.S_IMODE(probe.stat().st_mode) != 0o644:
            raise BundleError("App output filesystem does not preserve POSIX permissions. Build under /private/tmp and copy only the resulting ZIP or PKG to this volume.")


def normalize_permissions(root: Path, file_modes: dict[str, int] | None = None) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_symlink() or is_metadata_sidecar(path.relative_to(root)):
            continue
        if path.is_dir():
            mode = 0o755
        elif file_modes is not None:
            key = path.relative_to(root).as_posix()
            if key not in file_modes or file_modes[key] not in {0o644, 0o755}:
                raise BundleError("Payload file is missing a valid recorded permission mode")
            mode = file_modes[key]
        else:
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
        path.chmod(mode)
        if stat.S_IMODE(path.stat().st_mode) != mode:
            raise BundleError("Build staging filesystem cannot preserve required POSIX permissions")


def build_bundle(*, runtime_archive: Path, architecture: str, profile: str, wheelhouse: Path | None,
                 wheel_lock: Path | None, output_dir: Path, source_root: Path, revision: str) -> Path:
    if output_dir.exists() or output_dir.is_symlink():
        raise BundleError("Output directory already exists; refusing to overwrite it")
    if platform.system() != "Darwin" or platform.machine() != architecture or architecture not in ARCHITECTURES:
        raise BundleError("Build on a native macOS runner matching the selected architecture")
    if not REVISION.fullmatch(revision):
        raise BundleError("Revision must be the source commit's full 40-character SHA")
    if profile not in {"core", "documents"}:
        raise BundleError("Unknown engine profile")
    manifest = load_runtime_manifest()
    runtime = manifest["runtimes"][architecture]
    verify_sha256(runtime_archive, runtime["sha256"])
    wheels, lock = [], None
    if profile == "documents":
        if wheelhouse is None or wheel_lock is None:
            raise BundleError("Document profile requires a verified wheelhouse and lock")
        wheels, lock = verify_wheelhouse(wheelhouse, wheel_lock, architecture)
    version = _source_version(source_root)
    require_posix_output(output_dir.parent)
    temporary = Path(tempfile.mkdtemp(prefix="omd-engine-build-", dir="/private/tmp"))
    try:
        app = temporary / APP_NAME
        contents = app / "Contents"
        contents.mkdir(parents=True)
        extract_runtime(runtime_archive, contents / "runtime")
        site = contents / "runtime/lib/python3.12/site-packages"
        site.mkdir(parents=True, exist_ok=True)
        environment = _environment(contents, revision)
        python = contents / "runtime/bin/python3"
        identity = json.loads(_run([str(python), "-s", "-B", "-P", "-c", "import json,platform,sys; print(json.dumps({'version': platform.python_version(), 'architecture': platform.machine(), 'prefix': sys.prefix}))"], environment=environment))
        if identity["version"] != manifest["python_version"] or identity["architecture"] != architecture or not Path(identity["prefix"]).is_relative_to(contents / "runtime"):
            raise BundleError("Bundled interpreter identity does not match the pinned target")
        if wheels:
            # A pinned PBS runtime already contains pip. Install this profile
            # into a fresh site directory so two pip versions cannot coexist.
            clear_site(site)
            pip_wheel = next(path for path in wheels if path.name.lower().startswith("pip-"))
            bootstrap_environment = {**environment, "PYTHONPATH": str(pip_wheel)}
            _run([str(python), "-s", "-B", "-P", "-m", "pip", "--isolated", "--no-cache-dir", "install", "--no-index", "--no-deps", "--no-compile", "--only-binary=:all:", "--target", str(site), *map(str, wheels)], environment=bootstrap_environment)
        _copy_source(source_root, site, version)
        _wrap_console_scripts(contents, revision)
        write_launchers(app, revision)
        resources = contents / "Resources"
        resources.mkdir()
        license_path = source_root / "LICENSE"
        if license_path.is_file():
            shutil.copyfile(license_path, resources / "OMD-LICENSE")
        numeric_version = ".".join(re.findall(r"\d+", version)[:3])
        with (contents / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundleName": "OMD Engine", "CFBundleDisplayName": "OMD Engine", "CFBundleIdentifier": APP_ID, "CFBundlePackageType": "APPL", "CFBundleExecutable": "OMD Engine", "CFBundleShortVersionString": numeric_version, "CFBundleVersion": numeric_version, "LSMinimumSystemVersion": minimum_macos(wheels), "NSHighResolutionCapable": True}, handle)
        command = contents / "MacOS/omd"
        if _run([str(command), "--version"], environment=environment).strip() != f"omd {version}":
            raise BundleError("OMD version smoke test failed")
        capabilities = json.loads(_run([str(command), "capabilities", "--json"], environment=environment))
        if capabilities.get("package_version") != version or capabilities.get("build_revision") != revision or capabilities.get("protocol_version") != 1:
            raise BundleError("OMD capability identity smoke test failed")
        if profile == "documents":
            _run([str(python), "-s", "-B", "-P", "-m", "pip", "check"], environment=environment)
            fixture = temporary / "document-smoke.html"
            output = temporary / "document-smoke.md"
            fixture.write_text("<h1>OMD document smoke</h1><p>Bundled document conversion.</p>")
            _run([str(command), str(fixture), "-o", str(output)], environment=environment)
            if "OMD document smoke" not in output.read_text():
                raise BundleError("Bundled document conversion smoke test failed")
            fixture.unlink()
            output.unlink()
            for sidecar in temporary.glob("document-smoke.*"):
                if sidecar.is_file():
                    sidecar.unlink()
        provenance = {"schema_version": 1, "name": "OMD Engine", "version": version, "build_revision": revision,
                      "architecture": architecture, "profile": profile, "python_version": identity["version"],
                      "minimum_macos": minimum_macos(wheels),
                      "runtime": runtime, "wheelhouse": lock, "signing": "unsigned; release signing and notarization required",
                      "optional_components": ["ollama", "models", "tesseract", "ocr-language-data", "ffmpeg", "yt-dlp", "transcription", "browser-ui"]}
        (resources / "omd-engine.json").write_text(json.dumps(provenance, indent=2) + "\n")
        normalize_permissions(app)
        checksums = []
        for path in sorted(app.rglob("*")):
            if is_metadata_sidecar(path.relative_to(app)):
                continue
            relative = path.relative_to(temporary).as_posix()
            if path.is_symlink():
                checksums.append({"path": relative, "symlink": os.readlink(path)})
            elif path.is_file():
                checksums.append({"path": relative, "sha256": digest(path), "mode": stat.S_IMODE(path.stat().st_mode)})
        (temporary / "bundle-manifest.json").write_text(json.dumps({"schema_version": 1, "bundle": provenance, "files": checksums}, indent=2) + "\n")
        _atomic_publish(temporary, output_dir)
        return output_dir / APP_NAME
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-archive", type=Path, required=True)
    parser.add_argument("--architecture", choices=sorted(ARCHITECTURES), required=True)
    parser.add_argument("--profile", choices=("documents", "core"), default="documents")
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--wheel-lock", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=HERE.parents[1])
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    try:
        app = build_bundle(**vars(args))
    except BundleError as exc:
        parser.exit(1, f"Build rejected: {exc}\n")
    print(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

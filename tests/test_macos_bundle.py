"""Regression coverage for untrusted build inputs and relocatable launchers."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import textwrap
from xml.etree import ElementTree
import zipfile

import pytest


BUILDER_PATH = Path(__file__).resolve().parents[1] / "packaging/macos/build_bundle.py"
spec = importlib.util.spec_from_file_location("omd_macos_bundle", BUILDER_PATH)
assert spec is not None and spec.loader is not None
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)
pkg_spec = importlib.util.spec_from_file_location("omd_macos_pkg", BUILDER_PATH.with_name("build_pkg.py"))
assert pkg_spec is not None and pkg_spec.loader is not None
pkg = importlib.util.module_from_spec(pkg_spec)
pkg_spec.loader.exec_module(pkg)


def archive(path, entries):
    with tarfile.open(path, "w:gz") as handle:
        for name, kind, value in entries:
            item = tarfile.TarInfo(name)
            if kind == "file":
                data = value.encode()
                item.size = len(data)
                item.mode = 0o755 if name.endswith("python3.12") else 0o644
                handle.addfile(item, io.BytesIO(data))
            elif kind == "directory":
                item.type = tarfile.DIRTYPE
                handle.addfile(item)
            elif kind == "symlink":
                item.type = tarfile.SYMTYPE
                item.linkname = value
                handle.addfile(item)
            elif kind == "hardlink":
                item.type = tarfile.LNKTYPE
                item.linkname = value
                handle.addfile(item)
            else:
                item.type = tarfile.CHRTYPE
                handle.addfile(item)


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "python/../../escape", "python\\escape"])
def test_runtime_archive_rejects_traversal_before_extracting(tmp_path, name):
    source = tmp_path / "runtime.tar.gz"
    archive(source, [("python/good", "file", "ok"), (name, "file", "unsafe")])
    destination = tmp_path / "runtime"
    with pytest.raises(bundle.BundleError):
        bundle.extract_runtime(source, destination)
    assert not destination.exists()


@pytest.mark.parametrize("kind,target", [("symlink", "../../escape"), ("symlink", "/tmp/escape"), ("hardlink", "../escape")])
def test_runtime_archive_rejects_escaping_links(tmp_path, kind, target):
    source = tmp_path / "runtime.tar.gz"
    archive(source, [("python/bin/link", kind, target)])
    with pytest.raises(bundle.BundleError):
        bundle.extract_runtime(source, tmp_path / "runtime")


def test_runtime_archive_rejects_link_parent_even_with_valid_target(tmp_path):
    source = tmp_path / "runtime.tar.gz"
    archive(source, [("python/real", "directory", ""), ("python/link", "symlink", "real"), ("python/link/file", "file", "unsafe")])
    with pytest.raises(bundle.BundleError):
        bundle.extract_runtime(source, tmp_path / "runtime")


@pytest.mark.parametrize("entries", [
    [("python/file", "file", "first"), ("python/file", "file", "second")],
    [("python/device", "device", "")],
    [("python/a", "symlink", "b"), ("python/b", "symlink", "a")],
    [("unexpected/file", "file", "wrong archive")],
])
def test_runtime_archive_rejects_duplicates_devices_cycles_and_wrong_root(tmp_path, entries):
    source = tmp_path / "runtime.tar.gz"
    archive(source, entries)
    with pytest.raises(bundle.BundleError):
        bundle.extract_runtime(source, tmp_path / "runtime")


def test_runtime_archive_enforces_expansion_limits(tmp_path):
    source = tmp_path / "runtime.tar.gz"
    archive(source, [("python/large", "file", "123456")])
    with pytest.raises(bundle.BundleError):
        bundle.extract_runtime(source, tmp_path / "runtime", max_bytes=5)


def test_runtime_archive_preserves_internal_python_symlink(tmp_path):
    source = tmp_path / "runtime.tar.gz"
    archive(source, [("python/bin/python3.12", "file", "interpreter"), ("python/bin/python3", "symlink", "python3.12")])
    destination = tmp_path / "runtime"
    bundle.extract_runtime(source, destination)
    assert (destination / "bin/python3").is_symlink()
    assert (destination / "bin/python3").read_text() == "interpreter"
    assert os.access(destination / "bin/python3", os.X_OK)


def test_digest_mismatch_does_not_accept_archive(tmp_path):
    source = tmp_path / "runtime.tar.gz"
    source.write_bytes(b"unexpected")
    with pytest.raises(bundle.BundleError, match="SHA-256"):
        bundle.verify_sha256(source, "0" * 64)


def test_pinned_runtime_targets_are_explicit_and_complete():
    manifest = bundle.load_runtime_manifest()
    assert set(manifest["runtimes"]) == {"arm64", "x86_64"}
    for value in manifest["runtimes"].values():
        assert value["url"].startswith("https://github.com/astral-sh/python-build-standalone/releases/download/20261110/")
        assert len(value["sha256"]) == 64


def wheel_fixture(tmp_path, filename="pip-26.1.2-py3-none-any.whl"):
    wheel = tmp_path / filename
    distribution, version = filename.split("-")[:2]
    with zipfile.ZipFile(wheel, "w") as handle:
        handle.writestr(f"{distribution}-{version}.dist-info/METADATA", f"Name: {distribution}\nVersion: {version}\n")
    lock = {"schema_version": 1, "architecture": "arm64", "python": "3.12", "profile": "documents", "wheels": [{"filename": filename, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}]}
    lock_path = tmp_path / "wheelhouse.json"
    lock_path.write_text(json.dumps(lock))
    return wheel, lock_path, lock


def test_wheel_lock_rejects_tampering_and_path_injection(tmp_path):
    wheel, lock_path, lock = wheel_fixture(tmp_path)
    wheel.write_bytes(b"tampered")
    with pytest.raises(bundle.BundleError):
        bundle.verify_wheelhouse(tmp_path, lock_path, "arm64")
    lock["wheels"][0]["filename"] = "../outside.whl"
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(bundle.BundleError):
        bundle.verify_wheelhouse(tmp_path, lock_path, "arm64")


def test_wheel_lock_rejects_other_architecture(tmp_path):
    _, lock_path, lock = wheel_fixture(tmp_path)
    lock["architecture"] = "x86_64"
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(bundle.BundleError):
        bundle.verify_wheelhouse(tmp_path, lock_path, "arm64")


def test_wheel_lock_rejects_incompatible_platform_wheel(tmp_path):
    _, lock_path, _ = wheel_fixture(tmp_path, "pip-26.1.2-cp312-cp312-manylinux_2_17_aarch64.whl")
    with pytest.raises(bundle.BundleError):
        bundle.verify_wheelhouse(tmp_path, lock_path, "arm64")


def test_wheelhouse_ignores_exfat_metadata_but_never_installs_it(tmp_path):
    pip, lock_path, lock = wheel_fixture(tmp_path)
    document_wheel, _, document_lock = wheel_fixture(tmp_path, "markitdown-0.1.6-py3-none-any.whl")
    lock["wheels"].extend(document_lock["wheels"])
    lock_path.write_text(json.dumps(lock))
    (tmp_path / ("._" + pip.name)).write_text("AppleDouble metadata")
    wheels, _ = bundle.verify_wheelhouse(tmp_path, lock_path, "arm64")
    assert {path.name for path in wheels} == {pip.name, document_wheel.name}
    lock["wheels"].append({"filename": "._" + pip.name, "sha256": "0" * 64})
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(bundle.BundleError):
        bundle.verify_wheelhouse(tmp_path, lock_path, "arm64")


def test_matching_hash_does_not_allow_a_nonzip_wheel(tmp_path):
    wheel, lock_path, lock = wheel_fixture(tmp_path)
    wheel.write_bytes(b"not a wheel")
    lock["wheels"][0]["sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(bundle.BundleError, match="ZIP"):
        bundle.verify_wheelhouse(tmp_path, lock_path, "arm64")


def test_core_launcher_works_after_moving_app_with_spaces_and_minimal_path(tmp_path):
    app = tmp_path / "Before" / "OMD Engine.app"
    runtime = app / "Contents/runtime"
    (runtime / "bin").mkdir(parents=True)
    interpreter = runtime / "bin/python3"
    interpreter.write_text('#!/bin/sh\nprintf "%s\\n" "$OMD_BUILD_REVISION" "$PYTHONPATH" "$@"\n')
    interpreter.chmod(0o755)
    bundle.write_launchers(app, "a" * 40)
    moved = tmp_path / "Moved with spaces" / "OMD Engine.app"
    moved.parent.mkdir()
    app.rename(moved)
    output = subprocess.check_output([str(moved / "Contents/MacOS/omd"), "--version"], env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/untrusted", "PYTHONHOME": "/untrusted"}, text=True)
    assert output.splitlines()[0] == "a" * 40
    assert str(moved / "Contents/runtime/lib/python3.12/site-packages") in output
    assert "/untrusted" not in output
    assert output.splitlines()[-1] == "--version"
    assert (moved / "Contents/MacOS/python3").resolve() == (moved / "Contents/runtime/bin/python3").resolve()


def test_launcher_retains_external_optional_tools_and_prefers_bundled_tools(tmp_path):
    app = tmp_path / "OMD Engine.app"
    binaries = app / "Contents/runtime/bin"
    binaries.mkdir(parents=True)
    python = binaries / "python3"
    python.write_text('#!/bin/sh\ncommand -v optional-tool\ncommand -v markitdown\n')
    python.chmod(0o755)
    external = tmp_path / "custom-tools"
    external.mkdir()
    for path in [external / "optional-tool", external / "markitdown", binaries / "markitdown"]:
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    bundle.write_launchers(app, "b" * 40)
    output = subprocess.check_output([str(app / "Contents/MacOS/omd"), "--version"], env={"PATH": str(external) + ":/usr/bin:/bin"}, text=True)
    assert output.splitlines() == [str(external / "optional-tool"), str(binaries / "markitdown")]


def test_launcher_can_be_called_through_the_installer_cli_symlink(tmp_path):
    app = tmp_path / "Applications/OMD Engine.app"
    python = app / "Contents/runtime/bin/python3"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$PYTHONPATH"\n')
    python.chmod(0o755)
    bundle.write_launchers(app, "c" * 40)
    command = tmp_path / "bin/omd"
    command.parent.mkdir()
    command.symlink_to(app / "Contents/MacOS/omd")
    output = subprocess.check_output([str(command), "--version"], env={"PATH": "/usr/bin:/bin"}, text=True)
    assert output.strip() == str(app / "Contents/runtime/lib/python3.12/site-packages")


def test_cleanup_tolerates_automatically_removed_appledouble_files(tmp_path, monkeypatch):
    regular = tmp_path / "README.txt"
    sidecar = tmp_path / "._README.txt"
    regular.write_text("readme")
    sidecar.write_text("metadata")
    original_unlink = Path.unlink

    def simulated_exfat_unlink(path, *args, **kwargs):
        if path == regular:
            original_unlink(sidecar, missing_ok=True)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", simulated_exfat_unlink)
    bundle.clear_site(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_existing_output_is_preserved_on_build_rejection(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("original")
    with pytest.raises(bundle.BundleError):
        bundle.build_bundle(runtime_archive=tmp_path / "missing", architecture="arm64", profile="core", wheelhouse=None, wheel_lock=None, output_dir=output, source_root=BUILDER_PATH.parents[2], revision="a" * 40)
    assert marker.read_text() == "original"


def test_document_wheels_set_the_real_minimum_macos_version():
    assert bundle.minimum_macos([Path("onnxruntime-1.24.0-cp312-cp312-macosx_14_0_arm64.whl")]) == "14.0"
    assert bundle.minimum_macos([]) == "11.0"


def run_preinstall(tmp_path):
    script = tmp_path / "preinstall"
    script.write_text(pkg.preinstall_script(platform.machine()))
    script.chmod(0o755)
    return subprocess.run([str(script), "test.pkg", "/", str(tmp_path / "volume")], capture_output=True, text=True)


@pytest.mark.skipif(platform.system() != "Darwin", reason="Tests the native macOS Installer preflight")
def test_installer_refuses_to_replace_an_existing_user_or_homebrew_binary(tmp_path):
    executable = tmp_path / "volume/usr/local/bin/omd"
    executable.parent.mkdir(parents=True)
    executable.write_text("existing user executable")
    result = run_preinstall(tmp_path)
    assert result.returncode == 1
    assert "standalone app archive" in result.stderr
    assert executable.read_text() == "existing user executable"


@pytest.mark.skipif(platform.system() != "Darwin", reason="Tests the native macOS Installer preflight")
def test_installer_refuses_a_link_to_another_installation(tmp_path):
    executable = tmp_path / "volume/usr/local/bin/omd"
    executable.parent.mkdir(parents=True)
    executable.symlink_to("/opt/homebrew/bin/omd")
    result = run_preinstall(tmp_path)
    assert result.returncode == 1
    assert os.readlink(executable) == "/opt/homebrew/bin/omd"


@pytest.mark.parametrize("target", ["/Applications/OMD Engine.app/Contents/MacOS/omd", "../../../Applications/OMD Engine.app/Contents/MacOS/omd"])
@pytest.mark.skipif(platform.system() != "Darwin", reason="Tests the native macOS Installer preflight")
def test_installer_allows_updating_its_own_cli_link(tmp_path, target):
    executable = tmp_path / "volume/usr/local/bin/omd"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(target)
    assert run_preinstall(tmp_path).returncode == 0


@pytest.mark.skipif(platform.system() != "Darwin", reason="Tests the native macOS Installer preflight")
def test_installer_detects_a_link_cycle_without_modifying_it(tmp_path):
    binaries = tmp_path / "volume/usr/local/bin"
    binaries.mkdir(parents=True)
    (binaries / "omd").symlink_to("loop")
    (binaries / "loop").symlink_to("omd")
    assert run_preinstall(tmp_path).returncode == 1
    assert (binaries / "omd").is_symlink()


@pytest.mark.skipif(platform.system() != "Darwin", reason="Tests the native macOS Installer preflight")
def test_installer_refuses_a_conflicting_app(tmp_path):
    app = tmp_path / "volume/Applications/OMD Engine.app"
    app.mkdir(parents=True)
    assert run_preinstall(tmp_path).returncode == 1
    assert app.is_dir()


@pytest.mark.skipif(platform.system() != "Darwin", reason="Tests the native macOS Installer preflight")
def test_installer_rejects_an_unsupported_macos_before_installing(tmp_path):
    script = tmp_path / "preinstall"
    script.write_text(pkg.preinstall_script(platform.machine(), "99.0"))
    script.chmod(0o755)
    result = subprocess.run([str(script), "test.pkg", "/", str(tmp_path / "volume")], capture_output=True, text=True)
    assert result.returncode == 1
    assert "requires macOS 99.0" in result.stderr
    assert not (tmp_path / "volume").exists()


def test_installer_pins_app_location_and_identifier_for_safe_upgrades():
    policy = pkg.component_configuration()
    assert len(policy) == 1
    assert policy[0]["RootRelativeBundlePath"] == "Applications/OMD Engine.app"
    assert policy[0]["BundleIsRelocatable"] is False
    assert policy[0]["BundleHasStrictIdentifier"] is True
    assert policy[0]["BundleOverwriteAction"] == "upgrade"


def test_installer_minimum_macos_is_validated_before_shell_generation():
    with pytest.raises(pkg.BundleError):
        pkg.preinstall_script("arm64", "14.0; unsafe")


def test_reserved_publish_never_overwrites_a_racing_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "new").write_text("candidate")
    target = tmp_path / "target"
    target.mkdir()
    (target / "original").write_text("keep")
    with pytest.raises(bundle.BundleError):
        bundle._reserved_publish(source, target)
    assert (target / "original").read_text() == "keep"
    assert not (target / "new").exists()


def test_reserved_publish_supports_cross_volume_package_files(tmp_path):
    source = tmp_path / "source.pkg"
    source.write_bytes(b"compressed installer")
    target = tmp_path / "result.pkg"
    bundle._reserved_publish(source, target)
    assert target.read_bytes() == source.read_bytes()


def test_permission_normalization_uses_recorded_modes_in_posix_staging():
    import tempfile
    with tempfile.TemporaryDirectory(dir="/private/tmp" if platform.system() == "Darwin" else None) as temporary:
        root = Path(temporary)
        (root / "program").write_text("executable")
        (root / "data").write_text("source copied from exFAT")
        bundle.normalize_permissions(root, {"program": 0o755, "data": 0o644})
        assert (root / "program").stat().st_mode & 0o777 == 0o755
        assert (root / "data").stat().st_mode & 0o777 == 0o644


def test_app_output_rejects_a_filesystem_that_ignores_permission_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "chmod", lambda *args, **kwargs: None)
    with pytest.raises(bundle.BundleError, match="/private/tmp"):
        bundle.require_posix_output(tmp_path)


@pytest.mark.parametrize("relocatable,relocate_content,strict_id,bundle_path,accepted", [
    ("false", "", "org.omd-local.engine", "./Applications/OMD Engine.app", True),
    ("true", "", "org.omd-local.engine", "./Applications/OMD Engine.app", False),
    ("false", '<bundle id="org.omd-local.engine"/>', "org.omd-local.engine", "./Applications/OMD Engine.app", False),
    ("false", "", "another.app", "./Applications/OMD Engine.app", False),
    ("false", "", "org.omd-local.engine", "./Other/OMD Engine.app", False),
])
def test_ci_accepts_pkgbuild_empty_relocation_marker_but_rejects_relocatable_or_wrong_identity(
    tmp_path, relocatable, relocate_content, strict_id, bundle_path, accepted
):
    # Execute the actual workflow assertions against pkgbuild-shaped XML.
    # Apple's nonrelocatable packages contain an empty <relocate/> element.
    (tmp_path / "PackageInfo").write_text(
        f'<pkg-info relocatable="{relocatable}" identifier="org.omd-local.engine">'
        f'<bundle path="{bundle_path}" id="org.omd-local.engine"/>'
        f'<strict-identifier><bundle id="{strict_id}"/></strict-identifier>'
        f'<relocate>{relocate_content}</relocate></pkg-info>'
    )
    workflow = (BUILDER_PATH.parents[2] / ".github/workflows/macos-engine.yml").read_text()
    start = workflow.index('          info = ElementTree.parse(expanded / "PackageInfo").getroot()')
    end = workflow.index('          assert (expanded / "Payload/', start)
    assertions = textwrap.dedent(workflow[start:end])
    namespace = {"ElementTree": ElementTree, "expanded": tmp_path}
    if accepted:
        exec(compile(assertions, "macos-engine.yml", "exec"), namespace)
    else:
        with pytest.raises(AssertionError):
            exec(compile(assertions, "macos-engine.yml", "exec"), namespace)


def test_license_inventory_covers_exact_locked_wheels_and_runtime_components(tmp_path):
    manifest = json.loads((BUILDER_PATH.parent / "licenses/manifest.json").read_text())
    runtime = bundle.load_runtime_manifest()
    for architecture in ("arm64", "x86_64"):
        lock = json.loads((BUILDER_PATH.parent / f"wheels-{architecture}.lock.json").read_text())
        for wheel in lock["wheels"]:
            record = manifest["wheels"][wheel["filename"]]
            assert record["sha256"] == wheel["sha256"]
            assert record["embedded"] or record["supplemental"]
        resources = tmp_path / architecture
        resources.mkdir()
        inventory = bundle.write_license_materials(resources, runtime, architecture, [])
        notices = resources / "third-party-licenses"
        assert (notices / f"runtime/{architecture}/licenses/LICENSE.openssl-3.txt").is_file()
        assert (notices / f"runtime/{architecture}/PYTHON.json").is_file()
        assert inventory["release_blockers"] == []  # Diagnostic core omits cobble.
        assert json.loads((notices / "inventory.json").read_text()) == inventory


def license_fixture(tmp_path, monkeypatch):
    import shutil
    root = tmp_path / "packaging"
    shutil.copytree(BUILDER_PATH.parent / "licenses", root / "licenses",
                    ignore=shutil.ignore_patterns("._*"))
    monkeypatch.setattr(bundle, "HERE", root)
    resources = tmp_path / "Resources"
    resources.mkdir()
    return root, resources


@pytest.mark.parametrize("alteration", ["missing", "tampered", "symlink"])
def test_build_rejects_missing_or_changed_runtime_notices(tmp_path, monkeypatch, alteration):
    root, resources = license_fixture(tmp_path, monkeypatch)
    source = root / "licenses/runtime/arm64/licenses/LICENSE.openssl-3.txt"
    if alteration == "missing":
        source.unlink()
    elif alteration == "tampered":
        source.write_text("Not the reviewed upstream license")
    else:
        replacement = tmp_path / "external-license"
        source.rename(replacement)
        source.symlink_to(replacement)
    with pytest.raises(bundle.BundleError):
        runtime = json.loads((BUILDER_PATH.parent / "runtime-manifest.json").read_text())
        bundle.write_license_materials(resources, runtime, "arm64", [])
    assert not (resources / "third-party-licenses").exists()


def test_runtime_license_materials_cannot_silently_follow_runtime_updates(tmp_path):
    resources = tmp_path / "Resources"
    resources.mkdir()
    runtime = bundle.load_runtime_manifest()
    runtime["runtimes"]["arm64"]["sha256"] = "0" * 64
    with pytest.raises(bundle.BundleError, match="do not match"):
        bundle.write_license_materials(resources, runtime, "arm64", [])


def test_license_manifest_rejects_unreviewed_wheel_and_unsafe_notice_paths(tmp_path, monkeypatch):
    root, resources = license_fixture(tmp_path, monkeypatch)
    wheel, _, _ = wheel_fixture(tmp_path)
    runtime = json.loads((BUILDER_PATH.parent / "runtime-manifest.json").read_text())
    with pytest.raises(bundle.BundleError, match="license materials"):
        bundle.write_license_materials(resources, runtime, "arm64", [wheel])
    path = root / "licenses/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["runtimes"]["arm64"]["materials"].append("../outside-LICENSE")
    path.write_text(json.dumps(manifest))
    with pytest.raises(bundle.BundleError, match="Unsafe license"):
        bundle.write_license_materials(resources, runtime, "arm64", [])
    assert not (tmp_path / "outside-LICENSE").exists()


def test_embedded_and_supplemental_licenses_ship_with_original_notice_and_blockers(tmp_path, monkeypatch):
    root, resources = license_fixture(tmp_path, monkeypatch)
    wheel, _, _ = wheel_fixture(tmp_path, "cobble-0.1.4-py3-none-any.whl")
    embedded_path = "cobble-0.1.4.dist-info/licenses/NOTICE"
    original = b"Fixture upstream copyright notice\n"
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr(embedded_path, original)
    path = root / "licenses/manifest.json"
    manifest = json.loads(path.read_text())
    record = manifest["wheels"][wheel.name]
    record["sha256"] = bundle.digest(wheel)
    record["embedded"] = [{"path": embedded_path, "sha256": hashlib.sha256(original).hexdigest()}]
    path.write_text(json.dumps(manifest))
    runtime = json.loads((BUILDER_PATH.parent / "runtime-manifest.json").read_text())
    with pytest.raises(bundle.BundleError, match="Unresolved third-party license release blockers: cobble"):
        bundle.write_license_materials(resources, runtime, "arm64", [wheel], strict_release=True)
    assert not (resources / "third-party-licenses").exists()
    inventory = bundle.write_license_materials(resources, runtime, "arm64", [wheel])
    notices = resources / "third-party-licenses"
    assert (notices / "wheels" / wheel.name / embedded_path).read_bytes() == original
    assert b"2012-2015 Janne Vanhala" in (notices / "wheels/cobble-inflection-LICENSE").read_bytes()
    assert inventory["release_blockers"][0]["component"] == "cobble==0.1.4"
    # The reference BSD template must never be claimed as the original notice.
    assert "reference template" in inventory["release_blockers"][0]["reason"]

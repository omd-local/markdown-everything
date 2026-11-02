"""Packaging provenance stays stable without executing generated source code."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def packaging(monkeypatch):
    monkeypatch.delenv("OMD_BUILD_REVISION", raising=False)
    spec = spec_from_file_location("omd_packaging", Path(__file__).parents[1] / "setup.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_archive_preserves_revision_without_consulting_git(packaging, tmp_path, monkeypatch):
    revision = "a1" * 20
    packaging._write_build_info(tmp_path, revision)
    # Even a surrounding checkout must not replace source archive provenance.
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(packaging.subprocess, "run", lambda *a, **k: pytest.fail("git called"))
    assert packaging._build_revision(tmp_path) == revision


def test_checkout_revision_is_full_commit(packaging, tmp_path, monkeypatch):
    revision = "b2" * 20
    (tmp_path / ".git").write_text("gitdir: worktree")

    def git(args, **kwargs):
        assert args == ["git", "rev-parse", "HEAD"]
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(args, 0, stdout=revision + "\n")

    monkeypatch.setattr(packaging.subprocess, "run", git)
    assert packaging._build_revision(tmp_path) == revision


def test_unversioned_source_does_not_search_ancestor_git(packaging, tmp_path, monkeypatch):
    monkeypatch.setattr(packaging.subprocess, "run", lambda *a, **k: pytest.fail("git called"))
    assert packaging._build_revision(tmp_path) is None


def test_generated_file_is_read_as_data_not_executed(packaging, tmp_path, monkeypatch):
    package = tmp_path / "omd"
    package.mkdir()
    (package / "_build_info.py").write_text("raise RuntimeError('must not execute')\n")
    monkeypatch.setattr(packaging.subprocess, "run", lambda *a, **k: pytest.fail("git called"))
    assert packaging._build_revision(tmp_path) is None


@pytest.mark.parametrize("revision", ["", "abc123", "z" * 40, "a" * 40 + "\n"])
def test_packaging_override_rejects_ambiguous_identity(packaging, tmp_path, monkeypatch, revision):
    monkeypatch.setenv("OMD_BUILD_REVISION", revision)
    with pytest.raises(ValueError, match="full 40-character"):
        packaging._build_revision(tmp_path)


def test_packaging_override_accepts_full_commit(packaging, tmp_path, monkeypatch):
    monkeypatch.setenv("OMD_BUILD_REVISION", "AB" * 20)
    assert packaging._build_revision(tmp_path) == "ab" * 20


def test_rewriting_generated_archive_file_does_not_modify_hardlinked_source(packaging, tmp_path):
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    packaging._write_build_info(source, "a1" * 20)
    (archive / "omd").mkdir(parents=True)
    (archive / "omd" / "_build_info.py").hardlink_to(source / "omd" / "_build_info.py")
    packaging._write_build_info(archive, "b2" * 20)
    assert packaging._build_revision(source) == "a1" * 20
    assert packaging._build_revision(archive) == "b2" * 20

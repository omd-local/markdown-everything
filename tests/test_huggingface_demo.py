from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SPACE_DIR = REPO_ROOT / "demo" / "huggingface-space"


def load_upload_module():
    spec = importlib.util.spec_from_file_location(
        "omd_hf_upload_existing", SPACE_DIR / "upload_existing.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_space_image_runs_as_hugging_face_uid_1000():
    dockerfile = (SPACE_DIR / "Dockerfile").read_text(encoding="utf-8")

    assert "useradd -m -u 1000 user" in dockerfile
    assert "USER user" in dockerfile
    assert dockerfile.index("USER user") < dockerfile.index('CMD ["python", "app.py"]')


def test_space_requirements_exclude_disabled_media_runtime():
    requirements = (SPACE_DIR / "requirements.txt").read_text(encoding="utf-8")

    assert "faster-whisper" not in requirements
    assert "yt-dlp" not in requirements
    assert "markitdown[pdf,docx,pptx,xlsx,xls,outlook]==0.1.6" in requirements


def test_space_short_description_respects_hugging_face_limit():
    readme = (SPACE_DIR / "README.md").read_text(encoding="utf-8")
    description_line = next(
        line for line in readme.splitlines() if line.startswith("short_description:")
    )

    assert len(description_line.partition(":")[2].strip()) <= 60


def test_stage_only_builds_allowlisted_context_without_hugging_face_access(tmp_path):
    stage_dir = tmp_path / "stage"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    hf_marker = tmp_path / "hf-was-called"
    fake_hf = fake_bin / "hf"
    fake_hf.write_text(f"#!/bin/sh\ntouch '{hf_marker}'\nexit 97\n", encoding="utf-8")
    fake_hf.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["STAGE_DIR"] = str(stage_dir)
    env["KEEP_STAGE"] = "1"

    result = subprocess.run(
        [str(SPACE_DIR / "deploy.sh"), "--stage-only"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not hf_marker.exists()
    assert {path.name for path in stage_dir.iterdir()} == {
        "Dockerfile",
        "README.md",
        "app.py",
        "requirements.txt",
        "smoke.py",
        "src",
    }
    forbidden = {".git", ".omx", "__pycache__", ".DS_Store"}
    assert not any(path.name in forbidden or path.name.startswith("._") for path in stage_dir.rglob("*"))
    assert not any(path.suffix == ".pyc" for path in stage_dir.rglob("*"))


def test_stage_only_rejects_non_empty_custom_stage_directory(tmp_path):
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    existing = stage_dir / "do-not-upload.txt"
    existing.write_text("private", encoding="utf-8")
    env = os.environ.copy()
    env["STAGE_DIR"] = str(stage_dir)

    result = subprocess.run(
        [str(SPACE_DIR / "deploy.sh"), "--stage-only"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "must be empty" in result.stderr
    assert existing.read_text(encoding="utf-8") == "private"


def test_deploy_updates_existing_space_without_recreating_repo(tmp_path):
    stage_dir = tmp_path / "stage"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    hf_log = tmp_path / "hf.log"
    fake_hf = fake_bin / "hf"
    fake_hf.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{hf_log}'\nexit 0\n",
        encoding="utf-8",
    )
    fake_hf.chmod(0o755)
    python_log = tmp_path / "python.log"
    fake_python = fake_bin / "python"
    fake_python.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{python_log}'\nexit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["STAGE_DIR"] = str(stage_dir)
    env["PYTHON"] = str(fake_python)
    env["SPACE_ID"] = "omd-local/omd-public-demo"

    result = subprocess.run(
        [str(SPACE_DIR / "deploy.sh"), "--deploy"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = hf_log.read_text(encoding="utf-8")
    assert "spaces info omd-local/omd-public-demo" in calls
    assert "repos create" not in calls
    assert "upload omd-local/omd-public-demo" not in calls
    upload_call = python_log.read_text(encoding="utf-8")
    assert "upload_existing.py omd-local/omd-public-demo" in upload_call


def test_deploy_requires_explicit_space_id(tmp_path):
    stage_dir = tmp_path / "stage"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    hf_marker = tmp_path / "hf-was-called"
    fake_hf = fake_bin / "hf"
    fake_hf.write_text(f"#!/bin/sh\ntouch '{hf_marker}'\nexit 0\n", encoding="utf-8")
    fake_hf.chmod(0o755)
    env = os.environ.copy()
    env.pop("SPACE_ID", None)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["STAGE_DIR"] = str(stage_dir)

    result = subprocess.run(
        [str(SPACE_DIR / "deploy.sh"), "--deploy"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Set SPACE_ID" in result.stderr
    assert not hf_marker.exists()


def test_existing_space_upload_replaces_stale_remote_files(tmp_path, monkeypatch):
    upload = load_upload_module()
    captured = {}

    class FakeApi:
        def upload_folder(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(commit_url="https://huggingface.co/commit/example")

    monkeypatch.setattr(upload, "HfApi", FakeApi)

    upload.upload_existing_space("owner/demo", tmp_path)

    assert captured["delete_patterns"] == ["*"]

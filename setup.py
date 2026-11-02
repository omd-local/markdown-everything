from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.dist_info import dist_info as _dist_info
from setuptools.command.egg_info import egg_info as _egg_info
from setuptools.command.sdist import sdist as _sdist

try:
    from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
except ModuleNotFoundError:  # setuptools < 70.1 delegates to wheel.
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel


def _build_revision(root: Path) -> str | None:
    override = os.environ.get("OMD_BUILD_REVISION")
    if override is not None:
        if not re.fullmatch(r"[0-9a-fA-F]{40}", override):
            raise ValueError("OMD_BUILD_REVISION must be a full 40-character git commit")
        return override.lower()

    # A source archive must retain the identity of the commit it came from,
    # including when extracted inside an unrelated git checkout.
    generated = root / "omd" / "_build_info.py"
    if generated.is_file():
        match = re.search(
            r'^BUILD_REVISION = [\'\"]([0-9a-fA-F]{40})[\'\"]$',
            generated.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if match:
            return match.group(1).lower()

    if not (root / ".git").exists():
        return None
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return revision.lower() if re.fullmatch(r"[0-9a-fA-F]{40}", revision) else None


def _write_build_info(root: str | Path, revision: str | None) -> None:
    target = Path(root) / "omd" / "_build_info.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    # sdist may have hard-linked this file from a previous source archive.
    target.unlink(missing_ok=True)
    target.write_text(
        "# Generated during packaging; do not edit.\n"
        f"BUILD_REVISION = {revision!r}\n",
        encoding="utf-8",
    )


def _remove_appledouble_files(root: str | Path | None) -> None:
    if not root:
        return
    base = Path(root)
    if not base.exists():
        return
    for path in base.rglob("._*"):
        if path.is_file():
            path.unlink()


class egg_info(_egg_info):
    def run(self) -> None:
        super().run()
        _remove_appledouble_files(self.egg_info)


class build_py(_build_py):
    def run(self) -> None:
        super().run()
        _write_build_info(self.build_lib, _build_revision(Path(__file__).resolve().parent))
        _remove_appledouble_files(self.build_lib)


class sdist(_sdist):
    def make_release_tree(self, base_dir: str, files: list[str]) -> None:
        super().make_release_tree(base_dir, files)
        _write_build_info(base_dir, _build_revision(Path(__file__).resolve().parent))
        _remove_appledouble_files(base_dir)


class dist_info(_dist_info):
    def run(self) -> None:
        super().run()
        _remove_appledouble_files(self.dist_info_dir)


class bdist_wheel(_bdist_wheel):
    def write_wheelfile(self, wheelfile_base: str, generator: str = "bdist_wheel") -> None:
        super().write_wheelfile(wheelfile_base, generator)
        _remove_appledouble_files(self.bdist_dir)


if __name__ == "__main__":
    setup(
        cmdclass={
            "egg_info": egg_info,
            "build_py": build_py,
            "sdist": sdist,
            "dist_info": dist_info,
            "bdist_wheel": bdist_wheel,
        },
    )

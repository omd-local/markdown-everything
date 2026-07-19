from __future__ import annotations

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.dist_info import dist_info as _dist_info
from setuptools.command.egg_info import egg_info as _egg_info

try:
    from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
except ModuleNotFoundError:  # setuptools < 70.1 delegates to wheel.
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel


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
        _remove_appledouble_files(self.build_lib)


class dist_info(_dist_info):
    def run(self) -> None:
        super().run()
        _remove_appledouble_files(self.dist_info_dir)


class bdist_wheel(_bdist_wheel):
    def write_wheelfile(self, wheelfile_base: str, generator: str = "bdist_wheel") -> None:
        super().write_wheelfile(wheelfile_base, generator)
        _remove_appledouble_files(self.bdist_dir)


setup(
    cmdclass={
        "egg_info": egg_info,
        "build_py": build_py,
        "dist_info": dist_info,
        "bdist_wheel": bdist_wheel,
    },
)

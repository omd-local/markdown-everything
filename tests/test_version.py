import re
from pathlib import Path

from omd import __version__


def test_package_version_matches_project_metadata():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    project_section = pyproject.read_text(encoding="utf-8").split("[project]", 1)[1]
    match = re.search(r'^version = "([^"]+)"$', project_section, re.MULTILINE)

    assert match is not None
    assert __version__ == match.group(1)

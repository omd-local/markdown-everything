"""Keep the Home backend independently installable while the UI is deferred."""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from omd import __version__
from omd.ui_unavailable import main


ROOT = Path(__file__).resolve().parents[1]


def test_distribution_extras_do_not_install_deferred_ui_dependencies():
    # Read the simple dependency arrays without requiring tomllib on Python 3.10.
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    extras = metadata.split("[project.optional-dependencies]", 1)[1].split(
        "[project.scripts]", 1
    )[0]
    requirements = {
        name: ast.literal_eval(array)
        for name, array in re.findall(
            r'^(\w+) = (\[(?:[^"\]]|"[^"]*")*\])', extras, re.MULTILINE
        )
    }
    assert "ui" not in requirements
    assert {"markitdown", "reel", "all", "test", "audit"} <= requirements.keys()
    for values in requirements.values():
        assert not any(
            re.match(r"(gradio|python-multipart|starlette)\b", requirement)
            for requirement in values
        )
    for extra in ("markitdown", "all"):
        assert [req for req in requirements[extra] if req.startswith("pillow")] == [
            "pillow>=13.0.0,<14"
        ]
    assert 'dependencies = []' in metadata


def test_distribution_entrypoints_keep_cli_and_mcp_and_gate_browser_ui():
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    scripts = metadata.split("[project.scripts]", 1)[1].split("[project.urls]", 1)[0]
    assert dict(re.findall(r'^([\w-]+) = "([^"]+)"$', scripts, re.MULTILINE)) == {
        "omd": "omd.cli:main",
        "omd-mcp": "omd.mcp_server:main",
        "omd-ui": "omd.ui_unavailable:main",
    }


@pytest.mark.parametrize("argv", [[], ["--port", "7861", "--no-browser"]])
def test_browser_ui_diagnostic_explains_available_backend(capsys, argv):
    assert main(argv) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "core backend release" in output.err
    assert "OMD Home integration remain available" in output.err
    assert "security fixes" in output.err


def test_browser_ui_help_is_available(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    output = capsys.readouterr()
    assert "usage: omd-ui" in output.out
    assert "core backend release" in output.out
    assert output.err == ""


def test_direct_ui_main_cannot_build_browser_app(monkeypatch, capsys):
    from omd import ui

    def unexpected_build():
        pytest.fail("The core release must stop before building a browser app")

    monkeypatch.setattr(ui, "build_app", unexpected_build)
    assert ui.main(["--port", "7861"]) == 2
    assert f"browser UI is unavailable in OMD {__version__}" in capsys.readouterr().err


def test_browser_ui_diagnostic_needs_no_packages_or_network():
    # Disable site packages and reject network/UI imports before importing OMD.
    script = """
import builtins
import sys
sys.path.insert(0, sys.argv[1])
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in {'gradio', 'socket', 'urllib', 'requests', 'httpx'}:
        raise AssertionError('Unexpected UI or network import: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from omd.ui_unavailable import main
raise SystemExit(main([]))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "core backend release" in result.stderr
    assert "Traceback" not in result.stderr


def test_core_cli_works_without_site_packages():
    result = subprocess.run(
        [sys.executable, "-S", "-m", "omd", "capabilities", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    capabilities = json.loads(result.stdout)
    assert capabilities["package_version"] == __version__
    assert capabilities["protocol_version"] == 1


@pytest.mark.parametrize("target", ["ui", "install-ui"])
def test_make_ui_targets_only_run_diagnostic(target):
    # Dry-run verifies install-ui cannot start a pip resolver or install the UI.
    result = subprocess.run(
        ["make", "--dry-run", target, "PYTHON=python3"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "python3 -m omd.ui_unavailable"

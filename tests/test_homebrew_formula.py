import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMULA = ROOT / "packaging" / "homebrew" / "omd.rb"


def test_homebrew_formula_installs_ui_extra_only_when_source_includes_ui():
    formula = FORMULA.read_text(encoding="utf-8")

    command_link = "venv.pip_install_and_link buildpath"
    ui_install = (
        'venv.pip_install "#{buildpath}[ui]" '
        'if (buildpath/"omd/ui.py").exist?'
    )

    assert command_link in formula
    assert ui_install in formula
    assert formula.index(command_link) < formula.index(ui_install)
    assert 'venv.pip_install_and_link "#{buildpath}[ui]"' not in formula


def test_homebrew_formula_smokes_ui_only_when_it_was_installed():
    formula = FORMULA.read_text(encoding="utf-8")

    assert 'if (bin/"omd-ui").exist?' in formula
    assert "from omd.ui import build_app; assert build_app()" in formula


def test_homebrew_formula_uses_organisation_repository_urls():
    formula = FORMULA.read_text(encoding="utf-8")
    owners = re.findall(
        r"github\.com/([^/\s\"']+)/markdown-everything",
        formula,
    )

    assert owners
    assert set(owners) == {"omd-local"}


def test_homebrew_formula_pins_current_public_release_asset():
    formula = FORMULA.read_text(encoding="utf-8")

    assert 'version "0.3.0b2"' not in formula
    assert (
        'url "https://github.com/omd-local/markdown-everything/releases/'
        'download/v0.3.0b2/omd-0.3.0b2.tar.gz"'
    ) in formula
    checksum = re.search(r'^\s*sha256 "([0-9a-f]{64})"$', formula, re.MULTILINE)
    assert checksum
    assert "archive/refs/tags/v0.2.0.tar.gz" not in formula

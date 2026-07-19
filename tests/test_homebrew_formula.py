import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMULA = ROOT / "packaging" / "homebrew" / "omd.rb"


def test_homebrew_formula_resolves_only_default_desktop_dependencies():
    formula = FORMULA.read_text(encoding="utf-8")

    assert '"#{buildpath}[ui]"' in formula
    assert '"markitdown[docx,outlook,pdf,pptx,xls,xlsx]>=0.1.6,<0.2"' in formula
    assert '"--python=#{venv.root}/bin/python"' in formula
    assert '"install", "--no-compile", *runtime_packages' in formula
    assert '"#{buildpath}[all]"' not in formula
    assert 'runtime_packages << "mlx-whisper"' not in formula
    assert 'venv.pip_install "#{buildpath}[ui]"' not in formula
    assert 'venv.pip_install ["markitdown[all]>=0.1.6,<0.2"' not in formula


def test_homebrew_formula_preserves_extension_wheel_rpaths():
    formula = FORMULA.read_text(encoding="utf-8")

    assert re.search(r"^\s+preserve_rpath$", formula, re.MULTILINE)


def test_homebrew_formula_always_smokes_the_published_ui():
    formula = FORMULA.read_text(encoding="utf-8")

    assert "from omd.ui import build_app; assert build_app()" in formula
    assert 'if (bin/"omd-ui").exist?' not in formula


def test_homebrew_formula_closes_mcp_stdin_without_shell_redirection():
    formula = FORMULA.read_text(encoding="utf-8")

    assert 'pipe_output(bin/"omd-mcp", "", 0)' in formula
    assert 'shell_output("#{bin}/omd-mcp < /dev/null")' not in formula


def test_homebrew_formula_converts_pdf_and_asserts_extracted_text():
    formula = FORMULA.read_text(encoding="utf-8")

    assert '(testpath/"hi.pdf").binwrite(pdf)' in formula
    assert 'system bin/"omd", testpath/"hi.pdf", "-o", testpath/"pdf.md"' in formula
    assert 'assert_match "OMD PDF smoke", (testpath/"pdf.md").read' in formula


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

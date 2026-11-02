import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMULA = ROOT / "packaging" / "homebrew" / "omd.rb"


def test_homebrew_formula_resolves_only_core_document_dependencies():
    formula = FORMULA.read_text(encoding="utf-8")

    assert '"#{buildpath}[ui]"' not in formula
    assert '"markitdown[docx,outlook,pdf,pptx,xls,xlsx]>=0.1.6,<0.2"' in formula
    assert '"pillow>=13.0.0,<14"' in formula
    assert '"--python=#{venv.root}/bin/python"' in formula
    assert '"install", "--no-compile", *runtime_packages' in formula
    assert '"#{buildpath}[all]"' not in formula
    assert 'runtime_packages << "mlx-whisper"' not in formula
    assert 'venv.pip_install "#{buildpath}[ui]"' not in formula
    assert 'venv.pip_install ["markitdown[all]>=0.1.6,<0.2"' not in formula


def test_homebrew_formula_preserves_extension_wheel_rpaths():
    formula = FORMULA.read_text(encoding="utf-8")

    assert re.search(r"^\s+preserve_rpath$", formula, re.MULTILINE)


def test_homebrew_formula_checks_ui_is_deferred_without_gradio():
    formula = FORMULA.read_text(encoding="utf-8")

    assert "importlib.util.find_spec('gradio') is None" in formula
    assert "from PIL import __version__; assert __version__.startswith('13.')" in formula
    assert 'shell_output("#{bin}/omd-ui --help")' in formula
    assert 'shell_output("#{bin}/omd-ui 2>&1", 2)' in formula
    assert "from omd.ui import build_app" not in formula
    assert 'if (bin/"omd-ui").exist?' not in formula


def test_homebrew_formula_checks_home_contract_and_persistent_build_identity():
    formula = FORMULA.read_text(encoding="utf-8")

    assert "env -u OMD_BUILD_REVISION #{bin}/omd capabilities --json" in formula
    assert 'assert_equal version.to_s, capabilities.fetch("package_version")' in formula
    assert 'assert_equal 1, capabilities.fetch("protocol_version")' in formula
    assert 'capabilities.fetch("enrich_note").fetch("supported")' in formula
    assert 'assert_equal [1], capabilities.fetch("enrich_note").fetch("schema_versions")' in formula
    assert r'assert_match(/\A[0-9a-f]{40}\z/, capabilities.fetch("build_revision"))' in formula
    assert 'disclosure = capabilities.fetch("hosted_ai_disclosure")' in formula
    assert 'assert_equal 1, disclosure.fetch("schema_version")' in formula


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

    assert 'version "0.3.0b3"' not in formula
    assert (
        'url "https://github.com/omd-local/markdown-everything/releases/'
        'download/v0.3.0b4/omd-0.3.0b4.tar.gz"'
    ) in formula
    checksum = re.search(r'^\s*sha256 "([0-9a-f]{64})"$', formula, re.MULTILINE)
    assert checksum
    assert "archive/refs/tags/v0.2.0.tar.gz" not in formula

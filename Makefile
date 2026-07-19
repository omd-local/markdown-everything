# omd — Makefile shortcuts.
PYTHON ?= python3
VERSION ?= $(shell $(PYTHON) -c "import tomllib,sys; sys.stdout.write(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
TARBALL_URL = https://github.com/omd-local/markdown-everything/archive/refs/tags/v$(VERSION).tar.gz

.PHONY: help install install-dev install-ui install-audit ui test audit smoke clean uninstall release-tag release-sha

help:
	@echo "omd targets:"
	@echo "  make install        pip install -e .  (then \`omd\` is on PATH)"
	@echo "  make install-dev    install all conversion, test, and audit extras"
	@echo "  make install-ui     install just the Gradio UI extra"
	@echo "  make install-audit  install dependency audit tooling"
	@echo "  make ui             launch the browser UI (omd-ui)"
	@echo "  make smoke          run a quick OCR routing smoke test"
	@echo "  make test           pytest"
	@echo "  make audit          pip-audit dependency CVE scan"
	@echo "  make clean          remove build artifacts and __pycache__"
	@echo "  make uninstall      pip uninstall omd"
	@echo "  make release-tag    git tag v\$$(VERSION) and push (reads pyproject.toml)"
	@echo "  make release-sha    print sha256 of the release tarball (paste into omd.rb)"

install:
	$(PYTHON) -m pip install -e .

install-dev:
	$(PYTHON) -m pip install -e '.[all,test,audit]'

install-ui:
	$(PYTHON) -m pip install -e '.[ui]'

install-audit:
	$(PYTHON) -m pip install -e '.[audit]'

ui:
	$(PYTHON) -m omd.ui

test:
	$(PYTHON) -m pytest tests/ -q

audit:
	$(PYTHON) -m pip_audit

smoke:
	@echo "→ Routing dry-run via python -m omd.cli --help"
	$(PYTHON) -m omd.cli --help | head -20

clean:
	rm -rf build/ dist/ *.egg-info/
	find . -name "__pycache__" -type d -exec rm -rf {} +
	find . -name "*.pyc" -delete

uninstall:
	$(PYTHON) -m pip uninstall -y omd

release-tag:
	@echo "→ tagging v$(VERSION) and pushing"
	git tag v$(VERSION)
	git push origin main --tags

release-sha:
	@echo "→ sha256 of $(TARBALL_URL)"
	@curl -sL $(TARBALL_URL) | shasum -a 256 | awk '{print $$1}'

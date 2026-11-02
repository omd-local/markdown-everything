# omd — Makefile shortcuts.
PYTHON ?= python3
VERSION ?= $(shell $(PYTHON) -c "import tomllib,sys; sys.stdout.write(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
TARBALL_URL = https://github.com/omd-local/markdown-everything/releases/download/v$(VERSION)/omd-$(VERSION).tar.gz

.PHONY: help install install-dev install-ui install-audit ui test audit check-disclosures smoke clean uninstall release-tag release-sha

help:
	@echo "omd targets:"
	@echo "  make install        pip install -e .  (then \`omd\` is on PATH)"
	@echo "  make install-dev    install all conversion, test, and audit extras"
	@echo "  make install-ui     explain why the browser UI is deferred (fails)"
	@echo "  make install-audit  install dependency audit tooling"
	@echo "  make ui             explain why the browser UI is deferred (fails)"
	@echo "  make smoke          run a quick OCR routing smoke test"
	@echo "  make test           pytest"
	@echo "  make audit          pip-audit dependency CVE scan"
	@echo "  make check-disclosures  fail when a policy review is due within 14 days"
	@echo "  make clean          remove build artifacts and __pycache__"
	@echo "  make uninstall      pip uninstall omd"
	@echo "  make release-tag    git tag v\$$(VERSION) and push (reads pyproject.toml)"
	@echo "  make release-sha    print sha256 of the release tarball (paste into omd.rb)"

install:
	$(PYTHON) -m pip install -e .

install-dev:
	$(PYTHON) -m pip install -e '.[all,test,audit]'

install-ui:
	$(PYTHON) -m omd.ui_unavailable

install-audit:
	$(PYTHON) -m pip install -e '.[audit]'

ui:
	$(PYTHON) -m omd.ui_unavailable

test:
	$(PYTHON) -m pytest tests/ -q

audit:
	$(PYTHON) -m pip_audit

check-disclosures:
	$(PYTHON) -m omd.provider_disclosure

smoke:
	@echo "→ Routing dry-run via python -m omd.cli --help"
	$(PYTHON) -m omd.cli --help | head -20

clean:
	rm -rf build/ dist/ *.egg-info/
	find . -name "__pycache__" -type d -exec rm -rf {} +
	find . -name "*.pyc" -delete

uninstall:
	$(PYTHON) -m pip uninstall -y omd

release-tag: check-disclosures
	@echo "→ tagging v$(VERSION) and pushing"
	git tag v$(VERSION)
	git push origin refs/tags/v$(VERSION)

release-sha:
	@echo "→ sha256 of $(TARBALL_URL)"
	@asset=$$(mktemp); trap 'rm -f "$$asset"' EXIT; \
	  curl -fL "$(TARBALL_URL)" -o "$$asset" && shasum -a 256 "$$asset" | awk '{print $$1}'

PYTHON ?= python
UV ?= uv
HOST ?= 127.0.0.1
PORT ?= 8000
BRIDGE_DRY_RUN ?= true

.PHONY: help sync sync-dev run run-dry run-file test check compile health clean

help:
	@echo "ReaperGPT Make targets"
	@echo "  make sync       - Install runtime dependencies with uv"
	@echo "  make sync-dev   - Install dev dependencies with uv"
	@echo "  make run        - Run FastAPI dev server"
	@echo "  make run-dry    - Run FastAPI dev server in dry-run bridge mode"
	@echo "  make run-file   - Run FastAPI dev server in REAPER file-bridge mode"
	@echo "  make test       - Run pytest"
	@echo "  make compile    - Syntax check via compileall"
	@echo "  make check      - Run compile + tests"
	@echo "  make health     - Hit local /health endpoint"
	@echo "  make clean      - Remove Python cache files"

sync:
	$(UV) sync

sync-dev:
	$(UV) sync --dev

run:
	$(UV) run uvicorn companion.main:app --reload --host $(HOST) --port $(PORT)

run-dry:
	powershell -ExecutionPolicy Bypass -File scripts/run_dev.ps1

run-file:
	powershell -ExecutionPolicy Bypass -File scripts/run_dev_file.ps1

test:
	$(UV) run pytest -q

compile:
	$(UV) run $(PYTHON) -m compileall companion tests

check: compile test

health:
	$(UV) run $(PYTHON) -c "import urllib.request; print(urllib.request.urlopen('http://$(HOST):$(PORT)/health').read().decode())"

clean:
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	$(PYTHON) -c "import pathlib; [p.unlink() for p in pathlib.Path('.').rglob('*.pyc') if p.exists()]"

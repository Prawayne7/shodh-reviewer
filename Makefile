# One CPU-only, offline command on a fresh clone:  make eval
PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: setup test eval clean

# The stamp file makes `make eval` build the venv itself if it is missing,
# and rebuild it when requirements.txt changes.
$(VENV)/.stamp: requirements.txt
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --quiet -r requirements.txt
	touch $@

setup: $(VENV)/.stamp

test: setup
	$(BIN)/python -X utf8 -m pytest -q tests

# Offline tests, then the frozen evaluation (fake model client only, no network).
eval: setup
	$(BIN)/python -X utf8 verify_local.py

clean:
	rm -rf .pytest_cache .cache $(VENV)

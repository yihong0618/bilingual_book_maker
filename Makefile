SHELL := /bin/bash

fmt:
	@echo "Running formatter ..."
	venv/bin/black .

.PHONY:tests
tests:
	@echo "Running tests ..."
	venv/bin/pytest tests/test_integration.py

serve-docs:
	mkdocs serve

.PHONY: all lint type test cov

all: lint type cov

lint:
	ruff check src/

type:
	mypy src/

test:
	python -m pytest tests/ -x --tb=short

cov:
	python -m pytest tests/ --cov=src/selfheal --cov-report=term-missing --cov-branch -q

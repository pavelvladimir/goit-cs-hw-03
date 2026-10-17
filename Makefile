.DEFAULT_GOAL := help
.PHONY: help init sync demo benchmark test-concurrency lint lint-python lint-shell lint-docs format typecheck test test-unit test-integration test-postgresql test-mongodb test-all check-unit check coverage coverage-report audit

help: ## Показати доступні команди без побічних дій
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

init: sync ## Створити відсутню конфігурацію й незалежні паролі
	uv run --locked python -m scripts.init_local

sync: ## Встановити залежності Python 3.14 з lockfile
	uv sync --locked

demo: ## Показати 14 SQL-запитів і MongoDB CRUD у тимчасових базах
	uv run --locked python -m scripts.demo

benchmark: ## Виміряти два SQL-запити до й після індексів; зберегти плани
	uv run --locked python -m scripts.benchmark

test-concurrency: ## Перевірити конфлікти запису двома незалежними клієнтами
	uv run --locked pytest --integration tests/integration/test_concurrency.py

lint: lint-python lint-shell lint-docs ## Перевірити Python, shell і документацію

lint-python: ## Перевірити Python через Ruff і форматування
	uv run --locked ruff check --output-format concise .
	uv run --locked ruff format --check .

lint-shell: ## Перевірити синтаксис Bash і ShellCheck
	bash -n docker/init-postgres.sh
	bash -n docker/archive-postgres.sh
	bash -n docker/archive-runner.sh
	uv run --locked shellcheck --format=gcc docker/*.sh

lint-docs: ## Перевірити Markdown через PyMarkdown
	uv run --locked pymarkdown --config pyproject.toml scan --recurse --respect-gitignore --alternate-extensions .md,.markdown --exclude .git .

format: ## Форматувати Python через Ruff
	uv run --locked ruff format .

typecheck: ## Перевірити типи через strict mypy
	uv run --locked mypy --show-column-numbers --no-pretty --no-color-output

test: test-unit ## Запустити unit-тести без Docker

test-unit: ## Перевірити модулі й конфігурацію CLI без Docker
	uv run --locked pytest tests/unit

test-integration: ## Перевірити ізольовані Percona контейнери й прибрати їх
	uv run --locked pytest --integration tests/integration

test-postgresql: ## Перевірити лише PostgreSQL в ізольованому контейнері
	uv run --locked pytest --integration -m postgresql tests/integration

test-mongodb: ## Перевірити лише MongoDB в ізольованому контейнері
	uv run --locked pytest --integration -m mongodb tests/integration

test-all: ## Запустити обидва набори з branch coverage не менше 90%
	uv run --locked coverage erase
	uv run --locked coverage run -m pytest --integration
	$(MAKE) coverage-report

check-unit: lint typecheck test-unit ## Виконати перевірки без Docker

check: lint typecheck test-all ## Виконати всі перевірки та coverage

coverage: test-all ## Сумісний псевдонім для test-all

coverage-report: ## Створити термінальний, HTML і XML звіти coverage
	uv run --locked coverage report
	uv run --locked coverage html
	uv run --locked coverage xml

audit: ## Перевірити зафіксовані залежності; потрібен доступ до мережі
	@mkdir -p artifacts
	uv export --locked --all-groups --format requirements-txt --output-file artifacts/audit-requirements.txt --quiet
	uv run --locked pip-audit --cache-dir artifacts/audit-cache --require-hashes --disable-pip --progress-spinner off -r artifacts/audit-requirements.txt

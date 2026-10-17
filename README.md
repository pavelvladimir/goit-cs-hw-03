# GoIT CS — домашнє завдання 3

Реляційна модель завдань у PostgreSQL і консольний CRUD котів у MongoDB.

Проєкт використовує Python 3.14, uv, Ruff, strict mypy, pytest і branch coverage
з порогом 90%. Бази працюють у контейнерах Percona з автентифікацією:
PostgreSQL 17.11 і MongoDB 8.0.32-14. Теги та multiarch digests
зафіксовані в `compose.yaml`.

## Відповідність завданню

| Вимога | Реалізація | Перевірка |
| --- | --- | --- |
| users, status, tasks; UNIQUE, FK, CASCADE | `create_tables.py` | Інтеграційні тести схеми |
| Випадкові дані Faker, три статуси | `seed.py` | Дані, повторний запуск, rollback |
| Усі 14 SQL-запитів | `queries.sql` | Результат кожного запиту |
| CRUD котів через PyMongo | `main.py` | CRUD, меню, унікальність, валідація |
| Обробка помилок | `conf/`, `main.py` | CLI exit codes, stderr, секрети |

Деталі перевірок: [PostgreSQL](docs/postgresql.md), [MongoDB](docs/mongodb.md),
[результати верифікації](docs/verification.md).

## Швидкий старт

Потрібні [uv](https://docs.astral.sh/uv/getting-started/installation/), Make,
Docker із Compose **2.24.4+**, вільне місце для образів і даних та сумісний Linux kernel
Docker engine. Python 3.14 встановлюється через uv за потреби.
Команди нижче виконуйте з кореня проєкту в Bash на macOS або Linux.
Python працює на хості через uv, сервери БД — у Docker.

Для відтворюваного огляду обох завдань виконайте `make demo`.
Команда сама створює тимчасові бази, показує результати всіх 14 SQL-запитів і MongoDB CRUD,
а потім прибирає свої контейнери, volumes і секрети. `make init` для demo не потрібний.
Очікувані результати, вимірювання індексів і перевірки конкурентних записів —
у [практичних сценаріях](docs/showcase.md).

Для роботи зі збереженням власних даних спочатку виконайте:

```bash
make init
```

Команда встановлює залежності, створює відсутні `.env` і чотири незалежні
випадкові паролі в `secrets/`; повторний запуск зберігає наявні файли.
Перед створенням файлів перевіряються параметри підключення обох БД та шляхи секретів.
Паролі не передаються в аргументах процесів або URI.

### PostgreSQL — завдання 1

```bash
docker compose up -d --wait postgres
uv run --locked python create_tables.py
uv run --locked python seed.py
make test-postgresql
```

За замовчуванням PostgreSQL доступний на `127.0.0.1:5433`. Скрипти повідомлять про створення таблиць
і додавання 10 користувачів та 30 завдань. MongoDB для цього сценарію не потрібна.

### MongoDB — завдання 2

```bash
docker compose up -d --wait mongodb
uv run --locked python main.py --seed
uv run --locked python main.py
make test-mongodb
```

За замовчуванням MongoDB доступна на `127.0.0.1:27018`. Seed повідомить «Приклади котів додано.».
PostgreSQL для цього сценарію не потрібний. Обидва сервери можна запустити разом
через `docker compose up -d --wait`.

`main.py` показує меню з операціями 1–7; `0` завершує роботу.
SQL-запити та приклади результатів описані в [PostgreSQL](docs/postgresql.md).
Зупинити робочі сервери зі збереженням даних: `docker compose stop`.
Конфігурація, повторний запуск і backup/restore — у [настанові з експлуатації](docs/operations.md).

## Перевірки

```bash
make                  # довідка без побічних дій
make check-unit       # Ruff, ShellCheck, mypy, Markdown, unit-тести; Docker не потрібний
make test-integration
make test-concurrency # два незалежні клієнти: UNIQUE, rollback, $addToSet
make benchmark        # 30 / 100 000 завдань, плани й медіани до/після індексів
make audit            # аудит залежностей; потрібна мережа
make check            # усі перевірки та coverage >= 90%
```

Інтеграційні тести самі запускають Percona в окремих унікальних Compose-проєктах з тимчасовими
секретами й динамічними локальними портами, а потім прибирають власні ресурси.
Вони не використовують робочий `.env` або робочі volumes.
Поточний стан перевірок, включно з обмеженнями середовища, наведено у
[звіті](docs/verification.md).

## Документація

- [PostgreSQL: схема, seed і 14 запитів](docs/postgresql.md)
- [MongoDB: документи, CRUD і меню](docs/mongodb.md)
- [Практичні сценарії: demo, плани SQL і конкурентні записи](docs/showcase.md)
- [Розробка: команди, тести та редактор](docs/development.md)
- [Експлуатація: конфігурація, backup/restore й діагностика](docs/operations.md)
- [Верифікація: фактичні результати](docs/verification.md)

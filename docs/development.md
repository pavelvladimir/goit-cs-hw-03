# Розробка

[README](../README.md) · [Розробка](development.md) · [Експлуатація](operations.md) · [Верифікація](verification.md)

## Середовище та команди

Єдина підтримувана версія — Python 3.14. Вона узгоджена в `.python-version`,
`pyproject.toml`, Ruff і mypy. `uv.lock` фіксує залежності; prerelease заборонені.
Після зміни залежностей виконайте `uv lock`, перегляньте lockfile і перевірте проєкт.

| Команда | Призначення |
| --- | --- |
| `make` / `make help` | Довідка без запуску сервісів |
| `make sync` | `uv sync --locked` |
| `make init` | Синхронізація й безпечне створення конфігурації |
| `make demo` | Відтворювані 14 SQL-запитів і MongoDB CRUD у тимчасових базах |
| `make benchmark` | Плани до/після індексів на 30 і 100 000 завданнях |
| `make test-concurrency` | UNIQUE, rollback і `$addToSet` двома клієнтами |
| `make format` | Один прохід форматування Ruff |
| `make lint` | lint-python, lint-shell, lint-docs |
| `make lint-python` | Ruff E, F, I, UP, B і перевірка форматування |
| `make lint-shell` | Bash syntax і зафіксований ShellCheck |
| `make typecheck` | strict mypy |
| `make lint-docs` | PyMarkdown рекурсивно для `.md` і `.markdown` |
| `make test` / `make test-unit` | Unit-тести без Docker |
| `make check-unit` | lint, typecheck, test-unit |
| `make test-postgresql` | Лише PostgreSQL: SQL, CLI, timeout, SIGINT, backup/restore |
| `make test-mongodb` | Лише MongoDB: CRUD, CLI, відновлення після збою, backup/restore |
| `make test-integration` | Реальні ізольовані Percona контейнери |
| `make test-all` / `make coverage` | Обидві suites і branch coverage ≥ 90% |
| `make coverage-report` | Звіт у терміналі, HTML і XML з наявних даних |
| `make check` | lint, typecheck, test-all; unit-тести виконуються один раз |
| `make audit` | pip-audit для всіх залежностей lockfile; потрібна мережа |

`htmlcov/index.html` і `coverage.xml` генеруються локально та ігноруються Git.
Coverage охоплює три entrypoint-модулі, `conf/` і `scripts/`.
Виключені лише стандартні `if __name__ == "__main__"` guards.

`make audit` експортує всі групи з `uv.lock` разом із hashes і запускає
`pip-audit --require-hashes --disable-pip`, без повторного розв’язання залежностей.
Експорт зберігається в `artifacts/audit-requirements.txt`, HTTP cache —
в `artifacts/audit-cache/`; глобальний pip cache не використовується. Аудит окремий
від `make check`: недоступність сервісу аудиту не приховується й не означає успіху.

## Організація коду

Кореневі `create_tables.py`, `seed.py`, `queries.sql` і `main.py` залишаються
точками входу відповідно до завдання. `conf/settings.py` перевіряє конфігурацію,
`conf/db.py` створює з'єднання, `conf/logging.py` забезпечує безпечні повідомлення.
Локальна ініціалізація й перевірені backup bundles — у `scripts/`;
bootstrap, ротація й native archive wrappers — у `docker/`.
Короткі health checks з автентифікацією записані безпосередньо в `compose.yaml`.
`docker/archive-runner.sh` обмежує час і скасовує окрему групу archive-процесів
усередині контейнера, включно з дочірніми утилітами й тимчасовими реквізитами.
`scripts/stack.py` надає спільний lifecycle для тестів, demo й benchmark.
До запуску Docker помилка запису або Ctrl+C спричиняє прибирання власних
частково створених файлів. Наявні файли в каталозі не перезаписуються.
Demo/benchmark також прибирають порожній тимчасовий каталог.
Тонкий адаптер `tests/integration/stack.py` реєструє секрети у pytest reporter;
прикладні скрипти не імпортують тести або pytest.

## Тести

```bash
uv run --locked pytest
uv run --locked pytest --integration -m postgresql tests/integration
uv run --locked pytest --integration -m mongodb tests/integration
uv run --locked pytest --integration tests/integration
uv run --locked coverage run -m pytest --integration
```

Без `--integration` збираються лише unit-тести.
Інтеграційними вважаються лише файли всередині справжнього `tests/integration`;
назва `integration` у шляху до checkout або всередині unit-набору не змінює класифікацію.
Прямий запуск інтеграційного файла без прапорця безпечно пропускає його тести.
Частина тестів має формат unittest; їх також запускає pytest.
Параметризовані сценарії використовують fixtures pytest.
Для інтеграції потрібні Docker Compose 2.24.4+ і доступ до registry
під час першого завантаження образів.

`scripts/stack.py` створює унікальне ім'я `hw03-test-*`, тимчасовий `.env`,
чотири секрети й окремі volumes. `compose.test.yaml` через `!override` замінює порти
вільними портами на loopback, вибраними runner перед startup. Вони залишаються
сталими після restart; зайнятий конкурентом порт спричиняє помилку startup.
Підключення отримують порт саме свого Compose-проєкту;
робочі змінні PG/MONGO/COMPOSE і робочий `.env` не є джерелом тестових реквізитів.

Session fixtures `postgres_stack` і `mongo_stack` незалежні. Маркери `postgresql`
і `mongodb` вибирають тільки потрібний сервер; CLI та lifecycle сценарії також
параметризовані за БД. Повний набір додатково перевіряє cleanup спільного stack.
`make check` залишається обов'язковою перевіркою обох серверів із coverage ≥ 90%.

Тести перевіряють SQL, CRUD, реальні CLI-процеси, rollback після часткового seed,
автентифікацію, права ролей, ротацію паролів і збереження даних після restart.
Сценарії стійкості утримують справжній SQL lock, переривають seed сигналом SIGINT
після вставки користувачів, зупиняють MongoDB під час роботи меню й перевіряють
відновлення того самого процесу. Архіви створюються штатними утилітами й
відновлюються в іншому Compose-проєкті з новими секретами; перевіряються дані,
обмеження, індекси та права ролі. Команди описані в [operations](operations.md).
Окремі тести утримують SQL lock під час `pg_dump`: після timeout або SIGINT
перевіряється відсутність його сесії, навіть поки lock залишається активним.
На обох Percona образах перевіряється примусове завершення дочірніх процесів,
які ігнорують TERM, видалення реквізитів і скасування до запуску.

Окремий дочірній pytest навмисно падає; батьківський тест перевіряє cleanup
контейнерів, мережі та всіх фактично змонтованих volumes, включно з anonymous.
При збої fixture показує очищені від секретів логи й виконує `down --volumes`
лише для свого проєкту. Після успішного cleanup видаляються й тимчасові секрети.
Якщо cleanup не вдався, env і секрети залишаються для повторної спроби.
`tests/reporting.py` очищає traceback, captured stdout/stderr/logs, sections і properties
перед передаванням у terminal/JUnit reporters у setup/call/teardown. Реальні дочірні
pytest-процеси перевіряють raw, URL, JSON, Python repr і libpq escaped паролі.
Properties перед експортом перетворюються на очищені рядки, включно з bytes,
bytearray, об'єктами та вкладеними контейнерами. Непридатне текстове представлення
замінюється на `[UNPRINTABLE]`; числові й логічні значення зберігають свій зміст.
Для skipped-тестів зберігається потрібна pytest структура й очищається причина пропуску.
Реєструються тільки власні тимчасові секрети, включно з ротованими; це не універсальний
пошук довільних секретів. Для subprocess pytest plugin підключається явно через
`-p tests.reporting`, якщо його тести розташовані поза `tests/`.

Додаткові перевірки створюють реальний конфлікт порту до startup і після запуску
першого сервера, перевіряють cleanup усіх mounts, шляхи з апострофом/Unicode,
завершення завислого дочірнього процесу та окремо SQL statement/lock timeout.
Примусове завершення процесу/машини може завадити cleanup;
відновлення описано в [operations](operations.md).

Конкурентні сценарії використовують два незалежні з'єднання й `threading.Barrier`
із timeout. PostgreSQL перевіряє один успішний запис, один `UniqueViolation` та rollback
попереднього запису програвшої транзакції. MongoDB перевіряє `DuplicateKeyError`,
відсутність повторів однакової характеристики й збереження двох різних характеристик.
Порядок переможців не фіксується; `sleep` для синхронізації не використовується.
Демо перевіряється на повторюваний вивід, benchmark — на однакові результати запитів
до/після індексів і справжні JSON-плани. Час і конкретний вибір planner не є assertions.
Деталі запуску та інтерпретації: [практичні сценарії](showcase.md).

## Редактор

`.editorconfig` задає UTF-8, LF, 4 пробіли для Python, 2 для YAML/JSON і tab для Make.
Ruff використовує 88 символів, Markdown — 100. `.gitattributes` нормалізує текст у LF.
VS Code використовує `.venv/bin/python`, Ruff із цього середовища та Pylance.
Рекомендовані Mypy Type Checker і ShellCheck мають явні шляхи до `.venv/bin/mypy`
та `.venv/bin/shellcheck`, щоб використовувати версії з `uv.lock`.
Відкрийте корінь проєкту як workspace, виконайте `make sync` і встановіть потрібні
розширення зі списку Workspace Recommendations у VS Code. Сам список їх не встановлює.
Шляхи `.venv/bin/` відповідають середовищу macOS/Linux.
Mypy читає `pyproject.toml`, включно зі strict rules
і переліком `files` через reporting scope `custom`; обидва інструменти працюють
із кореня workspace. Docker DX допомагає редагувати Compose, а Container Tools
надає перегляд контейнерів, образів, volumes і мереж.
GitLens, Better Comments і Todo Tree рекомендовані для роботи з історією Git,
позначками в коментарях і списком TODO.
Під час збереження Python застосовуються Ruff fixAll, organize imports і formatter.
cSpell перевіряє англійську й українську; pytest discovery охоплює unit-тести.
Make tasks запускаються як process із cwd кореня проєкту; `make check` — default test task.
Tasks містять окремі PostgreSQL/MongoDB suites, coverage і coverage-report.
Ruff, mypy, ShellCheck та PyMarkdown передають клікабельні помилки у Problems.
Markdown перевіряйте task `make lint-docs`: це той самий PyMarkdown із тією самою
конфігурацією `pyproject.toml`, що й у CLI, без окремих правил розширення редактора.
Скануються всі вкладені `.md`/`.markdown`; `.git/` і шляхи з `.gitignore` виключені,
зокрема `.venv/`, artifacts, coverage та dist. Автовизначення indentation вимкнено;
налаштування VS Code узгоджені з `.editorconfig`.
Рекомендовані розширення й Make tasks збережено в `.vscode/`.

Перед review запустіть `make check`. Якщо середовище блокує інтеграцію, зафіксуйте
точну причину у звіті; успішні unit-тести не означають успішного повного прогону.

## Оновлення залежностей та образів

1. Для окремої Python-залежності виконайте `uv lock --upgrade-package NAME`.
   Для узгодженого оновлення всіх залежностей — `uv lock --upgrade`.
   Перегляньте зміни `uv.lock`, release notes та сумісність із Python 3.14;
   major-версії потребують перегляду меж у `pyproject.toml`.
2. Виконайте `uv sync --locked`, `make check-unit`, `make audit`, потім `make check`.
   Запишіть результати й відбиток lockfile у [verification](verification.md).
3. Для контейнерів виберіть stable release в офіційній документації
   [Percona PostgreSQL](https://docs.percona.com/postgresql/17/docker.html) або
   [Percona MongoDB](https://docs.percona.com/percona-server-for-mongodb/8.0/install/docker.html).
   Перевірте `linux/arm64` і `linux/amd64` через
   `docker buildx imagetools inspect IMAGE:TAG` та release notes, зокрема вимоги kernel.
4. Оновіть у `compose.yaml` **і тег, і digest manifest list**; узгодьте очікувану
   версію в `tests/integration/test_lifecycle.py`. Виконайте `docker compose pull`.
   Після `make check` перевірте окремий backup/restore перед оновленням робочих даних.
   Зміна major-версії PostgreSQL потребує міграції, а не підключення старого volume.
5. Збережіть попередні image references і перевірені архіви до завершення перевірок.
   Для відкату міграції відновлюйте архів у новий сумісний volume; старі дані
   не видаляйте автоматично. Запишіть фактичні версії, платформу, команди й результати.

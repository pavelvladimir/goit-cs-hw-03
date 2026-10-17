# Верифікація

[README](../README.md) · [Розробка](development.md) · [Експлуатація](operations.md)

## Стан на 2026-10-17

Останній повний `make check` перевірив поточний прикладний код, усі тести,
SQL, Docker scripts і Compose-конфігурацію після виправлення перевірки зарезервованих
імен PostgreSQL та обробки невалідного UTF-8 в env-файлі archive CLI.
SHA-256 порівняння підтверджує незмінність перевіреного snapshot.
Документацію додатково перевірено після оновлення цього звіту.

Середовище повного прогону: macOS arm64, Python 3.14.6, uv 0.11.6,
Docker Desktop 4.95.0, Docker Engine 29.8.2, kernel `7.0.14-linuxkit`.
Використано точні образи з `compose.yaml`: Percona PostgreSQL 17.11
та Percona Server for MongoDB 8.0.32-14, зафіксовані тегами й multiarch digests.

| Перевірка | Результат |
| --- | --- |
| `make check` | 251 passed, 135,27 s; exit code 0 |
| Склад повного прогону | 194 unit + 57 integration |
| Ruff, ShellCheck, strict mypy, PyMarkdown | Успішно |
| Coverage з урахуванням гілок | 96,91%; 910 statements, 224 branches; поріг 90% |
| Окремий unit regression | 104 passed, 17,05 s; configuration, init, archive |
| Попередні два `make demo` | Exit code 0, текст побайтово однаковий |
| Попередній benchmark CLI smoke check | 30 tasks, 3 повтори; exit code 0, SELECT незмінні |
| Попередній `make benchmark` | 30 / 100 000 tasks, 5 повторів; цього разу не повторювався |

Окремі demo та benchmark CLI цього разу не повторювалися; їхні попередні результати
залишено як історію. Інтеграційні тести demo й benchmark входять до поточного `make check`.
Перевірка UTF-8 запускає справжній дочірній CLI; його виконання не входить до coverage
батьківського процесу pytest.

## Що підтвердили перевірки

- Init: невалідні імена, хости, порти, ліміти часу та plaintext passwords відхиляються
  до створення `.env` чи секретів. Невалідне перевизначення середовища не змінює
  наявну конфігурацію й не відновлює відсутній секрет.
- Зарезервовані імена: `public`/`none` відхиляються для `PG_USER`, а
  `template0`/`template1` — для `PG_DB`, до створення конфігурації. Перевірено межі
  цих правил для інших полів; Bash bootstrap і rotation відхиляють заборонені ролі
  до читання секретів та виклику psql.
- Archive CLI: невалідний UTF-8 повертає 1 без traceback чи вмісту env у діагностиці.
  Для backup/restore обох БД перевірено, що Docker не запускається, новий архів
  не створюється, а наявний файл залишається незмінним.
- PostgreSQL: схема, усі 14 SQL-запитів, CLI, права ролі, автентифікація, повторний seed,
  rollback, SQL statement/lock timeout і SIGINT без часткових вставок.
- MongoDB: CRUD, меню, унікальність, валідація, права ролі та відновлення того самого
  CLI-процесу після справжнього stop/start сервера.
- Обидві БД: збереження даних після restart, ротація паролів, backup/restore між
  незалежними проєктами з різними секретами. Пошкоджений/чужий bundle та непорожня
  ціль відхиляються; наявний backup не перезаписується.
- Archive: заблокований `pg_dump` завершується після timeout та SIGINT, поки тест
  ще утримує блокування таблиці. На обох Percona образах перевірено завершення
  нащадків, які ігнорують TERM, прибирання тимчасового каталогу з credentials
  і скасування операції до її запуску.
- Формат bundle: ZIP entries з компресією, відмінною від `ZIP_STORED`, відхиляються
  до читання вмісту та звернення до БД. Перевірено звичайні й пошкоджені DEFLATE entries
  окремо для manifest та archive; CLI повертає 1 без traceback.
- Діагностика: demo/benchmark зберігають очищену причину помилки Compose з кодом
  `OPERATION`; довільні винятки не розкривають внутрішніх даних або паролів.
- Lifecycle: прибирання контейнерів, мереж, named та anonymous volumes після помилки,
  конфлікту порту, часткового startup й навмисного падіння дочірнього pytest.
  Частковий запис секретів/env, помилка chmod і Ctrl+C до startup прибирають власні
  файли; наявні сторонні файли зберігаються. Demo/benchmark прибирають також каталог.
  Якщо Docker cleanup не вдався, конфігурація й секрети зберігаються для відновлення.
- Pytest: очищення зареєстрованих секретів у terminal і JUnit, включно з properties,
  escaped values, `--showlocals` та skipped reports; коректний відбір integration.
- Практичні сценарії: повторюваний demo-вивід, реальні конкурентні записи двома клієнтами,
  справжні JSON-плани й незмінність результатів SELECT до/після додавання індексів.

У попередньому benchmark на 100 000 завданнях індекс `(user_id, id)` зменшив роботу запиту 1.
Індекс `(status_id, id)` у запиті 2 не був обраний; покращення для нього не доведено.
Плани, вимірювання й обмеження наведено у [практичних сценаріях](showcase.md).
Пороги швидкодії та конкретний вибір planner не є умовами успішності тестів.

Після поточного повного прогону не залишилося контейнерів, volumes
або мереж `hw03-test-*`, `hw03-demo-*`, `hw03-benchmark-*`.
Окремо перевірено, що раніше запущений сторонній сервіс залишився `running / healthy`.
Це результат перевірки на момент завершення прогону, а не моніторинг поточного Docker.

## Локальні докази

| Артефакт | Зміст |
| --- | --- |
| `artifacts/check-fix-review-1-3-2026-10-17.log` | Поточний `make check` і coverage |
| `artifacts/fix-review-1-3-unit-2026-10-17.log` | Поточний unit regression |
| `artifacts/demo-fix-followup-1-3-4-1-2026-10-17.log` | Попередній перший demo |
| `artifacts/demo-fix-followup-1-3-4-2-2026-10-17.log` | Попередній другий demo |
| `artifacts/benchmark-fix-followup-1-3-4-2026-10-17.log` | Попередній benchmark CLI smoke check |
| `artifacts/benchmarks/20261017T074659861018Z/results.json` | Плани попереднього smoke check |
| `artifacts/benchmark-showcase-final-2026-10-17.log` | Попереднє вимірювання benchmark |
| `artifacts/benchmarks/20261017T001858614098Z/results.json` | Усі плани й метадані |
| `artifacts/cleanup-fix-review-1-3-2026-10-17.json` | Поточна перевірка залишених ресурсів |
| `artifacts/source-manifest-fix-review-1-3-2026-10-17.json` | 62 файли поточного прогону |

SHA-256 однакового demo-виводу:
`57acb93d68fcbdd519806a5f03e93692bb22044ff8de3d03dc57af6e86f09d8a`.
HTML coverage зберігається у `htmlcov/index.html`, XML — у `coverage.xml`.
Ці звіти й `artifacts/` генеруються локально та ігноруються Git: у свіжому checkout
їх немає. Наведені шляхи ідентифікують докази цього запуску, а не файли постачання.

Для відтворення з кореня проєкту, із запущеним сумісним Docker:

```bash
make check
make demo
make benchmark
```

`make init` для цих ізольованих сценаріїв не потрібний. `make check` оновлює coverage,
benchmark створює новий каталог із планами; логи термінала збережіть окремо, якщо потрібні.
`make audit` — окрема мережева перевірка залежностей і не входить до `make check`.
Результат аудиту залежностей цим звітом не засвідчується.

## Перевірка документації та редактора

Під час попереднього review README та всі шість документів звірено з кодом,
Makefile, Compose, налаштуваннями VS Code і збереженими результатами.
Уточнено межі `make init`, валідацію MongoDB,
створення ObjectId, виконання SQL зі зміною даних та аварійне прибирання всіх трьох
типів тимчасових проєктів. Інструкції щодо kernel звірено з upstream;
попередній блокер `7.0.12-linuxkit` усунуто оновленням Docker до сумісного kernel.

Поточні рекомендації VS Code містять 14 унікальних extension IDs, включно з
GitLens, Better Comments і Todo Tree. Шляхи Mypy Type Checker і ShellCheck ведуть
до виконуваних файлів `.venv/bin/`; mypy використовує scope `custom` і конфігурацію
`pyproject.toml`. Рекомендації не означають, що розширення вже встановлені в редакторі.

Попередня перевірка охопила 45 відносних посилань із anchors, 13 офіційних URL,
синтаксис 23 Bash-блоків і 4 вкладених Python-блоків, `make help`, чотири CLI `--help`
та приклад валідації конфігурації без підключення до БД. Її докази:
`artifacts/documentation-review-2026-10-17.json`,
`artifacts/documentation-static-2026-10-17.log` і
`artifacts/documentation-collection-2026-10-17.log`.
Ці історичні файли містять результати до додавання нових archive regression tests.

Після поточних виправлень operations описує обмеження імен ролей/БД PostgreSQL
і вимогу UTF-8 для archive env-файлу. Markdown повторно перевірено після оновлення
цього звіту. Посилання й блоки команд у цих виправленнях не змінювалися.

## Ідентичність джерел

Snapshot охоплює 62 файли: прикладний код, тести, SQL, Docker scripts, Compose,
Makefile, `.env.example`, Python metadata, lockfile та конфігурацію редактора.
Робочий `.env`, секрети, caches, логи й документація виключені.

SHA-256 snapshot останнього повного прогону:
`01cf0395aa28131a6657272be785245b56f34a81ac0072c77d13bf5ef8502b23`.

Щоб записати новий manifest, не перезаписуючи докази попередніх перевірок:

```bash
uv run --locked python - <<'PY'
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

paths = [Path(name) for name in (
    'Makefile', '.env.example', '.python-version', 'pyproject.toml', 'uv.lock',
    'compose.yaml', 'compose.test.yaml', 'queries.sql',
    'create_tables.py', 'seed.py', 'main.py', '.editorconfig', '.gitignore',
    '.vscode/settings.json', '.vscode/tasks.json', '.vscode/extensions.json',
)]
for directory in ('conf', 'scripts', 'tests', 'docker'):
    paths += [p for p in Path(directory).rglob('*')
              if p.is_file() and p.suffix in {'.py', '.sh', '.js'}]
files = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
fingerprint = hashlib.sha256(''.join(f'{p}\t{h}\n' for p, h in files.items()).encode())
Path('artifacts').mkdir(exist_ok=True)
stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
target = Path('artifacts') / f'source-manifest-{stamp}.json'
with target.open('x', encoding='utf-8') as stream:
    json.dump({'sha256': fingerprint.hexdigest(), 'files': files}, stream, indent=2)
    stream.write('\n')
print(target)
print(fingerprint.hexdigest())
PY
```

Після зміни коду повторіть відповідні перевірки й збережіть новий manifest.
Створення manifest саме по собі не запускає тести й не засвідчує їхній успіх.
Попередні логи залишаються локально як історія; їхні результати не підсумовуються
з поточними й не переносяться на змінені джерела.

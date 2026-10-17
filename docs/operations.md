# Експлуатація та діагностика

[README](../README.md) · [Розробка](development.md) · [Експлуатація](operations.md) · [Верифікація](verification.md)

## Конфігурація

`make init` створює `.env` (0600), каталог `secrets/` (0700) і чотири файли паролів
(0444). Файли доступні non-root користувачам контейнерів через Compose secrets;
каталог на хості закритий для інших користувачів. Наявні файли не перезаписуються.
Небезпечні права, symlink, однакові паролі чи спільні шляхи викликають помилку.
До створення `.env`, каталогу чи секретів `make init` перевіряє параметри підключення
обох БД: імена, хости, порти, ліміти часу та використання файлів паролів.
Використовуються ті самі правила `conf/settings.py`, що й під час запуску застосунків.
Помилка в цих параметрах не змінює наявні файли й не створює відсутні.
Перевірка не встановлює з'єднання з БД; значення за замовчуванням нижче — з `.env.example`.

| Параметри | Значення та межі |
| --- | --- |
| `PG_HOST`, `MONGO_HOST` | За замовчуванням 127.0.0.1 |
| `PG_PORT`, `MONGO_PORT` | 5433 / 27018; допустимо 1–65535 |
| `PG_DB`, `MONGO_DB` | task_manager / cats_db |
| `PG_USER`, `MONGO_USER` | hw03_app; окремий користувач застосунку |
| `PG_PASSWORD_FILE`, `MONGO_PASSWORD_FILE` | Паролі застосунку |
| `PG_ADMIN_PASSWORD_FILE`, `MONGO_ADMIN_PASSWORD_FILE` | Bootstrap і явна адміністративна ротація |
| `DB_TIMEOUT` | 5 секунд; 1–30: з’єднання, пошук сервера й socket timeout MongoDB |
| `DB_QUERY_TIMEOUT` | 10 секунд; 1–300: SQL statement/lock timeout і MongoDB CSOT |
| `HW03_ENV_FILE` | Інший dotenv-файл для Python; стандартно кореневий .env |

Імена БД/користувачів: `[a-z_][a-z0-9_]{0,62}`, без зарезервованих імен.
Для `PG_USER` додатково заборонені `public` і `none`, для `PG_DB` — системні
шаблони `template0` і `template1`. `make init` відхиляє їх до створення файлів.
Змінні процесу мають пріоритет над dotenv. Відносні шляхи секретів Python
обчислюються від каталогу обраного dotenv-файлу. Compose використовує `--env-file`
і шляхи від каталогу проєкту, тому для зовнішнього env задавайте абсолютні шляхи.
Значення `*_PASSWORD` не підтримуються: використовуйте `*_PASSWORD_FILE`.
Старі `DATABASE_URL`/`MONGODB_URI` слід замінити параметрами з `.env.example`.

Паролі передаються драйверам окремими параметрами, тому `@:/?#%$&`, пробіли й лапки
не потребують ручного URI escaping. Секрет не може бути порожнім або багаторядковим.

Після ручної зміни `.env` перевірте налаштування обох застосунків без підключення до БД:

```bash
uv run --locked python - <<'PY'
from conf.logging import report_error
from conf.settings import ConfigurationError, settings

try:
    for prefix in ("PG", "MONGO"):
        settings(prefix)
except ConfigurationError as error:
    report_error("Помилка конфігурації", error)
    raise SystemExit(1) from None
print("Параметри застосунків коректні; доступ до серверів ще не перевірено.")
PY
```

## Життєвий цикл і дані

```bash
docker compose up -d --wait
docker compose ps
docker compose stop
docker compose start
```

`stop`, `restart` і звичайний `down` зберігають named volumes `pg_data`, `mongo_data`.
Bootstrap виконується тільки для порожніх volumes. Зміна файлу пароля не змінює
пароль наявного користувача в БД; ротація потребує узгодженого адміністративного
оновлення БД і секрету. `make init` не виконує ротацію.

`docker compose down --volumes` видаляє дані цього проєкту. Використовуйте його
лише для свідомого скидання навчальної БД після потрібного резервного копіювання.
Старі volumes від попередніх PostgreSQL/MongoDB конфігурацій не видаляються
й не під'єднуються автоматично; перенесення даних потребує логічного export/import.

### Аварійне прибирання тимчасового проєкту

Тести, demo й benchmark прибирають власні named та anonymous volumes через `down -v`.
Після аварії знайдіть точне ім'я `hw03-test-*`, `hw03-demo-*` або `hw03-benchmark-*`
у `docker compose ls -a`. Переконайтеся, що відповідний запуск уже завершився.
Якщо cleanup demo/benchmark не вдався, stderr містить `Recovery configuration:`
зі шляхом до збереженого `.env`. Для тестів env розташований у тимчасовому каталозі pytest;
для demo/benchmark — у системному тимчасовому каталозі `hw03-demo-*` / `hw03-benchmark-*`.
Зіставте env із контейнерами саме цього запуску; не використовуйте робочий `.env`.

З кореня проєкту, у shell без змінних `PG_*`, `MONGO_*`, `HW03_*`, `COMPOSE_*`,
замініть шлях та ім'я нижче на точні значення залишеного проєкту:

```bash
docker compose --project-directory "$PWD" --env-file /absolute/recovery/.env \
  -p hw03-demo-REPLACE_ME -f compose.yaml -f compose.test.yaml \
  down --volumes --remove-orphans
```

Обидва compose-файли потрібні для відтворення конфігурації runner.
Після успішного cleanup видаліть лише env і секрети цього запуску.
Якщо їх уже втрачено, визначайте залишені ресурси за точним Compose project label
та mounts його контейнерів. Не використовуйте загальний `docker system prune`.

## Ротація паролів застосунку на наявних volumes

Цей порядок змінює лише два паролі застосунку. Адміністративні паролі залишаються
чинними й використовуються для автентифікації операції. `make init` не змінює
облікові записи БД. Перед ротацією зупиніть клієнти застосунку й зробіть потрібний
backup даних; volumes не видаляються.
Наведений приклад призначений для стандартного кореневого `.env`:
виконуйте кроки в shell без зовнішніх перевизначень `PG_*`, `MONGO_*`, `HW03_*`, `COMPOSE_*`.

1. Збережіть старі секрети в приватному каталозі й створіть нові значення.
   Запускайте з кореня проєкту; блок відмовиться перезаписувати наявний backup.
   Запис виконується в той самий inode, який уже змонтовано в контейнер:

   ```bash
   uv run --locked python - <<'PY'
   import secrets
   from conf.settings import ROOT, read_secret, read_values, secret_path

   values = read_values()
   paths = [secret_path(values, key, ROOT) for key in
            ("PG_PASSWORD_FILE", "MONGO_PASSWORD_FILE")]
   originals = [read_secret(path) for path in paths]
   backup = ROOT / "secrets" / "rotation-backup"
   backup.mkdir(mode=0o700)
   for prefix, value in zip(("pg", "mongo"), originals, strict=True):
       saved = backup / (prefix + "_password")
       saved.touch(mode=0o600, exist_ok=False)
       saved.write_text(value + "\n", encoding="utf-8")
   for path in paths:
       path.chmod(0o600)
       try:
           path.write_text(secrets.token_urlsafe(48) + "\n", encoding="utf-8")
       finally:
           path.chmod(0o444)
   PY
   ```

2. Оновіть паролі в БД через чинні адміністративні облікові записи:

   ```bash
   docker compose exec -T postgres bash \
     /docker-entrypoint-initdb.d/10-app.sh --rotate-app-password
   docker compose exec -T -e HW03_ROTATE_APP_PASSWORD=1 mongodb \
     mongosh --quiet --norc /docker-entrypoint-initdb.d/10-app.js
   docker compose up -d --wait
   ```

3. Перевірте доступ через застосунок і відновіть клієнти. Після підтвердження
   знищіть лише два застарілі секрети та каталог `secrets/rotation-backup`.

Обидва виклики ротації можна повторити з тим самим новим секретом. Якщо другий
сервер недоступний, виправте причину й повторіть відповідний виклик; не запускайте
генерацію ще раз. Для відкату відновіть старі значення з backup у ті самі файли
зі збереженням inode та прав 0444, а потім повторіть обидві команди ротації.
Просте повернення файлів без зміни пароля в БД недостатнє.

`tests/integration/test_rotation.py` змінює паролі лише свого тестового проєкту,
перевіряє відмову зі старим паролем, доступ із новим, збереження прав і даних після
restart, а наприкінці відновлює початкові тестові паролі. Результат фактичного
прогону наведено у [verification](verification.md).

## Таймаути та повідомлення CLI

`DB_QUERY_TIMEOUT` обмежує кожен SQL statement і очікування lock, а не весь seed.
Seed залишається однією транзакцією: timeout або SIGINT відкочує всі вставки;
SIGINT повертає exit code 130. У MongoDB CSOT обмежує всю окрему операцію,
а `DB_TIMEOUT` може завершити мережеве очікування раніше. Після помилки меню
залишається активним; коли сервер повернувся на ту саму адресу й порт, можна
повторно вибрати операцію без перезапуску CLI. Перед повторенням запису перевірте
дані: обрив відповіді не доводить, що сервер не виконав запис.

| Код | Дія |
| --- | --- |
| `CONFIG` | Перевірити названий параметр або файл секрету |
| `AUTH` | Перевірити користувача, пароль і завершення ротації |
| `PERMISSION` | Перевірити права прикладної ролі в потрібній БД |
| `TIMEOUT` | Перевірити блокування, навантаження та ліміти |
| `CONNECTION` | Перевірити контейнер, адресу, порт і реквізити |
| `SCHEMA` | Спочатку виконати `create_tables.py` |
| `DATABASE` | Перевірити операцію та стан БД |
| `OPERATION` | Прочитати очищену причину помилки Docker Compose у demo/benchmark |

Категорії визначаються типом винятку, SQLSTATE або кодом MongoDB, без друку
сирого повідомлення драйвера. libpq іноді повертає помилку входу без SQLSTATE:
тоді CLI консервативно показує `CONNECTION`, а не вгадує причину за текстом.
Для `OPERATION` зберігається безпечна діагностика: наприклад, зайнятий порт,
недоступний Docker або timeout. Паролі й URI очищуються перед показом;
довільні повідомлення інших винятків і далі не виводяться.
Помилки startup повертають 1; EOF і Ctrl+C у меню — нормальне завершення з 0.

## Резервне копіювання та відновлення

PostgreSQL використовує `pg_dump` custom format для таблиць і послідовностей `public`,
MongoDB — `mongodump --archive --gzip` для прикладної БД. Ролі, паролі й адміністративні
БД не переносяться. Для цього навчального standalone MongoDB зупиніть записи на час
backup, щоб отримати узгоджені дані. PostgreSQL dump використовує узгоджений snapshot.
Архіви містять дані застосунку: тримайте їх у приватному каталозі поза Git.

З кореня проєкту, після запуску потрібного сервера. `--project` має точно відповідати
імені вашого Compose-проєкту (`docker compose ls`); нижче — стандартне ім'я каталогу:

```bash
uv run --locked python -m scripts.archive backup postgres artifacts/backups/postgres.zip \
  --env-file .env --project goit-cs-hw-03
uv run --locked python -m scripts.archive backup mongodb artifacts/backups/mongodb.zip \
  --env-file .env --project goit-cs-hw-03
```

`scripts/archive.py` створює приватний тимчасовий каталог 0700 і файли 0600.
Лише після успішного dump і запису manifest він атомарно публікує ZIP через hard link
на тому самому filesystem. Наявний файл не перезаписується, навіть за конкурентного
запуску. Помилка, timeout або Ctrl+C прибирають тимчасові файли й не публікують backup.
Після SIGKILL чи аварії машини може залишитися приватний `.hw03-backup-*`;
це не готовий архів. Видаляйте його тільки коли відповідний процес уже не працює.
Filesystem має підтримувати hard links; якщо ні, операція завершується помилкою.
Кожна archive-операція має ліміт 120 секунд усередині контейнера.
Спільний `docker/archive-runner.sh` керує окремою групою процесів і після TERM
дає до 2 секунд на завершення перед примусовим KILL. Клієнт Docker має додаткові
5 секунд для завершення й прибирання. Timeout не залишає працювати `pg_dump`,
`pg_restore`, `mongodump` або `mongorestore` після завершення wrapper.
Wrapper також прибирає приватний каталог із тимчасовими MongoDB реквізитами.
Ctrl+C адресно скасовує лише поточну операцію за її випадковим ID;
скасування до запуску блокує запізнілий старт. Якщо Docker недоступний і підтвердити
скасування неможливо, CLI прямо повідомляє про це; внутрішній deadline залишається чинним.

ZIP містить native `archive` і `manifest.json`: формат, engine, ім'я БД, фактичну
версію сервера, UTC-час, розмір і SHA-256. Це окремий формат резервних копій БД.
Обидва ZIP entries мають метод `ZIP_STORED`; native archive зберігає власну компресію.
Інші методи ZIP-компресії відхиляються до читання entries та підключення до БД,
зокрема для пошкоджених DEFLATE-блоків. Не перепаковуйте bundle зі зміною цього методу.
Перед restore перевіряються склад ZIP, manifest, розмір, checksum, engine, ім'я БД
і major-версія сервера. Зіпсований або обрізаний bundle відхиляється до зміни даних.
Checksum виявляє пошкодження, але не підтверджує автора: використовуйте довірені копії.
Паролі не входять до manifest чи argv. MongoDB wrapper видаляє приватний конфіг
автентифікації після завершення; PostgreSQL читає пароль із Compose secret.

Відновлюйте в **новий окремий Compose-проєкт**. Підготуйте `/absolute/restore.env`
за `.env.example`: ті самі імена БД, нові користувачі/секрети, абсолютні шляхи до
секретів і вільні порти. Для bootstrap використайте цей env і окремий project:

```bash
docker compose --env-file /absolute/restore.env -p hw03-restore up -d --wait
uv run --locked python -m scripts.archive restore postgres artifacts/backups/postgres.zip \
  --env-file /absolute/restore.env --project hw03-restore
uv run --locked python -m scripts.archive restore mongodb artifacts/backups/mongodb.zip \
  --env-file /absolute/restore.env --project hw03-restore
```

Для bootstrap запускайте shell без змінних `PG_*`, `MONGO_*`, `HW03_*`, `COMPOSE_*`.
Archive CLI сам очищає ці змінні та використовує лише явно обраний env і project.
Env-файл має бути в UTF-8; помилка декодування повертає код 1 без traceback
і без запуску Docker чи зміни архіву.
До restore не створюйте таблиці/колекції й не запускайте seed або інші клієнти.
Wrapper відмовляє для непорожньої цільової схеми/БД. PostgreSQL restore виконується
однією транзакцією. MongoDB restore не атомарний: після помилки відновлюйте в нову
порожню БД. Перевірка bundle не гарантує успіху наступної операції на сервері.

Автоматична репетиція на ізольованих тестових даних:

```bash
uv run --locked pytest --integration tests/integration/test_backup.py
```

Вона перевіряє дані, UNIQUE/FK/CASCADE, послідовності, ObjectId/Unicode, індекси,
прикладні права, збереження джерела й відмову повторної/пошкодженої/чужої копії.
Після ручної репетиції та перевірки даних видаліть лише її проєкт:

```bash
docker compose --env-file /absolute/restore.env -p hw03-restore down --volumes --remove-orphans
```

Потім видаліть її тимчасовий env і секрети. Результати — у [verification](verification.md).
Документація утиліт: [pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html),
[pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html),
[mongodump](https://www.mongodb.com/docs/database-tools/mongodump/),
[mongorestore](https://www.mongodb.com/docs/database-tools/mongorestore/).

## Пошук проблем

Діагностичні команди без зміни сервісів:

```bash
uv --version
uv run --locked python --version
docker version
docker compose version
docker info --format '{{.KernelVersion}}'
docker system df
docker compose config --quiet
docker compose ps
```

Для `compose.test.yaml` потрібен Compose 2.24.4+ (`!override`).
Повторний `make init` перевіряє локальні файли й права, зберігаючи наявні значення.
Перевірку параметрів застосунків наведено в розділі [конфігурації](operations.md#конфігурація).

За `No space left on device` перевірте `docker system df` і місткість диска
Docker Desktop. Вільне місце macOS не дорівнює вільному місцю Docker VM.
Звільняйте тільки явно визначені непотрібні ресурси або збільшуйте диск VM.

Зафіксований образ Percona MongoDB 8.0.32-14 відмовився запускатися на
`7.0.12-linuxkit`; повні перевірки пройшли на `7.0.14-linuxkit`.
Причину несумісності та виправлення kernel 7.0.14+ / 7.1.0+ описано в
[SERVER-125742](https://jira.mongodb.org/browse/SERVER-125742).
[Docker Desktop 4.93.0](https://docs.docker.com/desktop/release-notes/#4930)
оновлює kernel до 7.0.14 і прямо виправляє цю проблему. Використовуйте цю або
новішу сумісну версію; після оновлення перевірте фактичний kernel через `docker info`.
Оновлення перезапускає Docker та впливає на інші запущені контейнери.
Не обходьте захисну перевірку MongoDB.
Сумісність інших комбінацій версій і дистрибутивів перевіряйте за актуальною матрицею upstream.

Помилки `authentication failed` після зміни конфігурації зазвичай означають,
що дані користувача в наявному volume не збігаються з локальним секретом.
Health checks автентифікуються як користувач застосунку, а не лише перевіряють порт.

Образи зафіксовані тегом і multiarch digest; оновлюйте їх разом після перевірок.
Офіційні інструкції:
[Percona PostgreSQL](https://docs.percona.com/postgresql/17/docker.html),
[Percona MongoDB](https://docs.percona.com/percona-server-for-mongodb/8.0/install/docker.html).

-- Завдання 1: усі 14 запитів у порядку з умови.
-- Приклади використовують user_id = 1, task id = 1 та id = 2.
-- Ці ID існують після першого seed.py у новій базі.
-- Для власних даних замініть ID, статуси та шаблони email.
-- Запити 3, 5, 7 і 9 змінюють дані; COMMIT зберігає ці зміни.

BEGIN;

-- 1. Усі завдання певного користувача.
SELECT * FROM tasks WHERE user_id = 1 ORDER BY id;

-- 2. Завдання зі статусом 'new' (підзапит).
SELECT * FROM tasks
WHERE status_id = (SELECT id FROM status WHERE name = 'new')
ORDER BY id;

-- 3. Оновлення статусу конкретного завдання.
UPDATE tasks
SET status_id = (SELECT id FROM status WHERE name = 'in progress')
WHERE id = 1;

-- 4. Користувачі без завдань (NOT IN та підзапит).
SELECT * FROM users
WHERE id NOT IN (SELECT user_id FROM tasks)
ORDER BY id;

-- 5. Нове завдання для конкретного користувача.
INSERT INTO tasks (title, description, status_id, user_id)
VALUES (
    'Вивчити PostgreSQL',
    'Виконати запити з домашнього завдання',
    (SELECT id FROM status WHERE name = 'new'),
    1
);

-- 6. Усі незавершені завдання.
SELECT * FROM tasks
WHERE status_id <> (SELECT id FROM status WHERE name = 'completed')
ORDER BY id;

-- 7. Видалення конкретного завдання.
DELETE FROM tasks WHERE id = 2;

-- 8. Користувачі з email, що відповідає шаблону.
SELECT * FROM users WHERE email LIKE '%@example.com' ORDER BY id;

-- 9. Оновлення імені конкретного користувача.
UPDATE users SET fullname = 'Олександр Коваленко' WHERE id = 1;

-- 10. Кількість завдань для кожного статусу, зокрема з нульовою кількістю.
SELECT s.name, COUNT(t.id) AS task_count
FROM status AS s
LEFT JOIN tasks AS t ON t.status_id = s.id
GROUP BY s.id, s.name
ORDER BY s.id;

-- 11. Завдання користувачів із доменом example.com (LIKE та JOIN).
SELECT t.*, u.email
FROM tasks AS t
JOIN users AS u ON u.id = t.user_id
WHERE u.email LIKE '%@example.com'
ORDER BY t.id;

-- 12. Завдання без опису: NULL, порожній рядок або лише пробіли.
SELECT * FROM tasks
WHERE description IS NULL OR TRIM(description) = ''
ORDER BY id;

-- 13. Користувачі та їхні завдання зі статусом 'in progress'.
SELECT u.id AS user_id, u.fullname, t.id AS task_id, t.title, s.name AS status
FROM users AS u
INNER JOIN tasks AS t ON t.user_id = u.id
INNER JOIN status AS s ON s.id = t.status_id
WHERE s.name = 'in progress'
ORDER BY u.id, t.id;

-- 14. Користувачі та кількість їхніх завдань (LEFT JOIN та GROUP BY).
SELECT u.id, u.fullname, COUNT(t.id) AS task_count
FROM users AS u
LEFT JOIN tasks AS t ON t.user_id = u.id
GROUP BY u.id, u.fullname
ORDER BY u.id;

COMMIT;

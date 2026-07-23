---
name: apple-reminders
description: Добавление напоминаний/задач в приложение "Напоминания" на iPhone пользователя через iCloud CalDAV. Триггеры — "добавь в напоминания", "поставь задачу на айфон", "напомни на телефоне", "добавь в reminders", запросы создать задачу, которая должна появиться на iPhone.
---

# Apple Reminders через CalDAV

Напоминания синхронизируются с iPhone через iCloud CalDAV — никакого
отдельного API у Apple для этого нет, но CalDAV работает надёжно и
появляется на телефоне почти сразу.

## Команда

Рабочая директория — vault, скрипт на уровень выше в скилле:

```bash
uv run --project .. python .claude/skills/apple-reminders/scripts/add_reminder.py "Текст задачи"
```

С датой выполнения (ISO-формат, локальное время пользователя):

```bash
uv run --project .. python .claude/skills/apple-reminders/scripts/add_reminder.py \
  "Позвонить клиенту" --due 2026-07-25T10:00:00
```

С указанием конкретного списка (если у пользователя их несколько):

```bash
uv run --project .. python .claude/skills/apple-reminders/scripts/add_reminder.py \
  "Купить билеты" --list "Работа"
```

С заметкой:

```bash
uv run --project .. python .claude/skills/apple-reminders/scripts/add_reminder.py \
  "Встреча" --notes "Обсудить контракт"
```

Посмотреть, какие списки вообще есть в Напоминаниях:

```bash
uv run --project .. python .claude/skills/apple-reminders/scripts/add_reminder.py --list-lists
```

## Требования

Скрипт сам читает `APPLE_ID` и `APPLE_APP_PASSWORD` из `.env` в корне
проекта. Если их там нет — скажи пользователю, что нужно сгенерировать
app-specific password на appleid.apple.com (Безопасность → Пароли для
приложений) и добавить в `.env`:

```
APPLE_ID=его_apple_id@icloud.com
APPLE_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

Никогда не проси и не подставляй основной пароль Apple ID — только
app-specific password.

## Поведение

- Без `--list` задача уходит в первый доступный список Напоминаний
- Если указанный список не найден, скрипт выведет список доступных — покажи
  их пользователю и уточни, какой имелся в виду
- Успех подтверждается строкой `✅ Добавлено в «Список»: текст` — перескажи
  это пользователю своими словами, не копируй технический вывод дословно

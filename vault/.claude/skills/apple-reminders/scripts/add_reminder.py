#!/usr/bin/env python3
"""Add a reminder to Apple Reminders (iCloud) via CalDAV.

Standalone script — reads APPLE_ID / APPLE_APP_PASSWORD from the project
.env (walks up from this file), same convention as scripts/notify.sh.

Usage:
    add_reminder.py "Купить молоко"
    add_reminder.py "Позвонить клиенту" --due 2026-07-25T10:00:00 --list Работа
    add_reminder.py --list-lists
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import caldav

CALDAV_URL = "https://caldav.icloud.com"


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def get_credentials() -> tuple[str, str]:
    # file: vault/.claude/skills/apple-reminders/scripts/add_reminder.py
    # parents[5] -> agent-second-brain/ (project root, where .env lives)
    project_root = Path(__file__).resolve().parents[5]
    env = {**load_env(project_root / ".env"), **os.environ}
    apple_id = env.get("APPLE_ID")
    app_password = env.get("APPLE_APP_PASSWORD")
    if not apple_id or not app_password:
        print(
            "Ошибка: APPLE_ID / APPLE_APP_PASSWORD не заданы в .env проекта.",
            file=sys.stderr,
        )
        sys.exit(1)
    return apple_id, app_password


def get_todo_calendars(principal) -> list:
    calendars = principal.calendars()
    todo_lists = []
    for cal in calendars:
        try:
            comps = cal.get_supported_components()
        except Exception:
            comps = None
        if not comps or "VTODO" in comps:
            todo_lists.append(cal)
    return todo_lists or calendars


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("title", nargs="?", help="Reminder title")
    parser.add_argument("--list", dest="list_name", default=None, help="Target Reminders list name")
    parser.add_argument("--due", default=None, help="Due date/time, ISO format (2026-07-25T10:00:00)")
    parser.add_argument("--notes", default=None, help="Notes")
    parser.add_argument("--list-lists", action="store_true", help="Print available Reminders lists and exit")
    args = parser.parse_args()

    apple_id, app_password = get_credentials()
    client = caldav.DAVClient(url=CALDAV_URL, username=apple_id, password=app_password)
    principal = client.principal()
    todo_lists = get_todo_calendars(principal)

    if args.list_lists:
        for cal in todo_lists:
            print(cal.name)
        return

    if not args.title:
        parser.error("title is required unless --list-lists is given")

    target = todo_lists[0]
    if args.list_name:
        matches = [c for c in todo_lists if c.name and c.name.lower() == args.list_name.lower()]
        if not matches:
            available = ", ".join(c.name for c in todo_lists)
            print(f"Список «{args.list_name}» не найден. Доступные: {available}", file=sys.stderr)
            sys.exit(1)
        target = matches[0]

    kwargs = {"summary": args.title}
    if args.due:
        kwargs["due"] = datetime.fromisoformat(args.due)
    if args.notes:
        kwargs["description"] = args.notes

    target.add_todo(**kwargs)
    print(f"✅ Добавлено в «{target.name}»: {args.title}")


if __name__ == "__main__":
    main()

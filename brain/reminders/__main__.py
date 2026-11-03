"""Envio matinal manual/cron: python -m brain.reminders (também na imagem de produção)."""

import asyncio

from brain.memory.mongo import MongoConnection
from brain.reminders.delivery import send_morning
from brain.reminders.store import ReminderStore


def main() -> None:
    connection = MongoConnection()
    try:
        connection.check()
        count = asyncio.run(send_morning(ReminderStore(connection)))
        print(f"Lembretes enviados: {count}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()

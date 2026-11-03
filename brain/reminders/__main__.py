"""Uma passagem manual de envio: python -m brain.reminders (o servidor já roda a cada minuto)."""

import asyncio

from brain.memory.mongo import MongoConnection
from brain.reminders.delivery import send_due
from brain.reminders.store import ReminderStore


def main() -> None:
    connection = MongoConnection()
    try:
        connection.check()
        count = asyncio.run(send_due(ReminderStore(connection)))
        print(f"Lembretes enviados: {count}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()

"""Throwaway check of the Google Calendar setup (service account + shared calendars, ADR-008).

Usage: python scripts/check_google.py
Reads the key from settings.google.credentials_file and never prints it.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from brain.config import settings

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def main() -> int:
    cfg = settings.google
    print(f"Chave: {cfg.credentials_file}")
    if not cfg.credentials_file.exists():
        print("  ✗ arquivo não encontrado")
        return 1
    try:
        creds = service_account.Credentials.from_service_account_file(
            str(cfg.credentials_file), scopes=SCOPES
        )
    except (ValueError, OSError) as exc:
        print(f"  ✗ chave ilegível ({type(exc).__name__})")
        return 1
    print(f"  ✓ conta de serviço: {creds.service_account_email}")

    if not cfg.calendar_id:
        print("google.calendar_id não configurado")
        return 1
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    now = datetime.now(UTC)
    calendars = [("Juniper (escrita)", cfg.calendar_id)] + [
        (f"leitura: {cid}", cid) for cid in cfg.read_calendar_ids
    ]
    failed = False
    for label, cid in calendars:
        try:
            result = (
                service.events()
                .list(
                    calendarId=cid,
                    timeMin=now.isoformat(),
                    timeMax=(now + timedelta(days=7)).isoformat(),
                    maxResults=5,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except HttpError as exc:
            hint = {404: "ID incorreto ou agenda não compartilhada", 403: "sem permissão"}.get(
                exc.resp.status, "erro"
            )
            print(f"  ✗ {label}: HTTP {exc.resp.status} ({hint})")
            failed = True
            continue
        print(f"  ✓ {label}: acessível, {len(result.get('items', []))} evento(s) nos próximos 7 dias")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

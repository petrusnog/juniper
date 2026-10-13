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
    calendars = (
        [("Juniper (escrita)", cfg.calendar_id)]
        + [(f"gerenciada: {cid}", cid) for cid in cfg.manage_calendar_ids]
        + [(f"leitura: {cid}", cid) for cid in cfg.read_calendar_ids]
    )
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
        print(
            f"  ✓ {label}: acessível, {len(result.get('items', []))} evento(s) nos próximos 7 dias"
        )
        if label.startswith("gerenciada") and not can_write(service, cid):
            print(
                "    ✗ mas SEM permissão de escrita: em Configurações da agenda → Compartilhar com"
            )
            print(f"      pessoas específicas, mude {creds.service_account_email} para")
            print('      "Fazer alterações em eventos"')
            failed = True
        elif label.startswith("gerenciada"):
            print("    ✓ permissão de escrita ok")
    if cfg.default_calendar_id and cfg.default_calendar_id not in (
        cfg.calendar_id,
        *cfg.manage_calendar_ids,
    ):
        print(
            "  ✗ default_calendar_id precisa ser a agenda Juniper ou estar em manage_calendar_ids"
        )
        failed = True
    return 1 if failed else 0


def can_write(service: object, calendar_id: str) -> bool:
    """Probe write access without changing anything: delete an id that cannot exist.

    Google checks permission before looking for the event: a writer gets 404 ("no such
    event"), a reader gets 403.
    """
    try:
        service.events().delete(calendarId=calendar_id, eventId="juniperprobe0000").execute()  # type: ignore[attr-defined]
    except HttpError as exc:
        return exc.resp.status in (404, 410)
    return True


if __name__ == "__main__":
    sys.exit(main())

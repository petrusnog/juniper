"""Publicação do app Android pelo próprio cérebro (ADR-026).

O pipeline (ou `scripts/publish_app.sh`) deixa numa pasta o APK mais recente e um `latest.json`
com a versão, o tamanho e o SHA-256. O servidor só **lê** essa pasta: o app pergunta se há versão
nova (`GET /app/update`) e, se houver, baixa o arquivo (`GET /app/update/apk`). Quem autentica é o
servidor (o mesmo token dos clientes); quem garante que o APK é da Juniper é o Android, que só
aceita uma atualização assinada com a mesma chave do app instalado.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

APK_NAME: Final = "app-release.apk"
META_NAME: Final = "latest.json"
# Um APK de React Native tem dezenas de MB; acima disto algo está errado (e o arquivo seria lido
# inteiro para a memória).
MAX_APK_BYTES: Final = 200 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AppRelease:
    version_code: int
    version_name: str
    sha256: str
    size: int
    released_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "available": True,
            "version_code": self.version_code,
            "version_name": self.version_name,
            "sha256": self.sha256,
            "size": self.size,
            "released_at": self.released_at,
        }


class AppUpdates:
    """A pasta de publicação (somente leitura)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def release(self) -> AppRelease | None:
        """A versão publicada, ou `None` se não há nada válido (nunca levanta)."""
        meta_path = self.directory / META_NAME
        apk_path = self.directory / APK_NAME
        try:
            if not meta_path.is_file() or not apk_path.is_file():
                return None
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            release = AppRelease(
                version_code=int(data["version_code"]),
                version_name=str(data["version_name"]),
                sha256=str(data["sha256"]).lower(),
                size=int(data["size"]),
                released_at=str(data.get("released_at", "")),
            )
            actual = apk_path.stat().st_size
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Publicação do app ilegível: %s", type(exc).__name__)
            return None
        if release.version_code < 1 or not _SHA256.match(release.sha256):
            logger.warning("latest.json com versão ou SHA-256 inválidos")
            return None
        if release.size != actual or not 0 < actual <= MAX_APK_BYTES:
            # Um upload pela metade (ou um latest.json de outra versão): melhor não oferecer.
            logger.warning("O APK publicado não bate com o latest.json (%s bytes)", actual)
            return None
        return release

    def apk_bytes(self, release: AppRelease) -> bytes | None:
        try:
            data = (self.directory / APK_NAME).read_bytes()
        except OSError:
            return None
        return data if len(data) == release.size else None

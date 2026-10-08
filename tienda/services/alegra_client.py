"""Cliente de solo lectura para la API v1 de Alegra.

Este módulo expone únicamente operaciones GET. No contiene métodos de escritura
ni lee las credenciales desde settings o desde la base de datos.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AlegraError(Exception):
    """Error controlado al consultar Alegra."""


class AlegraPaginationError(AlegraError):
    """La API repitió una página y no se puede afirmar cobertura completa."""


class AlegraConfigurationError(AlegraError):
    """Faltan credenciales o la URL base no es válida."""


class AlegraHTTPError(AlegraError):
    """Alegra respondió con un estado HTTP no exitoso."""

    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class AlegraResponse:
    status: int
    data: Any
    url: str


class AlegraReadOnlyClient:
    """Cliente mínimo, paginado y deliberadamente restringido a GET."""

    def __init__(self, *, timeout: float | None = None):
        self.base_url = os.environ.get("ALEGRA_BASE_URL", "https://api.alegra.com/api/v1").strip().rstrip("/")
        self.email = os.environ.get("ALEGRA_EMAIL", "").strip()
        self.token = os.environ.get("ALEGRA_API_TOKEN", "").strip()
        self.timeout = timeout or float(os.environ.get("ALEGRA_TIMEOUT", "15"))
        if not self.email or not self.token:
            raise AlegraConfigurationError(
                "Faltan ALEGRA_EMAIL y/o ALEGRA_API_TOKEN; no se realizarán llamadas externas."
            )
        if not self.base_url.startswith(("https://", "http://")):
            raise AlegraConfigurationError("ALEGRA_BASE_URL debe ser una URL HTTP(S).")

    def _auth_header(self) -> str:
        raw = f"{self.email}:{self.token}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def get(self, path: str, params: dict[str, Any] | None = None) -> AlegraResponse:
        """Ejecuta una única petición GET; no acepta ni expone otros verbos."""
        if not path.startswith("/"):
            path = "/" + path
        query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        request = Request(
            url,
            headers={"Authorization": self._auth_header(), "Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                data = json.loads(body) if body else None
                return AlegraResponse(response.status, data, url)
        except HTTPError as exc:
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After")
                message = "Alegra rate limitado (HTTP 429)"
                if retry_after:
                    message += f"; Retry-After={retry_after}"
            elif exc.code in (401, 403):
                message = f"Alegra rechazó la autenticación/autorización (HTTP {exc.code})"
            else:
                message = f"Alegra respondió HTTP {exc.code}"
            raise AlegraHTTPError(exc.code, message) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise AlegraError(f"No fue posible conectar con Alegra: {exc.__class__.__name__}") from None

    def paged_get(
        self,
        path: str,
        *,
        limit: int | None,
        params: dict[str, Any] | None = None,
        max_pages: int | None = None,
        pause: float = 0,
        stop_on_short_page: bool = True,
    ) -> list[AlegraResponse]:
        """Consulta por páginas; ``limit=None`` recorre hasta el fin verificable."""
        page_size = min(max(int(limit or 30), 1), 30)
        remaining = max(int(limit), 1) if limit is not None else None
        start = 0
        responses: list[AlegraResponse] = []
        pages = 0
        fingerprints: set[tuple[str, ...]] = set()
        declared_total = None
        while (remaining is None or remaining > 0) and (max_pages is None or pages < max_pages):
            current = min(page_size, remaining) if remaining is not None else page_size
            page_params = dict(params or {})
            page_params.update({"start": start, "limit": current, "metadata": "true"})
            response = self.get(path, page_params)
            responses.append(response)
            pages += 1
            rows = extract_rows(response.data)
            fingerprint = tuple(str(row.get("id") or "") for row in rows if isinstance(row, dict))
            if fingerprint and fingerprint in fingerprints:
                raise AlegraPaginationError("Alegra repitió una página; cobertura incompleta.")
            if fingerprint:
                fingerprints.add(fingerprint)
            if isinstance(response.data, dict) and isinstance(response.data.get("metadata"), dict):
                raw_total = response.data["metadata"].get("total") or response.data["metadata"].get("count")
                try:
                    declared_total = int(raw_total) if raw_total is not None else declared_total
                except (TypeError, ValueError):
                    pass
            if not rows or (stop_on_short_page and len(rows) < current):
                break
            start += len(rows)
            if remaining is not None:
                remaining -= len(rows)
            if declared_total is not None and start >= declared_total:
                break
            if pause and (remaining is None or remaining > 0) and (max_pages is None or pages < max_pages):
                time.sleep(max(float(pause), 0))
        return responses


def extract_rows(data: Any) -> list[Any]:
    """Normaliza las respuestas de Alegra que pueden ser lista o {data: [...]}"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list):
            return rows
    return []

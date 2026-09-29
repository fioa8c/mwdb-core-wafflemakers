"""HTTP client for the phpdeobf sidecar.

Converts transport failures (connection refused, 5xx, timeout) into a typed
`UnavailableResult` so the resource layer can treat "sidecar down" as a
single state independent of which thing failed.
"""

from dataclasses import dataclass, field
from typing import Optional, Union

import requests

DEFAULT_TIMEOUT = 90.0


@dataclass
class OkResult:
    output: str
    elapsed_ms: int
    peeled: Optional[dict] = None
    analysis: Optional[dict] = None
    warnings: list = field(default_factory=list)
    engine: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)


@dataclass
class ErrorResult:
    code: str
    message: str


@dataclass
class UnavailableResult:
    """Sidecar is unreachable / unhealthy. The cause is logged but not
    surfaced to callers — the user-facing message is the same regardless."""

    detail: str


@dataclass
class HealthResult:
    evalhook_available: bool
    phpdeobf: str


SidecarResult = Union[OkResult, ErrorResult, UnavailableResult]


def deobfuscate(
    source: str,
    *,
    filename: str = "input.php",
    base_url: str,
    timeout: float = DEFAULT_TIMEOUT,
    options: Optional[dict] = None,
) -> SidecarResult:
    url = f"{base_url.rstrip('/')}/deobfuscate"
    body = {"source": source, "filename": filename}
    if options is not None:
        body["options"] = options
    try:
        resp = requests.post(
            url,
            json=body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return UnavailableResult(detail=f"transport error: {exc}")

    if resp.status_code >= 500:
        return UnavailableResult(detail=f"sidecar HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError:
        return UnavailableResult(detail="sidecar returned non-JSON body")

    status = body.get("status")
    if status == "ok":
        return OkResult(
            output=body.get("output", ""),
            elapsed_ms=int(body.get("elapsed_ms", 0)),
            peeled=body.get("peeled"),
            analysis=body.get("analysis"),
            warnings=list(body.get("warnings") or []),
            engine=dict(body.get("engine") or {}),
            options=dict(body.get("options") or {}),
        )
    if status == "error":
        return ErrorResult(
            code=str(body.get("code", "unknown")),
            message=str(body.get("message", "")),
        )
    return UnavailableResult(detail=f"unexpected sidecar status: {status!r}")


def health(*, base_url: str, timeout: float = 5.0):
    try:
        resp = requests.get(f"{base_url.rstrip('/')}/health", timeout=timeout)
    except requests.RequestException as exc:
        return UnavailableResult(detail=f"transport error: {exc}")
    if resp.status_code != 200:
        return UnavailableResult(detail=f"sidecar HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError:
        return UnavailableResult(detail="sidecar returned non-JSON body")
    return HealthResult(
        evalhook_available=bool(body.get("evalhook_available", False)),
        phpdeobf=str(body.get("phpdeobf", "unknown")),
    )

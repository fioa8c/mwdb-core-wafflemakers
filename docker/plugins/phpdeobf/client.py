"""HTTP client for the phpdeobf sidecar.

Converts transport failures (connection refused, 5xx, timeout) into a typed
`UnavailableResult` so the resource layer can treat "sidecar down" as a
single state independent of which thing failed.
"""
from dataclasses import dataclass
from typing import Union

import requests

DEFAULT_TIMEOUT = 30.0


@dataclass
class OkResult:
    output: str
    elapsed_ms: int


@dataclass
class ErrorResult:
    code: str
    message: str


@dataclass
class UnavailableResult:
    """Sidecar is unreachable / unhealthy. The cause is logged but not
    surfaced to callers — the user-facing message is the same regardless."""
    detail: str


SidecarResult = Union[OkResult, ErrorResult, UnavailableResult]


def deobfuscate(
    source: str,
    *,
    filename: str = "input.php",
    base_url: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> SidecarResult:
    url = f"{base_url.rstrip('/')}/deobfuscate"
    try:
        resp = requests.post(
            url,
            json={"source": source, "filename": filename},
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
        )
    if status == "error":
        return ErrorResult(
            code=str(body.get("code", "unknown")),
            message=str(body.get("message", "")),
        )
    return UnavailableResult(detail=f"unexpected sidecar status: {status!r}")

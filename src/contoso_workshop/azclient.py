"""Credentials, retrying HTTP and `az` CLI helpers shared by all modules."""

from __future__ import annotations

import json
import random
import shutil
import subprocess  # nosec B404 - only fixed `az` argument lists, never shell=True
import threading
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any, TypeVar

import requests
from azure.core.credentials import TokenCredential
from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential

from .errors import PermissionDenied, Throttled, WorkshopError

T = TypeVar("T")

ARM = "https://management.azure.com"
SCOPE_ARM = "https://management.azure.com/.default"
SCOPE_COGNITIVE = "https://cognitiveservices.azure.com/.default"
SCOPE_FOUNDRY = "https://ai.azure.com/.default"
SCOPE_SEARCH = "https://search.azure.com/.default"
SCOPE_STORAGE = "https://storage.azure.com/.default"

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


@lru_cache(maxsize=1)
def credential() -> TokenCredential:
    """Azure CLI first (participant laptops), then managed identity etc. (hosted agent runtime)."""
    return _CachingCredential(ChainedTokenCredential(
        AzureCliCredential(process_timeout=30),
        DefaultAzureCredential(exclude_interactive_browser_credential=True, exclude_cli_credential=True),
    ))


class _CachingCredential:
    """Per-scope token cache: AzureCliCredential spawns `az` (~5-8 s) on every get_token otherwise."""

    def __init__(self, inner: TokenCredential) -> None:
        self._inner = inner
        self._cache: dict[tuple[tuple[str, ...], tuple[tuple[str, Any], ...]], Any] = {}
        self._lock = threading.Lock()

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        key = (scopes, tuple(sorted((k, str(v)) for k, v in kwargs.items())))
        with self._lock:
            tok = self._cache.get(key)
            if tok is not None and tok.expires_on - time.time() > 300:
                return tok
            tok = self._inner.get_token(*scopes, **kwargs)
            self._cache[key] = tok
            return tok

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close:
            close()


def bearer(scope: str, cred: TokenCredential | None = None) -> str:
    return (cred or credential()).get_token(scope).token


def backoff_delay(attempt: int, retry_after: str | None = None, base: float = 1.0, cap: float = 30.0) -> float:
    if retry_after:
        try:
            return min(float(retry_after), cap)
        except ValueError:
            pass
    return min(cap, base * (2**attempt)) * (0.5 + random.random() / 2)


def request(
    method: str,
    url: str,
    *,
    scope: str | None = None,
    token: str | None = None,
    json_body: Any = None,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60,
    retries: int = 5,
    ok: tuple[int, ...] = (200, 201, 202, 204),
    session: requests.Session | None = None,
) -> requests.Response:
    """HTTP with bearer auth and exponential backoff on transient failures only."""
    hdrs = dict(headers or {})
    if scope or token:
        hdrs["Authorization"] = f"Bearer {token or bearer(scope or '')}"
    sender = session or requests
    last: requests.Response | None = None
    for attempt in range(retries + 1):
        try:
            resp = sender.request(method, url, json=json_body, params=params, headers=hdrs, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == retries:
                raise WorkshopError(f"{method} {url} failed: {exc}") from exc
            time.sleep(backoff_delay(attempt))
            continue
        if resp.status_code in ok:
            return resp
        last = resp
        if resp.status_code in TRANSIENT_STATUS and attempt < retries:
            time.sleep(backoff_delay(attempt, resp.headers.get("Retry-After")))
            continue
        break
    assert last is not None
    raise http_error(last, f"{method} {url}")


def http_error(resp: requests.Response, what: str) -> WorkshopError:
    request_id = resp.headers.get("x-ms-request-id") or resp.headers.get("apim-request-id") or ""
    body = resp.text[:800]
    msg = f"{what} -> HTTP {resp.status_code} (request-id {request_id}): {body}"
    if resp.status_code in (401, 403):
        return PermissionDenied(msg, fix="check the role assignments in docs/security-and-rbac.md",
                                owner="facilitator")
    if resp.status_code == 429:
        return Throttled(msg, fix="wait a minute; the facilitator may raise deployment capacity")
    return WorkshopError(msg)


def retry_call(fn: Callable[[], T], *, retries: int = 5, is_transient: Callable[[Exception], bool]) -> T:
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == retries or not is_transient(exc):
                raise
            time.sleep(backoff_delay(attempt))
    raise AssertionError("unreachable")


def azure_sdk_transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return status in TRANSIENT_STATUS


# ---- Azure CLI ---------------------------------------------------------------


def az_path() -> str | None:
    return shutil.which("az")


def az(*args: str, timeout: float = 120, check: bool = True) -> Any:
    """Run `az ... -o json` and parse the output. Raises WorkshopError on failure if check."""
    exe = az_path()
    if exe is None:
        raise WorkshopError("Azure CLI (az) not found", fix="install Azure CLI: https://aka.ms/azcli")
    proc = subprocess.run(  # noqa: S603  # nosec B603
        [exe, *args, "-o", "json"], capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        if check:
            raise WorkshopError(f"az {' '.join(args[:3])} failed: {proc.stderr.strip()[:800]}")
        return None
    out = proc.stdout.strip()
    return json.loads(out) if out else None


def arm(method: str, path: str, api_version: str, body: Any = None, **kw: Any) -> requests.Response:
    url = path if path.startswith("http") else f"{ARM}{path}"
    sep = "&" if "?" in url else "?"
    return request(method, f"{url}{sep}api-version={api_version}", scope=SCOPE_ARM, json_body=body, **kw)

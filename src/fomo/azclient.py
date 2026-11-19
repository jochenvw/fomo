"""Credentials, retrying HTTP and `az` CLI helpers shared by all modules."""

from __future__ import annotations

import json
import logging
import random
import shutil
import subprocess  # nosec B404 - only fixed `az` argument lists, never shell=True
import threading
import time
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


# ---- Azure CLI ---------------------------------------------------------------


def az_path() -> str | None:
    return shutil.which("az")


def az(*args: str, timeout: float = 120, check: bool = True) -> Any:
    """Run `az ... -o json`; retry only explicitly known read commands on transient failure."""
    exe = az_path()
    if exe is None:
        raise WorkshopError("Azure CLI (az) not found", fix="install Azure CLI: https://aka.ms/azcli")
    read_only = args[:2] in {
        ("account", "show"), ("account", "list"), ("account", "get-access-token"),
        ("group", "show"), ("group", "exists"), ("group", "list"),
    } or args[:3] in {("deployment", "group", "show"), ("deployment", "group", "list")}
    attempts = 3 if read_only else 1
    for attempt in range(attempts):
        try:
            proc = subprocess.run(  # noqa: S603  # nosec B603
                [exe, *args, "-o", "json"], capture_output=True, text=True, timeout=timeout, check=False
            )
            if proc.returncode == 0:
                out = proc.stdout.strip()
                return json.loads(out) if out else None
            error = proc.stderr.strip()[:800]
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = str(exc)[:800]
        except json.JSONDecodeError as exc:
            error = f"incomplete JSON response: {exc}"
        permanent = any(text in error.lower() for text in (
            "authorizationfailed", "authenticationfailed", "forbidden", "requestdisallowedbypolicy",
            "resourcegroupnotfound", "resourcenotfound", "aadsts", "az login",
        ))
        transient = not permanent and any(text in error.lower() for text in (
            "connection interrupted", "connection aborted", "connection reset", "remotedisconnected",
            "connectionerror", "connection broken", "remote end closed connection", "incomplete json response",
            "timed out", "timeout", "temporarily unavailable", "temporary failure", "name resolution",
            "getaddrinfo failed", "503 service unavailable", "502 bad gateway", "504 gateway timeout",
            "429 too many requests",
        ))
        if transient and attempt + 1 < attempts:
            delay = 2 ** attempt
            logging.getLogger(__name__).warning("Azure CLI read interrupted; retrying attempt %s/%s in %ss",
                                              attempt + 2, attempts, delay)
            time.sleep(delay)
            continue
        if check:
            fix = None
            if transient:
                fix = ("check connectivity, VPN/proxy and Azure status, then rerun the read-only check"
                       if read_only else
                       "inspect the target's current state before retrying; the interrupted write may have completed")
            raise WorkshopError(f"az {' '.join(args[:3])} failed after {attempt + 1} attempt(s): {error}", fix=fix)
        return None
    raise AssertionError("unreachable")


def arm(method: str, path: str, api_version: str, body: Any = None, **kw: Any) -> requests.Response:
    url = path if path.startswith("http") else f"{ARM}{path}"
    sep = "&" if "?" in url else "?"
    return request(method, f"{url}{sep}api-version={api_version}", scope=SCOPE_ARM, json_body=body, **kw)

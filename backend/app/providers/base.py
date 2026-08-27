"""Shared plumbing for external data providers (HLD ADR-4, §4.2 F).

Every provider is an adapter with the same contract: it either returns typed
data or raises `ProviderUnavailableError`. Nothing above this layer knows which HTTP
service was called, so a source can be swapped or a fallback inserted without
touching the services that consume it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.logging import get_logger

log = get_logger("providers")

DEFAULT_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 3

#: A list of pairs as well as a mapping: some services (SoilGrids) take the same
#: query key more than once, which a dict cannot express.
QueryParams = dict[str, Any] | list[tuple[str, Any]] | None


class ProviderUnavailableError(RuntimeError):
    """The source could not answer. Carries the provider name for the response."""

    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(f"{provider}: {detail}")
        self.provider = provider
        self.detail = detail


class ProviderNotConfiguredError(ProviderUnavailableError):
    """The capability exists but its credentials are absent (HLD M8-12)."""


@dataclass(frozen=True)
class Provenance:
    """Where a value came from, carried through to the API response."""

    provider: str
    dataset: str
    resolution: str | None = None
    licence: str | None = None
    accessed_via: str = "https"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "dataset": self.dataset,
            "resolution": self.resolution,
            "licence": self.licence,
            "requires_credential": False,
        }


#: Retry only on transient failures. A 4xx means the request was wrong and
#: repeating it wastes the caller's time; a timeout or 5xx is worth another try.
_transient = retry_if_exception_type((httpx.TimeoutException, httpx.TransportError))


@retry(
    stop=stop_after_attempt(MAX_ATTEMPTS),
    wait=wait_exponential_jitter(initial=0.5, max=4.0),
    retry=_transient,
    reraise=True,
)
def _get(url: str, params: QueryParams = None, timeout: float = DEFAULT_TIMEOUT_S) -> Any:
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        return client.get(url, params=params)


def get_json(
    provider: str,
    url: str,
    params: QueryParams = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Any:
    """GET and parse JSON, translating every failure into `ProviderUnavailableError`."""
    try:
        response = _get(url, params, timeout)
    except Exception as exc:
        raise ProviderUnavailableError(provider, f"request failed: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise ProviderUnavailableError(
            provider, f"HTTP {response.status_code} from {response.url.host}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderUnavailableError(provider, "response was not valid JSON") from exc

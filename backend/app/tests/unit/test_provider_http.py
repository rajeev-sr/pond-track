"""The shared GET helper: how many times it tries, and what a failure becomes."""

from __future__ import annotations

import httpx
import pytest

from app.providers import base
from app.providers.base import ProviderUnavailableError


def hanging(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every GET times out, as a connection from two of the lab systems does."""
    calls: list[int] = []

    def get(self: object, *_a: object, **_k: object) -> None:
        calls.append(1)
        raise httpx.ConnectTimeout("hung")

    monkeypatch.setattr(httpx.Client, "get", get)
    return calls


def test_a_timeout_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = hanging(monkeypatch)
    with pytest.raises(ProviderUnavailableError, match="ConnectTimeout"):
        base.get_json("svc", "https://example.invalid/x", timeout=0.1)
    assert len(calls) == base.MAX_ATTEMPTS


def test_a_probe_tries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its question is whether the service answers at all."""
    calls = hanging(monkeypatch)
    with pytest.raises(ProviderUnavailableError, match="request failed: ConnectTimeout"):
        base.get_json("svc", "https://example.invalid/x", timeout=0.1, attempts=1)
    assert len(calls) == 1

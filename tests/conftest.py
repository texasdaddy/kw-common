"""Shared fixtures, and the two properties every test in this suite must have.

1. **No test reaches the network.** A test that forgets to replace the channels would otherwise
   make a real DNS lookup and a real connection attempt — which passes or fails depending on where
   it runs, takes the resolver timeout with it, and (worse) turns an assertion about SUPPRESSION
   into an assertion about connectivity. That is not a hypothetical: it is exactly what happened
   the first time this suite ran, and nine tests reported `"failed"` where they meant
   `"suppressed"`. The block below makes the mistake impossible rather than remembered.
2. **No test leaks the process-wide default** installed by `configure()` into the next one.
"""

from __future__ import annotations

import smtplib
import urllib.request

import pytest

from kw_common import alerting


class NetworkAccessInTests(RuntimeError):
    """Raised by the block below. Its own type so a test cannot pass by catching `Exception`."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a real send impossible for every test in this suite.

    Deliberately raises rather than returning a stub: a stub that answers whatever it is asked
    would let a test "pass" while proving nothing about what was actually sent.
    """
    def refuse(*args: object, **kwargs: object) -> object:
        raise NetworkAccessInTests(
            "a test tried to open a real connection — replace the channels with spies "
            "(see the `channels` fixture) instead of letting one reach the network")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture(autouse=True)
def _reset_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`configure()` installs a process-wide default; no test may leave one behind."""
    monkeypatch.setattr(alerting, "_default", None)

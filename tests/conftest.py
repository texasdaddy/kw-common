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
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a real send impossible for every test in this suite.

    Deliberately raises rather than returning a stub: a stub that answers whatever it is asked
    would let a test "pass" while proving nothing about what was actually sent.

    ⭐ `OpenerDirector.open` IS BLOCKED, NOT ONLY `urlopen` — and that gap was live, measured.
    `urlopen` is one opener among many; the ntfy channel now sends through a module-level opener
    of its own (the one that refuses redirects), so patching `urlopen` stopped covering the path
    that actually sends. Three tests walked straight past this fixture into a real DNS lookup —
    precisely the failure the module docstring above describes, one release later. Blocking the
    CLASS method covers every opener, including one a future change builds. A test may still
    shadow `open` on its own opener INSTANCE, which is what a spy does.

    `@pytest.mark.allow_loopback` narrows the block instead of lifting it, and the difference is
    the point. Exactly one kind of test needs the opt-out — the redirect refusal cannot be proved
    against a stub, because what is under test is what `urllib`'s own handler stack does with a
    real `3xx` — and an opt-out that simply returned would have been the first way for a test to
    walk out of the property this file's docstring calls "impossible rather than remembered", for
    ANY host. So under the marker `smtplib` stays blocked outright and the opener is allowed
    through ONLY to `127.0.0.1`, which is what the marker's name promises.
    """
    def refuse(*args: object, **kwargs: object) -> object:
        raise NetworkAccessInTests(
            "a test tried to open a real connection — replace the channels with spies "
            "(see the `channels` fixture) instead of letting one reach the network")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)

    if not request.node.get_closest_marker("allow_loopback"):
        monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
        return

    real_open = urllib.request.OpenerDirector.open

    def loopback_only(self: object, fullurl: object, *args: object, **kwargs: object) -> object:
        url = getattr(fullurl, "full_url", fullurl)
        if not str(url).startswith(("http://127.0.0.1:", "http://127.0.0.1/")):
            raise NetworkAccessInTests(
                f"@pytest.mark.allow_loopback permits 127.0.0.1 and nothing else; this test "
                f"tried to reach {str(url)[:60]!r}")
        return real_open(self, fullurl, *args, **kwargs)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", loopback_only)


@pytest.fixture(autouse=True)
def _reset_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`configure()` installs a process-wide default; no test may leave one behind."""
    monkeypatch.setattr(alerting, "_default", None)

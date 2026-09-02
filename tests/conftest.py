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
from urllib.parse import urlsplit

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
    ANY host. So under the marker `smtplib` stays blocked and the opener is allowed through only
    to a loopback host.

    ⚠️ WHAT THIS DOES NOT COVER, stated because a guard that implies coverage it lacks is worse
    than none. All three were measured:

    * **Only the opener layer.** A test that calls `http.client`, `socket.create_connection` or
      `socket.getaddrinfo` directly reaches the real stack. That is deliberate — those are what
      `_post_ntfy`'s loopback tests need underneath them — but it means this is not a sandbox.
    * **`smtplib` is blocked by NAME.** `smtplib.SMTP` and `SMTP_SSL` are replaced, so the shipped
      module (which uses `smtplib.SMTP`) cannot connect; a test that did
      `from smtplib import SMTP` at import time would hold the real class and escape.
    * **A configured proxy defeats the loopback check.** `ProxyHandler` rewrites the destination
      INSIDE `open()`, after this wrapper has judged the URL, and `Request.set_proxy` leaves
      `full_url` untouched — so on a machine with `http_proxy` set, a "loopback" request leaves
      the machine. The two tests that use this marker patch `urllib.request.proxy_bypass` for
      exactly that reason (see `_direct_to_loopback`); the fixture alone does not guarantee it.
    """
    def refuse(*args: object, **kwargs: object) -> object:
        raise NetworkAccessInTests(
            "a test tried to open a real connection — replace the channels with spies "
            "(see the `channels` fixture) instead of letting one reach the network")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    # SMTP_SSL too: it SUBCLASSES SMTP, so replacing only `SMTP` left `SMTP_SSL(...)` returning a
    # half-built object with `sock=None` rather than raising — the "stub that answers whatever it
    # is asked" this fixture's second paragraph exists to refuse.
    monkeypatch.setattr(smtplib, "SMTP_SSL", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)

    if not request.node.get_closest_marker("allow_loopback"):
        monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
        return

    real_open = urllib.request.OpenerDirector.open

    def loopback_only(self: object, fullurl: object, *args: object, **kwargs: object) -> object:
        # By HOST, not by a literal prefix. The prefix form permitted exactly one spelling of one
        # address over one scheme — refusing `http://127.0.0.1` with no port, `https://`,
        # `localhost`, `[::1]` and every other 127/8 address, any of which a later test could
        # legitimately want.
        url = str(getattr(fullurl, "full_url", fullurl))
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            host = ""
        if host not in {"localhost", "::1"} and not host.startswith("127."):
            raise NetworkAccessInTests(
                f"@pytest.mark.allow_loopback permits a loopback host and nothing else; this "
                f"test tried to reach {url[:60]!r}")
        return real_open(self, fullurl, *args, **kwargs)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", loopback_only)


@pytest.fixture(autouse=True)
def _reset_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`configure()` installs a process-wide default; no test may leave one behind."""
    monkeypatch.setattr(alerting, "_default", None)

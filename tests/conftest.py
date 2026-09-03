"""Shared fixtures, and the three properties every test in this suite must have.

1. **No test reaches the network.** A test that forgets to replace the channels would otherwise
   make a real DNS lookup and a real connection attempt — which passes or fails depending on where
   it runs, takes the resolver timeout with it, and (worse) turns an assertion about SUPPRESSION
   into an assertion about connectivity. That is not a hypothetical: it is exactly what happened
   the first time this suite ran, and nine tests reported `"failed"` where they meant
   `"suppressed"`. The block below makes the mistake impossible rather than remembered.
2. **No test leaks the process-wide default** installed by `configure()` into the next one.
3. **No test leaves a leak-guard configuration installed.** `leakguard.apply_config` rebinds
   module-level names, which is what lets the engine stay one file rather than threading a config
   object through forty functions — and the cost of that choice is exactly this: a test that
   applies a config and does not put it back changes what a LATER test measures, silently and in
   the direction that makes a guard look weaker than it is.
"""

from __future__ import annotations

import ipaddress
import smtplib
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from kw_common import alerting, leakguard


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
    * **Everything is blocked by NAME, so an import-time ALIAS escapes.** A test module doing
      `from smtplib import SMTP` or `from urllib.request import urlopen` at import time holds the
      real object and walks past this. The shipped module uses the attribute spellings, so it
      cannot connect; a future test that aliases can. Same for an `OpenerDirector` SUBCLASS that
      overrides `open` — the patch is on the base class.
    * **A configured proxy defeats the loopback check.** `ProxyHandler` rewrites the destination
      INSIDE `open()`, after this wrapper has judged the URL, and `Request.set_proxy` leaves
      `full_url` untouched — so on a machine with `http_proxy` set, a "loopback" request leaves
      the machine. The two tests that use this marker patch `urllib.request.proxy_bypass` for
      exactly that reason (see `_direct_to_loopback`); the fixture alone does not guarantee it.
    * **`pytestmark = pytest.mark.allow_loopback` at MODULE scope narrows the block for the whole
      file.** `get_closest_marker` honours module-level marks. That is one line, so put the marker
      on the individual test that needs it.
    """
    def refuse(*args: object, **kwargs: object) -> object:
        raise NetworkAccessInTests(
            "a test tried to open a real connection — replace the channels with spies "
            "(see the `channels` fixture) instead of letting one reach the network")

    # ⭐ EVERY `SMTP` SUBCLASS, FOUND BY WALKING THE MODULE — not a hand-written list of names.
    # The list started as `SMTP`, which left `SMTP_SSL(...)` RETURNING a half-built object with
    # `sock=None` instead of raising (the "stub that answers whatever it is asked" the second
    # paragraph refuses). Adding `SMTP_SSL` by name then left `LMTP`, a third subclass, doing the
    # same thing — fixing the instance and not the class, one round apart. This asks the module
    # which of its names are SMTP classes, so a fourth cannot appear behind the guard's back.
    #
    # ⚠️ NO `assert` HERE, and that is deliberate. A sanity check on what the sweep found belongs
    # in a TEST, not in an autouse fixture: `smtplib` defines `SMTP_SSL` only under
    # `if _have_ssl:`, so on a build without ssl — or after any upstream rename — an assertion
    # here ERRORS EVERY TEST IN THE SUITE, turning a one-name upstream change into 260 failures
    # that read like a broken harness. `test_no_network.py::test_the_block_refuses_every_smtp_class`
    # names all three and fails as ONE legible test instead.
    #
    # ⛔ THE NAMES ARE COLLECTED BEFORE ANY PATCHING, and the two steps must not interleave:
    # patching `smtplib.SMTP` replaces it with a FUNCTION, so the next `issubclass(obj,
    # smtplib.SMTP)` raises `TypeError: issubclass() arg 2 must be a class`. Measured — written
    # the interleaved way it errored all 262 tests at once.
    real_smtp = smtplib.SMTP
    smtp_classes = [name for name, obj in vars(smtplib).items()
                    if isinstance(obj, type) and issubclass(obj, real_smtp)]
    for name in smtp_classes:
        monkeypatch.setattr(smtplib, name, refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)

    if not request.node.get_closest_marker("allow_loopback"):
        monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
        return

    real_open = urllib.request.OpenerDirector.open

    def loopback_only(self: object, fullurl: object, *args: object, **kwargs: object) -> object:
        # ⭐ PARSED AS AN ADDRESS, not matched as text. A literal prefix permitted exactly one
        # spelling of one address over one scheme; widening it to `startswith("127.")` then
        # permitted `127.evil.example.com` — a DNS NAME, resolved off this machine — because a
        # string prefix on a hostname is not a loopback test. `ipaddress` answers the actual
        # question, and covers `::1`, `127.0.0.2` and every other 127/8 address for free.
        url = str(getattr(fullurl, "full_url", fullurl))
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            host = ""
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            # Not an IP literal. `localhost` is the one NAME allowed, because RFC 6761 reserves
            # it — any other name would have to be resolved to be judged, and resolving it is
            # already the thing this block exists to prevent.
            loopback = host == "localhost"
        if not loopback:
            raise NetworkAccessInTests(
                f"@pytest.mark.allow_loopback permits a loopback address (or `localhost`) and "
                f"nothing else; this test tried to reach {url[:60]!r}")
        return real_open(self, fullurl, *args, **kwargs)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", loopback_only)


@pytest.fixture(autouse=True)
def _reset_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`configure()` installs a process-wide default; no test may leave one behind."""
    monkeypatch.setattr(alerting, "_default", None)


# The repository (or unpacked sdist) this suite is running from. `parents[1]` because the tests
# live one level down in both layouts, which is what `MANIFEST.in` guarantees for the sdist.
REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_CONFIG = REPO_ROOT / leakguard.CONFIG_FILENAME


@pytest.fixture(autouse=True)
def _leakguard_config_is_restored() -> object:
    """Put the guard's live allowances back, and FAIL a test that walked off with them.

    ⛔ RESTORING SILENTLY WOULD HIDE THE BUG. A test that applies a config and forgets to undo it
    is not a tidiness problem: the next test measures a guard configured by an unrelated one, and
    the failure surfaces as an unreproducible verdict in a file that never mentions config at all.
    So this both restores the default AND says which test left it changed.
    """
    before = (leakguard.ALLOW_LITERALS, leakguard.PATH_EXEMPT)
    yield
    after = (leakguard.ALLOW_LITERALS, leakguard.PATH_EXEMPT)
    if after != before:
        leakguard.apply_config(leakguard.DEFAULT_CONFIG)
        pytest.fail(
            f"this test left a leak-guard configuration installed: {before!r} -> {after!r}. "
            f"Use the `injected_literals` fixture, or call "
            f"`leakguard.apply_config(leakguard.DEFAULT_CONFIG)` when you are done.")


@pytest.fixture
def injected_literals() -> object:
    """This repository's OWN `.leakguard.json`, applied — the worked example, not a mock.

    ⭐ THE POINT OF USING THE REAL FILE. The allowances a test measures are then the allowances
    this repository actually scans under, so a config that stopped being honoured, or stopped
    parsing, fails a test here rather than being discovered by a consumer. A fixture that built a
    `GuardConfig` inline would pass just as happily with the file deleted.
    """
    assert REPO_CONFIG.is_file(), (
        f"{REPO_CONFIG} is missing. It is this repository's injected leak-guard configuration and "
        f"an argument for these tests; `MANIFEST.in` ships it in the sdist for that reason.")
    config = leakguard.load_config(REPO_CONFIG)
    assert config.allow_literals, (
        f"{REPO_CONFIG} declares no allow_literals, so every test using this fixture is vacuous")
    # ⛔ AND IT MUST DECLARE NO PATH EXEMPTIONS. The first version of this file excused all eight
    # patterns on the engine's own path — a whole-file skip written in data, through which a REAL
    # leak appended to that file passed. The guard recognises its own source by its BYTES now, so
    # there is nothing left for a path exemption here to do, and an entry reappearing would mean
    # the identity test has stopped working and somebody papered over it.
    assert not config.path_exempt, (
        f"{REPO_CONFIG} declares path_exempt entries. This repository needs none: the guard "
        f"recognises its own source by content. An entry here is a whole-file skip in disguise.")
    leakguard.apply_config(config)
    yield config
    leakguard.apply_config(leakguard.DEFAULT_CONFIG)

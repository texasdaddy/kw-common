"""The suite's own network block, tested.

⭐⭐ WHY THIS FILE EXISTS. `tests/conftest.py::_no_network` is a guard, and it had no test at all —
so a verification round mutated it four ways and the ENTIRE suite stayed green every time:
lifting the block completely under `@pytest.mark.allow_loopback` (which is exactly the defect the
narrowing was written to close), deleting the `smtplib` block, deleting the `urlopen` block, and
permitting any host under the marker.

That is the shape this repository keeps relearning: a guard whose REJECTING path nobody runs. The
tests below run it, in both directions — the forbidden thing must be refused, and the permitted
thing must get through — because "the block returns without raising" is equally true of a working
block and of one that has been switched off.

The assertions here are about the TEST HARNESS, not about `kw_common`. They belong with it: a
suite whose no-network property silently lapses reports connectivity failures as behaviour
failures, which is precisely what happened once already and cost nine misread results.
"""

from __future__ import annotations

import smtplib
import urllib.error
import urllib.request

import pytest

from conftest import NetworkAccessInTests

# An address that must never be reachable. RFC 5737 documentation range: it is reserved for
# exactly this, it is not routed, and it is in the leak guard's allowed vocabulary. A literal IP,
# so no DNS is involved and a hijacked resolver cannot make any test here pass for a wrong reason.
NOT_LOOPBACK = "http://203.0.113.10:9/topic"

# ⭐ HOSTS THAT LOOK LOOPBACK-ISH AND ARE NOT. Every one of these is a DNS name resolved off this
# machine, and each defeats a string-matching form of the check that a previous version used:
#   `startswith("127.")`  -> `127.evil.example` and `127.0.0.1.nip.io` both pass
#   `"127" in host`        -> so does `evil127.example`
#   `"localhost" in host`  -> so does `localhost.evil.example`
# Two such widenings survived the whole suite, because no test ever asked the predicate about a
# non-loopback host that satisfies them. These are that test.
LOOPBACK_LOOKALIKES = [
    "http://127.evil.example:9/topic",
    "http://127.0.0.1.evil.example:9/topic",
    "http://evil127.example:9/topic",
    "http://localhost.evil.example:9/topic",
    "http://not-localhost.example:9/topic",
]

# The timeout matters only if the block REGRESSES: without it a leaked open() dials an unroutable
# address on a socket with no deadline, and the per-test ceiling is the only thing that ends it.
DIAL_TIMEOUT_S = 5


def test_the_block_refuses_an_opener() -> None:
    with pytest.raises(NetworkAccessInTests):
        urllib.request.build_opener().open(NOT_LOOPBACK, timeout=DIAL_TIMEOUT_S)


def test_the_block_refuses_urlopen() -> None:
    """⚠️ MEASURED REDUNDANCY, recorded rather than hidden: OUTSIDE the marker this assertion is
    satisfied by the `OpenerDirector.open` patch alone — `urlopen` builds an opener and calls it —
    so deleting the `urlopen` patch survives THIS test. The patch is load-bearing only under the
    marker, and that is what `test_the_marker_keeps_smtplib_and_urlopen_blocked` pins."""
    with pytest.raises(NetworkAccessInTests):
        urllib.request.urlopen(NOT_LOOPBACK, timeout=DIAL_TIMEOUT_S)


def test_the_block_refuses_every_smtp_class() -> None:
    """⭐ ALL THREE, and the third is why the fixture stopped using a hand-written list.

    `SMTP_SSL` and `LMTP` both subclass `SMTP`, so replacing only `SMTP` left each of them
    RETURNING a half-built object with `sock=None` instead of raising — a stub that answers
    whatever it is asked, which proves nothing. `SMTP_SSL` was added by name; `LMTP` was then
    found doing exactly the same thing one round later. Fixing the instance and not the class,
    twice, so the fixture now walks `smtplib` for subclasses.
    """
    for factory, port in ((smtplib.SMTP, 587), (smtplib.SMTP_SSL, 465), (smtplib.LMTP, 24)):
        with pytest.raises(NetworkAccessInTests):
            factory("smtp.example.com", port)


def test_the_block_refuses_an_opener_built_before_the_fixture_ran() -> None:
    """The block patches the CLASS, so an opener a module built at import time is covered too —
    which is the whole reason it is not patched per-instance."""
    opener = urllib.request.build_opener()
    with pytest.raises(NetworkAccessInTests):
        opener.open(NOT_LOOPBACK, timeout=DIAL_TIMEOUT_S)


@pytest.mark.allow_loopback
def test_the_marker_narrows_the_block_rather_than_lifting_it() -> None:
    """⭐ THE MUTATION THAT SURVIVED. Reverting `allow_loopback` to `return` early — the exact
    round-1 defect — passes every other test in this suite. It does not pass this one."""
    with pytest.raises(NetworkAccessInTests):
        urllib.request.build_opener().open(NOT_LOOPBACK, timeout=DIAL_TIMEOUT_S)


@pytest.mark.allow_loopback
def test_the_marker_keeps_smtplib_and_urlopen_blocked() -> None:
    """⭐ THE `urlopen` PATCH, PINNED WHERE IT IS ACTUALLY LOAD-BEARING — at a LOOPBACK url.

    Against a non-loopback address this proves nothing: the narrowed opener refuses that anyway.
    Against `127.0.0.1` the opener WOULD allow it, so only the `urlopen` patch stands in the way,
    and deleting that patch reddens exactly here. The distinction matters because the marker is
    an opt-out for the ONE opener the module under test sends through — never for `urlopen`,
    which nothing in this library uses.
    """
    with pytest.raises(NetworkAccessInTests):
        smtplib.SMTP("smtp.example.com", 587)
    with pytest.raises(NetworkAccessInTests):
        urllib.request.urlopen("http://127.0.0.1:9/topic", timeout=DIAL_TIMEOUT_S)


@pytest.mark.allow_loopback
def test_the_marker_still_refuses_a_host_that_merely_LOOKS_like_loopback() -> None:
    """⭐⭐ THE REFUSING SIDE OF THE HOST PREDICATE, which nothing pinned.

    Every URL here is a DNS name that would be resolved off this machine, and each one defeats a
    text-matching form of the check. Two such widenings — `"127" in host` and
    `"localhost" in host` — survived the entire suite, because every test only ever asked the
    predicate about real loopback addresses and one obviously-remote one. A guard is only proven
    by the case it must REJECT.
    """
    for url in LOOPBACK_LOOKALIKES:
        with pytest.raises(NetworkAccessInTests):
            urllib.request.build_opener().open(url, timeout=DIAL_TIMEOUT_S)


@pytest.mark.allow_loopback
def test_the_marker_lets_a_loopback_url_through_to_the_real_stack() -> None:
    """⭐ THE NEGATIVE DIRECTION, and without it every assertion above is satisfied by a block
    that refuses everything — which would make the two redirect tests unrunnable.

    Port 9 (discard) with nothing listening: reaching the real stack means a CONNECTION error,
    which is the proof.

    ⚠️ The assertion is on the TYPE ACTUALLY RAISED, not `pytest.raises(URLError)` plus an
    `isinstance` check inside it. That form was dead code: `NetworkAccessInTests` subclasses
    `RuntimeError`, not `URLError`, so `pytest.raises(URLError)` could never capture it and the
    inner assertion could never run. The test did still fail when the fixture refused — the
    exception escaped uncaught — but by a different mechanism than the line claimed.
    """
    for url in ("http://127.0.0.1:9/topic", "http://localhost:9/topic",
                "http://127.0.0.2:9/topic"):
        try:
            urllib.request.build_opener().open(url, timeout=DIAL_TIMEOUT_S)
        except NetworkAccessInTests as refused:
            pytest.fail(f"the fixture refused {url}, a loopback address it is named for: "
                        f"{refused}")
        except urllib.error.URLError:
            continue  # reached the real stack and nothing was listening — the proof
        pytest.fail(f"{url} unexpectedly SUCCEEDED; something is listening on port 9")

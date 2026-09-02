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
# exactly this, it is not routed, and it is in the leak guard's allowed vocabulary.
NOT_LOOPBACK = "http://203.0.113.10:9/topic"


def test_the_block_refuses_an_opener() -> None:
    with pytest.raises(NetworkAccessInTests):
        urllib.request.build_opener().open(NOT_LOOPBACK)


def test_the_block_refuses_urlopen() -> None:
    """⚠️ MEASURED REDUNDANCY, recorded rather than hidden: OUTSIDE the marker this assertion is
    satisfied by the `OpenerDirector.open` patch alone — `urlopen` builds an opener and calls it —
    so deleting the `urlopen` patch survives THIS test. The patch is load-bearing only under the
    marker, and that is what `test_the_marker_keeps_smtplib_and_urlopen_blocked` pins."""
    with pytest.raises(NetworkAccessInTests):
        urllib.request.urlopen(NOT_LOOPBACK)


def test_the_block_refuses_smtplib() -> None:
    """Both classes. `SMTP_SSL` subclasses `SMTP`, so replacing only `SMTP` left `SMTP_SSL(...)`
    RETURNING a half-built object instead of raising — a stub that answers whatever it is asked,
    which proves nothing."""
    with pytest.raises(NetworkAccessInTests):
        smtplib.SMTP("smtp.example.com", 587)
    with pytest.raises(NetworkAccessInTests):
        smtplib.SMTP_SSL("smtp.example.com", 465)


def test_the_block_refuses_an_opener_built_before_the_fixture_ran() -> None:
    """The block patches the CLASS, so an opener a module built at import time is covered too —
    which is the whole reason it is not patched per-instance."""
    opener = urllib.request.build_opener()
    with pytest.raises(NetworkAccessInTests):
        opener.open(NOT_LOOPBACK)


@pytest.mark.allow_loopback
def test_the_marker_narrows_the_block_rather_than_lifting_it() -> None:
    """⭐ THE MUTATION THAT SURVIVED. Reverting `allow_loopback` to `return` early — the exact
    round-1 defect — passes every other test in this suite. It does not pass this one."""
    with pytest.raises(NetworkAccessInTests):
        urllib.request.build_opener().open(NOT_LOOPBACK)


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
        urllib.request.urlopen("http://127.0.0.1:9/topic")


@pytest.mark.allow_loopback
def test_the_marker_lets_a_loopback_url_through_to_the_real_stack() -> None:
    """⭐ THE NEGATIVE DIRECTION, and without it every assertion above is satisfied by a block
    that refuses everything — which would make the two redirect tests unrunnable.

    Port 9 (discard) with nothing listening: reaching the real stack means a CONNECTION error,
    which is the proof. `NetworkAccessInTests` here would mean the narrowing refuses what its own
    name permits.
    """
    for url in ("http://127.0.0.1:9/topic", "http://localhost:9/topic",
                "http://127.0.0.2:9/topic"):
        with pytest.raises(urllib.error.URLError) as raised:
            urllib.request.build_opener().open(url, timeout=5)
        assert not isinstance(raised.value, NetworkAccessInTests), (
            f"the fixture refused {url}, which is a loopback address it is named for")

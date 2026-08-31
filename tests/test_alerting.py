"""Tests for `kw_common.alerting`.

These travel WITH the module, deliberately. A behaviour that is not tested here becomes six
untested copies the moment it ships — which is the duplication this library exists to end.

Nothing here touches the network or a real SMTP server: the channels are replaced with spies that
RECORD what they were asked, and the assertions read the recording. A spy that answers whatever it
is asked, or a test that asserts only that a call happened, proves nothing.
"""

from __future__ import annotations

import json
import logging
import ssl
import subprocess
import sys
import urllib.error
from pathlib import Path
from typing import ClassVar

import pytest

from kw_common import alerting
from kw_common.alerting import (
    ERROR,
    MAX_SMTP_PORT,
    MIN_SMTP_PORT,
    OK,
    WARN,
    AlertConfig,
    Alerter,
    AlertSettings,
    smtp_port_fault,
)


# --------------------------------------------------------------------------- fixtures / helpers
@pytest.fixture
def settings(tmp_path: Path) -> AlertSettings:
    """A fully-configured service. Every path is inside `tmp_path`; nothing is assumed."""
    return AlertSettings(
        service="svc",
        config_file=str(tmp_path / "alerting.env"),
        ntfy_url="https://ntfy.example.com/svc",
        state_file=str(tmp_path / "state.json"),
        error_log=str(tmp_path / "logs" / "svc-errors.log"),
    )


@pytest.fixture
def alerter(settings: AlertSettings) -> Alerter:
    return Alerter(settings)


class Spy:
    """Records every (config, spec, title, message) it is handed. Never raises by itself.

    It RECORDS rather than asserts: an assertion raised in here would be swallowed by the
    `except Exception` that wraps each channel, so the test would pass whether or not the guard
    under test exists. The assertions live in the test, reading `self.calls`.
    """

    def __init__(self, fail: BaseException | None = None) -> None:
        self.calls: list[tuple[AlertConfig, alerting.SeveritySpec, str, str]] = []
        self.fail = fail

    def __call__(self, cfg: AlertConfig, spec: alerting.SeveritySpec, title: str,
                 message: str) -> None:
        self.calls.append((cfg, spec, title, message))
        if self.fail is not None:
            raise self.fail


@pytest.fixture(autouse=True)
def channels(monkeypatch: pytest.MonkeyPatch) -> dict[str, Spy]:
    """Replace both channels with spies, keeping the registry shape the code reads.

    ⭐ AUTOUSE. A test that merely forgot to ask for this would otherwise attempt a real send, and
    the failure is not a loud one: `_dispatch` catches it and reports `"failed"`, so a test
    asserting `"suppressed"` fails for a reason that has nothing to do with de-duplication. Nine
    tests here did exactly that before this was autouse. `conftest.py` blocks the network as well;
    this makes the intended object available to every test whether or not it names it.
    """
    spies = {"email": Spy(), "ntfy": Spy()}
    monkeypatch.setattr(alerting, "_CHANNELS",
                        tuple((name, spies[name]) for name in ("email", "ntfy")))
    return spies


def write_email_config(settings: AlertSettings, **overrides: str) -> None:
    values = {
        "EMAIL_TO": "ops@example.com",
        "EMAIL_FROM": "svc@example.com",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_USER": "svc@example.com",
        "SMTP_PASSWORD": "not-a-real-password",
    }
    values.update(overrides)
    assert settings.config_file is not None
    Path(settings.config_file).write_text(
        "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")


# ================================================================= the config-file line boundary
# ⭐ THE TWO HALVES OF THE PARSER FIX. `_parse_env_file` opens the file with `newline=""` AND
# splits on `"\n"`. Either half alone leaves a live silent-truncation bug, so both directions are
# pinned below and neither test can be satisfied by the other's fix.

# Every character `str.splitlines()` treats as a line boundary but a KEY=VALUE file does not.
# `\n` is deliberately absent — it IS a real separator and has its own test below.
LINE_BOUNDARY_CHARS = [
    pytest.param("\r", id="CR"),
    pytest.param("\v", id="VT"),
    pytest.param("\f", id="FF"),
    pytest.param("\x1c", id="FS"),
    pytest.param("\x1d", id="GS"),
    pytest.param("\x1e", id="RS"),
    pytest.param("\x85", id="NEL"),
    pytest.param(" ", id="LINE-SEPARATOR"),
    pytest.param(" ", id="PARAGRAPH-SEPARATOR"),
]


@pytest.mark.parametrize("boundary", LINE_BOUNDARY_CHARS)
def test_a_unicode_line_boundary_never_truncates_a_setting(tmp_path: Path, boundary: str) -> None:
    """A character only `splitlines()` calls a line break must stay INSIDE the value.

    ⭐ WRITTEN WITH `write_bytes`, NOT `write_text`. The text writer applies newline translation,
    which rewrites a lone `\\r` before it ever reaches the file — silently turning the CR case
    (the one the `newline=""` half of the fix exists for) into a test of something else entirely.
    """
    path = tmp_path / "alerting.env"
    value = f"a@example.com{boundary}b@example.invalid"
    path.write_bytes(f"EMAIL_TO={value}\n".encode())

    parsed = alerting._parse_env_file(str(path))

    assert parsed["EMAIL_TO"] == value, (
        f"{boundary!r} was treated as a line boundary, so the value was truncated to "
        f"{parsed['EMAIL_TO']!r} and a recipient was silently dropped"
    )


def test_a_real_newline_still_separates_settings_including_crlf(tmp_path: Path) -> None:
    """The NEGATIVE direction: `\\n` and `\\r\\n` must still end a line.

    Without this, a parser that never splits at all satisfies the test above. With it, the only
    implementation that passes both is the one that splits on `\\n` and strips the `\\r` a CRLF
    file leaves behind.
    """
    path = tmp_path / "alerting.env"
    path.write_bytes(b"EMAIL_TO=a@example.com\nEMAIL_FROM=b@example.com\r\n"
                     b"SMTP_HOST=smtp.example.com\n")

    parsed = alerting._parse_env_file(str(path))

    assert parsed["EMAIL_TO"] == "a@example.com"
    assert parsed["EMAIL_FROM"] == "b@example.com", "the CR of a CRLF line ending survived"
    assert parsed["SMTP_HOST"] == "smtp.example.com"


def test_a_cr_only_file_is_the_documented_dual(tmp_path: Path) -> None:
    """Classic-Mac line endings parse as ONE line. Known, accepted, and pinned so it stays a
    DECISION rather than becoming a surprise — translating a lone CR *is* the bug being fixed."""
    path = tmp_path / "alerting.env"
    path.write_bytes(b"EMAIL_TO=a@example.com\rEMAIL_FROM=b@example.com\r")

    parsed = alerting._parse_env_file(str(path))

    assert list(parsed) == ["EMAIL_TO"]
    assert parsed["EMAIL_TO"] == "a@example.com\rEMAIL_FROM=b@example.com"


def test_the_parser_tolerates_the_shapes_an_operator_hand_writes(tmp_path: Path) -> None:
    path = tmp_path / "alerting.env"
    path.write_text(
        "# a comment\n"
        "\n"
        "export EMAIL_TO='ops@example.com'\n"
        '  SMTP_HOST = "smtp.example.com"  \n'
        "not a setting line\n"
        "SMTP_PORT=587\n",
        encoding="utf-8")

    parsed = alerting._parse_env_file(str(path))

    assert parsed == {"EMAIL_TO": "ops@example.com", "SMTP_HOST": "smtp.example.com",
                      "SMTP_PORT": "587"}


def test_a_missing_config_file_is_not_an_error(tmp_path: Path) -> None:
    assert alerting._parse_env_file(str(tmp_path / "nope.env")) == {}


def test_an_undecodable_config_file_disables_email_without_raising(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "alerting.env"
    path.write_bytes(b"EMAIL_TO=\xff\xfe\x00not-utf-8\n")

    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert alerting._parse_env_file(str(path)) == {}
    assert "unreadable" in caplog.text


# ============================================================================== recipient parsing
@pytest.mark.parametrize(("raw", "expected"), [
    ("ops@example.com", "ops@example.com"),
    ("a@example.com;b@example.com", "a@example.com, b@example.com"),
    ("a@example.com , ; b@example.com", "a@example.com, b@example.com"),
    ("a@example.com,,b@example.com", "a@example.com, b@example.com"),
    (";", ""),
    ("  ", ""),
    ('"Doe, Jane" <j@example.com>', '"Doe, Jane" <j@example.com>'),
])
def test_recipients_normalise_to_what_smtplib_can_parse(raw: str, expected: str) -> None:
    assert alerting._recipients(raw) == expected


# ================================================================================ the SMTP port
@pytest.mark.parametrize("raw", ["587", "25", str(MIN_SMTP_PORT), str(MAX_SMTP_PORT), "  465  "])
def test_a_usable_port_has_no_fault(raw: str) -> None:
    assert smtp_port_fault(raw) == ""


@pytest.mark.parametrize("raw", ["", "   "])
def test_a_blank_port_is_not_a_fault(raw: str) -> None:
    """That is how a container template passes an unset optional setting."""
    assert smtp_port_fault(raw) == ""


@pytest.mark.parametrize(("raw", "why"), [
    ("not-a-port", "is not a number"),
    ("0", "outside the usable range"),
    ("-1", "outside the usable range"),
    ("65536", "outside the usable range"),
    ("1" + "0" * 400, "outside the usable range"),
])
def test_a_rejected_port_says_why_without_echoing_the_value(raw: str, why: str) -> None:
    fault = smtp_port_fault(raw)
    assert why in fault
    assert f"its {len(raw.strip())}-character value" in fault
    assert raw not in fault, "the rejected value was echoed — it may be a pasted password"


def test_a_400_digit_port_does_not_raise_inside_the_guard() -> None:
    """`int()` accepts it happily; converting it to a float raises `OverflowError` INSIDE the
    guard meant to stop a bad setting breaking anything. The comparison must stay exact."""
    assert "outside the usable range" in smtp_port_fault("1" + "0" * 400)


def test_smtp_port_falls_back_and_complains(caplog: pytest.LogCaptureFixture) -> None:
    cfg = AlertConfig(email={"SMTP_PORT": "65536"})
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert cfg.smtp_port() == alerting.DEFAULT_SMTP_PORT
    assert "SMTP_PORT" in caplog.text
    assert "65536" not in caplog.text


def test_smtp_port_falls_back_silently_for_a_blank_value(
        caplog: pytest.LogCaptureFixture) -> None:
    cfg = AlertConfig(email={"SMTP_PORT": "   "})
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert cfg.smtp_port() == alerting.DEFAULT_SMTP_PORT
    assert caplog.text == ""


def test_setting_faults_reports_the_same_sentence_the_send_path_logs() -> None:
    """The point of the pure classifier: a boot report and the send-time log line cannot drift
    into describing the same fault two different ways."""
    cfg = AlertConfig(email={"SMTP_PORT": "70000"})
    assert cfg.setting_faults() == [smtp_port_fault("70000")]


def test_setting_faults_is_empty_when_nothing_was_rejected() -> None:
    assert AlertConfig(email={"SMTP_PORT": "587"}).setting_faults() == []
    assert AlertConfig(email={"SMTP_PORT": ""}).setting_faults() == []


# ============================================================================ channel readiness
def test_email_ready_needs_every_required_setting(settings: AlertSettings) -> None:
    write_email_config(settings)
    assert AlertConfig.load(settings).email_ready() is True


def test_email_is_not_ready_when_nothing_is_configured(
        settings: AlertSettings, caplog: pytest.LogCaptureFixture) -> None:
    """The `any(...)` shortcut answers "nothing configured" and stays QUIET — that is not a
    misconfiguration to shout about on every send, it is a channel the boot warning reports."""
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert AlertConfig.load(settings).email_ready() is False
    assert caplog.text == ""


def test_a_separator_only_recipient_list_is_a_reported_misconfiguration(
        settings: AlertSettings, caplog: pytest.LogCaptureFixture) -> None:
    """`EMAIL_TO=;` alongside the other four settings must be REPORTED, not passed over."""
    write_email_config(settings, EMAIL_TO=";")
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert AlertConfig.load(settings).email_ready() is False
    assert "EMAIL_TO" in caplog.text
    assert "missing or unusable" in caplog.text


def test_a_file_holding_only_a_separator_recipient_is_reported_not_called_unconfigured(
        settings: AlertSettings, caplog: pytest.LogCaptureFixture) -> None:
    """⭐⭐ THE LOAD-BEARING ORDERING, AND THE ONLY INPUT THAT PINS IT.

    `email_ready()` asks `any(...)` of the RAW values BEFORE normalising `EMAIL_TO`. Swap those
    two steps and this is the file that changes answer — which is why the test above, written
    with all five settings present, could not tell the orderings apart and let the swap survive
    a mutation sweep:

        file = {EMAIL_TO: ";", + the other four}   raw any()=True,  resolved any()=True   (same)
        file = {EMAIL_TO: ";"}  <- this test       raw any()=True,  resolved any()=False  (differs)

    With the RAW ordering the shortcut does not fire, so normalisation runs and the
    misconfiguration is REPORTED by name. With the RESOLVED ordering all five look absent, and
    the function returns False having logged NOTHING — silently reclassifying a real
    misconfiguration as "nothing was configured". Going quiet is this module's one unforgivable
    failure, so the ordering is pinned here rather than merely described in a comment.
    """
    assert settings.config_file is not None
    Path(settings.config_file).write_text("EMAIL_TO=;\n", encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert AlertConfig.load(settings).email_ready() is False

    assert "EMAIL_TO" in caplog.text, (
        "email_ready() went SILENT on a real misconfiguration — the `any()` shortcut is asking "
        "the RESOLVED values instead of the raw ones")
    assert "missing or unusable" in caplog.text


def test_email_readiness_never_names_the_password(
        settings: AlertSettings, caplog: pytest.LogCaptureFixture) -> None:
    """The complaint names the SETTING, never its value.

    ⚠️ The password must be PRESENT for this to mean anything. An earlier version blanked
    `SMTP_PASSWORD` to provoke the complaint and then asserted its value was absent from the log
    — but a blanked password is never written to the config file at all, so that assertion could
    not fail whatever the code did. Here the password is real and a DIFFERENT required setting is
    blanked, so the value is genuinely present in the config the code just read.
    """
    secret = "s3cret-app-password"  # noqa: S105 — a fixture value, and the point of the test
    write_email_config(settings, SMTP_PASSWORD=secret, SMTP_USER="")
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert AlertConfig.load(settings).email_ready() is False

    assert "SMTP_USER" in caplog.text          # the NAME of what is missing
    assert secret not in caplog.text           # never the value of what is not
    # And the password is really in the config the check just read, so the assertion above is
    # answering a question that could have gone the other way.
    assert AlertConfig.load(settings).email["SMTP_PASSWORD"] == secret  # type: ignore[index]


@pytest.mark.parametrize("url", [
    "https://ntfy.example.com/topic",
    "http://ntfy.example.com/topic",
])
def test_a_full_topic_url_is_ready(url: str) -> None:
    assert AlertConfig(ntfy_url=url).ntfy_ready() is True


@pytest.mark.parametrize("url", [
    "",
    "a-bare-topic",
    "ftp://ntfy.example.com/topic",
    "https:///topic",
    "https://ntfy.example.com/to pic",
    "https://ntfy.example.com/\x00topic",
    "https://[::1/topic",
])
def test_an_unusable_ntfy_url_is_not_ready(url: str) -> None:
    """Including the two that RAISE rather than return False in a naive implementation: a control
    character (http.client `InvalidURL`, whose message quotes the topic) and an unclosed IPv6
    bracket (`urlsplit` raises `ValueError`)."""
    assert AlertConfig(ntfy_url=url).ntfy_ready() is False


def test_a_readiness_check_that_raises_disables_only_its_own_channel(
        caplog: pytest.LogCaptureFixture) -> None:
    class Exploding(AlertConfig):
        def ntfy_ready(self) -> bool:
            raise RuntimeError("boom")

    cfg = Exploding(ntfy_url="https://ntfy.example.com/t",
                    email=dict.fromkeys(alerting.EMAIL_KEYS, "x"))
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert cfg.is_ready("ntfy") is False
        assert cfg.is_ready("email") is True
    assert "RuntimeError" in caplog.text


def test_is_ready_answers_false_for_a_channel_that_does_not_exist() -> None:
    """`is_ready` derives the check from the channel NAME, so an unknown one must be refused
    rather than raising the AttributeError its own docstring promises it never will."""
    assert AlertConfig().is_ready("carrier-pigeon") is False


# ================================================================================ notify() basics
def test_notify_sends_to_every_ready_channel(
        alerter: Alerter, settings: AlertSettings, channels: dict[str, Spy]) -> None:
    write_email_config(settings)

    results = alerter.notify(ERROR, "svc: db unreachable", "connection refused")

    assert results == {"email": "sent", "ntfy": "sent"}
    for spy in channels.values():
        assert len(spy.calls) == 1
        _, spec, title, message = spy.calls[0]
        assert spec is alerting.SEVERITIES[ERROR]
        assert (title, message) == ("svc: db unreachable", "connection refused")


def test_an_unconfigured_channel_is_skipped_not_failed(
        alerter: Alerter, channels: dict[str, Spy]) -> None:
    """No email config file was written, so email is unconfigured; ntfy is fine."""
    results = alerter.notify(WARN, "svc: slow", "p95 up")

    assert results == {"email": "skipped", "ntfy": "sent"}
    assert channels["email"].calls == []


def test_one_dead_channel_never_silences_the_other(
        alerter: Alerter, settings: AlertSettings, monkeypatch: pytest.MonkeyPatch) -> None:
    write_email_config(settings)
    email = Spy(fail=OSError("smtp down"))
    ntfy = Spy()
    monkeypatch.setattr(alerting, "_CHANNELS", (("email", email), ("ntfy", ntfy)))

    results = alerter.notify(ERROR, "svc: broken", "...")

    assert results == {"email": "failed", "ntfy": "sent"}
    assert len(ntfy.calls) == 1, "the failing channel took the healthy one down with it"


def test_the_severity_drives_the_priority_and_the_prefix(
        alerter: Alerter, channels: dict[str, Spy]) -> None:
    for severity, priority, prefix in [(OK, "low", "[OK]"), (WARN, "high", "[WARN]"),
                                       (ERROR, "urgent", "[ERROR]")]:
        alerter.notify(severity, f"svc: {severity}", "...")
        _, spec, _, _ = channels["ntfy"].calls[-1]
        assert (spec.ntfy_priority, spec.prefix) == (priority, prefix)


def test_an_unknown_severity_is_loud_and_still_delivered(
        alerter: Alerter, channels: dict[str, Spy], caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        results = alerter.notify("CATASTROPHE", "svc: ?", "...")

    assert results["ntfy"] == "sent"
    assert "unknown alert severity" in caplog.text
    _, spec, _, _ = channels["ntfy"].calls[-1]
    assert spec is alerting.SEVERITIES[ERROR], "an unknown severity must be treated as ERROR"


def test_an_unhashable_severity_does_not_reach_the_caller(
        alerter: Alerter, channels: dict[str, Spy]) -> None:
    """`dict.get` propagates whatever the key's own `__hash__` raises. `notify()` never raises."""
    results = alerter.notify(["not", "hashable"], "svc: ?", "...")  # type: ignore[arg-type]
    assert results["ntfy"] == "sent"


def test_the_log_line_goes_out_even_when_every_channel_is_dead(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """Logging is not paging. The process log stays the complete record."""
    monkeypatch.setattr(alerting, "_CHANNELS",
                        (("email", Spy(fail=OSError("x"))), ("ntfy", Spy(fail=OSError("y")))))
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        alerter.notify(ERROR, "svc: everything is on fire", "details here")
    assert "svc: everything is on fire" in caplog.text
    assert "details here" in caplog.text


def test_a_dispatch_that_fails_before_any_channel_is_reported_as_failed(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(self: Alerter) -> AlertConfig:
        raise RuntimeError("unreadable mount")

    monkeypatch.setattr(Alerter, "config", explode)
    assert alerter.notify(ERROR, "svc: x", "...") == {"email": "failed", "ntfy": "failed"}


def test_a_tls_verification_failure_is_reported_louder_than_an_outage(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """⭐ `urlopen` re-raises the handshake's `OSError` as `URLError`, which is NOT an
    `SSLError` — so a plain `except ssl.SSLCertVerificationError` is dead code on this path."""
    wrapped = urllib.error.URLError(ssl.SSLCertVerificationError("self-signed"))
    monkeypatch.setattr(alerting, "_CHANNELS", (("ntfy", Spy(fail=wrapped)),))

    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        alerter.notify(ERROR, "svc: x", "...")
    assert "TLS VERIFICATION" in caplog.text


def test_a_self_referencing_cause_chain_does_not_spin_forever() -> None:
    exc = RuntimeError("loop")
    exc.__cause__ = exc
    assert alerting._is_cert_failure(exc) is False


# ================================================================= the channels THEMSELVES
# ⭐ WHY THIS SECTION EXISTS. Every test above replaces `_CHANNELS` with spies, so until these
# were written NOTHING executed `_send_email` or `_post_ntfy` — the two functions that actually
# talk to the outside world. A mutation sweep confirmed the hole: dropping the TLS context,
# deleting the `login()`, never calling `send_message()`, and — sharpest of all — replacing
# `msg["To"] = _recipients(...)` with the raw value ALL survived a fully green suite. That last
# one un-does this module's headline recipient fix at the ONE place it ships, while
# `_recipients` itself stays exhaustively unit-tested.
#
# These use in-process fakes rather than a socket: the point is what the module ASKS the
# transport to do, which is exactly what a spy can answer and a live server cannot, reliably.


class FakeSMTP:
    """Records the whole conversation `_send_email` drives. Never raises by itself."""

    instances: ClassVar[list[FakeSMTP]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.starttls_context: ssl.SSLContext | None = None
        self.starttls_called = False
        self.login_args: tuple[str, str] | None = None
        self.sent: list[object] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self, context: ssl.SSLContext | None = None) -> None:
        self.starttls_called = True
        self.starttls_context = context

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)

    def send_message(self, msg: object) -> None:
        self.sent.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    monkeypatch.setattr(alerting.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def test_send_email_normalises_the_recipient_header_before_it_is_sent(
        settings: AlertSettings, fake_smtp: type[FakeSMTP]) -> None:
    """⭐ The headline `;` fix, asserted AT THE SEND SITE rather than only on the helper.

    `smtplib.send_message()` derives its recipient list from the `To` header, so a `;` reaching
    it silently costs recipients. `_recipients` is unit-tested to death above; this is the test
    that `_send_email` actually CALLS it.
    """
    write_email_config(settings, EMAIL_TO="a@example.com;b@example.com")
    cfg = AlertConfig.load(settings)

    alerting._send_email(cfg, alerting.SEVERITIES[ERROR], "svc: down", "refused")

    (smtp,) = fake_smtp.instances
    (msg,) = smtp.sent
    assert msg["To"] == "a@example.com, b@example.com", (
        "the raw EMAIL_TO reached the header — send_message() will drop a recipient in silence")
    assert msg["Subject"] == "[ERROR] svc: down"
    assert msg["From"] == "svc@example.com"
    assert msg.get_content().strip() == "refused"


def test_send_email_verifies_the_server_certificate(
        settings: AlertSettings, fake_smtp: type[FakeSMTP]) -> None:
    """⭐ `starttls()` with NO context does not verify the server: smtplib falls back to a
    check_hostname=False / CERT_NONE context, so the channel is encrypted but unauthenticated and
    anyone able to intercept the egress collects the app password on the next `login()`.

    That is the whole reason the password lives in a file rather than the environment, so the
    context is asserted here — and it is asserted BEFORE the login, because the ordering is what
    makes it matter.
    """
    write_email_config(settings)
    cfg = AlertConfig.load(settings)

    alerting._send_email(cfg, alerting.SEVERITIES[WARN], "svc: slow", "p95 up")

    (smtp,) = fake_smtp.instances
    assert smtp.starttls_called, "STARTTLS was never negotiated"
    ctx = smtp.starttls_context
    assert ctx is not None, "starttls() was called with NO context — the server is unverified"
    assert ctx.check_hostname is True
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert smtp.login_args == ("svc@example.com", "not-a-real-password")
    assert smtp.timeout == alerting.SMTP_TIMEOUT_S, "an SMTP send with no timeout can hang forever"


def test_send_email_uses_the_configured_port_and_host(
        settings: AlertSettings, fake_smtp: type[FakeSMTP]) -> None:
    """The one path that proves a VALID SMTP_PORT reaches the transport. `smtp_port()`'s
    `return int(raw)` branch is otherwise never executed by anything."""
    write_email_config(settings, SMTP_PORT="2525")
    alerting._send_email(AlertConfig.load(settings), alerting.SEVERITIES[ERROR], "t", "m")
    (smtp,) = fake_smtp.instances
    assert (smtp.host, smtp.port) == ("smtp.example.com", 2525)


def test_post_ntfy_sends_the_body_and_the_severity_headers(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The headers ARE the rendering: ntfy turns the `Tags` NAME into the emoji, and `Priority`
    decides whether the phone buzzes. Swapping the two survived a mutation sweep."""
    captured: dict[str, object] = {}

    def fake_urlopen(req: object, timeout: float | None = None) -> object:
        captured["url"] = req.full_url  # type: ignore[attr-defined]
        captured["data"] = req.data  # type: ignore[attr-defined]
        captured["headers"] = dict(req.headers)  # type: ignore[attr-defined]
        captured["timeout"] = timeout

        class Response:
            def read(self) -> bytes:
                return b"ok"

        return Response()

    monkeypatch.setattr(alerting.urllib.request, "urlopen", fake_urlopen)
    cfg = AlertConfig(ntfy_url="https://ntfy.example.com/svc")

    alerting._post_ntfy(cfg, alerting.SEVERITIES[ERROR], "svc: down", "connection refused")

    assert captured["url"] == "https://ntfy.example.com/svc"
    assert captured["data"] == b"connection refused"
    assert captured["timeout"] == alerting.NTFY_TIMEOUT_S
    headers = captured["headers"]
    assert headers["Title"] == "[ERROR] svc: down"
    assert headers["Priority"] == "urgent"
    assert headers["Tags"] == "red_circle"


def test_post_ntfy_keeps_the_title_header_ascii(monkeypatch: pytest.MonkeyPatch) -> None:
    """`http.client` encodes header values latin-1, so putting the emoji itself in `Title` raises
    `UnicodeEncodeError` before anything is sent. The emoji travels as a TAG NAME instead — this
    pins that the severity table's `emoji` field never reaches a header."""
    seen: dict[str, object] = {}

    def fake_urlopen(req: object, timeout: float | None = None) -> object:
        seen.update(dict(req.headers))  # type: ignore[attr-defined]

        class Response:
            def read(self) -> bytes:
                return b"ok"

        return Response()

    monkeypatch.setattr(alerting.urllib.request, "urlopen", fake_urlopen)
    for severity in (OK, WARN, ERROR):
        alerting._post_ntfy(AlertConfig(ntfy_url="https://ntfy.example.com/t"),
                            alerting.SEVERITIES[severity], "svc: x", "m")
        for name, value in seen.items():
            assert str(value).isascii(), f"{name} header is not latin-1 safe: {value!r}"


# ============================================================ edge-trigger / escalate / clears
def test_a_repeat_condition_is_suppressed_until_it_clears(
        alerter: Alerter, channels: dict[str, Spy]) -> None:
    first = alerter.notify(ERROR, "svc: backup failed", "attempt 1")
    second = alerter.notify(ERROR, "svc: backup failed", "attempt 2")

    assert first["ntfy"] == "sent"
    assert second == {"email": "suppressed", "ntfy": "suppressed"}
    assert len(channels["ntfy"].calls) == 1


def test_an_ok_is_never_suppressed(alerter: Alerter, channels: dict[str, Spy]) -> None:
    for _ in range(3):
        assert alerter.notify(OK, "svc: heartbeat", "alive")["ntfy"] == "sent"
    assert len(channels["ntfy"].calls) == 3


def test_an_ok_clears_only_its_own_title_and_what_it_names(
        alerter: Alerter, channels: dict[str, Spy]) -> None:
    """⭐ The failure this prevents: an earlier version cleared EVERY condition on any OK, so a
    routine hourly confirmation re-armed every edge-triggered condition the service had."""
    alerter.notify(ERROR, "svc: backup failed", "...")
    alerter.notify(ERROR, "svc: census stalled", "...")

    alerter.notify(OK, "svc: backup ok", "...", clears="svc: backup failed")

    # the backup condition was re-armed, so its next occurrence alerts again
    assert alerter.notify(ERROR, "svc: backup failed", "again")["ntfy"] == "sent"
    # the census condition was NOT touched, so it is still suppressed
    assert alerter.notify(ERROR, "svc: census stalled", "still")["ntfy"] == "suppressed"


def test_clears_accepts_an_iterable_of_titles(alerter: Alerter) -> None:
    alerter.notify(ERROR, "svc: a", "...")
    alerter.notify(ERROR, "svc: b", "...")
    alerter.notify(OK, "svc: recovered", "...", clears=["svc: a", "svc: b"])
    assert alerter.notify(ERROR, "svc: a", "...")["ntfy"] == "sent"
    assert alerter.notify(ERROR, "svc: b", "...")["ntfy"] == "sent"


def test_clears_on_a_non_ok_is_refused_and_says_so(
        alerter: Alerter, caplog: pytest.LogCaptureFixture) -> None:
    """An alert that resolved its own condition would reset the escalation backoff on every
    retry, so a permanent failure would page at the base gap forever and never escalate."""
    alerter.notify(ERROR, "svc: db down", "attempt 1")
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        alerter.notify(ERROR, "svc: db down", "attempt 2", clears="svc: db down")
    assert "is IGNORED" in caplog.text
    # still suppressed — the clear was not honoured
    assert alerter.notify(ERROR, "svc: db down", "attempt 3")["ntfy"] == "suppressed"


def test_a_nonsense_clears_argument_loses_the_clear_but_not_the_alert(
        alerter: Alerter, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        results = alerter.notify(OK, "svc: ok", "...", clears=object())
    assert results["ntfy"] == "sent"
    assert "neither a title nor an iterable" in caplog.text


def test_an_escalating_condition_backs_off_then_re_pages(
        alerter: Alerter, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(alerting, "_now", lambda: clock["t"])

    assert alerter.notify(ERROR, "svc: consent lapsing", "1", escalating=True)["ntfy"] == "sent"
    clock["t"] += 59 * 60  # 59 minutes — inside the 1h base gap
    assert alerter.notify(ERROR, "svc: consent lapsing", "2", escalating=True)[
        "ntfy"] == "suppressed"
    clock["t"] += 2 * 60  # now past 1h
    assert alerter.notify(ERROR, "svc: consent lapsing", "3", escalating=True)["ntfy"] == "sent"
    clock["t"] += 61 * 60  # 61 min later: the gap has doubled to 2h, so still quiet
    assert alerter.notify(ERROR, "svc: consent lapsing", "4", escalating=True)[
        "ntfy"] == "suppressed"

    assert len(channels["ntfy"].calls) == 2


@pytest.mark.parametrize(("count", "hours"), [
    (1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 16.0), (6, 24.0), (99, 24.0),
    (0, 1.0), (-5, 1.0),
])
def test_the_backoff_curve_doubles_and_caps(count: int, hours: float) -> None:
    assert alerting._escalate_gap_hours(count) == hours


def test_a_backwards_clock_does_not_mute_an_escalating_condition(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch) -> None:
    """`0 <= waited_h < gap_h` on purpose: a negative wait means the clock jumped BACKWARDS, and
    treating that as "not long enough" would suppress the alert for as long as the skew lasts."""
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(alerting, "_now", lambda: clock["t"])
    alerter.notify(ERROR, "svc: x", "1", escalating=True)
    clock["t"] -= 10_000  # the clock went backwards
    assert alerter.notify(ERROR, "svc: x", "2", escalating=True)["ntfy"] == "sent"


def test_a_delivery_that_reached_nobody_is_not_recorded_as_reported(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch) -> None:
    """⭐ Otherwise a delivery OUTAGE becomes a permanent silence: the condition reads as
    already-reported, so its next occurrence is de-duplicated against a send that never landed."""
    monkeypatch.setattr(alerting, "_CHANNELS", (("ntfy", Spy(fail=OSError("down"))),))
    assert alerter.notify(ERROR, "svc: backup failed", "1") == {"ntfy": "failed"}

    delivered = Spy()
    monkeypatch.setattr(alerting, "_CHANNELS", (("ntfy", delivered),))
    assert alerter.notify(ERROR, "svc: backup failed", "2") == {"ntfy": "sent"}
    assert len(delivered.calls) == 1


def test_de_duplication_fails_open(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """Every error in the escalation bookkeeping SENDS the alert. Over-alerting is a nuisance; a
    de-duplicator that silently swallows a lapsed consent is the failure this exists to prevent."""
    def explode(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("state is on fire")

    monkeypatch.setattr(Alerter, "_should_send", explode)
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert alerter.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert "de-duplication failed" in caplog.text


def test_corrupt_state_is_treated_as_nothing_firing(
        alerter: Alerter, settings: AlertSettings, caplog: pytest.LogCaptureFixture) -> None:
    assert settings.state_file is not None
    Path(settings.state_file).write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert alerter.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert "unreadable" in caplog.text


def test_a_state_file_holding_a_json_list_is_ignored(
        alerter: Alerter, settings: AlertSettings) -> None:
    assert settings.state_file is not None
    Path(settings.state_file).write_text("[1, 2, 3]", encoding="utf-8")
    assert alerter.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"


def test_the_state_file_is_pruned_to_the_configured_cap(tmp_path: Path) -> None:
    settings = AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                             state_file=str(tmp_path / "state.json"),
                             max_tracked_conditions=3)
    alerter = Alerter(settings)
    state = {f"cond-{i}": {"first": float(i), "last": float(i), "count": 1} for i in range(10)}

    alerter._write_state(state)

    kept = json.loads(Path(str(settings.state_file)).read_text(encoding="utf-8"))
    assert set(kept) == {"cond-7", "cond-8", "cond-9"}, "the OLDEST must be the ones dropped"


def test_state_file_none_delivers_everything_and_writes_nothing(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch) -> None:
    """The upstream-de-duplicates-its-own-conditions opt-out.

    ⭐ The CWD assertion is the non-obvious half. The predecessor spelled this opt-out as a magic
    string in a path-valued setting, which resolved to a RELATIVE file of that name in the working
    directory — created, written and left behind on the container's disposable layer by the first
    alert. Running from an empty CWD and requiring it to stay empty is what pins that it cannot
    come back.
    """
    monkeypatch.chdir(tmp_path)
    alerter = Alerter(AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                                    state_file=None))
    for _ in range(3):
        assert alerter.notify(ERROR, "svc: repeated", "...")["ntfy"] == "sent"
    assert len(channels["ntfy"].calls) == 3
    assert list(tmp_path.iterdir()) == [], "nothing may be persisted with the opt-out set"


def test_a_read_only_state_directory_re_alerts_rather_than_raising(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    alerter = Alerter(AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                                    state_file=str(tmp_path / "state.json")))

    def refuse(*args: object, **kwargs: object) -> object:
        raise PermissionError("read-only")

    monkeypatch.setattr(alerting.os, "replace", refuse)
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert alerter.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert "could not save alert state" in caplog.text


# ============================================================================ the error-log sink
def test_warn_and_error_are_recorded_and_ok_is_not(
        alerter: Alerter, settings: AlertSettings, channels: dict[str, Spy]) -> None:
    alerter.notify(OK, "svc: heartbeat", "alive")
    alerter.notify(WARN, "svc: slow", "p95 up")
    alerter.notify(ERROR, "svc: down", "refused")

    records = alerter.read_errors(limit=0)
    assert [r["severity"] for r in records] == [WARN, ERROR]
    assert [r["title"] for r in records] == ["svc: slow", "svc: down"]
    assert all(r["service"] == "svc" for r in records)
    assert settings.error_log is not None


def test_a_suppressed_alert_is_still_recorded(alerter: Alerter) -> None:
    """De-duplication protects the operator's phone; hiding an occurrence from the file used to
    troubleshoot it would be the opposite of what this sink is for."""
    alerter.notify(ERROR, "svc: backup failed", "attempt 1")
    assert alerter.notify(ERROR, "svc: backup failed", "attempt 2")["ntfy"] == "suppressed"

    records = alerter.read_errors(limit=0)
    assert [r["message"] for r in records] == ["attempt 1", "attempt 2"]


def test_an_unknown_severity_is_recorded_as_error(alerter: Alerter) -> None:
    """It is treated as ERROR everywhere else; this must not be the one place a caller bug makes
    something vanish from the file a reader filters on WARN/ERROR."""
    alerter.notify("CATASTROPHE", "svc: ?", "...")
    assert [r["severity"] for r in alerter.read_errors(limit=0)] == [ERROR]


def test_a_record_is_one_line_even_for_a_traceback(alerter: Alerter,
                                                   settings: AlertSettings) -> None:
    alerter.notify(ERROR, "svc: crash", "Traceback:\nline one\nline two")
    assert settings.error_log is not None
    body = Path(settings.error_log).read_text(encoding="utf-8")
    assert body.count("\n") == 1
    assert json.loads(body)["message"] == "Traceback:\nline one\nline two"


def test_an_enormous_message_is_bounded(tmp_path: Path) -> None:
    """The roll checks the size BEFORE appending, so without a per-field cap one unbounded
    message writes one unbounded record and the size cap is not a cap at all."""
    settings = AlertSettings(service="svc", error_log=str(tmp_path / "errors.log"),
                             max_record_field=100)
    Alerter(settings).notify(ERROR, "svc: big", "x" * 10_000)
    record = json.loads(Path(str(settings.error_log)).read_text(encoding="utf-8"))
    assert record["message"] == "x" * 100 + "…[truncated]"


def test_an_object_whose_repr_raises_is_recorded_as_unrepresentable(tmp_path: Path) -> None:
    """`_field` calls `repr()` inside its own guard: an object whose `__repr__` raises is exactly
    the kind of thing that turns up in an exception handler, and this runs on the path that must
    never raise.

    `__str__` is left working on purpose, so this isolates the `repr` guard. The harsher case —
    a `__str__` that raises too, which reaches the `%`-formatting inside `logging` — is pinned
    separately below, in a subprocess, because it cannot be measured under pytest's log handler.
    """
    class ReprRaises:
        def __str__(self) -> str:
            return "printable"

        def __repr__(self) -> str:
            raise RuntimeError("no")

    settings = AlertSettings(service="svc", error_log=str(tmp_path / "errors.log"))
    Alerter(settings).notify(ERROR, "svc: x", ReprRaises())  # type: ignore[arg-type]
    record = json.loads(Path(str(settings.error_log)).read_text(encoding="utf-8"))
    assert record["message"] == "<unrepresentable>"


def test_notify_does_not_raise_even_when_str_itself_raises(tmp_path: Path) -> None:
    """⭐ THE "NEVER RAISES" INVARIANT, MEASURED WHERE IT ACTUALLY LIVES.

    A message object whose `__str__` raises reaches the `%`-formatting inside `logging`, which is
    OUTSIDE this module's guards on purpose — the log line goes out first, before everything.
    The stdlib swallows it (`Handler.handleError` prints the traceback to stderr and returns), so
    `notify()` still returns normally and the caller's loop survives.

    ⚠️ THIS CANNOT BE MEASURED WITH `caplog`. Pytest's log handler overrides `handleError` and
    RE-RAISES formatting errors, so an in-process version of this test reports a failure that the
    shipped code does not have. Running it in a subprocess under default logging is what makes
    the assertion about the module rather than about the harness.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import logging, sys\n"
        "from kw_common.alerting import Alerter, AlertSettings, ERROR\n"
        "logging.basicConfig(level=logging.ERROR)\n"
        "class Hostile:\n"
        "    def __str__(self): raise RuntimeError('no str')\n"
        "    def __repr__(self): raise RuntimeError('no repr')\n"
        "results = Alerter(AlertSettings(service='svc')).notify(ERROR, 'svc: x', Hostile())\n"
        "assert results == {'email': 'skipped', 'ntfy': 'skipped'}, results\n"
        "sys.stdout.write('SURVIVED')\n",
        encoding="utf-8")

    result = subprocess.run([sys.executable, probe.name], cwd=tmp_path, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=60, check=False)

    assert result.stdout.strip() == "SURVIVED", (
        f"notify() let a caller's broken __str__ escape:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}")


def test_an_unwritable_error_log_does_not_cost_the_notification(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    settings = AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                             error_log=str(tmp_path / "errors.log"))

    def refuse(*args: object, **kwargs: object) -> int:
        raise PermissionError("full disk")

    monkeypatch.setattr(alerting.os, "open", refuse)
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert Alerter(settings).notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert "could not append to the error log" in caplog.text


def test_error_log_none_writes_nothing_and_reads_empty(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    alerter = Alerter(AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                                    error_log=None))
    assert alerter.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert alerter.read_errors(limit=0) == []
    assert list(tmp_path.iterdir()) == []


def test_the_log_rolls_and_the_reader_spans_the_rotation(tmp_path: Path) -> None:
    settings = AlertSettings(service="svc", error_log=str(tmp_path / "errors.log"),
                             error_log_max_bytes=400)
    alerter = Alerter(settings)
    for i in range(20):
        alerter.notify(ERROR, f"svc: cond-{i}", "x" * 50)

    assert Path(f"{settings.error_log}.1").exists(), "the log never rolled"
    titles = [r["title"] for r in alerter.read_errors(limit=0)]
    assert titles == sorted(titles, key=lambda t: int(t.rsplit("-", 1)[1])), "not oldest-first"
    assert "svc: cond-19" in titles


def test_the_reader_and_the_roller_agree_about_generation_count(tmp_path: Path) -> None:
    """A reader that looks for fewer generations than the writer keeps silently loses the oldest
    records — a data-loss bug no test of either side alone would catch."""
    settings = AlertSettings(service="svc", error_log=str(tmp_path / "errors.log"),
                             error_log_max_bytes=300, error_log_backups=3)
    alerter = Alerter(settings)
    for i in range(40):
        alerter.notify(ERROR, f"svc: cond-{i}", "y" * 60)

    on_disk = sum(1 for i in (1, 2, 3) if Path(f"{settings.error_log}.{i}").exists())
    assert on_disk == 3
    read_back = len(alerter.read_errors(limit=0))
    from_all_files = sum(
        len([line for line in Path(p).read_text(encoding="utf-8").splitlines() if line.strip()])
        for p in [f"{settings.error_log}.{i}" for i in (1, 2, 3)] + [str(settings.error_log)]
    )
    assert read_back == from_all_files


def test_the_reader_returns_the_newest_limit_records(tmp_path: Path) -> None:
    settings = AlertSettings(service="svc", error_log=str(tmp_path / "errors.log"))
    alerter = Alerter(settings)
    for i in range(10):
        alerter.notify(ERROR, f"svc: cond-{i}", "...")
    assert [r["title"] for r in alerter.read_errors(limit=3)] == [
        "svc: cond-7", "svc: cond-8", "svc: cond-9"]


def test_the_reader_skips_a_truncated_final_line(tmp_path: Path) -> None:
    path = tmp_path / "errors.log"
    path.write_text('{"ts": "2026-08-30T10:00:00Z", "title": "good"}\n{"ts": "trunc',
                    encoding="utf-8")
    assert [r["title"] for r in alerting.read_jsonl_tail(str(path), limit=0)] == ["good"]


def test_an_absurd_limit_is_clamped_rather_than_raising(tmp_path: Path) -> None:
    """`deque(maxlen=)` narrows to a C ssize_t and raises `OverflowError` above 2**63."""
    path = tmp_path / "errors.log"
    path.write_text('{"ts": "2026-08-30T10:00:00Z", "title": "one"}\n', encoding="utf-8")
    assert len(alerting.read_jsonl_tail(str(path), limit=99999999999999999999)) == 1


# ---- the `since` window ------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "2026-08-30T10:00:00Z",
    "2026-08-30T10:00:00z",
    "2026-08-30T10:00:00+00:00",
])
def test_parse_since_normalises_the_z_suffix(text: str) -> None:
    """⭐ `fromisoformat` only learned `Z` in 3.11, and 3.10 is supported — AND `Z` is exactly
    what this module's own sink writes, so an unnormalised parser fails on its own records."""
    stamp = alerting.parse_since(text)
    assert stamp is not None
    assert stamp.isoformat() == "2026-08-30T10:00:00+00:00"


def test_parse_since_is_none_for_no_bound() -> None:
    assert alerting.parse_since("") is None
    assert alerting.parse_since("   ") is None


def test_parse_since_raises_on_garbage() -> None:
    with pytest.raises(ValueError):
        alerting.parse_since("last tuesday")


def test_parse_since_raises_overflow_rather_than_500ing() -> None:
    """An endpoint must catch BOTH ValueError and OverflowError and answer 422."""
    with pytest.raises(OverflowError):
        alerting.parse_since("9999-12-31T23:59:59-05:00")


def test_the_since_window_compares_instants_not_strings(tmp_path: Path) -> None:
    """⭐ A string compare is wrong for two spellings that both look right: a non-UTC offset
    sorts below every `+00:00` record, and `Z` (0x5A) sorts above the `.` of a fraction."""
    path = tmp_path / "errors.log"
    path.write_text(
        '{"ts": "2026-08-30T13:00:00Z", "title": "before"}\n'
        '{"ts": "2026-08-30T15:00:00Z", "title": "after"}\n', encoding="utf-8")

    # 09:00 US-Central-ish == 14:00Z. A string compare would sort this BELOW both records and
    # return them both.
    kept = alerting.read_jsonl_tail(str(path), limit=0, since_iso="2026-08-30T09:00:00-05:00")
    assert [r["title"] for r in kept] == ["after"]


def test_a_record_with_an_unparseable_timestamp_is_kept(tmp_path: Path) -> None:
    """Dropping it would hide a corrupt writer behind an empty result."""
    path = tmp_path / "errors.log"
    path.write_text('{"ts": "not-a-time", "title": "anomaly"}\n', encoding="utf-8")
    kept = alerting.read_jsonl_tail(str(path), limit=0, since_iso="2026-08-30T00:00:00Z")
    assert [r["title"] for r in kept] == ["anomaly"]


# =============================================================================== settings & boot
def test_settings_refuse_a_blank_service_name() -> None:
    """At CONSTRUCTION, not at notify() — a startup crash the operator sees beats records that
    say "unknown" forever."""
    for bad in ["", "   ", None]:
        with pytest.raises(ValueError, match="service"):
            AlertSettings(service=bad)  # type: ignore[arg-type]


def test_settings_strip_the_service_name() -> None:
    assert AlertSettings(service="  svc  ").service == "svc"


@pytest.mark.parametrize("name", ["config_file", "state_file", "error_log"])
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_settings_refuse_a_blank_path_rather_than_silently_disabling_it(
        name: str, blank: str) -> None:
    """⭐⭐ THE MISCONFIGURATION THIS LIBRARY IS MOST LIKELY TO MEET.

    The natural port of an environment-driven consumer is
    `state_file=os.environ.get("ALERT_STATE_FILE", "")`, and a container platform that passes
    every unset optional Variable as an EMPTY STRING makes that the common case. A blank that
    behaves like `None` disables de-duplication silently: every escalating condition then
    re-pages at the caller's full cycle rate with no backoff, and nothing says why.

    `None` means off and is spelled `None`. A blank is a mistake and is refused at construction,
    where an operator can still see it.
    """
    with pytest.raises(ValueError, match=name):
        AlertSettings(service="svc", **{name: blank})  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["config_file", "state_file", "error_log"])
def test_settings_accept_a_pathlike_for_every_path(name: str, tmp_path: Path) -> None:
    """`Path(...)` is the obvious thing to hand a public library, and it used to work for two of
    these three and break the third — `state_file` reached `.strip()` and raised, so
    de-duplication went silently off and every notification logged a traceback. All three now
    coerce, so the asymmetry cannot come back."""
    settings = AlertSettings(service="svc", **{name: tmp_path / "thing.json"})  # type: ignore[arg-type]
    value = getattr(settings, name)
    assert isinstance(value, str)
    assert value == str(tmp_path / "thing.json")


def test_a_pathlike_state_file_still_de_duplicates(tmp_path: Path,
                                                   channels: dict[str, Spy]) -> None:
    """The behavioural half of the test above: coercion is only worth anything if the resulting
    alerter actually de-duplicates."""
    alerter = Alerter(AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                                    state_file=tmp_path / "state.json"))  # type: ignore[arg-type]
    assert alerter.notify(ERROR, "svc: x", "1")["ntfy"] == "sent"
    assert alerter.notify(ERROR, "svc: x", "2")["ntfy"] == "suppressed"
    assert alerter.state_file_problem() == ""


@pytest.mark.parametrize("name", ["config_file", "state_file", "error_log"])
def test_settings_refuse_a_path_that_is_not_a_path(name: str) -> None:
    with pytest.raises(ValueError, match=name):
        AlertSettings(service="svc", **{name: 17})  # type: ignore[arg-type]


def test_an_error_log_whose_path_raises_does_not_break_the_alert(
        channels: dict[str, Spy], caplog: pytest.LogCaptureFixture) -> None:
    """⭐ Resolving the error-log path happens INSIDE the record-building guard.

    It sat outside, which made `_append_error_record`'s own docstring false: `self.settings` is
    an attribute access, and an attribute access can run arbitrary code. A duck-typed or lazily
    resolved settings object is enough to reach it, and the exception travelled straight out of
    `notify()` — into a caller that is usually already inside an `except` handler.
    """
    class ExplodingSettings:
        service = "svc"
        config_file = None
        ntfy_url = "https://ntfy.example.com/t"
        state_file = None
        max_record_field = 4000
        max_tracked_conditions = 200
        error_log_max_bytes = 1_000_000
        error_log_backups = 1

        @property
        def error_log(self) -> str:
            raise RuntimeError("the settings object resolves this lazily and it failed")

    alerter = Alerter(ExplodingSettings())  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        results = alerter.notify(ERROR, "svc: x", "...")

    assert results["ntfy"] == "sent", "a broken error-log path cost the notification"
    assert "could not build an error-log record" in caplog.text


@pytest.mark.parametrize("name", ["error_log_max_bytes", "error_log_backups", "max_record_field",
                                  "max_tracked_conditions"])
@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "10"])
def test_settings_refuse_a_non_positive_sizing_knob(name: str, bad: object) -> None:
    with pytest.raises(ValueError, match=name):
        AlertSettings(service="svc", **{name: bad})  # type: ignore[arg-type]


def test_warn_if_unconfigured_names_the_ready_channels(
        alerter: Alerter, settings: AlertSettings, caplog: pytest.LogCaptureFixture) -> None:
    write_email_config(settings)
    with caplog.at_level(logging.INFO, logger="kw_common.alerting"):
        assert alerter.warn_if_unconfigured() == ["email", "ntfy"]
    assert "email + ntfy ready for svc" in caplog.text


def test_warn_if_unconfigured_shouts_when_nothing_is_usable(
        caplog: pytest.LogCaptureFixture) -> None:
    alerter = Alerter(AlertSettings(service="svc"))
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert alerter.warn_if_unconfigured() == []
    assert "ALERTING UNCONFIGURED for svc" in caplog.text
    assert "go NOWHERE" in caplog.text


def test_warn_if_unconfigured_reports_the_dedup_opt_out(
        caplog: pytest.LogCaptureFixture) -> None:
    alerter = Alerter(AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t"))
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        alerter.warn_if_unconfigured()
    assert "disables de-duplication" in caplog.text


def test_warn_if_unconfigured_reports_an_unmounted_error_log_volume(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    missing = tmp_path / "not-mounted" / "logs" / "svc-errors.log"
    alerter = Alerter(AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                                    error_log=str(missing)))
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        alerter.warn_if_unconfigured()
    assert "does not exist" in caplog.text
    assert "the volume is not mounted" in caplog.text


def test_warn_if_unconfigured_cannot_raise(monkeypatch: pytest.MonkeyPatch,
                                           caplog: pytest.LogCaptureFixture) -> None:
    def explode(self: Alerter) -> AlertConfig:
        raise RuntimeError("boom")

    monkeypatch.setattr(Alerter, "config", explode)
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert Alerter(AlertSettings(service="svc")).warn_if_unconfigured() == []
    assert "ALERTING CONFIG UNREADABLE" in caplog.text


def test_state_file_problem_reports_a_missing_directory(tmp_path: Path) -> None:
    alerter = Alerter(AlertSettings(service="svc",
                                    state_file=str(tmp_path / "nope" / "state.json")))
    assert "does not exist" in alerter.state_file_problem()


def test_a_healthy_state_file_has_no_problem(tmp_path: Path) -> None:
    alerter = Alerter(AlertSettings(service="svc", state_file=str(tmp_path / "state.json")))
    assert alerter.state_file_problem() == ""


# =============================================================== the process-default convenience
# (`_reset_default` in conftest.py clears the installed default around every test.)
def test_configure_installs_the_default_and_returns_it(settings: AlertSettings,
                                                       channels: dict[str, Spy]) -> None:
    installed = alerting.configure(settings)
    assert alerting.current_alerter() is installed
    assert alerting.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert channels["ntfy"].calls[0][2] == "svc: x"


def test_module_notify_without_configure_still_logs_and_never_raises(
        caplog: pytest.LogCaptureFixture) -> None:
    """A caller inside an `except` handler must not be punished for a startup mistake — but the
    reason its alert went nowhere has to be stated, loudly."""
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        results = alerting.notify(ERROR, "svc: db down", "refused")
    assert results == {"email": "skipped", "ntfy": "skipped"}
    assert "is NOT configured" in caplog.text
    assert "svc: db down" in caplog.text, "the log line must go out regardless"
    assert "refused" in caplog.text


def test_module_warn_if_unconfigured_without_configure_names_the_service(
        caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert alerting.warn_if_unconfigured("svc") == []
    assert "svc" in caplog.text


def test_the_unconfigured_path_never_raises_either(tmp_path: Path) -> None:
    """⭐⭐ THE UNCONFIGURED BRANCH IS HELD TO THE SAME STANDARD AS THE CONFIGURED ONE.

    It was not, and that was the worst possible place to be lax: the population this branch
    exists to protect is exactly the process that got its startup wrong, and it died inside the
    caller's `except` handler instead of logging. Two causes, both fixed —
    `SEVERITIES.get(severity)` was unguarded (`dict.get` propagates whatever the key's own
    `__hash__` raises), and the message was built with an EAGER f-string, which evaluates the
    caller's `__repr__` at the call site before `logging` is ever reached.

    ⚠️ IN A SUBPROCESS, for the same reason as
    `test_notify_does_not_raise_even_when_str_itself_raises`: pytest's log handler overrides
    `handleError` and RE-RAISES formatting errors, while the stdlib prints them to stderr and
    returns. Measured — an in-process version of this test fails on all four hostile cases
    against code that is correct, which would have sent me chasing a defect that does not exist.
    """
    probe = tmp_path / "probe_unconfigured.py"
    probe.write_text(
        "import logging, sys\n"
        "from kw_common import alerting\n"
        "from kw_common.alerting import ERROR\n"
        "logging.basicConfig(level=logging.CRITICAL)\n"
        "assert alerting.current_alerter() is None\n"
        "class Hostile:\n"
        "    def __str__(self): raise RuntimeError('no str')\n"
        "    def __repr__(self): raise RuntimeError('no repr')\n"
        "cases = {\n"
        "    'unhashable severity': (['ERROR'], 'svc: t', 'm'),\n"
        "    'hostile severity':    (Hostile(), 'svc: t', 'm'),\n"
        "    'hostile title':       (ERROR, Hostile(), 'm'),\n"
        "    'hostile message':     (ERROR, 'svc: t', Hostile()),\n"
        "}\n"
        "for label, args in cases.items():\n"
        "    got = alerting.notify(*args)\n"
        "    assert got == {'email': 'skipped', 'ntfy': 'skipped'}, (label, got)\n"
        "assert alerting.warn_if_unconfigured(Hostile()) == []\n"
        "sys.stdout.write('SURVIVED')\n",
        encoding="utf-8")

    result = subprocess.run([sys.executable, probe.name], cwd=tmp_path, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=60, check=False)

    assert result.stdout.strip() == "SURVIVED", (
        f"the unconfigured path let a caller's bad argument escape:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}")


def test_configure_replaces_a_previous_default(settings: AlertSettings) -> None:
    first = alerting.configure(settings)
    second = alerting.configure(AlertSettings(service="other"))
    assert alerting.current_alerter() is second
    assert second is not first


# ====================================================================== the public API surface
def test_everything_in_dunder_all_exists() -> None:
    """`__all__` is the semver contract. A name in it that does not resolve is a broken import
    for every consumer that follows the documentation."""
    missing = [name for name in alerting.__all__ if not hasattr(alerting, name)]
    assert missing == []


def test_the_extracted_surface_is_exported() -> None:
    """The symbols this library was created to stop six repos from copying.

    This list is deliberately hardcoded — it is the CONTRACT, so it must be written down
    independently of `__all__` rather than read from it. A test that read `__all__` here would
    agree with any `__all__`, including one that had quietly dropped a name.
    """
    required = {"notify", "warn_if_unconfigured", "configure", "AlertConfig", "AlertSettings",
                "Alerter", "smtp_port_fault", "read_jsonl_tail", "parse_since",
                "MIN_SMTP_PORT", "MAX_SMTP_PORT", "OK", "WARN", "ERROR"}
    missing = sorted(required - set(alerting.__all__))
    assert missing == [], f"{missing} must be part of the public contract"


def test_the_port_bounds_are_a_tcp_port() -> None:
    assert (MIN_SMTP_PORT, MAX_SMTP_PORT) == (1, 65535)

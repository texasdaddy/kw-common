"""The four surfaces the leak-guard ENGINE was blind to, and the plant-and-measure for each.

WHY THIS FILE EXISTS
    `scripts/check_no_internal_info.py` is the fleet's shared guard: every other repository takes
    a copy of it, so a gap here is a gap everywhere at once. Four were measured against
    `main@046fc7e` (`_sessions/unraid-templates/AUDIT.md`, 2026-08-29), and every one of them
    exited 0 on content that a push would have published permanently:

    ⚠️ HOW THE PROVENANCE IS CITED, AND WHY IT READS ODDLY. `consumer#NN` is an issue in a PRIVATE
    consumer of this library, named in the project audit note above and deliberately not here:
    this file ships inside the sdist attached to every public release, so the fleet's inventory of
    service names would ship with it. It is the SAME spelling `src/kw_common/leakguard.py` uses,
    for the same issues — a second pseudonym for one referent would leave a reader unable to tell
    two issues from one, in the repository whose whole reason for pseudonymising is that guessing
    wrong about which repository an issue lives in is what leaks the estate. `unraid-templates#NN`
    is spelled out because that repository is public. A bare `#NN` below is this repository's own.

      consumer#20   A PATH was read by nothing. A tracked `<rfc1918-addr>.conf`, or a
                   `docs/<host>.lan/` directory, with perfectly clean file CONTENT, passed both
                   scans. A filename is rendered on every GitHub file listing and lands in every
                   clone. (The `<host>.lan-runbook/` spelling is NOT caught and never was — the
                   `.lan` bound rejects a following hyphen. It is pinned as a stated limit by
                   `test_the_path_scan_INHERITS_the_patterns_LIMITS_and_says_so`, which is where
                   the claim and the behaviour are kept in agreement.)
      unraid-templates#39 / consumer#21  The range scan read a commit's IDENTITY
                   (`%an%ae%cn%ce`) and its DIFF, and nothing else. A leak written into a commit
                   MESSAGE — or into an annotated tag's name, tagger or message — was published
                   and reported clean.
      unraid-templates#33 / consumer#23  The pre-commit layer read the WORKTREE. `git add` a leak,
                   tidy the worktree without re-staging, and the hook exits 0 while the commit
                   records the leak.
      consumer#22   `SKIP_SUFFIXES` was trusted outright, so an ASCII runbook named `.pdf` was read
                   by neither scan; and a run that scanned ZERO files printed the same cheerful
                   "no internal info found" a real clean run prints.

    The tests below fall into TWO kinds, and only the first is a reproduction:

      * the CATCH tests — each is one of the gaps above, committed. Each FAILS against the engine
        as it was and PASSES against the engine as it is, which is what makes it the anti-rot
        check for its issue: if a future edit reopens the gap, this is what says so.
      * the FALSE-RED tests — `test_an_ordinary_or_allow_listed_path_is_not_a_finding`,
        `test_an_ORDINARY_commit_message_does_not_block_a_push`, `test_a_tag_THIS_PUSH_DOES_NOT_
        SEND_is_NOT_scanned`, `test_the_floor_does_not_*`, and the control halves generally. These
        pass against BOTH engines by construction — the old one scanned neither surface, so it
        could not false-red on it. They are not reproductions of the four gaps; they are the
        regression tests for the SECOND round of defects, where the fixes above reddened trees
        that leak nothing (a machine-local override filename, a message naming one, a scratch tag,
        an `rm` of a tracked image, an empty repository). Saying so matters because "every test
        here fails against the old engine" is the kind of claim this file exists to keep out.

⚠️ EVERY LEAKING LITERAL HERE IS ASSEMBLED AT RUNTIME, exactly as in
    `test_leak_guard_range.py`, and every one is synthetic. This file is scanned by the guard —
    only `scripts/check_no_internal_info.py` is exempt — so a deny case written out whole would
    make the guard fail on its own test suite.

⚠️ EVERY `git` CALL PINS ITS CONFIG. A test that inherits the machine's git config is the
    config-dependent sibling of a time-bomb test: the CI runner has no `user.email`, so a
    `git commit` that needs one silently no-ops there and a test whose SETUP no-ops passes
    VACUOUSLY while looking like coverage. Identity, signing and autocrlf are all pinned, and the
    plants are re-read from git before anything is asserted about them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from kw_common import leakguard as guard

# ⭐ AN ORDINARY IMPORT. This suite used to load the engine by path out of `scripts/`, because
# that is where each repository kept its COPY. The engine is packaged now. `_SCRIPT` survives for
# the tests that run it as a SUBPROCESS, and `_REPO_ROOT` is anchored on THIS file rather than on
# the module's, because an installed module can be anywhere — `site-packages` included.
_SCRIPT = Path(guard.__file__).resolve()
_REPO_ROOT = Path(__file__).resolve().parents[1]

COMPILED = guard.compile_patterns()

# ⚠️ SPLIT FRAGMENTS. Neither half matches a pattern on its own; joined at runtime they are the
# deny shapes. All synthetic: `.lan` resolves nowhere, `.77.77` is on no network here, `.invalid`
# is reserved by RFC 2606, and the UUID is a counting pattern.
_HOST = "host-a." + "lan"
_ADDR = "192.168." + "77.77"
_POOL = "/mnt/" + "user" + "/appdata/svc"
_UUID = "11111111-2222-" + "3333-4444-555555555555"
_MAIL = "someone@" + "gmail.invalid"

# The documented ALLOWED spellings, from the guard's own `_MUST_PASS` corpus.
_OK_HOST = "svc.your-domain.example"
_OK_ADDR = "198.51.100.5"

_LEAK_LINE = f"AGENT_URL=http://{_HOST}:9999/mcp"

# ⚠️ SPLIT FOR THE SAME REASON AS THE DENY CASES, even though this one is ALLOWED here. The
# dotenv local filename CONTAINS a `private lan domain` match by construction — that is precisely
# why the canonical engine carries an allow-span for it — and the project-side real-literal guard
# run before every push is an older copy of this engine that does NOT yet have that span. Written
# out whole, this string reddens that guard on a file that leaks nothing. Assembled, it exercises
# exactly the same path scan and satisfies both layers.
_DOTENV_LOCAL = ".env" + ".local"

# ⚠️ SPLIT FOR THE SAME REASON, and the reason is sharper here than anywhere else in this file:
# these are the strings that must PASS on the PATH and MESSAGE surfaces, and they must still be
# DENIED in file CONTENT — where a `.local` host really is a leak and the bound is load-bearing.
# This file IS file content. Written out whole they are findings of the very guard they exist to
# regression-test, which is not a false positive: it is the guard being right about the surface it
# is looking at. `scripts/check_no_internal_info.py` holds the same values whole in
# `_MUST_PASS_PATHS`, and it can, because it is the one file both scans skip.
_LOCAL = "." + "local"          # the machine-local override suffix, as a FILENAME
_MNT_USER = "/mnt/" + "user"
_MNT_CACHE = "/mnt/" + "cache"
_VERSION_4 = "10.0." + "0.1"    # a four-part assembly version, not an address

# `-c` rather than `git config`, so nothing depends on what the repository or the machine holds.
_PINNED = ("-c", "user.name=guard test", "-c", "user.email=t@example.com",
           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
           "-c", "core.autocrlf=false")


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *_PINNED, *args], cwd=repo, capture_output=True, check=True,
                         timeout=300)
    return out.stdout.decode("utf-8", errors="replace")


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")


def _write(repo: Path, rel: str, text: str) -> None:
    """Write a fixture file with the bytes it says, on every platform.

    ⛔ `write_bytes`, NOT `write_text`. `Path.write_text` opens in TEXT mode with `newline=None`,
    which translates every `\\n` to `os.linesep` — so on Windows a fixture written as `"clean\\n"`
    lands as `clean\\r\\n`, git (with `core.autocrlf=false` pinned, as it is here) stores the CR
    verbatim, and a test asserting on the exact added line passes on the runner and fails locally
    or the reverse. That is the config-dependent sibling of a time-bomb test, and it bit this file
    on its first run.
    """
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


def _seeded(repo: Path) -> str:
    """A repository with one ordinary clean commit."""
    _init(repo)
    _write(repo, "README.md", "seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return _git(repo, "rev-parse", "HEAD").strip()


def _commit(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").strip()


def _commit_with_message(repo: Path, tmp_path: Path, body: str) -> str:
    """Commit with a multi-line message, via a FILE.

    ⚠️ `-F`, never `-m "…"`: a message passed inline through a shell has its backtick spans
    command-substituted and its newlines mangled, which would quietly change the very text this
    file is asserting about.
    """
    msg = tmp_path / "commit-message.txt"
    msg.write_bytes(body.encode("utf-8"))   # bytes, for the reason `_write` gives
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-F", str(msg))
    return _git(repo, "rev-parse", "HEAD").strip()


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the guard exactly as a hook or CI does — a real process, real argv, real exit code."""
    return subprocess.run([sys.executable, str(_SCRIPT), *args], cwd=repo,
                          capture_output=True, check=False, timeout=300,
                          encoding="utf-8", errors="replace")


def _out(res: subprocess.CompletedProcess) -> str:
    return (res.stdout or "") + (res.stderr or "")


# ===========================================================================================
# consumer#20 — a leak in a PATH
# ===========================================================================================


def test_scan_path_finds_a_leak_in_a_path_string() -> None:
    """The pure half: no repository, so this is the fast pin on the matcher itself."""
    assert guard.scan_path(f"{_ADDR}.conf"), "an address in a filename must be a hit"
    assert guard.scan_path(f"docs/{_HOST}/notes.md"), (
        "a LAN host in a DIRECTORY name is published exactly like one in a filename")
    # ⭐ `.lan` KEEPS A LOOSER RIGHT BOUND ON THIS SURFACE than it has in file content, so an
    # ordinary EXTENSION does not hide the host. Content cannot do this: there, the bound is what
    # stops `settings.local.json` reddening every repository that has one.
    assert guard.scan_path(f"{_HOST}.conf"), "an extension must not hide a `.lan` host"


def test_compile_for_REJECTS_an_override_that_would_be_silently_inert() -> None:
    """⭐ `compile_for` had ZERO direct tests, and it is the mechanism that decides what each
    surface scans. Both of its documented refusals are pinned here.

    An override key naming no pattern (a renamed label) and an override VALUE of `""` reach the
    same bad end: the content bound comes back on a surface it was measured to false-red on, and
    nothing says so. `""` is the sharper one, because it is a plausible way to spell "disable
    this" and `"" or rx` silently means "keep the original".
    """
    with pytest.raises(ValueError, match="naming no pattern"):
        guard.compile_for({"a label that does not exist": None})
    with pytest.raises(ValueError, match="empty override"):
        guard.compile_for({"unraid pool path": ""})
    # ...and the two legitimate spellings still work.
    removed = {label for label, _ in guard.compile_for({"unraid pool path": None})}
    assert "unraid pool path" not in removed
    replaced = dict(guard.compile_for({"unraid pool path": r"ZZ-NOT-A-POOL"}))
    assert replaced["unraid pool path"].pattern == r"ZZ-NOT-A-POOL"


def test_a_NEW_pattern_reaches_every_surface_unless_an_override_says_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⭐ THE PROPERTY `compile_for` EXISTS FOR, and it was untestable through the cached
    accessors: a pattern added tomorrow must apply on the path and message surfaces too, or the
    override dicts become a silent, fail-OPEN allowlist that nobody maintains.

    ⚠️ The `cache_clear` calls are load-bearing. `path_patterns()`/`message_patterns()` are
    `functools.cache`d, so without them this test is ORDER-DEPENDENT: green if it happens to run
    before anything warms the cache, silently vacuous afterwards. The suite already knows this
    hazard — it clears `_self_rel_path` the same way.

    ⛔ AND NOTHING MAY CALL THE CACHED ACCESSORS AFTER THE `finally` AND BEFORE `monkeypatch`
    UNDOES `PATTERNS` — that would re-warm the cache WITH the sentinel and poison every later test
    in the process. So the negative is asserted BEFORE the patch, not after it, which proves the
    rebuild just as well and cannot leave anything behind.
    """
    assert guard.scan_path("docs/ZZSENTINELZZ/x.md") == [], (
        "premise: the sentinel must not match before it is added, or this proves nothing")
    monkeypatch.setattr(guard, "PATTERNS", [*guard.PATTERNS, ("sentinel", r"ZZSENTINELZZ")])
    guard.path_patterns.cache_clear()
    guard.message_patterns.cache_clear()
    try:
        assert guard.scan_path("docs/ZZSENTINELZZ/x.md"), (
            "a pattern added to PATTERNS did not reach the PATH surface")
        assert guard.scan_message("abc", "mentions ZZSENTINELZZ"), (
            "a pattern added to PATTERNS did not reach the MESSAGE surface")
    finally:
        guard.path_patterns.cache_clear()
        guard.message_patterns.cache_clear()


# ================================ #11: two inherited false reds — text that identifies nobody
# ⚠️ Every shape below is ASSEMBLED at runtime — the firing ones because this file is scanned by
# the guard it tests and only the engine's own source is exempt; the two placeholder UUIDs
# because a SIBLING guard that has not learned this carve-out yet scans this repository too, and
# a literal here would redden it until it is re-ported.
_NIL_UUID = "-".join("0" * n for n in (8, 4, 4, 4, 12))
_MAX_UUID = "-".join("f" * n for n in (8, 4, 4, 4, 12))
_USERS = "C:" + "\\Users\\"


def test_the_windows_administrator_profile_is_a_built_in_and_does_not_fire() -> None:
    """`C:\\Users\\Administrator` is the canonical Windows Server account. It names no person, it
    appears in ordinary runbooks, and it is the same class as `runneradmin`, which was added to
    the carve-out for the same reason. A guard that reddens it gets switched off."""
    for text in (f"log files land in {_USERS}Administrator\\AppData\\Local\\Temp",
                 f"posix-separated {_USERS.replace(chr(92), '/')}Administrator/Documents"):
        assert guard.scan_text(text, guard.compile_patterns()) == [], text


@pytest.mark.parametrize("account", ["Administrator2", "Administrators", "Administrator-old"])
def test_a_profile_that_merely_begins_with_administrator_still_fires(account: str) -> None:
    """The carve-out is bounded on `(?![\\w.-])`: an account somebody CREATED, however it is
    spelled, is still a profile path naming them."""
    hits = guard.scan_text(f"profile at {_USERS}{account}\\AppData", guard.compile_patterns())
    assert [label for _, label, _ in hits] == ["windows profile path"]


def test_the_nil_and_max_uuids_are_the_placeholder_convention_and_do_not_fire() -> None:
    """RFC 9562 reserves all-zeroes and all-`f` exactly as RFC 5737 reserves the documentation
    ranges: they identify nobody by construction, and they reach real repositories as a
    `DEFAULT_TENANT` sentinel, a `.csproj` `ProjectGuid` or a migration's seed row."""
    for text in (f"DEFAULT_TENANT = '{_NIL_UUID}'", f"MAX = '{_MAX_UUID}'",
                 f"<ProjectGuid>{{{_MAX_UUID.upper()}}}</ProjectGuid>"):
        assert guard.scan_text(text, guard.compile_patterns()) == [], text


def test_one_digit_off_the_nil_uuid_is_a_real_id_and_still_fires() -> None:
    nearly = _NIL_UUID[:-1] + "1"
    hits = guard.scan_text(f"TENANT={nearly}", guard.compile_patterns())
    assert [label for _, label, _ in hits] == ["uuid (access policy / tenant id)"]


def test_the_nil_uuid_grants_no_amnesty_to_a_leak_beside_it() -> None:
    """A permitted span suppresses only what it CONTAINS — the containment rule the whole allowlist
    rests on — so a real value sharing the line with the placeholder is still reported."""
    host = "host-a" + ".lan"
    hits = guard.scan_text(f"TENANT={_NIL_UUID} on {host}", guard.compile_patterns())
    assert [label for _, label, _ in hits] == ["private lan domain"]


def test_the_PATH_surface_runs_a_DIFFERENT_pattern_set_and_says_which() -> None:
    """⭐⭐ A PATH IS NOT FILE CONTENT, and three patterns rely on bounds that only hold in the
    grammar they were written for. Applied verbatim to paths they reddened trees that leak nothing
    — measured, and one of them refused a `git commit` end-to-end. `PATH_PATTERN_OVERRIDES` is the
    correction; this pins WHICH patterns it removes, so removing a fourth is a deliberate act
    rather than a quiet widening of the blind spot.

    ⚠️ ASSERTS THE COMPILED PATTERN, NOT THE LABEL. It used to check that the LABEL survived — and
    a label survives while half its regex is replaced, so a third narrowing (`.local`, dropped by
    `_LAN_ONLY`) walked straight past a test whose docstring claimed to stop exactly that.
    """
    compiled = dict(guard.path_patterns())
    assert "uuid (access policy / tenant id)" not in compiled, (
        "a UUID FILENAME is a naming convention — migrations, fixtures, cassettes, snapshots. "
        "The pattern exists for a policy or tenant id, which is a value written INSIDE a file, "
        "and the content scan still catches it there")
    assert "unraid pool path" not in compiled, (
        "a repo-relative path can never BE an absolute pool path; the pattern can only match one "
        "directory deep, where it means a docs or fixtures tree")
    assert compiled["private lan domain"].pattern == guard._LAN_ONLY, (
        "the `.lan`-only variant is a THIRD narrowing — it drops `.local` — and it must stay "
        "visible here rather than hiding behind a surviving label")
    # ...and everything else applies here UNCHANGED, byte for byte against the content pattern.
    content = dict(guard.compile_patterns())
    for label in ("private IPv4 (RFC1918)", "cgnat address", "tailnet name",
                  "personal mail address", "windows profile path"):
        assert compiled[label].pattern == content[label].pattern, label


def test_the_MESSAGE_surface_keeps_the_pool_path_but_ANCHORS_it() -> None:
    """⛔ THE POOL PATH NEEDS ONLY A LEADING SLASH, which any nested repo path supplies — so a
    commit message that merely NAMED one of the paths `_MUST_PASS_PATHS` blesses blocked the push,
    while the file and its filename both passed. That is the path-surface defect repeated one dict
    down, and a message cannot be edited without rewriting history.

    Both directions, because an anchor that swallowed the real case would be worse than the bug.
    """
    compiled = dict(guard.message_patterns())
    assert compiled["unraid pool path"].pattern == guard._ABS_POOL_PATH
    assert guard.scan_message("abc", "docs: add docs" + _MNT_USER + "/notes.md") == [], (
        "a message naming a repo path must not block the push")
    assert guard.scan_message("abc", "moved appdata to " + _MNT_USER + "/appdata/svc"), (
        "a message naming an ABSOLUTE pool path is still the leak this pattern exists for")


def test_the_path_scan_INHERITS_the_patterns_LIMITS_and_says_so() -> None:
    """⚠️ DECLARED, NOT CLAIMED AWAY. Two limits, both deliberate, both pinned so the next reader
    finds them rather than assuming coverage.

    1. `<host>.lan-runbook/` is NOT caught: the `.lan` bound rejects a following HYPHEN, and
       loosening it would fire on ordinary hyphenated directory names.
    2. A four-component VERSION directory in the `10.` range IS caught, and that is a known
       OVER-match rather than a leak: a four-part assembly version and an RFC1918 address in that
       range are the same string, and no rule separates them. `192.168.*` and `172.16-31.*` have
       no such collision, which is why the pattern stays. Rename the directory, or use
       `ALLOW_LITERALS`. Filed as #45.
    """
    assert guard.scan_path(f"docs/{_HOST}-runbook/notes.md") == [], (
        "if this now fires, the `.lan` bound changed — check the `_MUST_PASS_PATHS` corpus still "
        "passes before keeping it")
    assert guard.scan_path(f"docs/{_VERSION_4}/index.html"), (
        "the four-part version over-match is a STATED limit; if it stops firing, say so here "
        "rather than leaving the docstring claiming a limit that no longer exists")
    assert guard.scan_path("docs/1.2.3/index.html") == [], "a three-part version is not an address"


@pytest.mark.parametrize("rel", [
    "README.md",
    "templates/service.xml",
    "icons/service.png",
    "scripts/deploy.sh",
    ".github/workflows/ci.yml",
    # ⭐ The allow-spans have to apply to PATHS too: the dotenv local filename CONTAINS a
    # `private lan domain` match by construction, which is why the engine carries a span for it.
    _DOTENV_LOCAL,
    ".env.example",
    # The qualified family (a further label follows), which the CONTENT bound already handled.
    ".claude/settings.local.json",
    "src/config.local.yml",
    "compose.local.yaml",
    # ⭐⭐ THE SEGMENT-TERMINAL FAMILY, which it did NOT. These are the standard
    # machine-local-override convention, and every one of them blocked a `git commit` before
    # PATH_PATTERN_OVERRIDES existed — the tree scan reddened forever and no later commit could
    # clear it. This is the regression test for that override.
    "home/zshrc" + _LOCAL,
    "home/vimrc" + _LOCAL,
    "home/gitconfig" + _LOCAL,
    "home/tmux.conf" + _LOCAL,
    "Makefile" + _LOCAL,
    "packages/app" + _LOCAL + "/index.js",
    # A UUID FILENAME is a naming convention, not a tenant id.
    f"db/migrations/{_UUID}.sql",
    f"test/fixtures/{_UUID}.json",
    # A repo-relative path can never BE an absolute pool path — and a fixture tree mirroring the
    # Unraid layout is exactly the shape THIS repository would grow.
    "docs" + _MNT_USER + "/notes.md",
    "tests/fixtures" + _MNT_CACHE + "/appdata/svc/config.yml",
])
def test_an_ordinary_or_allow_listed_path_is_not_a_finding(rel: str) -> None:
    assert guard.scan_path(rel) == [], rel


def test_a_leak_in_a_FILENAME_reds_the_TREE_scan(tmp_path: Path) -> None:
    """⭐ consumer#20, the measured repro. CONTENT clean, NAME not.

    Measured at `main@046fc7e`: exit 0, `no internal info found (3 tracked text files scanned)`.
    """
    repo = tmp_path / "paths"
    _init(repo)
    _write(repo, "README.md", "all clean here\n")
    _write(repo, f"{_ADDR}.conf", "nothing to see\n")
    _write(repo, f"docs/{_HOST}/notes.md", "clean body\n")
    _git(repo, "add", "-A")

    # ⚠️ VACUITY GUARD. If the plants were not tracked, the assertion below would be trivially
    # satisfiable by a guard that does nothing at all.
    tracked = _git(repo, "ls-files").split()
    assert f"{_ADDR}.conf" in tracked and f"docs/{_HOST}/notes.md" in tracked, tracked
    # And the premise the whole test rests on: the CONTENT really is clean.
    for rel in tracked:
        assert guard.scan_text((repo / rel).read_text(encoding="utf-8"), COMPILED) == [], rel

    res = _cli(repo)
    assert res.returncode == 1, f"a leak in a filename was not caught:\n{_out(res)}"
    out = _out(res)
    assert "<path>" in out, (
        f"the finding must say it is a PATH, not a line inside a file:\n{out}")
    # ⭐ BOTH plants, not just whichever one happens to fire first: a filename and a DIRECTORY name
    # publish identically, and reporting only one would be half a scan.
    assert f"{_ADDR}.conf" in out and f"docs/{_HOST}/notes.md" in out, out


def test_a_leak_in_a_PATH_is_scanned_REGARDLESS_of_its_suffix(tmp_path: Path) -> None:
    """⭐ A MUTATION SURVIVOR, and the case `_scan_tree`'s own comment cites. Making `scan_path`
    skip `SKIP_SUFFIXES` paths left the whole suite GREEN — so nothing pinned the one property that
    makes the path scan worth having at an asset path: the NAME is published whether or not the
    bytes are ever read.

    All three scans, because a skip that reached only one of them would be just as silent.
    """
    repo = tmp_path / "path_suffix"
    _init(repo)
    _write(repo, "README.md", "clean\n")
    (repo / "icons").mkdir()
    (repo / "icons" / f"{_HOST}.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(200))
    _git(repo, "add", "-A")

    assert guard._binary_suffix(f"icons/{_HOST}.png"), "vacuity guard: .png must be a skip suffix"
    assert guard._looks_binary((repo / "icons" / f"{_HOST}.png").read_bytes()), (
        "vacuity guard: the CONTENT must be genuinely binary, so only the NAME can be the finding")

    staged = _cli(repo, "--staged")
    assert staged.returncode == 1, f"the staged path scan skipped an asset name:\n{_out(staged)}"
    tree = _cli(repo)
    assert tree.returncode == 1, f"the tree path scan skipped an asset name:\n{_out(tree)}"
    _git(repo, "commit", "-q", "-m", "add the icon")
    rng = _cli(repo, "--range", "HEAD")
    assert rng.returncode == 1, f"the range path scan skipped an asset name:\n{_out(rng)}"
    assert "<path>" in _out(tree), _out(tree)


def test_scan_path_has_NO_SELF_EXEMPTION(monkeypatch: pytest.MonkeyPatch) -> None:
    """⭐ A MUTATION SURVIVOR. Making `scan_path` exempt its own path left the suite green, so the
    "NO SELF-EXEMPTION" rule in its docstring was an unenforced claim.

    It matters because the self-exemption is about not READING a file that carries synthetic deny
    cases by design. Nothing about that makes the guard's own NAME exempt — renaming it to
    something that names the estate is a leak whatever the file contains.

    ⚠️ THE SELF PATH IS FAKED BY REPLACING THE RESOLVER, not a constant — there is no longer a
    constant to patch, which is itself the fix that removed the installed guard's blind spot. The
    resolver is `functools.cache`d, so replacing the whole callable (rather than patching around
    it) also avoids leaving a poisoned cache entry behind for the next test.
    """
    leaky = f"scripts/{_HOST}.py"
    monkeypatch.setattr(guard, "_self_rel_path", lambda root=None: leaky)
    assert guard._is_self(leaky), "vacuity guard: the path must really be the self path now"
    assert guard.scan_path(leaky), (
        "the guard's own path must still be SCANNED as a path — the exemption covers its "
        "CONTENT, not its name")


def test_a_leak_in_a_FILENAME_reds_the_RANGE_scan(tmp_path: Path) -> None:
    """The push-path half. The tree scan is local; this is what CI and `pre-push` run."""
    repo = tmp_path / "paths_range"
    _seeded(repo)
    _write(repo, f"{_ADDR}.conf", "nothing to see\n")
    sha = _commit(repo, "add a config")

    assert f"{_ADDR}.conf" in _git(repo, "show", "--name-only", "--format=", sha)
    res = _cli(repo, "--range", f"{sha}^..{sha}")
    assert res.returncode == 1, f"a filename leak reached the range scan clean:\n{_out(res)}"
    assert "<path>" in _out(res), _out(res)


def test_a_clean_tree_of_ordinary_paths_still_passes_BOTH_scans(tmp_path: Path) -> None:
    """⛔ THE FALSE-RED DIRECTION, which matters as much as the catch: a guard that reddens
    correct work is one that gets switched off. Every path here occurs in ordinary repositories.
    """
    repo = tmp_path / "paths_clean"
    _init(repo)
    for rel in ("README.md", _DOTENV_LOCAL, ".env.example", "icons/app.png",
                "templates/svc.xml", "src/config.local.yml", ".claude/settings.local.json",
                "scripts/deploy.sh"):
        _write(repo, rel, "clean\n")
    sha = _commit(repo, "ordinary layout")

    tree = _cli(repo)
    assert tree.returncode == 0, f"a clean tree of ordinary paths reddened:\n{_out(tree)}"
    rng = _cli(repo, "--range", sha)
    assert rng.returncode == 0, f"a clean commit of ordinary paths reddened:\n{_out(rng)}"


def test_DELETING_a_badly_named_path_is_not_reported(tmp_path: Path) -> None:
    """⛔ A DELETION PUBLISHES NOTHING NEW, and its path was published by whichever commit ADDED
    it. Reporting it would make the commit that CLEANS UP a badly-named file fail forever — the
    same trap the removed-lines rule avoids, and the reason `changed_paths` passes
    `--diff-filter=d`.
    """
    repo = tmp_path / "paths_delete"
    _seeded(repo)
    _write(repo, f"{_ADDR}.conf", "clean\n")
    _commit(repo, "add it")
    (repo / f"{_ADDR}.conf").unlink()
    removal = _commit(repo, "remove it")

    assert f"{_ADDR}.conf" in _git(repo, "show", "--name-only", "--format=", removal), (
        "vacuity guard: the removal commit must actually touch that path")
    res = _cli(repo, "--range", f"{removal}^..{removal}")
    assert res.returncode == 0, f"a commit that DELETES a bad path was punished:\n{_out(res)}"


# ===========================================================================================
# unraid-templates#39 / consumer#21 — a leak in a commit MESSAGE or a TAG
# ===========================================================================================


def test_a_leak_in_a_commit_message_BODY_reds_the_range_scan(tmp_path: Path) -> None:
    """⭐ #39, the measured repro. Diff clean, identity clean, MESSAGE not.

    Measured at `main@046fc7e`: exit 0, `no internal info added across 1 commit(s)`.
    """
    repo = tmp_path / "message_body"
    _seeded(repo)
    _write(repo, "notes.md", "nothing internal here\n")
    body = (f"subject line is clean\n\n"
            f"deployed from {_HOST} at {_ADDR}, appdata under {_POOL}\n"
            f"policy {_UUID}, contact {_MAIL}\n")
    sha = _commit_with_message(repo, tmp_path, body)

    # Vacuity guards: the message really carries it, and the DIFF really does not.
    assert _HOST in _git(repo, "show", "-s", "--format=%B", sha)
    assert guard.scan_text((repo / "notes.md").read_text(encoding="utf-8"), COMPILED) == []

    res = _cli(repo, "--range", f"{sha}^..{sha}")
    assert res.returncode == 1, f"a leak in a commit message was published:\n{_out(res)}"
    assert "<commit message>" in _out(res), _out(res)


def test_a_leak_in_a_commit_SUBJECT_reds_the_range_scan(tmp_path: Path) -> None:
    """The one-line form. `%B` covers subject and body alike; a `%b`-only read would miss this."""
    repo = tmp_path / "message_subject"
    _seeded(repo)
    _write(repo, "notes.md", "clean\n")
    sha = _commit(repo, f"release cut on {_HOST}")

    res = _cli(repo, "--range", f"{sha}^..{sha}")
    assert res.returncode == 1, f"a leak in a commit SUBJECT was published:\n{_out(res)}"
    assert "<commit message>" in _out(res), _out(res)


def test_a_leak_in_an_ANNOTATED_TAG_message_reds_the_range_scan(tmp_path: Path) -> None:
    """A tag object is pushed and rendered exactly like a commit, and carries THREE leakable
    fields — its name, its tagger and its message.

    ⭐ THE RANGE NAMES THE TAG REF, which is what a consuming repository's `pre-push` hook and its
    tag CI job both pass. See `test_a_tag_CUT_AT_AN_ALREADY_PUSHED_COMMIT_is_still_scanned` for why
    the earlier "is the tagged commit in range" scoping was the wrong question.
    """
    repo = tmp_path / "tag_message"
    _seeded(repo)
    _write(repo, "notes.md", "clean\n")
    _commit(repo, "clean subject")
    tagmsg = tmp_path / "tag-message.txt"
    tagmsg.write_bytes(f"release cut on {_HOST}\n".encode())
    _git(repo, "tag", "-a", "v9.9.9", "-F", str(tagmsg))

    assert _HOST in _git(repo, "for-each-ref", "--format=%(contents)", "refs/tags/v9.9.9")
    res = _cli(repo, "--range", "refs/tags/v9.9.9 --not --remotes")
    assert res.returncode == 1, f"a leak in an annotated tag was published:\n{_out(res)}"
    assert "<tag object>" in _out(res), _out(res)


def test_a_tag_CUT_AT_AN_ALREADY_PUSHED_COMMIT_is_still_scanned(tmp_path: Path) -> None:
    """⭐⭐ THE OPERATIVE TAG PUSH, and the case the first version of this scan missed entirely.

    Cutting a tag at a commit that is already on the remote is the ordinary release gesture — this
    repo's CI even documents it ("merging IS the release"). Scoping tags by "is the tagged commit
    in the range" therefore asked the wrong question: the range resolves to ZERO commits, so the
    tag object, its name, its tagger and its message all reached the remote while the guard printed
    `no internal info added across 0 commit(s)` and exited 0.

    Both live callers are exercised: the `pre-push` form and the CI form.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, timeout=300)
    repo = tmp_path / "tag_pushed"
    _seeded(repo)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    _git(repo, "tag", "-a", "v1.0.0", "-m", f"deployed from {_HOST}")

    # Vacuity guard: the range really does resolve to no commits, so nothing but the tag is left
    # for the scan to look at.
    assert _git(repo, "rev-list", "refs/tags/v1.0.0", "--not", "--remotes").split() == [], (
        "premise changed: the tagged commit is not already on the remote")

    hook_form = _cli(repo, "--range", "refs/tags/v1.0.0 --not --remotes")
    assert hook_form.returncode == 1, f"the pre-push form missed it:\n{_out(hook_form)}"
    ci_form = _cli(repo, "--range", "origin/main..refs/tags/v1.0.0")
    assert ci_form.returncode == 1, f"the CI form missed it:\n{_out(ci_form)}"


def test_a_LIGHTWEIGHT_tag_carries_nothing_this_package_scans(tmp_path: Path) -> None:
    """⚠️ THE OTHER HALF OF THE #49 GAP, at the guard rather than the hook.

    A lightweight tag has no object, no tagger and no message — only a NAME. This package scans
    annotated tag OBJECTS (which is what its acceptance names), so a lightweight tag contributes
    nothing to scan and its name goes unread. Pinned so the boundary is visible next to the
    annotated-tag tests that DO catch things, rather than being inferred from their absence.

    ⚠️ THE CONTRAST IS THE POINT, and it is asserted rather than described: an ANNOTATED tag with
    the SAME leaking name and a clean message IS caught, because `git cat-file tag` puts a
    `tag <name>` header inside the object. "The name is not scanned" is true only of a name with
    no object behind it.
    """
    repo = tmp_path / "tag_name"
    _seeded(repo)
    _write(repo, "notes.md", "clean\n")
    _commit(repo, "clean subject")
    _git(repo, "tag", f"release-{_HOST}")

    assert f"release-{_HOST}" in _git(repo, "tag", "-l"), "vacuity guard: the tag must exist"
    assert guard.refs_being_published(repo, f"refs/tags/release-{_HOST} --not --remotes") == [], (
        "a lightweight tag has no object to read")
    res = _cli(repo, "--range", f"refs/tags/release-{_HOST} --not --remotes")
    assert res.returncode == 0, (
        f"a leaking tag NAME is now caught — #49 has been closed, so invert this:\n{_out(res)}")

    # ...and the contrast: the SAME name as an ANNOTATED tag IS caught, via the object's header.
    _git(repo, "tag", "-a", f"annotated-{_HOST}", "-m", "an entirely clean message")
    annotated = _cli(repo, "--range", f"refs/tags/annotated-{_HOST} --not --remotes")
    assert annotated.returncode == 1, (
        f"an ANNOTATED tag's name is inside its object and must be read:\n{_out(annotated)}")
    assert "<tag object>" in _out(annotated), _out(annotated)


def test_a_leak_in_a_NESTED_tag_is_read_at_every_level(tmp_path: Path) -> None:
    """⭐ `git tag -a outer … inner` makes a CHAIN, and reading only the outermost object left the
    inner one — with its own message — published and unread once its own ref was deleted.

    The peel `%(*objectname)` goes straight to the commit, so nothing else ever looks at the middle
    object. The walk follows `object <sha>` while the type is still `tag`.
    """
    repo = tmp_path / "tag_nested"
    _seeded(repo)
    _git(repo, "-c", "advice.nestedTag=false", "tag", "-a", "inner", "-m",
         f"inner leak from {_HOST}")
    _git(repo, "-c", "advice.nestedTag=false", "tag", "-a", "outer", "-m", "release 1.0", "inner")
    _git(repo, "tag", "-d", "inner")

    assert "inner" not in _git(repo, "tag", "-l").split(), (
        "vacuity guard: the inner REF must be gone, or it would be scanned in its own right")
    res = _cli(repo, "--range", "refs/tags/outer --not --remotes")
    assert res.returncode == 1, f"the inner tag object went unread:\n{_out(res)}"


def test_a_leak_in_a_tag_on_a_BLOB_is_scanned(tmp_path: Path) -> None:
    """A tag may point at a blob or a tree, not only a commit. `%(*objectname)` peels to the BLOB,
    which is never in a commit set — so the old scoping skipped it even with the commit in range.
    """
    repo = tmp_path / "tag_blob"
    _seeded(repo)
    blob = _git(repo, "rev-parse", "HEAD:README.md").strip()
    _git(repo, "tag", "-a", "rel-1", "-m", f"cut on {_HOST}", blob)

    assert _git(repo, "cat-file", "-t", f"{blob}").strip() == "blob", "vacuity guard"
    res = _cli(repo, "--range", "refs/tags/rel-1 --not --remotes")
    assert res.returncode == 1, f"a tag on a blob went unread:\n{_out(res)}"


def test_a_leaking_REF_NAME_is_a_STATED_GAP(tmp_path: Path) -> None:
    """⚠️ A DECLARED GAP, measured rather than believed.

    A ref NAME is published, and nothing scans one. `git push origin clean:refs/tags/<host>.lan`
    puts a leaking name on a remote with the guard reporting clean.

    ⛔ THIS IS A REVERT, NOT AN OVERSIGHT — issue #49. Ref-name scanning was built and then
    withdrawn after producing a production bypass in three consecutive verification rounds: first
    a local-vs-remote ref confusion, then — once that was closed — `<host>.lan-deploy` (one
    trailing hyphen), a UUID-named ref and a `.local`-named ref all still published clean, while
    the LOCAL name produced a false red whose printed remedy was inert against it. An addition
    that cannot converge inside a package does not get to hold it.

    ⭐ DRIVEN THROUGH THE ENGINE, NOT THROUGH A HOOK. The original version of this test installed
    this repository's `.githooks` into a scratch repository and pushed. That measured the gap AND
    the wiring at once, which was right while the guard lived in the repository it protected — a
    packaged engine has no hooks of its own, and a library that invented some so a copied test
    could pass would be building a feature to satisfy a test. What travels is the ENGINE claim:
    the refs a push publishes are read for their tag OBJECTS, and a ref NAME is not read as a name.

    Pinned in BOTH directions so the revert stays deliberate: the gap is real, and an ordinary
    range scan is not reddened by any leftover of the withdrawn machinery. If someone closes #49,
    the gap assertion inverts and this docstring is what tells them why it was ever here.

    ⚠️ THE GAP IS NARROWER THAN "REF NAMES", and measuring it is what showed that. An ANNOTATED
    tag's name IS caught — not by a name scan, but because `tag <name>` is a line of the tag
    OBJECT, which is read in full. Both directions are asserted below so the boundary is a
    measured fact rather than a summary: the name inside an object is covered, and a BRANCH name
    (or a lightweight tag, which has no object at all) is not.
    """
    repo = tmp_path / "refname"
    _seeded(repo)
    _write(repo, "ok.txt", f"host {_OK_HOST}\n")
    sha = _commit(repo, "an ordinary clean commit")

    # ---- the COVERED half: an annotated tag whose NAME leaks and whose message does not.
    _git(repo, "tag", "-a", f"{_HOST}", "-m", "an entirely clean tag message")
    tagged = guard.refs_being_published(repo, f"refs/tags/{_HOST}")
    assert tagged, "vacuity guard: the tag object was not read at all"
    assert guard.scan_tags(tagged), (
        "an annotated tag's own NAME is part of its object body and must be caught with the rest "
        "of it; if this stops firing, the object is no longer being read whole")

    # ---- the GAP: a BRANCH name. There is no object to carry it, and nothing scans a ref name.
    _git(repo, "branch", f"{_HOST}-deploy")
    branch = guard.refs_being_published(repo, f"refs/heads/{_HOST}-deploy")
    assert not branch, (
        "a branch ref now yields something to scan — #49 has been closed, so invert this "
        "assertion and delete the stated gap from `refs_being_published`")

    res = _cli(repo, "--range", f"refs/heads/{_HOST}-deploy")
    assert res.returncode == 0, (
        f"a leaking BRANCH name is a stated gap, but this scan RED — either #49 has been closed "
        f"or the withdrawn machinery has left a false red behind:\n{_out(res)}")
    assert sha[:10] in _out(res) or "no internal info" in _out(res), _out(res)

def test_a_tag_THIS_PUSH_DOES_NOT_SEND_is_NOT_scanned(tmp_path: Path) -> None:
    """⛔⛔ SCOPING IS A CORRECTNESS PROPERTY IN BOTH DIRECTIONS, and this is the half that bites
    the innocent. `git push origin main` sends NO tags — only `--tags`/`--follow-tags` do — so a
    purely local scratch tag must not block an ordinary branch push.

    It did: a `wip` tag on an in-range commit reddened the push, and the remedy the guard printed
    ("rewrite the offending commits") was not even the right fix — `git tag -d` was. Scanning by
    the refs the push NAMES is what makes both directions right at once.
    """
    repo = tmp_path / "tag_not_sent"
    _seeded(repo)
    _write(repo, "old.md", "clean\n")
    old = _commit(repo, "old work")
    _write(repo, "new.md", "clean\n")
    new = _commit(repo, "new work")
    _git(repo, "tag", "-a", "wip", "-m", f"checkpoint before swapping {_HOST}")

    assert "wip" in _git(repo, "tag", "-l").split(), "vacuity guard: the bad tag must exist"
    res = _cli(repo, "--range", f"{old}..{new}")
    assert res.returncode == 0, (
        f"a local tag this push does not send reddened a clean branch push:\n{_out(res)}")


def test_clean_messages_a_MERGE_and_a_clean_TAG_all_pass(tmp_path: Path) -> None:
    """The false-red direction for the message scan: an auto-generated merge subject, a message
    quoting the DOCUMENTED placeholder spellings, and an ordinary release tag."""
    repo = tmp_path / "message_clean"
    _seeded(repo)
    _git(repo, "checkout", "-q", "-b", "side")
    _write(repo, "side.txt", "side\n")
    _commit(repo, "side change")
    _git(repo, "checkout", "-q", "main")
    _write(repo, "main.txt", "main\n")
    base = _commit(repo, f"document the example host {_OK_HOST} at {_OK_ADDR}")
    _git(repo, "merge", "--no-ff", "-q", "-m",
         "Merge pull request #1 from texasdaddy/side", "side")
    head = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "tag", "-a", "v1.0.0", "-m", f"clean release, see {_OK_HOST}")

    assert head != base, "vacuity guard: the merge must have created a commit"
    res = _cli(repo, "--range", head)
    assert res.returncode == 0, f"ordinary clean history reddened:\n{_out(res)}"


@pytest.mark.parametrize("subject", [
    # ⭐⭐ EVERY ONE OF THESE BLOCKED A PUSH, and a commit message cannot be edited without
    # rewriting history — so a false red here is far more expensive than one on a file, and the
    # guard's printed remedy ("rewrite the offending commits") was the only lever it offered.
    # Worse, a merge of someone else's branch inherits their subjects, so the block landed on
    # commits the pusher did not write. `MESSAGE_PATTERN_OVERRIDES` is the correction.
    "Rename config" + _LOCAL + " to config.defaults",
    "chore: load tmux.conf" + _LOCAL + " from the user home",
    "feat: support Avahi hostnames like printer" + _LOCAL + " on the LAN",
    f"test: add a fixture for policy id {_UUID}",
    r"ci: fix path handling for C:\Users\runneradmin\AppData\Local\Temp",
    'Revert "add the thing"\n\nThis reverts commit 3d3c42e5aac5ba805825da76410c181273ba90b1.',
    "cherry-pick the fix\n\n(cherry picked from commit 3d3c42e5aac5ba805825da76410c181273ba90b1)",
])
def test_an_ORDINARY_commit_message_does_not_block_a_push(tmp_path: Path, subject: str) -> None:
    repo = tmp_path / f"msg_ok_{abs(hash(subject))}"
    _seeded(repo)
    _write(repo, "notes.md", "clean\n")
    sha = _commit_with_message(repo, tmp_path, subject + "\n")

    # Vacuity guard: the message really is the one under test.
    assert subject.splitlines()[0] in _git(repo, "show", "-s", "--format=%B", sha)
    res = _cli(repo, "--range", f"{sha}^..{sha}")
    assert res.returncode == 0, f"an ordinary commit message blocked the push:\n{_out(res)}"


def test_the_MESSAGE_surface_runs_a_DIFFERENT_pattern_set_and_says_which() -> None:
    """The message twin of the path override test: pin WHICH patterns free prose drops, so
    dropping a further one is a deliberate act rather than a quiet widening of the blind spot.
    """
    compiled = dict(guard.message_patterns())
    assert "uuid (access policy / tenant id)" not in compiled, (
        "a message quoting a fixture id is ordinary; the id itself is caught where it LIVES")
    # ⭐ `unraid pool path` STAYS on this surface, ANCHORED — a message saying appdata moved to a
    # stock pool root really does publish the storage layout. (Unlike a PATH, where the same shape
    # can only ever be a docs or fixtures tree, so it is dropped entirely.)
    assert "unraid pool path" in compiled
    assert compiled["private lan domain"].pattern == guard._LAN_ONLY, (
        "the `.lan`-only variant drops `.local` here too — assert the PATTERN, not the label")
    content = dict(guard.compile_patterns())
    for label in ("private IPv4 (RFC1918)", "cgnat address", "tailnet name",
                  "personal mail address", "windows profile path"):
        assert compiled[label].pattern == content[label].pattern, label


def test_the_dropped_local_half_is_DECLARED_not_merely_absent() -> None:
    """⚠️ A DECLARED GAP, pinned in both directions so it stays a decision.

    `_LAN_ONLY` drops the `.local` half of `private lan domain` from BOTH new surfaces, so a
    genuine mDNS host is not caught in a filename, a directory, a message or a tag. It is not
    separable: the machine-local override filenames the corpora require to PASS are the same string
    shape. It IS caught in file content, which is where such a host is actually configured.
    """
    host = "printer-b" + _LOCAL
    assert guard.scan_path(f"docs/{host}/readme.md") == [], "declared: not caught on a path"
    assert guard.scan_message("abc", f"wire up {host}") == [], "declared: not caught in a message"
    assert guard.scan_text(f"AGENT_URL=http://{host}:9999/mcp", COMPILED), (
        "...but it MUST still be caught in file CONTENT, which is the half that makes the trade "
        "acceptable. If this ever stops firing, the gap is no longer declared, it is total")


def test_the_message_read_returns_ONLY_the_message(tmp_path: Path) -> None:
    """The message read must return the message and nothing else.

    ⚠️ WHAT THIS DOES NOT PROVE, said plainly because it used to claim otherwise. Its fixture
    commit is UNSIGNED, and `log.showSignature=true` prepends nothing for an unsigned commit — so
    setting that config here changes no output, and this test passed unchanged against a
    `commit_message` with `--no-show-signature` REMOVED. It was proving the config was harmless,
    not that the flag was doing anything.

    The flag IS proven, by a test that actually signs:
    `test_leak_guard_range.py::test_a_signed_commit_with_log_showSignature_does_not_fabricate_a_
    finding`, which carries its own premise guard asserting git really emitted the signature block.
    That one covers both call sites, because it runs the whole range scan. Recorded here so the
    coverage is findable rather than assumed from a name.

    What is left is still worth having: that the message read returns the message verbatim, with no
    trailing or leading matter, whatever else is configured.
    """
    repo = tmp_path / "showsig"
    _seeded(repo)
    _git(repo, "config", "log.showSignature", "true")
    _write(repo, "notes.md", "clean\n")
    sha = _commit(repo, "an entirely clean subject")

    assert _git(repo, "config", "log.showSignature").strip() == "true", "vacuity guard"
    text = guard.commit_message(repo, sha)
    assert text.strip() == "an entirely clean subject", (
        f"the message read picked up more than the message: {text!r}")
    res = _cli(repo, "--range", f"{sha}^..{sha}")
    assert res.returncode == 0, f"a clean commit reddened under log.showSignature:\n{_out(res)}"


def test_the_message_scan_reads_the_REAL_commit_not_a_restatement(tmp_path: Path) -> None:
    """⭐ A test must EXECUTE the thing it names. This asserts on what `git` actually returned for
    a specific sha, so deleting `commit_message`'s call site cannot leave it green.
    """
    repo = tmp_path / "message_real"
    _seeded(repo)
    _write(repo, "a.txt", "a\n")
    first = _commit(repo, "first subject")
    _write(repo, "b.txt", "b\n")
    second = _commit(repo, "second subject")

    assert guard.commit_message(repo, first).strip() == "first subject"
    assert guard.commit_message(repo, second).strip() == "second subject"
    assert guard.scan_message(second, f"host {_HOST}"), (
        "scan_message must report a leak in the text it is handed")
    assert guard.scan_message(second, f"host {_OK_HOST}") == []


# ===========================================================================================
# unraid-templates#33 / consumer#23 — the INDEX, not the worktree
# ===========================================================================================


def test_a_leak_STAGED_then_TIDIED_is_caught_by_the_staged_scan(tmp_path: Path) -> None:
    """⭐ #33, the measured repro, both halves.

    Measured at `main@046fc7e`: the tree scan exits 0 (`2 tracked text files scanned`) and
    `git show HEAD:cfg.txt` returns the leak. The tree scan's answer is HONEST — the worktree
    really is clean — which is exactly why a second question had to be asked.
    """
    repo = tmp_path / "staged"
    _seeded(repo)
    _write(repo, "cfg.txt", f"{_LEAK_LINE}\n")
    _git(repo, "add", "cfg.txt")
    _write(repo, "cfg.txt", f"AGENT_URL=http://{_OK_HOST}:9999/mcp\n")

    # Vacuity guards for BOTH premises of this test.
    assert _HOST in _git(repo, "cat-file", "blob", ":cfg.txt"), "the INDEX must hold the leak"
    assert _HOST not in (repo / "cfg.txt").read_text(encoding="utf-8"), (
        "the WORKTREE must have been tidied, or there is nothing here the tree scan misses")

    tree = _cli(repo)
    assert tree.returncode == 0, (
        f"premise changed: the tree scan is no longer blind to this:\n{_out(tree)}")
    staged = _cli(repo, "--staged")
    assert staged.returncode == 1, f"the staged leak was not caught:\n{_out(staged)}"
    assert "STAGED FOR COMMIT" in _out(staged), _out(staged)


# ⛔ THE TWO `pre-commit` HOOK TESTS THAT SAT HERE ARE GONE, DELIBERATELY.
#
# They were the class-terminating form this file argues for everywhere else: copy `scripts/` and
# `.githooks/` into a scratch repository, run a REAL `git commit` under `core.hooksPath=.githooks`,
# and assert what it DID — the commit refused and HEAD unmoved for a staged-then-tidied leak, the
# commit created for a clean index. That is the right shape and it is not a property of this
# ENGINE: it measures a repository's WIRING, and this repository ships a library rather than
# hooks. Writing hooks here so that copied tests could pass would be building a feature to satisfy
# a test.
#
# What travels is the engine half, and it is directly below and directly above: `--staged` reds an
# index whose blob leaks after the worktree was tidied, and passes an index that is clean. A
# consuming repository keeps the hook tests beside the hooks they describe.


def test_the_staged_scan_ignores_a_leak_that_is_only_in_the_WORKTREE(tmp_path: Path) -> None:
    """`--staged` answers "what will this commit record". An unstaged scratch file is not part of
    it, and reddening on one would block ordinary mid-edit work."""
    repo = tmp_path / "staged_unstaged"
    _seeded(repo)
    _write(repo, "new.txt", f"host {_OK_HOST} addr {_OK_ADDR}\n")
    _git(repo, "add", "new.txt")
    _write(repo, "scratch.txt", f"{_LEAK_LINE}\n")   # never staged

    assert "scratch.txt" not in _git(repo, "diff", "--cached", "--name-only"), "vacuity guard"
    res = _cli(repo, "--staged")
    assert res.returncode == 0, f"an UNSTAGED leak blocked the commit:\n{_out(res)}"


def test_the_staged_scan_catches_a_leak_in_a_STAGED_PATH(tmp_path: Path) -> None:
    """The path surface applies to the index too, or `git add <bad-name>` slips the commit-time
    layer the same way it slipped the other two."""
    repo = tmp_path / "staged_path"
    _seeded(repo)
    _write(repo, f"{_ADDR}.conf", "clean content\n")
    _git(repo, "add", "-A")

    res = _cli(repo, "--staged")
    assert res.returncode == 1, f"a staged leaky PATH was not caught:\n{_out(res)}"
    assert "<path>" in _out(res), _out(res)


def test_the_staged_scan_works_on_a_repository_with_no_HEAD(tmp_path: Path) -> None:
    """⛔ THE INITIAL COMMIT is when a whole tree is staged at once, so a `--staged` that dies on
    a missing HEAD would leave the first and largest commit unscanned by this layer.

    ⚠️ ASSERTS THE FINDING, NOT JUST THE EXIT CODE. This used to check `returncode == 1` alone —
    which an uncaught Python traceback ALSO satisfies. Under a mutation that always used `HEAD` as
    the base, the guard died with a `CalledProcessError` and this test still passed: it was
    reporting a crash as a catch. A verdict test has to look at what the process SAID.
    """
    repo = tmp_path / "staged_no_head"
    _init(repo)
    _write(repo, "cfg.txt", f"{_LEAK_LINE}\n")
    _git(repo, "add", "-A")

    assert not guard.resolves(repo, "HEAD"), "vacuity guard: this repo must have no HEAD yet"
    res = _cli(repo, "--staged")
    assert res.returncode == 1, f"the first commit was not scanned:\n{_out(res)}"
    assert "cfg.txt" in _out(res), (
        f"exit 1 without naming the file is a crash, not a catch:\n{_out(res)}")
    assert "Traceback" not in _out(res), f"the guard crashed rather than reporting:\n{_out(res)}"


def test_the_staged_scan_FAILS_CLOSED_on_content_it_cannot_read(tmp_path: Path) -> None:
    """⭐ A MUTATION SURVIVOR. Every other `--staged` test reaches exit 1 through `findings`, so
    making `_scan_staged` ignore `unscannable` in its exit code left the suite green — the
    fail-closed half of the commit-time layer was entirely unproven, and a staged blob the scan
    could not read would have been cleared.

    A non-skipped suffix, so `_skipped` cannot swallow it, and NUL-bearing, so `resolve_unscannable`
    cannot rescue it with a `--text` re-diff either.
    """
    repo = tmp_path / "staged_failclosed"
    _seeded(repo)
    (repo / "notes.txt").write_bytes(f"{_LEAK_LINE}\n".encode("utf-16-le"))
    _git(repo, "add", "-A")

    assert not guard._binary_suffix("notes.txt"), "vacuity guard: .txt must NOT be a skip suffix"
    res = _cli(repo, "--staged")
    assert res.returncode == 1, f"unreadable staged content was cleared:\n{_out(res)}"
    assert "NOT CLEARED" in _out(res), _out(res)
    assert "notes.txt" in _out(res), _out(res)


def test_the_staged_scan_RESOLVES_a_gitattributes_binary_text_file(tmp_path: Path) -> None:
    """⛔ THE FALSE-RED THIS LAYER WOULD OTHERWISE HAVE INTRODUCED, and the reason the `--text`
    re-diff is shared rather than left inline in `added_lines`.

    Marking a generated lockfile `binary` in `.gitattributes` is standard practice, and git then
    answers `Binary files … differ` for an ordinary UTF-8 text file. The range scan has always
    re-diffed those with `--text` and scanned them properly; a `--staged` that did not would have
    reported the same file "not scanned, so NOT CLEARED" and blocked every commit touching it —
    a red on correct work, in the layer a developer meets most often.

    Both directions are asserted: the clean lockfile passes, and a leak inside one is still found.
    """
    repo = tmp_path / "staged_lock"
    _seeded(repo)
    _write(repo, ".gitattributes", "*.lock binary\n")
    _write(repo, "deps.lock", "resolved = 1\n")
    _git(repo, "add", "-A")

    # Vacuity guard: git must really be calling it binary, or this tests nothing.
    assert "Binary files" in _git(repo, "diff", "--cached", "--no-ext-diff", "HEAD"), (
        "the .gitattributes binary marking did not take effect")
    clean = _cli(repo, "--staged")
    assert clean.returncode == 0, (
        f"an ordinary text file marked `binary` blocked the commit:\n{_out(clean)}")

    _write(repo, "deps.lock", f"resolved = 1\nregistry = http://{_HOST}/simple\n")
    _git(repo, "add", "-A")
    dirty = _cli(repo, "--staged")
    assert dirty.returncode == 1, (
        f"a leak inside a `binary`-marked TEXT file was cleared:\n{_out(dirty)}")


def test_the_staged_scan_passes_a_clean_index(tmp_path: Path) -> None:
    repo = tmp_path / "staged_clean"
    _seeded(repo)
    _write(repo, "docs/notes.md", f"see {_OK_HOST} and {_OK_ADDR}\n")
    _git(repo, "add", "-A")
    res = _cli(repo, "--staged")
    assert res.returncode == 0, f"a clean index was refused:\n{_out(res)}"


# ===========================================================================================
# consumer#22 — the suffix is a hint, and a scan that read nothing is not clean
# ===========================================================================================


def test_looks_binary_asks_the_BYTES(tmp_path: Path) -> None:
    """The pure half. A NUL anywhere, or bytes that are not UTF-8, mean binary; ASCII does not."""
    assert guard._looks_binary(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    assert guard._looks_binary(b"plain text with a nul \x00 inside")
    assert guard._looks_binary(b"\xff\xd8\xff\xe0latin-1 \xe9 bytes")
    assert not guard._looks_binary(b"an ordinary ASCII runbook\n")
    assert not guard._looks_binary("accented but UTF-8: café\n".encode())


def test_an_ASCII_leak_in_a_pdf_NAMED_file_reds_the_TREE_scan(tmp_path: Path) -> None:
    """⭐ consumer#22, the measured repro. Renaming a text file must not defeat the guard.

    Measured at `main@046fc7e`: exit 0, `no internal info found (1 tracked text files scanned)`.
    """
    repo = tmp_path / "suffix_tree"
    _init(repo)
    _write(repo, "README.md", "clean\n")
    _write(repo, "deploy-notes.pdf", f"host {_HOST}\npool {_POOL}\naddr {_ADDR}\n")
    _git(repo, "add", "-A")

    assert guard._binary_suffix("deploy-notes.pdf"), "vacuity guard: .pdf must be a skip suffix"
    assert not guard._looks_binary((repo / "deploy-notes.pdf").read_bytes()), (
        "vacuity guard: the plant must really be text")

    res = _cli(repo)
    assert res.returncode == 1, f"an ASCII leak named .pdf walked past the tree scan:\n{_out(res)}"


def test_an_ASCII_leak_in_a_pdf_NAMED_file_reds_the_RANGE_scan(tmp_path: Path) -> None:
    repo = tmp_path / "suffix_range"
    _seeded(repo)
    _write(repo, "deploy-notes.pdf", f"host {_HOST}\npool {_POOL}\n")
    sha = _commit(repo, "add notes")

    res = _cli(repo, "--range", f"{sha}^..{sha}")
    assert res.returncode == 1, (
        f"an ASCII leak named .pdf walked past the range scan:\n{_out(res)}")


def test_a_GENUINELY_binary_asset_is_still_skipped_in_silence(tmp_path: Path) -> None:
    """⛔ THE FALSE-RED DIRECTION. The suffix list exists so an image is not reported as
    "unreadable, so not cleared" on every run; sniffing content must not cost that."""
    repo = tmp_path / "suffix_binary"
    _init(repo)
    _write(repo, "README.md", "clean\n")
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)) * 4
           + _HOST.encode() + b"\x00\xff\xfe")
    (repo / "icons").mkdir()
    (repo / "icons" / "logo.png").write_bytes(png)
    _git(repo, "add", "-A")

    assert guard._looks_binary(png), "vacuity guard: the plant must really be binary"
    res = _cli(repo)
    assert res.returncode == 0, f"an ordinary image reddened the tree scan:\n{_out(res)}"
    assert "UNREADABLE" not in _out(res), _out(res)


def test_a_NUL_bearing_blob_at_a_SKIPPED_suffix_is_still_skipped(tmp_path: Path) -> None:
    """⚠️ THE STATED SCOPE OF THIS CHANGE, pinned so it is discoverable rather than folklore.

    `_looks_binary` treats a NUL as binary, so a UTF-16 payload hidden in a file named `.pdf` is
    skipped silently — exactly as it was before this change, no better and no worse. At any OTHER
    path a NUL-bearing blob is still REFUSED and reported (the #242 posture). Widening this means
    re-litigating which suffixes are assets, which is issue #38's design call and not this one's.
    """
    repo = tmp_path / "suffix_nul"
    _init(repo)
    _write(repo, "README.md", "clean\n")
    utf16 = f"{_LEAK_LINE}\n".encode("utf-16-le")
    (repo / "notes.pdf").write_bytes(utf16)
    (repo / "notes.txt").write_bytes(utf16)
    _git(repo, "add", "-A")

    res = _cli(repo)
    assert res.returncode == 1, "a NUL-bearing .txt must still be refused"
    out = _out(res)
    assert "notes.txt" in out, f"the #242 refusal no longer fires on an ordinary path:\n{out}"
    assert "notes.pdf" not in out, (
        f"a declared binary asset is skipped silently, not reported:\n{out}")


def test_a_run_that_scanned_ZERO_files_is_an_ERROR_not_a_pass(tmp_path: Path) -> None:
    """⭐ consumer#22, second half. A scan that opened no file cannot clear a tree.

    Measured at `main@046fc7e`: `no internal info found (0 tracked text files scanned)`, exit 0 —
    a cheerful pass in the same words a real one uses. The count was printed all along; nothing
    acted on it.
    """
    repo = tmp_path / "floor"
    _init(repo)
    (repo / "only.pdf").write_bytes(b"\x89PDF\x00\x00binary-ish\x00\xff\xfe")
    _git(repo, "add", "-A")

    assert _git(repo, "ls-files").split() == ["only.pdf"], "vacuity guard"
    res = _cli(repo)
    assert res.returncode == 1, f"a scan that read nothing reported clean:\n{_out(res)}"
    assert "ZERO" in _out(res), _out(res)


def test_the_floor_does_not_fire_on_an_ordinary_tree(tmp_path: Path) -> None:
    """The false-red direction for the floor: one readable text file is enough."""
    repo = tmp_path / "floor_ok"
    _init(repo)
    _write(repo, "README.md", "clean\n")
    (repo / "logo.png").write_bytes(b"\x89PNG\x00\x00binary")
    _git(repo, "add", "-A")
    res = _cli(repo)
    assert res.returncode == 0, f"an ordinary tree tripped the zero-scan floor:\n{_out(res)}"


def test_the_floor_does_not_refuse_an_EMPTY_tree(tmp_path: Path) -> None:
    """⛔ AN EMPTY TRACKED LIST IS NOT "a scan that skipped everything" — it is a scan with nothing
    to do, and `git commit --allow-empty -m initial` is the standard way to start a repository.

    Without the `tracked and` guard the floor refused exactly that, so a fresh repo with the hooks
    installed could not be bootstrapped at all. The acceptance the floor exists for says "a
    0-files-scanned run against a NON-EMPTY tree", and the empty tree is the case it deliberately
    does not name.
    """
    repo = tmp_path / "floor_empty"
    _init(repo)

    assert _git(repo, "ls-files").split() == [], "vacuity guard: nothing may be tracked"
    res = _cli(repo)
    assert res.returncode == 0, f"an empty repository was refused:\n{_out(res)}"


def test_an_unstaged_rm_of_a_BINARY_ASSET_does_not_red(tmp_path: Path) -> None:
    """⛔ THE FALSE RED THE SUFFIX SPLIT INTRODUCED, and it was reachable by an everyday gesture.

    `rm icons/logo.png` without staging the deletion is ordinary mid-edit work. Once the suffix
    stopped gating the loop, that file reached the staged-blob branch, failed to decode, and
    REDDENED the commit — with a remedy that was inert, because it said "add its suffix to
    SKIP_SUFFIXES" and `.png` already is one. The staged blob now goes through the same
    bytes-decide rule as the worktree read, so one answer covers both sources.

    The `.pdf`-of-text half is asserted too: the fix must not re-open consumer#22 through the
    staged-blob door.
    """
    repo = tmp_path / "rm_asset"
    _init(repo)
    _write(repo, "README.md", "clean\n")
    (repo / "icons").mkdir()
    (repo / "icons" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(300))
    _write(repo, "notes.pdf", f"host {_HOST}\n")
    _git(repo, "add", "README.md", "icons/logo.png")
    _git(repo, "commit", "-q", "-m", "base")

    (repo / "icons" / "logo.png").unlink()
    assert "icons/logo.png" in _git(repo, "ls-files").split(), "vacuity guard: still tracked"
    assert not (repo / "icons" / "logo.png").exists(), "vacuity guard: gone from the worktree"

    res = _cli(repo)
    assert res.returncode == 0, (
        f"an unstaged rm of an ordinary image reddened the tree scan:\n{_out(res)}")

    # ...and the same branch must still REPORT a staged-but-absent file it genuinely cannot read.
    _git(repo, "add", "notes.pdf")
    (repo / "notes.pdf").unlink()
    res = _cli(repo)
    assert res.returncode == 1, (
        f"an absent .pdf whose staged blob is TEXT must still be scanned:\n{_out(res)}")
    assert _HOST in _out(res), _out(res)


# ===========================================================================================
# The engine's own wiring — assert what the scan DID, not what a constant holds
# ===========================================================================================


def test_the_range_scan_reads_ALL_FIVE_surfaces_of_every_commit(tmp_path: Path) -> None:
    """⭐ THE CLASS-TERMINATING INVARIANT for this package, in the style of
    `test_EVERY_git_diff_the_scan_runs_carries_the_pinned_config_and_flags`: run the real
    `scan_range` and record which readers it actually called, per commit.

    A per-surface test proves each reader WORKS; this proves `scan_range` still CALLS each of them,
    once per commit in range and not once for the repository. Those are different failures: a
    reader can be correct and unwired, and a wired reader can be called for the wrong commits.

    ⚠️ NOT claimed: that deleting a line from `scan_range` would leave every other test green.
    Several of them drive the CLI end-to-end and would red too. This one localises the failure to
    the wiring instead of leaving it to be inferred from a scan-wide exit code — which is worth
    having on its own, without the overstatement.
    """
    repo = tmp_path / "surfaces"
    _seeded(repo)
    _write(repo, "a.txt", "a\n")
    first = _commit(repo, "first")
    _write(repo, "b.txt", "b\n")
    second = _commit(repo, "second")

    called: dict[str, list[str]] = {"added": [], "paths": [], "identity": [], "message": [],
                                    "tags": []}
    originals = {name: getattr(guard, name) for name in
                 ("added_lines", "changed_paths", "commit_identity", "commit_message",
                  "refs_being_published")}

    def wrap(name: str, key: str, index: int):
        real = originals[name]

        def spy(*args, **kw):
            called[key].append(str(args[index]) if len(args) > index else "")
            return real(*args, **kw)
        return spy

    guard.added_lines = wrap("added_lines", "added", 1)
    guard.changed_paths = wrap("changed_paths", "paths", 2)
    guard.commit_identity = wrap("commit_identity", "identity", 1)
    guard.commit_message = wrap("commit_message", "message", 1)
    guard.refs_being_published = wrap("refs_being_published", "tags", 1)
    try:
        result = guard.scan_range(repo, f"{first}..{second}", COMPILED)
    finally:
        for name, real in originals.items():
            setattr(guard, name, real)

    assert result.commits == 1, result
    for key in ("added", "paths", "identity", "message"):
        assert called[key] == [second], (
            f"{key} was not read for exactly the commit in range: {called[key]}")
    assert len(called["tags"]) == 1, f"the tag pass did not run once: {called['tags']}"


def test_the_staged_scan_reads_both_the_index_DIFF_and_its_PATHS(tmp_path: Path) -> None:
    """The same invariant for `--staged`: it is two questions, and dropping either is silent."""
    repo = tmp_path / "staged_surfaces"
    _seeded(repo)
    _write(repo, "cfg.txt", "clean\n")
    _git(repo, "add", "-A")

    parsed, paths = guard.staged_diff(repo)
    assert paths == ["cfg.txt"], paths
    assert [c for _, _, c in parsed.added] == ["clean"], parsed.added


def test_changed_paths_excludes_deletions_and_keeps_modifications(tmp_path: Path) -> None:
    repo = tmp_path / "changed_paths"
    _seeded(repo)
    _write(repo, "keep.txt", "one\n")
    _write(repo, "gone.txt", "one\n")
    base = _commit(repo, "add two")
    _write(repo, "keep.txt", "two\n")
    (repo / "gone.txt").unlink()
    head = _commit(repo, "modify one, delete the other")

    got = guard.changed_paths(repo, base, head)
    assert got == ["keep.txt"], (
        f"a deletion must not be reported and a modification must be: {got}")

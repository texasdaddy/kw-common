"""The leak guard must scan the COMMITS being pushed, not just the working tree.

WHY THIS FILE EXISTS
    `scripts/check_no_internal_info.py` scanned `git ls-files` and nothing else. That answers
    "is the leak here NOW", and it has two blind spots that a push turns permanent:

    1. A BRAND-NEW FILE IS NOT TRACKED YET. `git ls-files` lists the index, so a file that has
       not been `git add`ed is not scanned, not reported unreadable, and not counted — it is
       simply absent from the run that prints "no internal info found". Run the guard before
       staging and it clears a tree that does not contain the very file you just wrote. That is
       how a synthetic internal hostname reached a public remote.
    2. A VALUE COMMITTED AND THEN DELETED is absent from the tree too, and stays permanently
       readable at the commit that added it once the branch reaches the remote.

    Both are recoverable only by a history rewrite and a force-push across every clone.

    `test_a_leak_in_a_BRAND_NEW_UNTRACKED_file_...` and
    `test_a_leak_added_then_deleted_...` are the tests that would have caught them. Each
    asserts BOTH halves — the tree scan's blindness AND the range scan closing it — so they
    fail if the range scan is removed and they document why the tree scan alone was never
    enough.

SHAPE
    The diff PARSING is a pure function (`parse_diff`), so most of this runs with no repository
    at all. The tests that build a real repo do so because the claim is about real git history,
    and a synthetic diff would be assuming the thing under test. Those are slow on Windows — a
    commit costs seconds once a virus scanner is watching `.git` — so they carry explicit
    timeouts.

⚠️ EVERY LEAKING LITERAL HERE IS BUILT AT RUNTIME, and every one of them is synthetic
    (`.invalid` is reserved by RFC 2606; the addresses are implausible). Written out whole, they
    would make THIS FILE a finding of the very scanner it tests: the guard skips at most one
    file, its own source, and the working-tree scan runs on every commit.

    ⭐ THAT MATTERS MORE SINCE THE ENGINE WAS EXTRACTED, not less. The guard now runs from an
    INSTALLED package as often as from a checkout, and an installed guard is not inside the
    repository it scans — so it recognises NO file there as its own and skips nothing. A test
    file that spelled a deny case out whole would red the repository it lives in, and there
    would be no self-exemption to fall back on.

    ⭐ THE DENYLIST IS SHAPES ONLY, so there is no longer a real token for this file to avoid
    naming. It used to hold real values — first as plaintext, then base64-encoded — and both
    forms shipped those values in a public repo. They now live in a guard kept in the project
    working directory that is never committed anywhere, and that guard is what runs against the
    staged tree before every push.

    That relocation is why `test_guard_source_carries_no_plaintext_token` is gone: a repository
    that no longer knows the real tokens cannot assert their absence, and a test that pretended
    to would be checking nothing. What CAN be asserted from here is that no real-literal denylist
    has crept back in at all — see `test_the_guard_carries_no_real_literal_denylist` and
    `test_the_denylist_is_the_agreed_shape_set`, which pin the structure rather than the values.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kw_common import leakguard as guard

# ⭐ AN ORDINARY IMPORT NOW, and that is the whole point of the extraction. This suite used to
# load the guard from `scripts/check_no_internal_info.py` by path, because `scripts/` is not a
# package — which is exactly what a consuming repository had to do with its own COPY of the file.
# The engine is `kw_common.leakguard`; a consumer pins a version instead of copying it.
#
# `_SCRIPT` remains, because several tests here run the guard as a SUBPROCESS from a relocated
# copy, which is how the self-exemption is measured. It is the module's own file, resolved the
# way the guard resolves it.
_SCRIPT = Path(guard.__file__).resolve()

# ⭐ A ROOT THE GUARD'S OWN FILE IS REALLY INSIDE, derived rather than assumed. The self-exemption
# is now "is this file THIS file, relative to the repository being scanned", so a test about it
# needs a root that actually contains the module — and where that is depends on how the package
# was installed (a checkout, an editable install, `site-packages`). Its own parent directory is
# such a root under every layout, and asking the guard for the answer means these tests cannot
# drift from it by hardcoding a path that used to be right.
_GUARD_ROOT = _SCRIPT.parent
_GUARD_REL = guard._self_rel_path(_GUARD_ROOT)
assert _GUARD_REL is not None, (
    f"the guard does not recognise itself under its own parent directory ({_GUARD_ROOT}); every "
    f"self-exemption test below would be vacuous")

# ⚠️ THE REPOSITORY, derived from THIS FILE rather than from the guard's. It used to be
# `_REPO_ROOT`, which was the repository root only while the guard lived in `scripts/`.
# The module is installed now — possibly in `site-packages`, arbitrarily far from the checkout —
# so the only reliable anchor for "the repository this suite belongs to" is the suite itself.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# The path a repository in this fleet used to VENDOR the guard at. It is the decoy path in the
# self-exemption tests, and it is named here rather than inline because its significance is
# historical: it is what the deleted `SELF_PATH` constant pointed at, so it is the exact path an
# installed guard used to hand a silent amnesty to.
_VENDORED_REL = "scripts/check_no_internal_info.py"

# ⛔ `guard.compile_patterns()`, NOT an inlined `re.compile(rx, re.IGNORECASE)`. This file used
# to inline the identical line, which meant the tests held their OWN correct copy of the flags:
# dropping `re.IGNORECASE` from the shipped scan would have left this suite and CI entirely green
# while a real repository containing an uppercase LAN host or a mixed-case freemail address
# scanned clean and exited 0. The tests must exercise what ships.
COMPILED = guard.compile_patterns()

# ⚠️ SPLIT FRAGMENTS AGAIN, AND FOR A BETTER REASON THAN THE FIRST TIME.
#
# These were fragments (`"rein" + "lie"`), then a read-back from the guard's base64 token table.
# Both were workarounds for one underlying problem: the denylist held REAL values, so this file
# could not name a deny case without republishing one. That problem is gone — the denylist is
# shapes only. Every value below is synthetic.
#
# They stay split because THIS FILE IS SCANNED BY THE GUARD. Only
# `scripts/check_no_internal_info.py` is exempt (SELF_PATH); a test file that spelled a deny case
# out whole would make the guard fail on its own test suite. Neither fragment matches alone.
_POOL = "/mnt/" + "apps"
_LEAK = f"{_POOL}/appdata/example/data"
# Matches "private lan domain". A `.lan` name is private by definition and resolves nowhere off
# the network that defines it.
_HOST = "nas-a." + "lan"
# Matches "private IPv4 (RFC1918)". Synthetic: .77.77 is not a host on any network here.
_ADDR = "192.168." + "77.77"


def _added(diff: str) -> list[tuple[str, int, str]]:
    return guard.parse_diff(diff).added


def _binary(diff: str) -> list[str]:
    return guard.parse_diff(diff).unscannable


def _hits(sha: str, added: list[tuple[str, int, str]]) -> list[str]:
    return guard.scan_added(sha, guard.ParsedDiff(added, []), COMPILED)[0]


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo, capture_output=True, check=True)
    return out.stdout.decode("utf-8", errors="replace")


def _tree_findings(repo: Path) -> list[str]:
    """The tree scan's own logic, minus `main()`'s printing: what `git ls-files` yields, skipping
    what the guard skips. Deliberately mirrors `main()` so the assertions below are about the
    real code path and not a convenient stand-in."""
    out = []
    for p in guard.tracked_files(repo):
        rel = p.relative_to(repo).as_posix()
        # ⚠️ `repo` and `rel` ARE PASSED, matching production exactly. Dropping them made this
        # stand-in skip MORE files and scan LESS than `_scan_tree` does — and since it is used to
        # establish NEGATIVE premises ("the files are clean, only the author is not"), a laxer
        # stand-in makes those premises easier to satisfy, which is the wrong direction.
        if guard._skipped(rel, repo):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        out += [f"{rel}:{n}: {label}" for n, label, _ in guard.scan_text(text, COMPILED, rel)]
    return out


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")


def _merge_expecting_conflict(repo: Path, branch: str) -> None:
    """Merge `branch`, tolerating the non-zero exit a conflict produces.

    ⚠️ THE IDENTITY FLAGS ARE LOAD-BEARING, and leaving them off passed locally and failed on CI.
    A bare `git merge` is refused outright where no global `user.email` is configured — which is
    every CI runner — so the merge never happened, no conflict state existed, and the test either
    failed on its precondition or, worse, PASSED VACUOUSLY because the thing it was counting was
    never created. `_git` injects an identity but uses `check=True`, and a conflicting merge exits
    non-zero by design, so this is the one git call that needs both.
    """
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "merge", branch],
        cwd=repo, capture_output=True, timeout=120)


# ------------------------------------------------------------------ THE regression (untracked)


@pytest.mark.timeout(300)
def test_a_leak_in_a_BRAND_NEW_UNTRACKED_file_is_invisible_to_the_tree_scan_and_caught_by_range(
    tmp_path: Path,
):
    """⭐ THE ONE THIS BATCH EXISTS FOR, on a real repository because the claim is about `git
    ls-files` and real history.

    The sequence that leaked: write a new file, run the guard (it reads the INDEX, which does
    not contain the file yet, and prints "no internal info found"), then `git add` + commit +
    push. Nothing ever looked at the file's contents.

    Three halves are asserted:
      1. the file is genuinely untracked — `tracked_files` does not list it;
      2. the tree scan therefore reports NOTHING, which is the blind spot, not a bug in it;
      3. once committed, the range scan reports it and names the commit to rewrite.

    Delete the range scan and (3) fails. "Fix" the tree scan to somehow see untracked files and
    (2) tells you the premise changed.
    """
    repo = tmp_path / "untracked"
    _init(repo)
    (repo / "README.md").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()

    # The new file. NOT staged — this is the state the guard was run in.
    leaky = repo / "templates" / "new-service.xml"
    leaky.parent.mkdir(parents=True, exist_ok=True)
    leaky.write_text(
        f'<Config Name="Data" Target="/data" Default="{_LEAK}"/>\n'
        f"<!-- reachable at {_HOST} ({_ADDR}) -->\n",
        encoding="utf-8",
    )

    # (1) genuinely untracked.
    tracked = [p.relative_to(repo).as_posix() for p in guard.tracked_files(repo)]
    assert "templates/new-service.xml" not in tracked, (
        "the premise is broken: the file is supposed to be UNTRACKED at this point"
    )

    # (2) so the tree scan clears a tree that does not contain it. This is the leak.
    assert _tree_findings(repo) == [], (
        "the tree scan is expected to MISS an unstaged file — it reads the index, and that "
        "blindness is the entire reason --range exists"
    )

    # ...and now it gets committed, exactly as it did when this leaked.
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add the new template")
    head = _git(repo, "rev-parse", "HEAD").strip()

    # (3) the range scan sees what the commit ADDED, so a new file's first commit is all of it.
    result = guard.scan_range(repo, f"{base}..{head}", COMPILED)
    assert result.commits == 1
    assert result.unscannable == []
    labels = " ".join(result.findings)
    assert "unraid pool path" in labels, f"the pool path was not caught: {result.findings}"
    assert "private lan domain" in labels, f"the hostname was not caught: {result.findings}"
    assert "private IPv4 (RFC1918)" in labels, f"the address was not caught: {result.findings}"
    assert all("templates/new-service.xml" in f for f in result.findings)
    assert all(head[:10] in f for f in result.findings), (
        "the offending COMMIT must be named — that is what gets rewritten"
    )


@pytest.mark.timeout(300)
def test_the_CLI_catches_the_untracked_file_case_and_exits_1(tmp_path: Path):
    """The same regression through the entry point CI and the hook actually call. The exit code
    IS the contract: a scan that finds this and returns 0 gates nothing."""
    repo = tmp_path / "untracked-cli"
    _init(repo)
    (repo / "README.md").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()

    (repo / "notes.md").write_text(f"see {_HOST}\n", encoding="utf-8")
    # The tree scan, run BEFORE staging, is clean — reproduce that explicitly.
    before_add = _cli(repo)
    assert before_add.returncode == 0, (
        "the pre-`git add` tree scan is expected to pass; if it now fails, this test's premise "
        f"changed:\n{before_add.stdout}"
    )
    assert "no internal info found" in before_add.stdout

    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adds an untracked-until-now file")
    head = _git(repo, "rev-parse", "HEAD").strip()

    # After `git add` the tree scan would now catch it too — assert that, because it is the
    # reason "run the scan AFTER git add" is the documented order.
    after_add = _cli(repo)
    assert after_add.returncode == 1, (
        f"once tracked, the tree scan must see it:\n{after_add.stdout}"
    )

    ranged = _cli(repo, "--range", f"{base}..{head}")
    assert ranged.returncode == 1, f"the range scan must fail on this:\n{ranged.stdout}"
    assert "pushing publishes HISTORY" in ranged.stdout
    assert "private lan domain" in ranged.stdout


# --------------------------------------------------------------- the added-then-deleted case


@pytest.mark.timeout(300)
def test_a_leak_added_then_deleted_passes_the_tree_scan_and_fails_the_range_scan(tmp_path: Path):
    """The other tree-scan blind spot: a value that is gone from the tree but permanent in the
    history. Both halves asserted, same reasoning as the untracked case."""
    repo = tmp_path / "throwaway"
    _init(repo)

    def commit(body: str, message: str) -> str:
        (repo / "notes.md").write_text(body, encoding="utf-8")
        _git(repo, "add", "notes.md")
        _git(repo, "commit", "-qm", message)
        return _git(repo, "rev-parse", "HEAD").strip()

    base = commit("nothing here\n", "base")
    commit(f"host path is {_LEAK}\n", "adds a leak")
    head = commit("nothing here\n", "removes it again")

    assert _tree_findings(repo) == [], (
        "the tree scan is expected to MISS this — it is the other reason --range exists")

    result = guard.scan_range(repo, f"{base}..{head}", COMPILED)
    assert result.commits == 2
    assert result.unscannable == []
    assert len(result.findings) == 1, f"expected one added leak, got {result.findings}"
    assert "unraid pool path" in result.findings[0]


# --------------------------------------------------------------------- the diff parser


def test_added_lines_are_returned_with_their_new_file_line_numbers():
    diff = (
        "diff --git a/a.md b/a.md\n"
        "--- a/a.md\n"
        "+++ b/a.md\n"
        "@@ -0,0 +7,2 @@\n"
        "+first\n"
        "+second\n"
    )
    assert _added(diff) == [("a.md", 7, "first"), ("a.md", 8, "second")]


def test_removed_lines_are_ignored():
    """Reporting removals would make every cleanup commit fail forever — the guard would punish
    the fix and get switched off."""
    diff = (
        "diff --git a/a.md b/a.md\n"
        "--- a/a.md\n"
        "+++ b/a.md\n"
        "@@ -3,1 +3,0 @@\n"
        f"-{_LEAK}\n"
    )
    assert _added(diff) == []


def test_a_deleted_file_contributes_nothing():
    diff = (
        "diff --git a/gone.md b/gone.md\n"
        "--- a/gone.md\n"
        "+++ /dev/null\n"
        "@@ -1,1 +0,0 @@\n"
        f"-{_LEAK}\n"
    )
    assert _added(diff) == []


def test_a_removed_line_and_an_added_line_cannot_FAKE_a_file_header():
    """GUARD ATTACK. A removed content line beginning `-- ` renders as `--- …`; an added one
    beginning `++ ` renders as `+++ …`. The pair is byte-identical to a real header, so a parser
    keying on it reads `logo.png` as the current file — which `scan_added` then skips as a binary
    asset, and a genuine leak on the very next line scans CLEAN. Taking the path from
    `diff --git` and gating on `in_hunk` closes it structurally: that is git's own grammar."""
    diff = (
        "diff --git a/notes.md b/notes.md\n"
        "--- a/notes.md\n"
        "+++ b/notes.md\n"
        "@@ -2 +2,3 @@ line one\n"
        "--- was a caption\n"
        "+++ logo.png\n"
        f"+{_LEAK}\n"
    )
    added = _added(diff)
    assert [a[0] for a in added] == ["notes.md", "notes.md"], (
        f"the faked header stole the path: {added}")
    assert _hits("abc", added), "the leak after a faked header was not reported"


def test_multiple_hunks_and_files_each_restart_their_line_count():
    diff = (
        "diff --git a/a.md b/a.md\n"
        "--- a/a.md\n"
        "+++ b/a.md\n"
        "@@ -0,0 +2,1 @@\n"
        "+in a\n"
        "@@ -0,0 +40,1 @@\n"
        "+also in a\n"
        "diff --git a/b.md b/b.md\n"
        "--- a/b.md\n"
        "+++ b/b.md\n"
        "@@ -0,0 +1,1 @@\n"
        "+in b\n"
    )
    assert _added(diff) == [
        ("a.md", 2, "in a"), ("a.md", 40, "also in a"), ("b.md", 1, "in b")]


# --------------------------------------------------------------- what gets scanned


def test_scan_added_reports_a_leak_with_its_file_and_line():
    findings = _hits("abcdef1234567890", [("notes.md", 12, f"path {_LEAK}")])
    assert len(findings) == 1
    assert "notes.md:12" in findings[0]
    assert "unraid pool path" in findings[0]
    assert "abcdef1234" in findings[0]


def test_scan_added_passes_a_clean_line():
    assert _hits("abc", [("notes.md", 1, "/srv/data/example is fine")]) == []


def test_a_binary_SUFFIX_no_longer_hides_TEXT_from_the_range_scan():
    """⭐ THIS TEST USED TO PIN THE DEFECT (consumer#22), and its old assertion is the change.

    It required every `SKIP_SUFFIXES` name to swallow a leak in an ADDED LINE, on the reasoning
    that the two scans must agree about which files count. They must — but they were agreeing on
    the wrong answer: a file called `deploy-notes.pdf` holding an ordinary ASCII runbook was read
    by neither, and `git add`ing a text file under a renamed extension defeated the whole guard.

    A line ARRIVING HERE has already passed a content check stricter than any filename: git serves
    a text diff only for a blob it judged to be text, and refuses one (`Binary files … differ`)
    for anything else, which is the `unscannable` path below and still filtered by `_skipped`.
    The suffix's job is to stop a wasted read, not to stop a scan.
    """
    for suffix in guard.SKIP_SUFFIXES:
        assert _hits("abc", [(f"assets/asset{suffix}", 1, _LEAK)]), (
            f"a leak in a TEXT line was dropped because the file is named {suffix}")


def test_a_binary_asset_is_still_not_REPORTED_as_unreadable_by_either_scan():
    """The half that did not change, stated separately now that the two are separate questions.

    An ordinary image git serves as a binary diff must not appear in the "not scanned, so NOT
    CLEARED" list — reporting one on every run is the false-red that gets a guard switched off,
    and it is what `_skipped` still exists for.
    """
    for suffix in guard.SKIP_SUFFIXES:
        diff = (f"diff --git a/assets/asset{suffix} b/assets/asset{suffix}\n"
                "new file mode 100644\n"
                f"Binary files /dev/null and b/assets/asset{suffix} differ\n")
        _, blind = guard.scan_added("abc", guard.parse_diff(diff), COMPILED)
        assert blind == [], suffix


def test_the_guards_own_source_is_skipped_in_range_mode_BY_EXACT_PATH_ONLY():
    """⭐ The self-skip is an EXACT repo-relative path, not a substring of the filename.

    The substring form (`SELF in path.name`) also matched
    `scripts/__pycache__/check_no_internal_info.cpython-312.pyc` — a tracked, binary,
    literal-bearing copy of the scanner that the scan therefore skipped in silence. Both
    directions are pinned here, in RANGE mode, because the range scan is the newer of the two
    and the one most likely to be re-derived from a repo that still has the loose form.

    ⭐ `root` IS PASSED, and that is now the whole question rather than a detail. There is no
    constant self-path any more: the exemption is "this file, relative to the repository being
    scanned", so a call with no root exempts NOTHING — pinned by the last assertion, which is the
    case an installed guard is in on every run.
    """
    assert guard.scan_added("abc", guard.ParsedDiff([(_GUARD_REL, 5, _LEAK)], []), COMPILED,
                            _GUARD_ROOT)[0] == [], (
        "the guard's own source must be skipped — it carries synthetic deny cases by design")
    assert guard.scan_added("abc", guard.ParsedDiff(
        [(f"__pycache__/{Path(_GUARD_REL).stem}.cpython-312.pyc", 5, _LEAK)], []),
        COMPILED, _GUARD_ROOT)[0], (
        "a .pyc copy of the scanner is NOT the scanner and must still be scanned")
    assert guard.scan_added("abc", guard.ParsedDiff([(f"docs/{_GUARD_REL}", 5, _LEAK)], []),
                            COMPILED, _GUARD_ROOT)[0], (
        "only the real path is exempt; a same-named file elsewhere is not")
    assert _hits("abc", [(_GUARD_REL, 5, _LEAK)]), (
        "with NO root the guard cannot know which file is itself, so it must exempt nothing — "
        "this is the case an installed guard is in, and the constant it used to fall back to "
        "handed a silent amnesty to whatever sat at that path in the scanned repository")


def test_allow_spans_still_apply_to_added_lines():
    """The range scan must share the tree scan's allowlist, or a documented RFC5737 example in a
    new file would block a push."""
    assert _hits("abc", [("README.md", 1, "host 192.0.2.5 # RFC5737")]) == []


def test_an_allowed_token_does_not_grant_amnesty_to_a_leak_sharing_the_line():
    findings = _hits("abc", [("README.md", 1, f"see example.com; real path {_LEAK}")])
    assert findings, "a permitted token on the same line hid a real leak"


def test_the_allow_spans_are_linear_not_quadratic():
    """⭐ A HANG IS THE FAILURE MODE A PUSH-PATH GUARD CANNOT HAVE.

    The flat `[\\w.-]*\\bexample\\.(?:com|org|net)` form backtracked quadratically: ~0.29 s at
    4 000 chars, 4.6 s at 16 000, 29 s at 32 000 — a 200 KB minified line or a base64 data URI
    extrapolates to ~19 minutes. Survivable while this only scanned a tracked tree; `--range`
    puts it on the push path and in CI. Anchoring each label with `(?:[\\w-]+\\.)*` makes it
    linear: 16 k chars in ~0.001 s. Bounded rather than timed precisely — the point is the ORDER
    of growth, and a tight wall-clock assertion on a shared runner is its own flake.
    """
    import time

    line = "z" * 16_000
    start = time.perf_counter()
    guard.scan_text(line, COMPILED)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, (
        f"scanning one 16k-char line took {elapsed:.1f}s — the allow-spans are backtracking "
        f"quadratically again; anchor each label with (?:[\\w-]+\\.)*"
    )


# ------------------------------------------------------- unreadable content is not cleared


def test_a_binary_file_is_reported_as_NOT_CLEARED_rather_than_passed_over():
    """A UTF-16 file carries no diff lines, so the range scan would simply see nothing — and once
    a later commit deletes it, the tree scan is clean too and the content was never read by
    anything. The tree scan refuses to vouch for what it cannot decode; the range scan takes the
    same posture or it is quieter than the scan it exists to reinforce."""
    diff = (
        "diff --git a/u16.txt b/u16.txt\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "Binary files /dev/null and b/u16.txt differ\n"
    )
    assert _binary(diff) == ["u16.txt"]
    _, blind = guard.scan_added("abc", guard.parse_diff(diff), COMPILED)
    assert blind == ["abc u16.txt"]


def test_DELETING_a_binary_file_is_not_reported():
    """A deletion publishes nothing. Reporting it would make every commit that removes a binary —
    and the delete half of a binary rename — fail forever, which is the same trap the
    removed-lines rule avoids: a guard that punishes cleanup gets switched off."""
    diff = ("diff --git a/old.xlsx b/old.xlsx\n"
            "deleted file mode 100644\n"
            "Binary files a/old.xlsx and /dev/null differ\n")
    assert _binary(diff) == []
    assert _added(diff) == []


# ------------------------------------- the entry point CI and the hook actually call


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(_SCRIPT), *args],
                          cwd=repo, capture_output=True, text=True, check=False)


@pytest.mark.timeout(300)
def test_the_CLI_entrypoint_returns_the_exit_codes_CI_and_the_hook_depend_on(tmp_path: Path):
    """⭐ `.github/workflows/ci.yml` and `.githooks/pre-push` call this entry point and nothing
    else, so these exit codes ARE the contract. The load-bearing one is the unresolvable range:
    a shallow clone or an unfetched base must not scan zero commits and print a clean bill of
    health."""
    repo = tmp_path / "cli"
    _init(repo)

    def commit(body: str, message: str) -> str:
        (repo / "notes.md").write_text(body, encoding="utf-8")
        _git(repo, "add", "notes.md")
        _git(repo, "commit", "-qm", message)
        return _git(repo, "rev-parse", "HEAD").strip()

    base = commit("clean\n", "base")
    commit(f"path {_LEAK}\n", "adds a leak")
    head = commit("clean again\n", "removes it")

    leaky = _cli(repo, "--range", f"{base}..{head}")
    assert leaky.returncode == 1, f"a leak in history exited {leaky.returncode}: {leaky.stdout}"
    assert "pushing publishes HISTORY" in leaky.stdout

    clean = _cli(repo, "--range", f"{head}..{head}")
    assert clean.returncode == 0, f"an empty range must pass: {clean.stdout}"

    # An unreachable BASE widens to the head's whole history rather than failing -- see
    # test_an_unreachable_base_widens_instead_of_redding for why. It must still REPORT the leak
    # that history contains, which is what this asserts: widening is not a way to pass.
    widened = _cli(repo, "--range", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef..HEAD")
    assert "WIDENING" in widened.stdout, (
        "an unreachable base must say so, not fail silently: " + widened.stdout)
    assert widened.returncode == 1, (
        "WIDENING MUST NOT LOSE THE FINDING -- it scans MORE history, not less: " + widened.stdout)

    # An unresolvable HEAD is a different matter: there is nothing to widen TO, so it stays an
    # error. This is the half of the old contract that must not be relaxed.
    broken = _cli(repo, "--range", "HEAD..deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
    assert broken.returncode == 1, (
        "AN UNRESOLVABLE HEAD MUST NOT REPORT CLEAN: " + broken.stdout)
    assert "could not scan" in broken.stdout

    assert _cli(repo, "--range").returncode == 2, "a missing argument is a usage error"

@pytest.mark.timeout(300)
def test_an_unreachable_base_widens_instead_of_redding(tmp_path: Path):
    """⭐ THE POST-REWRITE RED. After a history rewrite the previous tip stops existing, so the
    very next push hands CI `github.event.before..HEAD` with a base that is no longer in the
    repository. The workflow already special-cases the NULL sha (branch creation); it never
    handled an UNREACHABLE one, so the guard exited 1 on a repository that was in fact clean --
    and a required check that reds for a reason nobody can act on is a check people route around.

    The repair is deliberately fail-closed: an unreachable base widens to every commit reachable
    from the head, which is strictly MORE than the named range covered, never less. Here the
    history is clean, so it passes -- and says plainly that it widened.
    """
    repo = tmp_path / "rewritten"
    _init(repo)
    (repo / "README.md").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "clean root, as if just after a rewrite")

    result = _cli(repo, "--range", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef..HEAD")
    assert result.returncode == 0, (
        "a clean history behind an unreachable base must PASS, not red: " + result.stdout)
    assert "WIDENING" in result.stdout, (
        "the widening has to be announced -- a silent change of scope is its own defect: "
        + result.stdout)
    assert "no internal info added" in result.stdout


def test_the_selftest_still_passes_through_the_CLI():
    """`--selftest` proves the PATTERNS still bite. Both hooks and both CI paths run it first."""
    result = _cli(_REPO_ROOT, "--selftest")
    assert result.returncode == 0, result.stdout


# ------------------------------------------------- the wiring, without which none of it runs


# ⛔ THE THREE `.githooks` TESTS THAT USED TO SIT HERE ARE GONE, DELIBERATELY, AND THIS COMMENT IS
# WHAT STOPS THEM BEING "RESTORED" AS A REGRESSION.
#
# They asserted properties of `.githooks/pre-commit` and `.githooks/pre-push`: that neither probes
# with `command -v python3` (which resolves the Windows Store stub and aborts every commit), that
# pre-push runs the RANGE scan with `--not --remotes` so a brand-new branch is not scanned as zero
# commits, and that both are committed 100755 so git does not silently skip them on Linux. Every
# one of those is a real lesson and NONE of them is a property of this ENGINE: they describe how a
# repository WIRES the guard up.
#
# This repository has no hooks to assert them against, and writing hooks so that copied tests could
# pass would be building a feature to satisfy a test. They belong in each consuming repository,
# beside the hooks they describe; adopting hooks here is filed as its own issue.


def test_the_CI_workflow_runs_BOTH_scans_FROM_THE_INSTALLED_PACKAGE():
    """The range scan is worth nothing if CI does not run it, and it CANNOT run on the default
    shallow checkout — `git rev-list base..head` fails there, which the guard turns into exit 1.

    ⭐ AND CI MUST CALL IT THE WAY A CONSUMER WILL. This repository owns the engine, which makes it
    the one place where invoking the guard by file path would still work — and therefore the one
    place a broken console script, or a module left out of the wheel, goes unnoticed until it
    reaches somebody else's repository. So the assertion is on the ENTRY POINT, not only on the
    flags.
    """
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "kw-leak-guard --range" in ci, "CI does not run the commit-range scan"
    assert "fetch-depth: 0" in ci, "the range scan needs full history to resolve the base"
    assert "pull_request" in ci, "the PR path is not covered"
    assert "kw-leak-guard --selftest" in ci, "CI does not self-test the patterns"
    assert "pip install -e ." in ci, (
        "the leak-guard job does not install the package, so `kw-leak-guard` would be whatever "
        "happens to be on PATH rather than the guard in this checkout")
    assert "scripts/check_no_internal_info.py" not in ci, (
        "CI still names a vendored guard by path. That file is gone; a call to it would either "
        "fail or, worse, run a stale copy that somebody re-added")


# --------------------------------------------- the denylist must stay free of real values
#
# ⛔ WHAT REPLACED `test_guard_source_carries_no_plaintext_token`, AND WHY IT HAD TO GO.
#
# That test decoded the guard's base64 token table and asserted no decoded token appeared in the
# guard's source as a literal. It was the only check that could see past `SELF_PATH`, and it was
# genuinely load-bearing while the guard still carried real values.
#
# It cannot survive the move to a shapes-only denylist, because it depended on the repo knowing
# the real tokens in order to look for them. The whole point of the change is that the repo does
# NOT know them any more: plaintext republished them, and base64 still shipped them one `b64decode`
# away. Keeping a version of that test would mean keeping the values here to test against, which
# is the leak it was written to detect.
#
# So the real-literal assertion moved OUT of this repository, to a guard in the project working
# directory that is never committed and is run against the staged tree before every push. What
# these two tests do instead is pin the STRUCTURE: no encoded table, no decoder, and exactly the
# agreed set of shape patterns. Neither can prove "no real value is present" — nothing inside a
# repo that does not know the values can — and they are written not to imply otherwise.

_SHAPE_LABELS = {
    "private IPv4 (RFC1918)",
    "cgnat address",
    "tailnet name",
    "private lan domain",
    "unraid pool path",
    "personal mail address",
    "uuid (access policy / tenant id)",
    # Added deliberately: a Windows profile path names the operator in its third segment, the
    # same way an Unraid share root names the array. Anchored to a `Users` segment rather than to a
    # drive letter, so ordinary `C:\dev\...` instructions do not fire — see the near-misses in
    # the guard's `_MUST_PASS`.
    "windows profile path",
}


def test_the_denylist_is_the_agreed_shape_set():
    """The denylist is exactly these eight shapes -- no additions, no removals.

    A REMOVAL silently reduces coverage. An ADDITION is the more interesting failure: the way a
    real value gets back into this file is somebody adding a pattern for one ("host codename",
    "infra domain"), which is precisely what happened before. Pinning the set means that edit
    cannot land quietly -- it fails here, and the fix is to put the value in the project-side
    guard instead.
    """
    assert {label for label, _ in guard.PATTERNS} == _SHAPE_LABELS


def test_the_guard_carries_no_real_literal_denylist():
    """No encoded token table, and no decoder to read one.

    The previous design base64-encoded the real tokens and decoded them at import. That defeated
    a grep and a code search, which is a real improvement over plaintext, but it still SHIPPED the
    values -- `base64.b64decode` in the same file is the key sitting next to the lock. This test
    exists so that reintroducing the mechanism fails loudly rather than looking like a hardening
    step.

    ⚠️ WHAT THIS DOES NOT PROVE. It cannot show that no real value appears anywhere in the guard;
    a codename typed straight into a comment would pass this and every other test in this file.
    Only the project-side guard, which holds the actual list, can make that statement -- run it
    against this repo after `git add` and before pushing. Stated plainly here because a test whose
    limits are implied gets read as a guarantee it never made.
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "b64decode" not in src, (
        "the guard decodes an encoded token table again. Encoding does not un-ship a value -- "
        "put the real list in the project-side guard, which is never committed.")
    assert not re.search(r"^import base64|^from base64", src, re.MULTILINE), (
        "the guard imports base64, which it needs only to carry an encoded denylist")
    for attr in ("_CODENAMES", "_CODENAME", "_DOMAIN", "_POOLS", "_PERSON", "_CFAPP"):
        assert not hasattr(guard, attr), (
            f"guard.{attr} is back: that is the decoded real-token table this design removed")


# --------------------------------------------------------------------------------------------
# Two claims the guard's own comments make about their enforcement. Both named a test by name;
# neither test existed, so both comments were assertions about a guard's strength that nothing
# checked — the exact thing this repo treats as a defect rather than a doc nit.


def test_every_pattern_is_exercised():
    """`PATTERNS` says every entry MUST have a deny case AND a near-miss allow case.

    `selftest()` enforces the deny half per pattern and fails on any `_MUST_PASS` sample that
    trips, and it is run by `test_the_selftest_still_passes_through_the_CLI` — widening a
    pattern to `.*` fails it on the corpus, which was checked rather than assumed. What
    nothing enforces is that a given pattern HAS a near-miss: `_MUST_PASS` is unlabelled, so
    deleting every near-miss for one pattern leaves selftest green. That half is convention.

    What it adds is a named, specific failure inside the suite: selftest reports a wall of
    false-positive lines, this says which pattern label has no deny case. Keep it cheap and
    keep its claims small.
    """
    deny = {label for label, _ in guard._MUST_FAIL}
    labels = {label for label, _ in guard.PATTERNS}
    assert labels - deny == set(), f"pattern(s) with no deny case: {sorted(labels - deny)}"

    compiled = guard.compile_patterns()
    assert guard._MUST_PASS, "no near-miss allow cases at all"
    for sample in guard._MUST_PASS:
        hits = guard.scan_text(sample, compiled)
        assert not hits, (
            f"a near-miss sample now trips {[h[1] for h in hits]}: a pattern has been widened "
            f"until it fires on ordinary text — {sample!r}"
        )


def test_the_allow_literals_are_recognised_as_permitted_spans(injected_literals):
    """`_permitted_spans` is pinned DIRECTLY, because a scan verdict would pass either way.

    An allow-literal is inert against this pattern set — none of them matches any live shape — so
    asserting `scan_text` returns clean proves nothing about the carve-out: it would return clean
    with the whole allowlist deleted. Assert the mechanism itself.

    ⚠️ THIS REPLACED `test_the_allow_literals_are_removed_from_a_line`, and the difference is the
    entire point of the fix. The old test asserted that `_neutralize` DELETED the literal from the
    line — and deletion was the amnesty bug, because whatever a span can grow over, it can erase.
    A span's only power now is to suppress a hit it CONTAINS, so what must be pinned is the SPAN's
    extent, never a rewritten line. A test that still demanded deletion would be pressure to
    reintroduce the defect.

    ⭐ THE LITERALS ARE INJECTED, not read off the module. They used to be a constant in the
    engine; they are now configuration supplied by the repository being scanned, and the fixture
    supplies this repository's own — so this test measures the mechanism as a consumer meets it.
    """
    assert guard.ALLOW_LITERALS, "the carve-out list is empty; this test is then vacuous"
    for lit in guard.ALLOW_LITERALS:
        line = f"see https://{lit}/unraid-templates for the icon"
        spans = guard._permitted_spans(line)
        start = line.index(lit)
        assert (start, start + len(lit)) in spans, (
            f"{lit!r} is in ALLOW_LITERALS but is not recognised as a permitted span")
        # ⭐ THE SPAN MUST NOT REACH BEYOND THE LITERAL, IN EITHER DIRECTION. Asserted as an EXACT
        # extent: an earlier version of this checked only that the span did not contain the
        # substring "unraid-templates", which any leftward growth over "see https://" satisfied —
        # and leftward is precisely the direction the "no span may consume anything to its left"
        # rule exists for. A weaker assertion under a stronger comment is the shape this repo
        # treats as a defect.
        for s, e in spans:
            assert line[s:e] in guard.ALLOW_LITERALS, (
                f"a permitted span {line[s:e]!r} is not exactly one carve-out literal — it has "
                f"grown over its neighbours and could mask a leak written there")


def test_a_repeated_allow_literal_is_recognised_at_EVERY_occurrence(injected_literals) -> None:
    """`_permitted_spans` loops `line.find(lit, start + 1)`; nothing pinned the loop.

    Collapsing it to a single `find` left the whole suite green, because every other case puts one
    literal on a line. A second occurrence going unrecognised would report it as a hit.
    """
    a, b = guard.ALLOW_LITERALS[0], guard.ALLOW_LITERALS[1]
    line = f"{a} and again {a} and also {b}"
    spans = guard._permitted_spans(line)
    covered = sorted(line[s:e] for s, e in spans)
    assert covered.count(a) == 2, f"the repeated literal was found once, not twice: {covered}"
    assert b in covered, f"a second, different literal on the same line was missed: {covered}"


def test_a_permitted_span_still_suppresses_what_it_actually_contains():
    """The other direction: removing amnesty must not have made the allowlist inert.

    `scan_text` suppresses a hit that lies ENTIRELY inside a permitted span. With the current
    denylist no deny pattern can match inside an RFC5737 address, so this constructs the
    containment case directly against a pattern that CAN — proving the suppression arm runs at
    all, rather than trusting a clean verdict that would hold either way.
    """
    # An Unraid share-root hit sitting wholly inside a synthetic permitted span. Assembled from
    # fragments: this file is scanned by the guard, so the literal cannot be written out whole.
    leak = "/mnt/" + "user"
    # ⛔ TAKEN FROM THE SHIPPED SET BY LABEL, never re-spelled here. An inline `re.compile(...)`
    # copy of a shipped pattern is the exact drift `compile_patterns()` exists to stop: it keeps
    # passing against a stale regex after the real one changes, and this one had silently dropped
    # `re.IGNORECASE` too.
    rx = [(label, r) for label, r in COMPILED if label == "unraid pool path"]
    assert rx, "the 'unraid pool path' pattern has been renamed; this test would be vacuous"
    assert guard.scan_text(f"prefix {leak} suffix", rx), "the pattern does not bite at all"

    line = f"prefix {leak} suffix"
    # Monkeypatch a span covering exactly the leak, then assert it is suppressed.
    original = guard._permitted_spans
    start = line.index(leak)
    guard._permitted_spans = lambda _l: [(start, start + len(leak))]
    try:
        assert guard.scan_text(line, rx) == [], (
            "a hit lying entirely inside a permitted span was still reported — the allowlist "
            "has become inert, which is the opposite failure to amnesty")
        # ...and a hit only PARTLY covered is still reported.
        guard._permitted_spans = lambda _l: [(start, start + len(leak) - 1)]
        assert guard.scan_text(line, rx), (
            "a hit that merely TOUCHES a permitted span was suppressed — containment must be "
            "total, or a span can mask a leak by overlapping it")
    finally:
        guard._permitted_spans = original


# =============================================================================================
# HOLE 1 — the AMNESTY bypass (delete-then-match + an unbounded leading-label allow-span).
#
# Measured on this guard BEFORE the fix: 6 of 6 dotted shapes walked straight through, because
# appending `.example.com` to a leak made the allow-span match the LEAK TOO, and the
# delete-then-match pass then erased it. `AGENT=<rfc1918-addr>.example.com` scanned CLEAN and
# exited 0. Every test below fails on that code and passes on this.
# =============================================================================================


def _run_guard(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    """The real CLI, against a real repository. Bounded: a push-path guard may not hang.

    ⚠️ THE GUARD IS OUTSIDE `repo` HERE, which is the INSTALLED posture and now the common one.
    Nothing in `repo` is self-exempt under it. A test whose subject IS the self-exemption has to
    vendor a copy in — see `_vendor_guard`.
    """
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo", str(repo), *extra],
        capture_output=True, text=True, timeout=300)


def _vendor_guard(repo: Path, rel: str = _VENDORED_REL) -> Path:
    """A copy of the guard INSIDE `repo`, so that copy's self-exemption is live for `repo`.

    ⭐ THE ONLY WAY TO EXERCISE THE SELF-EXEMPTION SINCE THE ENGINE WAS EXTRACTED, and writing it
    out is what keeps these tests honest. The exemption is "this running file, relative to the
    repository being scanned"; an installed guard is never inside the repository it scans, so
    running `_SCRIPT` against a fixture repo exempts NOTHING there. A test that tried to construct
    the exemption from a constant path would silently stop testing anything.
    """
    dst = repo / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(_SCRIPT.read_bytes())
    return dst


def _run_vendored(vendored: Path, repo: Path, *extra: str) -> subprocess.CompletedProcess:
    """Run the copy that lives inside `repo` — the posture where the self-exemption applies."""
    return subprocess.run(
        [sys.executable, str(vendored), "--repo", str(repo), *extra],
        capture_output=True, text=True, timeout=300)


# ⚠️ ASSEMBLED AT RUNTIME. This file IS scanned by the guard — only the guard's own source is
# exempt — so a deny case spelled out whole would make the guard fail on its own test suite.
# Neither fragment matches alone. Every value is synthetic (RFC1918/CGNAT documentation shapes,
# `.invalid` hosts, an invented account name).
_LEAK_TOKENS: list[tuple[str, str]] = [
    ("private IPv4 (RFC1918)", "192.168." + "77.77"),
    ("cgnat address", "100.127." + "255.254"),
    ("tailnet name", "host-a.tailnet-example." + "ts" + ".net"),
    ("private lan domain", "nas-a." + "lan"),
    ("unraid pool path", "/mnt/" + "user"),
    ("personal mail address", "someone@" + "gmail" + ".invalid"),
    ("uuid (access policy / tenant id)", "11111111-2222-3333-" + "4444-555555555555"),
    ("windows profile path", "C:\\Users\\" + "operator" + "\\AppData"),
]

# The documented placeholder forms — every one of these is PERMITTED and none of them trips a
# pattern, which is why they can be written out whole here.
_PERMITTED_TOKENS = ("example.com", "example.org", "example.net", "your-domain.example",
                     ".env.example", ".env.local", "192.0.2.5", "198.51.100.5", "203.0.113.9")


def test_every_denylist_pattern_has_a_bare_leak_token_here() -> None:
    """`_LEAK_TOKENS` must cover the whole denylist, or the amnesty tests below have a blind spot.

    The recurring failure this closes is the one the canon names: a round names a defect CLASS and
    only the listed EXAMPLES get pinned, so the next round rediscovers the class one member over.
    The amnesty bypass is a class over PATTERNS, so the enumeration is asserted, not assumed.
    """
    assert {label for label, _ in _LEAK_TOKENS} == {label for label, _ in guard.PATTERNS}, (
        "a pattern has no bare leak token, so no amnesty case exercises it")


@pytest.mark.parametrize("want_label,token", _LEAK_TOKENS)
def test_each_bare_leak_token_is_actually_caught_on_its_own(want_label: str, token: str) -> None:
    """The precondition for every amnesty test: without it they could all pass vacuously.

    If a token did not trip its pattern in isolation, "it is still caught when a permitted token
    is appended" would be true of a guard that catches NOTHING.
    """
    labels = {label for _, label, _ in guard.scan_text(token, COMPILED)}
    assert want_label in labels, (
        f"{token!r} does not trip {want_label!r} on its own, so the amnesty cases built from it "
        f"prove nothing")


@pytest.mark.parametrize("want_label,sample", guard._MUST_FAIL_ADJACENT)
def test_the_allowlist_cannot_grant_amnesty_to_an_adjacent_leak(want_label: str,
                                                                sample: str) -> None:
    """⭐ THE BYPASS ITSELF, one curated case per shape.

    A permitted token written FLUSH AGAINST a leak must not hide it. Curated per label rather
    than generated, because concatenation is not meaning-preserving for every pattern: appending
    `.example.com` to a `.lan` host makes it a host UNDER example.com and genuinely not a `.lan`
    host any more, which is the documented right-bound behaviour and not a miss. The guard's own
    `_MUST_FAIL_ADJACENT` records which combinations are real and why the absent ones are absent.
    """
    labels = {label for _, label, _ in guard.scan_text(sample, COMPILED)}
    assert want_label in labels, (
        f"AMNESTY BYPASS: a permitted token flush against a leak hid it. Wanted {want_label!r}, "
        f"got {sorted(labels)} on {sample!r}")


@pytest.mark.parametrize("want_label,leak", _LEAK_TOKENS)
@pytest.mark.parametrize("permitted", _PERMITTED_TOKENS)
def test_a_permitted_token_elsewhere_on_the_line_never_excuses_a_leak(
    want_label: str, leak: str, permitted: str,
) -> None:
    """The universal half of the class: 8 shapes x 9 permitted tokens x 3 layouts.

    Unlike flush concatenation this IS meaning-preserving for every pattern — a permitted token
    somewhere else on the line cannot change what the leak is — so it can be swept exhaustively
    rather than curated. This is the direction `ALLOW_SPANS` neutralising the LINE would break.
    """
    for line in (f"{permitted} {leak}", f"{leak} {permitted}",
                 f"# see {permitted} -- real value is {leak}"):
        labels = {label for _, label, _ in guard.scan_text(line, COMPILED)}
        assert want_label in labels, (
            f"a permitted token on the same line hid a real leak. Wanted {want_label!r}, got "
            f"{sorted(labels)} on {line!r}")


def test_no_permitted_span_overlaps_the_leak_in_any_curated_amnesty_case() -> None:
    """⭐ THE INVARIANT THAT TERMINATES THE CLASS, rather than one more example of it.

    "No span may consume anything to its left" is the actual property; the individual bypasses
    were all symptoms of a span growing over its neighbours. Asserting the extent directly means
    a future widening of `ALLOW_SPANS` fails HERE, at the cause, instead of being rediscovered as
    yet another shape that slipped through.
    """
    for want_label, sample in guard._MUST_FAIL_ADJACENT:
        spans = guard._permitted_spans(sample)
        for _, label, match in guard.scan_text(sample, COMPILED):
            if label != want_label:
                continue
            start = sample.index(match)
            end = start + len(match)
            for s, e in spans:
                assert e <= start or s >= end, (
                    f"the permitted span {sample[s:e]!r} OVERLAPS the leak {match!r} in "
                    f"{sample!r} — a span has grown over its neighbour again, which is exactly "
                    f"how the delete-then-match design failed open")


def test_the_containment_index_answers_exactly_what_a_linear_scan_would() -> None:
    """The index replaced an O(matches x spans) scan that took 31 s on one 176 KB line. Speed is
    only worth having if the answer is unchanged, so both halves are pinned here.

    ⭐ THE NO-MERGING PROPERTY IS THE SAFETY ONE. Merging overlapping spans into one wider
    interval is the obvious way to make this fast, and it would let TWO permitted tokens jointly
    cover a leak that NEITHER contains — a fresh amnesty hole opened for performance. Containment
    must always be satisfiable by a SINGLE span.
    """
    # An EARLIER, wider span must still be found for a match that begins after a LATER, narrower
    # span starts. This is what the running maximum is for; a plain "last span wins" fails it.
    starts, best = guard._containment_index([(0, 20), (5, 8)])
    assert guard._contained(starts, best, 6, 15), (
        "a match inside the wide span (0,20) was not found because a narrower span starts before "
        "it — the index has forgotten the earlier span's extent")

    # ⛔ Two spans that TOGETHER cover (5,15) but neither of which contains it.
    starts, best = guard._containment_index([(0, 10), (8, 20)])
    assert not guard._contained(starts, best, 5, 15), (
        "two permitted spans were allowed to JOINTLY grant amnesty to a leak neither of them "
        "contains — the spans are being merged, which reopens the amnesty hole")

    # The ordinary cases.
    starts, best = guard._containment_index([(4, 9)])
    assert guard._contained(starts, best, 4, 9), "an exactly-coincident span must contain"
    assert not guard._contained(starts, best, 3, 9), "a match starting BEFORE the span"
    assert not guard._contained(starts, best, 4, 10), "a match ending AFTER the span"
    assert not guard._contained(*guard._containment_index([]), 0, 1), "no spans, no containment"


def test_a_permitted_span_may_only_start_mid_word_if_it_cannot_contain_a_leak() -> None:
    """⭐⭐ THE INVARIANT THAT WOULD HAVE CAUGHT THE `.env...local` BYPASS, stated generally.

    A permitted span is safe if EITHER of two things is true:
      (a) it cannot begin immediately after a word character, so it can never grow leftward out
          of the token it was written for; or
      (b) nothing it matches can contain a deny match, so even if it does grow, it has no leak to
          grant amnesty to.

    `\\.example` relies on (b) — it is a bare suffix with no left bound, and deliberately so, but
    `.example` contains no leak shape, so it can suppress nothing. `.env[.<qual>].local` relies on
    (a), because it DOES contain a `private lan domain` match by construction — and it shipped
    without a left bound, which is exactly how a `<host>.env.<qualifier>` LAN name scanned clean.

    Checking the condition rather than enumerating bypasses is what terminates the class: a new
    ALLOW_SPANS entry that satisfies neither arm fails HERE, at the cause, instead of being
    rediscovered later as one more shape that slipped through.
    """
    wordy = ("nas-a", "host-b", "operator", "svc1", "x")
    for token in _PERMITTED_TOKENS:
        can_contain_a_leak = any(rx.search(token) for _, rx in COMPILED)
        for prefix in wordy:
            line = f"{prefix}{token}"
            grew_left = [(s, e) for s, e in guard._permitted_spans(line)
                         if s > 0 and re.match(r"[\w-]", line[s - 1])]
            if grew_left and can_contain_a_leak:
                pytest.fail(
                    f"AMNESTY VECTOR: the permitted span {line[grew_left[0][0]:grew_left[0][1]]!r}"
                    f" begins immediately after a word character in {line!r}, AND the token "
                    f"{token!r} can contain a deny match. That span can be appended to a real "
                    f"leak to hide it — left-bound it with (?<![\\w-]).")


def test_the_untouchable_lan_local_pattern_is_exactly_as_decided() -> None:
    """⛔ DO NOT 'FIX' THE TRAILING-DOT GAP. It was repaired once and REVERTED.

    `(?![\\w-]|\\.[\\w-])` does catch `host-a.lan.` at a sentence end — and false-fires on
    `config.local.${ENV}`, `settings.local.*` and `.gitignore` globs, which are ordinary clean
    lines. `<label>.local.` and `<label>.lan.` at a sentence end are the same string shape and no
    regex separates them, so the gap is taken deliberately and declared in KNOWN LIMITS.

    ⚠️ THIS PIN EXISTS BECAUSE NOTHING ELSE CATCHES THE REVERT. The forbidden repair was
    re-applied experimentally and the ENTIRE suite stayed green, selftest included: `_MUST_PASS`
    happens to carry only the three `.local` FILENAME forms the bad repair also passes. Pinning
    the decision is the only thing that makes undoing it loud, so this asserts the regex source.
    """
    assert (dict(guard.PATTERNS)["private lan domain"]
            == r"(?<![\w-])[\w-]+\.(?:lan|local)(?![\w.-])"), (
        "the `private lan domain` pattern changed. If this is the trailing-dot 'repair', it was "
        "tried before and reverted for false-firing on config.local.${ENV} and .gitignore globs "
        "— see KNOWN LIMITS. Do not re-apply it.")


@pytest.mark.parametrize("sample", [
    # The exact shapes that made the trailing-dot repair unacceptable. `_MUST_PASS` pinned only
    # the three plain filename forms, all of which the BAD repair also passes — so none of them
    # would have caught the revert.
    "load_config('config.local.${ENV}')",
    "compose.local.$(uname).yaml",
    "the override file is settings.local.",
])
def test_the_false_positives_that_forced_the_trailing_dot_revert_stay_clean(sample: str) -> None:
    hits = guard.scan_text(sample, COMPILED)
    assert not hits, (
        f"{sample!r} now trips {[h[1] for h in hits]} — this is the false-positive class the "
        f"trailing-dot repair was reverted for")


def test_a_trailing_glob_IS_a_known_false_positive_and_is_recorded_as_one() -> None:
    """⚠️ AN HONEST PIN OF A REAL, PRE-EXISTING FALSE POSITIVE — not a claim that it is fine.

    A `.gitignore` glob of the form `*<name>.local*` trips `private lan domain` TODAY: the glob's
    trailing `*` is not in `[\\w.-]`, so the right bound is satisfied and the pattern reads it as
    a hostname. A repo carrying that line would redden CI while leaking nothing.

    It is pinned rather than fixed because the fix is a change to this exact pattern, which is
    frozen by decision (see `test_the_untouchable_lan_local_pattern_is_exactly_as_decided`) — and
    because a gap that is asserted is a gap somebody can find, whereas one mentioned in a comment
    is not. Filed as unraid-templates#32. If that issue is resolved, this test flips to the
    must-pass list above; until then it documents the true behaviour.
    """
    glob = "gitignore glob: *config." + "local*"     # fragmented: this file is scanned
    hits = guard.scan_text(glob, COMPILED)
    assert [h[1] for h in hits] == ["private lan domain"], (
        "the trailing-glob false positive changed behaviour. If it was FIXED, move this sample "
        "into test_the_false_positives_that_forced_the_trailing_dot_revert_stay_clean and close "
        "unraid-templates#32.")


@pytest.mark.timeout(300)
def test_a_non_ascii_path_does_not_CRASH_the_verdict(tmp_path: Path) -> None:
    """⚠️ The guard's own rule is "ASCII ONLY in anything PRINTED", and it was applied to the
    hand-written messages but not to the interpolated PATHS.

    From a git hook stdout is a pipe, so Python falls back to the locale encoding (cp1252 here)
    and one tracked file with an accented or CJK name raised UnicodeEncodeError mid-verdict —
    just as it was listing the finding. It failed CLOSED (exit stayed 1), so this is robustness
    rather than a bypass; the cost is that the operator never learns WHICH file leaked.
    """
    repo = tmp_path / "unicode"
    _init(repo)
    (repo / "café-日.md").write_text(f"AGENT={_ADDR}\n", encoding="utf-8")
    _git(repo, "add", "-A")

    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo", str(repo)],
        capture_output=True, timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"})
    assert proc.returncode == 1, f"the leak was not caught: {proc.stdout!r} {proc.stderr!r}"
    assert b"Traceback" not in proc.stderr, (
        f"the scan CRASHED while printing the finding instead of reporting it: "
        f"{proc.stderr.decode('utf-8', 'replace')}")
    assert b"private IPv4" in proc.stdout, proc.stdout


@pytest.mark.timeout(300)
def test_a_conflicted_path_is_scanned_and_reported_ONCE(tmp_path: Path) -> None:
    """`git ls-files -z` lists an unmerged path once PER INDEX STAGE, so a file in a conflict was
    scanned two or three times and each finding printed as many times — which reads as several
    separate leaks in different places."""
    repo = tmp_path / "conflict"
    _init(repo)
    f = repo / "c.txt"
    f.write_text("base\n", encoding="utf-8")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "other")
    f.write_text(f"AGENT={_ADDR}\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "theirs")
    _git(repo, "checkout", "-q", "main")
    f.write_text(f"HOST={_HOST}\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "ours")
    _merge_expecting_conflict(repo, "other")

    # ⛔ PRECONDITION, because without it this test PASSES VACUOUSLY. If the merge did not actually
    # conflict — as happened on CI, where a bare `git merge` is refused for want of a configured
    # identity — then `c.txt` has ONE index entry for the ordinary reason and the count below is
    # trivially 1, proving nothing about deduplication. Assert the multi-stage state exists first.
    stages = _git(repo, "ls-files", "-u", "--", "c.txt").strip().splitlines()
    assert len(stages) > 1, (
        f"the merge left no duplicate index stages, so there is nothing to deduplicate and this "
        f"test would prove nothing: {stages}")

    tracked = [p.name for p in guard.tracked_files(repo)]
    assert tracked.count("c.txt") == 1, (
        f"a conflicted path is listed once per index stage and was not deduplicated: {tracked}")


def test_the_delete_then_match_pass_is_gone_and_must_not_come_back() -> None:
    """`_neutralize` WAS the bug: deletion is what let a permitted token consume its neighbours.

    Pinned as an absence because the sibling repo kept the function alive after the rewrite as
    "so the allowlist can be inspected directly" — nothing called it, nothing tested it, and it
    sat in a safety-critical file still carrying the shape of the defect.
    """
    assert not hasattr(guard, "_neutralize"), (
        "the delete-then-match pass is back. A span must SUPPRESS a hit it contains, never "
        "rewrite the line — see scan_text.")


# =============================================================================================
# HOLE 2 — a tracked file that is STAGED-BUT-DELETED. Its content is in the index and goes into
# the commit; the tree scan used to `continue` past it in silence and print "no internal info
# found". Submodules are the reason that branch was written as a skip, so both are tested here.
# =============================================================================================


@pytest.mark.timeout(300)
def test_a_staged_then_deleted_file_is_REPORTED_not_silently_skipped(tmp_path: Path) -> None:
    """⭐ RED BEFORE / GREEN AFTER. On the previous guard this repo scanned clean and exited 0.

    `git add <leak>` then removing it from the worktree leaves the leak in the INDEX — which is
    what the commit will contain. `read_text` raises FileNotFoundError, and the old branch
    treated that as "nothing to see" rather than "I cannot vouch for this".
    """
    repo = tmp_path / "staged"
    _init(repo)
    leak = repo / "staged.txt"
    leak.write_text(f"AGENT_URL=http://{_HOST}:9999/mcp\n", encoding="utf-8")
    _git(repo, "add", "staged.txt")
    leak.unlink()

    assert _HOST in _git(repo, "show", ":staged.txt"), "precondition: the index still has it"
    proc = _run_guard(repo)
    assert proc.returncode == 1, (
        f"a staged-but-deleted leak scanned CLEAN: rc={proc.returncode} {proc.stdout}")
    # ⭐ IDENTIFIED, not merely "unreadable". The guard reads the staged BLOB, so it names the
    # file, the line and the pattern — an operator can act on that, where "I could not vouch for
    # this path" sent them looking by hand.
    assert "staged.txt:1" in proc.stdout, proc.stdout
    assert "private lan domain" in proc.stdout, proc.stdout


@pytest.mark.timeout(300)
def test_an_unresolved_merge_conflict_does_not_redden_a_clean_tree(tmp_path: Path) -> None:
    """A tree mid-merge, with entirely clean content, must not redden.

    Kept as a standing regression guard. It was written for a false-red that a staged-blob read
    introduced (an unmerged path has no stage-0 entry, so `git cat-file blob :<path>` failed and
    was reported as an encoding problem); that machinery has since been removed — see issue #33 —
    so the case now passes for the simpler reason. It stays because "a conflicted tree does not
    redden" is a property worth pinning whatever the implementation, and the next attempt at
    staged scanning will need it on day one.
    """
    repo = tmp_path / "conflictclean"
    _init(repo)
    f = repo / "a.txt"
    f.write_text("base\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "other")
    f.write_text("theirs, perfectly clean\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "theirs")
    _git(repo, "checkout", "-q", "main")
    f.write_text("ours, also clean\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "ours")
    _merge_expecting_conflict(repo, "other")

    stages = _git(repo, "ls-files", "-u", "--", "a.txt").strip().splitlines()
    assert len(stages) > 1, (
        f"precondition: the merge did not conflict, so this proves nothing: {stages}")
    proc = _run_guard(repo)
    assert proc.returncode == 0, (
        f"an unresolved conflict with entirely clean content reddened the tree: {proc.stdout}")


@pytest.mark.timeout(300)
def test_a_non_ascii_REVISION_RANGE_does_not_crash_the_announcement(tmp_path: Path) -> None:
    """⚠️ THE SAME CLASS AS THE PATH CRASH, in the three sites the first fix missed.

    `rev_range` is interpolated into the line that ANNOUNCES a finding, so with a non-ASCII branch
    name the guard died mid-sentence at the exact moment it had something to report. Fixing the
    finding LIST and not these was the instance rather than the class.
    """
    repo = tmp_path / "cjkbranch"
    _init(repo)
    _git(repo, "checkout", "-q", "-b", "feature/日本-branch")
    (repo / "leak.txt").write_text(f"AGENT={_ADDR}\n", encoding="utf-8")
    _git(repo, "add", "leak.txt")
    _git(repo, "commit", "-q", "-m", "leak")

    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo", str(repo), "--range", "feature/日本-branch"],
        capture_output=True, timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"})
    assert b"Traceback" not in proc.stderr, (
        f"the range scan CRASHED while announcing a finding: "
        f"{proc.stderr.decode('utf-8', 'replace')}")
    assert proc.returncode == 1, proc.stdout
    assert b"INTERNAL INFO FOUND" in proc.stdout, proc.stdout


@pytest.mark.timeout(300)
def test_a_CLEAN_non_ascii_revision_range_does_not_crash_either(tmp_path: Path) -> None:
    """⚠️ THE SUCCESS PATH, which the first `_ascii` repair missed.

    There are FOUR sites interpolating `rev_range`; three carry a failure or a finding and one
    reports success. Fixing only the first three left a perfectly CLEAN scan on a branch with a
    non-ASCII name dying with a traceback — a guard that crashes when it has nothing to report is
    the purest form of the false-red it exists to prevent. Same instance-not-class shape, on the
    same fix, twice.
    """
    repo = tmp_path / "cjkclean"
    _init(repo)
    _git(repo, "checkout", "-q", "-b", "feature/日本-clean")
    (repo / "ok.txt").write_text("nothing internal here\n", encoding="utf-8")
    _git(repo, "add", "ok.txt")
    _git(repo, "commit", "-q", "-m", "clean")

    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo", str(repo), "--range", "feature/日本-clean"],
        capture_output=True, timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"})
    assert b"Traceback" not in proc.stderr, (
        f"a CLEAN range scan crashed while reporting success: "
        f"{proc.stderr.decode('utf-8', 'replace')}")
    assert proc.returncode == 0, f"{proc.stdout!r} {proc.stderr!r}"


@pytest.mark.timeout(300)
def test_a_leak_STAGED_then_TIDIED_is_a_STATED_LIMIT_of_the_tree_scan(tmp_path: Path) -> None:
    """⚠️ A PROPERTY OF THE TREE SCAN, pinned so it stays deliberate. Issue #33, now CLOSED.

    The tree scan reads the WORKTREE. Stage a leak, overwrite the file with a clean version and do
    not re-stage: the index (and so the commit) still carries the leak, and this layer says clean —
    honestly, because the worktree really is clean. That is not a defect in the tree scan; it is
    the answer to a different question.

    ⭐ WHAT CHANGED, and why this test did NOT invert. An earlier version of this docstring
    predicted that closing #33 would make the TREE scan exit 1. It did not: the fix was a THIRD
    scan, `--staged`, which asks git what the commit will record, and `.githooks/pre-commit` runs
    both. Making the tree scan read the index instead would have broken the tree scan's own job —
    CI has nothing staged, so it would have had nothing to read.

    All three layers are asserted below, because each covers what the others do not:
      * the tree scan sees the whole tracked tree and says clean here — the premise;
      * `--staged` sees the INDEX and catches it at COMMIT time — the fix for #33;
      * `--range` sees what each commit ADDED and catches it on the PUSH path — the backstop that
        kept this from ever reaching a remote while #33 was open.

    ⛔ THE TWO SHAPES THAT WERE TRIED AND REMOVED stay recorded so they are not re-attempted:
    reading the staged blob per-file made the pre-commit hook take 78 s on a 1000-file worktree,
    and reading it via `cat-file --batch` desynchronised on a gitlink (`:<path>` on a submodule
    returns a COMMIT, whose body the parser must skip), mis-attributing one file's content to
    another and exiting 0 on an unread staged leak. `--staged` is neither: it is
    `git diff --cached` through the existing, well-tested `parse_diff`.
    """
    repo = tmp_path / "stagedtidied"
    _init(repo)
    cfg = repo / "cfg.txt"
    cfg.write_text(f"AGENT_URL=http://{_HOST}:9999/mcp\n", encoding="utf-8")
    _git(repo, "add", "cfg.txt")
    cfg.write_text("AGENT_URL=http://your-host.example:9999/mcp\n", encoding="utf-8")

    assert _HOST in _git(repo, "show", ":cfg.txt"), "precondition: the index holds the leak"
    assert _HOST not in cfg.read_text(encoding="utf-8"), "precondition: the worktree is clean"

    tree = _run_guard(repo)
    assert tree.returncode == 0, (
        f"the TREE scan now catches this — if that is deliberate, `--staged` is redundant and "
        f"this test and the guard's KNOWN LIMITS both need rewriting: {tree.stdout}")

    # ...the COMMIT-time layer catches it, which is what #33 asked for...
    staged = _run_guard(repo, "--staged")
    assert staged.returncode == 1, (
        f"the --staged scan missed a leak that is in the INDEX — that is #33 reopened, and the "
        f"pre-commit hook is back to clearing commits it never read: {staged.stdout}")
    assert "cfg.txt" in staged.stdout, staged.stdout

    # ...and the layer that actually gates the remote DOES catch it.
    _git(repo, "commit", "-q", "-m", "publish the staged leak")
    rng = _run_guard(repo, "--range", "HEAD")
    assert rng.returncode == 1, (
        f"the RANGE scan missed a staged-then-committed leak — this is the layer that keeps the "
        f"gap above tolerable, so if it stops working the limit is no longer acceptable: "
        f"{rng.stdout}")
    assert "cfg.txt" in rng.stdout, rng.stdout


@pytest.mark.timeout(300)
def test_an_ordinary_unstaged_edit_of_a_clean_file_does_not_redden(tmp_path: Path) -> None:
    """The other direction: reading both sides must not invent findings. An ordinary mid-edit
    tree — index and worktree differing, both clean — has to stay green, or every developer with
    unstaged work sees red."""
    repo = tmp_path / "midedit"
    _init(repo)
    cfg = repo / "cfg.txt"
    cfg.write_text("first version, perfectly clean\n", encoding="utf-8")
    _git(repo, "add", "cfg.txt")
    cfg.write_text("second version, also clean\n", encoding="utf-8")

    proc = _run_guard(repo)
    assert proc.returncode == 0, f"an ordinary unstaged edit reddened the tree: {proc.stdout}"


@pytest.mark.timeout(300)
def test_an_UNSTAGED_deletion_of_a_clean_file_does_NOT_block_the_commit(tmp_path: Path) -> None:
    """⭐ THE FALSE-RED THE HOLE-2 FIX NEARLY SHIPPED, and the reason it reads the staged blob.

    `rm <tracked-file>` without staging the deletion is ordinary mid-edit behaviour. The index
    still holds the file, so a blanket "tracked but absent -> cannot vouch for it" reddened a tree
    that publishes nothing internal — and told the operator to "re-stage it", which is the wrong
    instruction when the whole point was to delete it. A guard that reddens correct work is one
    that gets switched off, so this direction matters as much as the catching one.
    """
    repo = tmp_path / "unstaged"
    _init(repo)
    (repo / "keep.txt").write_text("nothing internal here\n", encoding="utf-8")
    (repo / "doomed.txt").write_text("also perfectly clean\n", encoding="utf-8")
    _git(repo, "add", "keep.txt", "doomed.txt")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "doomed.txt").unlink()          # deleted, deletion NOT staged

    proc = _run_guard(repo)
    assert proc.returncode == 0, (
        f"an unstaged deletion of a CLEAN tracked file reddened the tree: {proc.stdout}")


@pytest.mark.timeout(300)
def test_staged_content_that_is_not_UTF8_is_still_reported_as_not_cleared(tmp_path: Path) -> None:
    """The fallback still fails closed. Reading the blob answers the question when it CAN be
    decoded; when it cannot, the honest answer is unchanged — not scanned, so not cleared.

    ⚠️ THE PATH MATTERS AS MUCH AS THE VERDICT HERE. `wide.txt` has no binary suffix, so it must be
    REPORTED. The same bytes at a path that DOES claim to be an asset (`wide.pdf`) are skipped
    silently instead — that is the split `_looks_binary` exists for, and pinning both together is
    what stops one of them quietly adopting the other's behaviour.

    ⚠️ ASSERTS THE PROPERTY, NOT THE SENTENCE. This used to require the literal phrase "could not
    be read", which the message no longer uses — it now names the actual cause (the staged content
    is not UTF-8) rather than the two possible causes it used to hedge between. A test pinned to
    wording reddens on a message that got MORE accurate, so it asks for the file, the
    not-scanned-so-not-cleared verdict, and the exit code instead.
    """
    repo = tmp_path / "stagedbin"
    _init(repo)
    wide = f"AGENT={_HOST}\n".encode("utf-16")
    blob = repo / "wide.txt"
    blob.write_bytes(wide)
    asset = repo / "wide.pdf"
    asset.write_bytes(wide)
    _git(repo, "add", "wide.txt", "wide.pdf")
    blob.unlink()
    asset.unlink()

    proc = _run_guard(repo)
    assert proc.returncode == 1, f"undecodable staged content was cleared: {proc.stdout}"
    assert "not scanned, so not cleared" in proc.stdout, proc.stdout
    assert "wide.txt" in proc.stdout, proc.stdout
    assert "wide.pdf" not in proc.stdout, (
        f"a declared binary asset must be skipped silently, not reported: {proc.stdout}")


@pytest.mark.timeout(300)
def test_an_unmerged_absent_path_is_not_blamed_on_the_ENCODING(tmp_path: Path) -> None:
    """⚠️ The message must not name a cause the guard does not know.

    `staged_blob` returns None for two different reasons — the blob is not UTF-8, or `git cat-file`
    refused the path because it is UNMERGED and has no stage-0 entry. The message asserted the
    first, so a conflicted file that is also absent from the worktree sent the operator after an
    encoding problem that did not exist. The content here is valid UTF-8 throughout.
    """
    repo = tmp_path / "unmergedgone"
    _init(repo)
    f = repo / "c.txt"
    f.write_text("base\n", encoding="utf-8")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "other")
    f.write_text("theirs, valid utf-8\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "theirs")
    _git(repo, "checkout", "-q", "main")
    f.write_text("ours, valid utf-8\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "ours")
    _merge_expecting_conflict(repo, "other")
    f.unlink()      # conflicted AND absent from the worktree

    proc = _run_guard(repo)
    assert proc.returncode == 1, f"fail-closed is still required here: {proc.stdout}"
    assert "unmerged" in proc.stdout, (
        f"the guard blamed the encoding for what is an unmerged path with no stage-0 entry — the "
        f"content is valid UTF-8: {proc.stdout}")


@pytest.mark.timeout(300)
def test_a_submodule_does_not_redden_an_otherwise_clean_repo(tmp_path: Path) -> None:
    """⭐ THE REGRESSION THE FIX ABOVE WOULD OTHERWISE SHIP, and the WP's acceptance criterion.

    A gitlink and a staged-but-deleted file are indistinguishable to `read_text`: checked out a
    gitlink is a DIRECTORY, and in a clone without `--recurse-submodules` it does not exist at
    all. Reporting either would make any repo containing a submodule permanently RED — a
    false-fail on a clean tree, which costs more than the gap it closes. Asked of git
    (`ls-files -s`, mode 160000) instead of inferred from the filesystem. Both states exercised.
    """
    repo = tmp_path / "withsub"
    _init(repo)
    (repo / "ok.txt").write_text("nothing to see\n", encoding="utf-8")
    _git(repo, "add", "ok.txt")

    # A real inner repository, staged as a gitlink without needing a network remote.
    sub = repo / "vendor"
    sub.mkdir()
    _init(sub)
    (sub / "readme.md").write_text("vendored\n", encoding="utf-8")
    _git(sub, "add", "readme.md")
    _git(sub, "commit", "-q", "-m", "sub")
    sub_head = _git(sub, "rev-parse", "HEAD").strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sub_head},vendor")

    assert "vendor" in guard.gitlinks(repo), "precondition: the gitlink was not recognised"
    present = _run_guard(repo)
    assert present.returncode == 0, (
        f"a CHECKED-OUT submodule reddened a clean repo: {present.stdout}{present.stderr}")

    # ...and the NOT-checked-out state, which raises FileNotFoundError — the very branch that
    # now reports a staged-but-deleted file. Staged directly rather than by deleting the tree:
    # git marks its object files read-only, so removing a submodule on Windows is a chmod dance
    # that tests nothing.
    _git(repo, "update-index", "--add", "--cacheinfo",
         "160000,0123456789abcdef0123456789abcdef01234567,absent-vendor")
    assert "absent-vendor" in guard.gitlinks(repo)
    absent = _run_guard(repo)
    assert absent.returncode == 0, (
        f"a submodule that is NOT checked out reddened a clean repo: "
        f"{absent.stdout}{absent.stderr}")


@pytest.mark.timeout(300)
def test_a_gitlink_MODE_alone_cannot_exclude_a_real_file_from_the_scan(tmp_path: Path) -> None:
    """⭐ THE SPOOF THE SUBMODULE SKIP OPENS — the rejecting half of the gitlink logic.

    `git update-index --cacheinfo 160000,<sha>,<path>` marks ANY tracked path as a gitlink with
    no real submodule and no `.gitmodules` entry, while the file goes on sitting in the worktree,
    readable and published. Trusting the index MODE would let a planted leak scan clean, so the
    skip is conditional on "not a readable file" and a spoofed entry falls through to be scanned.
    """
    repo = tmp_path / "spoof"
    _init(repo)
    planted = repo / "vendored"
    planted.mkdir()
    (planted / "leak.txt").write_text(f"AGENT_URL=http://{_ADDR}:9999/mcp\n", encoding="utf-8")
    _git(repo, "add", "vendored/leak.txt")
    sha = _git(repo, "hash-object", "vendored/leak.txt").strip()
    _git(repo, "rm", "--cached", "-q", "vendored/leak.txt")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},vendored/leak.txt")

    assert "vendored/leak.txt" in guard.gitlinks(repo), "precondition: spoofed mode not set"
    assert (planted / "leak.txt").is_file(), "precondition: the leak must still be on disk"
    proc = _run_guard(repo)
    assert proc.returncode == 1, (
        f"a real file marked as a gitlink escaped the scan entirely: rc={proc.returncode} "
        f"{proc.stdout}{proc.stderr}")
    assert "vendored/leak.txt" in proc.stdout, proc.stdout


# =============================================================================================
# The COMMIT'S OWN identity. Author/committer name and email are published on every push and no
# guard in the fleet looked at them — a repo whose files are spotless still ships a personal
# address on every commit page if `user.email` was wrong, and only a history rewrite removes it.
# =============================================================================================


@pytest.mark.timeout(300)
def test_a_commit_authored_from_a_personal_address_FAILS_the_range_scan(tmp_path: Path) -> None:
    """⭐ RED BEFORE / GREEN AFTER — nothing scanned commit metadata at all.

    The diff here is deliberately CLEAN, so the only thing that can fail the scan is the identity.
    """
    repo = tmp_path / "authors"
    _init(repo)
    (repo / "ok.txt").write_text("nothing to see here\n", encoding="utf-8")
    _git(repo, "add", "ok.txt")
    bad = "someone@" + "gmail" + ".invalid"
    _git(repo, "-c", f"user.email={bad}", "commit", "-q", "-m", "clean diff, leaky author")

    assert _tree_findings(repo) == [], "precondition: the FILES are clean, only the author is not"
    proc = _run_guard(repo, "--range", "HEAD")
    assert proc.returncode == 1, (
        f"a commit authored from a personal address scanned clean: rc={proc.returncode} "
        f"{proc.stdout}{proc.stderr}")
    assert "author email" in proc.stdout, proc.stdout
    assert "personal mail address" in proc.stdout, proc.stdout


@pytest.mark.timeout(300)
def test_an_ordinary_noreply_author_does_NOT_redden_the_range_scan(tmp_path: Path) -> None:
    """The negative direction. Without it, "the identity scan fires" is equally true of one that
    can ONLY fire — and a guard that reddens every commit is one that gets switched off.

    The address here is the shape the fleet actually commits under.
    """
    repo = tmp_path / "goodauthor"
    _init(repo)
    (repo / "ok.txt").write_text("nothing to see here\n", encoding="utf-8")
    _git(repo, "add", "ok.txt")
    _git(repo, "-c", "user.email=1234567+someone@users.noreply.github.com",
         "-c", "user.name=someone", "commit", "-q", "-m", "clean")

    proc = _run_guard(repo, "--range", "HEAD")
    assert proc.returncode == 0, (
        f"an ordinary noreply author reddened a clean commit: {proc.stdout}{proc.stderr}")


def test_the_identity_scan_reads_every_field_not_just_the_author_email() -> None:
    """`commit_identity` returns four fields; a scan that only looked at one would still pass the
    test above. Pinned on the pure function so it does not need four crafted repositories."""
    assert guard._IDENT_FIELDS == (
        "author name", "author email", "committer name", "committer email")
    for field in guard._IDENT_FIELDS:
        leak = "someone@" + "gmail" + ".invalid"
        found = guard.scan_identity("abc1234567", [(field, leak)], COMPILED)
        assert found and field in found[0], f"{field} is not scanned: {found}"


# =============================================================================================
# The remaining engine refinements, each pinned by the behaviour it changes.
# =============================================================================================


@pytest.mark.timeout(300)
def test_the_self_exemption_follows_the_FILE_not_a_hardcoded_path(tmp_path: Path) -> None:
    """A constant self-path made the exemption land on whatever sits at that path.

    Copy the guard to a different path and run it: it scanned ITSELF (every synthetic deny case
    became a finding) while exempting an unrelated file at `scripts/…` — the exemption on the one
    file that does not need it, and gone from the one that does.

    ⚠️ AN ORDINARY CLEAN FILE IS TRACKED ALONGSIDE IT, and it is not decoration. A tree whose ONLY
    tracked file is the guard is a tree the scan reads NOTHING from, which the zero-scan floor
    (consumer#22) now refuses rather than reports clean — correctly, since a scan that opened no
    file cannot clear anything. Without the second file this test's exit code would be reporting
    the floor rather than the self-exemption, which is the question it exists to ask.
    """
    repo = tmp_path / "moved"
    _init(repo)
    (repo / "README.md").write_text("an ordinary clean file\n", encoding="utf-8")
    tools = repo / "tools"
    tools.mkdir()
    copied = tools / "check_no_internal_info.py"
    copied.write_bytes(_SCRIPT.read_bytes())
    _git(repo, "add", "README.md", "tools/check_no_internal_info.py")

    proc = subprocess.run([sys.executable, str(copied), "--repo", str(repo)],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"the guard reported its OWN synthetic deny cases as findings when run from a path "
        f"other than {_VENDORED_REL!r}: {proc.stdout}{proc.stderr}")

    # ⭐ THE OTHER HALF, and the half that actually catches a regression. The docstring names a
    # TWO-sided bug — "the exemption on the one file that does not need it, AND GONE FROM THE ONE
    # THAT DOES" — and only the second half was asserted. A decoy at the old vendored path must be
    # SCANNED, because it is not this file. Asserting the clean direction alone is equally true of
    # an exemption that has stopped working entirely.
    decoy = repo / _VENDORED_REL
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text(f"AGENT_URL=http://{_HOST}:9999/mcp\n", encoding="utf-8")
    _git(repo, "add", _VENDORED_REL)

    proc = subprocess.run([sys.executable, str(copied), "--repo", str(repo)],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 1, (
        f"an unrelated file sitting at {_VENDORED_REL!r} — the path the deleted constant named — "
        f"was exempted; the self-exemption is landing on a PATH rather than on the guard: "
        f"{proc.stdout}")
    assert _VENDORED_REL in proc.stdout, proc.stdout


@pytest.mark.timeout(300)
def test_the_RANGE_scan_resolves_the_self_path_the_same_way_the_tree_scan_does(
    tmp_path: Path,
) -> None:
    """⭐ THE TWO SCANS MUST AGREE, and one call site was missed.

    `added_lines` filtered its unscannable list with `_skipped(p)` — no `root` — so it fell back
    to the SELF_PATH CONSTANT while the tree scan resolved the real file from `__file__`. The
    consequence was not a cosmetic mismatch: a path wrongly judged "skipped" drops out of
    `pending`, and an empty `pending` returns early DISCARDING the unscannable list, so an
    unreadable blob was reported CLEAN by the range scan on the push path.

    Reproduced the way it actually bites: guard relocated to `tools/`, an undecodable file parked
    at the hardcoded `scripts/check_no_internal_info.py`.
    """
    repo = tmp_path / "twoscans"
    _init(repo)
    tools = repo / "tools"
    tools.mkdir()
    copied = tools / "check_no_internal_info.py"
    copied.write_bytes(_SCRIPT.read_bytes())
    scripts = repo / "scripts"
    scripts.mkdir()
    (scripts / "check_no_internal_info.py").write_bytes(
        f"AGENT=http://{_HOST}/\n".encode("utf-16"))
    _git(repo, "add", "tools/check_no_internal_info.py", "scripts/check_no_internal_info.py")
    _git(repo, "commit", "-q", "-m", "decoy at the hardcoded path")

    proc = subprocess.run([sys.executable, str(copied), "--repo", str(repo), "--range", "HEAD"],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 1, (
        f"the RANGE scan cleared a blob it never read, because it resolved the self-path "
        f"differently from the tree scan: rc={proc.returncode} {proc.stdout}{proc.stderr}")


def test_compile_patterns_is_the_single_definition_and_it_is_case_insensitive() -> None:
    """⛔ The drift this closes: `main` inlined `re.compile(rx, re.IGNORECASE)` and the test
    module inlined the identical line, so the tests carried their OWN correct copy of the flags.
    Dropping IGNORECASE from the shipped scan would have left the suite and CI fully green while
    an uppercase LAN host or a mixed-case freemail address scanned clean and exited 0.

    Asserted on BEHAVIOUR (an uppercase leak is caught through the shipped helper), not on the
    flag value — a flags comparison is satisfied by a helper nothing calls.
    """
    compiled = guard.compile_patterns()
    assert {label for label, _ in compiled} == {label for label, _ in guard.PATTERNS}
    upper = ("nas-a." + "lan").upper()
    labels = {label for _, label, _ in guard.scan_text(upper, compiled)}
    assert "private lan domain" in labels, (
        f"an UPPERCASE leak was not caught: {upper!r} — compile_patterns has lost IGNORECASE")


@pytest.mark.parametrize("sample", [
    # The array mounts the plural `disks` did not cover.
    "/mnt/" + "disk1" + "/appdata/svc",
    "/mnt/" + "disk17" + "/appdata/svc",
    # Provider domains the old alternation NAMED and still let through.
    "someone@" + "protonmail" + ".invalid",
    "someone@" + "live" + ".invalid",
    "someone@" + "googlemail" + ".invalid",
    # `_` is a word character, so `\b` did not hold and `<KEY>_<uuid>` walked past the bound.
    "app_id_" + "11111111-2222-3333-" + "4444-555555555555",
])
def test_the_shapes_the_old_bounds_let_through_are_now_caught(sample: str) -> None:
    """Each of these matched a pattern that was written FOR it and missed it on a bound."""
    assert guard.scan_text(sample, COMPILED), f"{sample!r} still walks through"


@pytest.mark.parametrize("sample", [
    r"clone it to C:\dev\unraid-templates",
    r"gh lives at C:\Program Files\GitHub CLI\gh.exe",
    r"export to D:\data\report.csv",
    r"put it in C:\Users\<you>\AppData\Local\app",
    r"set CACHE=C:\Users\%USERNAME%\AppData\Local\app",
    r"shared drop: C:\Users\Public\Documents\shared.csv",
])
def test_ordinary_windows_paths_do_NOT_fire(sample: str) -> None:
    """⭐ THE FALSE-POSITIVE HALF, which for a guard matters as much as the catching half.

    `[A-Za-z]:\\…` on its own fires on every ordinary Windows instruction — including the ones
    this repo's own README gives. A guard that reddens on the command it is telling you to run is
    a guard someone switches off, so the pattern is anchored to a `Users` segment and the two
    placeholder spellings fail structurally rather than by exception.
    """
    hits = guard.scan_text(sample, COMPILED)
    assert not hits, f"false positive on an ordinary Windows path: {sample!r} -> {hits}"


@pytest.mark.parametrize("sample", [
    # ⭐ THE SAME PATH IN ITS OTHER SPELLINGS. Only the `C:\` form was matched at first; each of
    # these is the identical leak on the identical machine, written the way that tool writes it.
    "cache = " + "/mnt/c/Users/" + "operator" + "/AppData/Local/svc",          # WSL view
    "set CACHE=" + "%SystemDrive%\\Users\\" + "operator" + "\\AppData",        # env-var prefix
    "path: " + "C:\\\\\\Users\\\\\\" + "operator",                            # 3+ escapes
])
def test_the_other_spellings_of_a_windows_profile_path_are_caught(sample: str) -> None:
    labels = {label for _, label, _ in guard.scan_text(sample, COMPILED)}
    assert "windows profile path" in labels, f"{sample!r} walks through -> {labels}"


@pytest.mark.parametrize("sample", [
    # ⭐ THE SEPARATOR CLASS, closed properly this time. `\\{1,2}` capped backslashes at two;
    # `(?:\\+|/)` fixed that and left the forward-slash side at ONE and forbade mixing. Each of
    # these walked through one of those two half-repairs.
    "cache = " + "C://Users/" + "operator" + "/AppData",
    "cache = " + "C:/Users//" + "operator",
    "cache = " + "/mnt/c//Users/" + "operator",
    "cache = " + "%SystemDrive%//Users//" + "operator",
    "url = " + "file:///C://Users//" + "operator" + "/AppData/Local",
    "cache = " + "C:\\/Users/" + "operator",
    "cache = " + "C:/\\Users\\" + "operator",
    "cache = " + "C:\\\\\\Users\\\\\\" + "operator",
])
def test_every_separator_spelling_of_a_windows_profile_path_is_caught(sample: str) -> None:
    labels = {label for _, label, _ in guard.scan_text(sample, COMPILED)}
    assert "windows profile path" in labels, f"{sample!r} walks through -> {labels}"


@pytest.mark.parametrize("sample", [
    # The separator widening must not start firing on ordinary text containing "/Users/".
    "GET /Users/me HTTP/1.1",
    "see https://example.com/Users/profile",
    "macOS home is /Users/ci-runner/work",
    "cd /Users/shared",
])
def test_the_separator_widening_did_NOT_start_firing_on_ordinary_paths(sample: str) -> None:
    hits = guard.scan_text(sample, COMPILED)
    assert not hits, f"false positive after widening the separator: {sample!r} -> {hits}"


def test_svg_and_other_text_formats_are_NEVER_skipped() -> None:
    """The leak an earlier batch removed was literally an icon URL inside an SVG.

    `SKIP_SUFFIXES` is the one list that can make the guard look away from a whole file, and
    nothing asserted that a TEXT-bearing format had not drifted into it. Adding `.svg` would have
    been silently green.
    """
    for suffix in (".svg", ".xml", ".md", ".json", ".yml", ".yaml", ".py", ".sh", ".txt"):
        assert suffix not in guard.SKIP_SUFFIXES, (
            f"{suffix} is a TEXT format and has been added to SKIP_SUFFIXES — an SVG is XML and "
            f"carries <title>/<desc>/href, which is where an icon-URL leak lived once")
    # ...and the exemption still works for what it is actually for.
    assert guard._skipped("icons/logo.png") and guard._skipped("a/b/FONT.WOFF2")


@pytest.mark.parametrize("label,rx", [(label, rx) for label, rx in guard.compile_patterns()])
def test_no_single_denylist_pattern_backtracks_quadratically(label: str, rx) -> None:
    """⭐ A HANG IS THE FAILURE MODE A PUSH-PATH GUARD CANNOT HAVE, and only the ALLOW-SPANS had
    a linearity test — the measured claims on `tailnet name` and `personal mail address` (the
    lookbehinds that once made a real `git push` take 2m06s) were comment-only. Dropping either
    bound left the suite green.

    Bounded rather than timed precisely: the point is the ORDER of growth, and a tight wall-clock
    assertion on a shared runner is its own flake. The inputs are the character runs each pattern
    is built from, which is what makes a missing left bound quadratic.
    """
    import time

    for filler in ("z" * 32_000, "a-b-" * 8_000, "0123456789abcdef-" * 2_000, "x.y." * 8_000):
        start = time.perf_counter()
        rx.search(filler)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, (
            f"{label!r} took {elapsed:.1f}s on a {len(filler)}-char line — it is backtracking "
            f"quadratically; check its left bound (?<![...]) is present")


def test_the_SUPPRESSION_path_is_linear_too() -> None:
    """The containment check runs once per match per pattern. Done as a linear scan over the
    permitted spans it is O(matches x spans), and a 176 KB minified line dense with permitted
    tokens took 31 SECONDS — on the push path. `_containment_index` makes each query O(log n).

    A line of many `.env.local` tokens is the shape that reaches it: each one is a permitted span
    AND contains a suppressible deny match, which is the worst case by construction.
    """
    import time

    line = "var e=" + '"'.join([".env.local"] * 8_000) + ";"
    start = time.perf_counter()
    guard.scan_text(line, COMPILED)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, (
        f"scanning one {len(line) // 1024} KB line of permitted tokens took {elapsed:.1f}s — the "
        f"containment check has gone quadratic again; it must stay indexed, not a linear scan")


@pytest.mark.timeout(300)
def test_a_leaky_COMMITTER_is_caught_even_when_the_author_is_clean(tmp_path: Path) -> None:
    """`commit_identity` reads four fields; the end-to-end tests only ever set the AUTHOR.

    Truncating `_IDENT_FIELDS` to the author pair left the whole suite green, so nothing pinned
    the committer half — and a committer address is set by exactly the same kind of stray
    `-c user.email` override the identity scan exists to catch.
    """
    repo = tmp_path / "committer"
    _init(repo)
    (repo / "ok.txt").write_text("nothing to see here\n", encoding="utf-8")
    _git(repo, "add", "ok.txt")
    bad = "someone@" + "yahoo" + ".invalid"
    _git(repo, "-c", "user.name=clean", "-c", "user.email=1234+clean@users.noreply.github.com",
         "-c", f"committer.email={bad}", "commit", "-q", "-m", "clean author, leaky committer",
         "--author", "clean <1234+clean@users.noreply.github.com>")

    proc = _run_guard(repo, "--range", "HEAD")
    assert proc.returncode == 1, (
        f"a leaky COMMITTER address scanned clean: {proc.stdout}{proc.stderr}")
    assert "committer email" in proc.stdout, proc.stdout


def test_a_short_identity_response_is_an_ERROR_not_a_silent_partial_scan() -> None:
    """`zip` stops at the shorter side, so a malformed git response would have quietly scanned
    only the fields that arrived and reported the commit clean on the rest — a guard reporting on
    less than it claims. Unverified must fail closed like everything else here."""
    original = guard._git
    guard._git = lambda *a, **k: "only-one-field"          # a response of the wrong shape
    try:
        with pytest.raises(ValueError, match="identity fields"):
            guard.commit_identity(Path("."), "abc1234567")
    finally:
        guard._git = original

    # ...and the well-formed response still parses into all four fields.
    guard._git = lambda *a, **k: "n\x00n@example.com\x00c\x00c@example.com\n"
    try:
        assert [f for f, _ in guard.commit_identity(Path("."), "abc")] == list(
            guard._IDENT_FIELDS)
    finally:
        guard._git = original


@pytest.mark.timeout(300)
def test_a_windows_profile_path_in_a_real_repo_fails_the_actual_tree_scan(tmp_path: Path) -> None:
    """The pattern proven end-to-end through the CLI, not just through `scan_text`."""
    repo = tmp_path / "winpath"
    _init(repo)
    leak = "C:\\Users\\" + "operator" + "\\AppData\\Local\\svc"
    (repo / "notes.md").write_text(f"cache lives in {leak}\n", encoding="utf-8")
    _git(repo, "add", "notes.md")

    proc = _run_guard(repo)
    assert proc.returncode == 1, f"a Windows profile path scanned clean: {proc.stdout}"
    assert "windows profile path" in proc.stdout, proc.stdout


# =============================================================================================
# ROUND 2 — the three fail-opens the amnesty hardening never touched (#241, #242, #250).
#
# All three live in the diff-PARSING and DECODE code rather than in the pattern matching, and all
# three have the same shape: the guard reports CLEAN about something it never read. That is worse
# than a missed pattern, because the run says so explicitly.
#
#   #250  an external diff driver replaces git's output, so no header is recognised, every line
#         is dropped, and the range scan exits 0 on a real leak.
#   #241  the path is derived wrongly (a path containing " b/", a git-quoted path, or a trailing
#         space eaten by `.strip()`), so the re-diff matches nothing, git exits 0 empty, and the
#         file is reported clean instead of unscannable. One shape also handed an arbitrary file
#         the guard's single self-exemption.
#   #242  BOM-less UTF-16LE of ASCII is VALID UTF-8, so every decode site succeeded, the
#         NUL-separated content matched no pattern, and the file was COUNTED AS SCANNED.
#
# ⭐ EVERY TEST BELOW WAS RUN AGAINST `main@8d3664d` FIRST and failed there. A guard test that has
# never been red proves only that it agrees with today's code.
# =============================================================================================


def _seeded(repo: Path) -> str:
    """A repository with one ordinary commit and NO dependence on machine git config.

    ⚠️ THE CONFIG IS PINNED, NOT INHERITED. A test that shells out to git and inherits whatever
    the machine has is the config-dependent sibling of a time-bomb test: it passes here and
    behaves differently on a runner. `core.quotePath` is pinned ON — git's own default — because
    the guard's job is to work regardless, and pinning it OFF here would quietly test the
    developer's config instead of the fix.
    """
    _init(repo)
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "config", "core.quotePath", "true")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed")
    return _git(repo, "rev-parse", "HEAD").strip()


def _commit(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").strip()


def _stage_literal(repo: Path, path: str, blob: bytes) -> None:
    """Put `path` in the INDEX with `blob` as its content, never touching the worktree.

    ⚠️ `core.protectNTFS=false` because Windows refuses to WRITE a name ending in a space. The
    flag guards writing such a name, not reading one, so a commit authored on Linux — every CI
    runner — carries it happily and the bypass is real wherever the repository is cloned.
    """
    sha = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=blob,
                         capture_output=True, check=True, timeout=120).stdout.decode().strip()
    _git(repo, "-c", "core.protectNTFS=false", "update-index", "--add",
         "--cacheinfo", f"100644,{sha},{path}")


def _unstage_literal(repo: Path, path: str) -> None:
    _git(repo, "-c", "core.protectNTFS=false", "update-index", "--force-remove", path)


def _commit_index(repo: Path, msg: str) -> str:
    """Commit exactly what is in the index, bypassing `git commit`'s worktree checks."""
    tree = _git(repo, "write-tree").strip()
    parent = _git(repo, "rev-parse", "HEAD").strip()
    sha = _git(repo, "commit-tree", tree, "-p", parent, "-m", msg).strip()
    _git(repo, "update-ref", "HEAD", sha)
    return sha


# Assembled at runtime, like every other leak literal in this file.
_LEAK_LINE = f"AGENT_URL=http://{_HOST}:9999/mcp"
_LEAK_ADDR_LINE = f"DATABASE_URL=postgresql://u:p@{_ADDR}:5432/db"
# A NUL inside the first 8000 bytes is what makes git call a blob binary.
_NUL_BLOB = b"\x00\x01\x02" + _LEAK_LINE.encode() + b"\n\x00"


# ------------------------------------------------------------------- #250 external diff driver


_HEADERLESS_DIFF = f"@@ -0,0 +1 @@\n+{_LEAK_LINE}\n"


def test_a_diff_with_hunks_but_no_header_is_UNREADABLE_rather_than_silently_clean() -> None:
    """#250. The state whose ABSENCE was the fail-open.

    An external diff driver makes git emit neither `diff --git` nor `+++ `, so no line can be
    attributed to a file. `parse_diff` dropped them all and returned empty-added AND
    empty-unscannable — byte-identical to "there was nothing to scan".
    """
    parsed = guard.parse_diff(_HEADERLESS_DIFF)
    assert parsed.added == [], "a line with no file attached must not be counted as scanned"
    assert parsed.unscannable == [guard._NO_HEADER], (
        "hunks with no recognised header must be REPORTED, not passed over")


def test_an_ordinary_diff_is_never_reported_as_headerless() -> None:
    """The negative direction. A state that fires on correct input is a false red, and a guard
    that reddens a correct tree gets switched off."""
    parsed = guard.parse_diff(
        f"diff --git a/cfg.txt b/cfg.txt\n--- /dev/null\n+++ b/cfg.txt\n"
        f"@@ -0,0 +1 @@\n+{_LEAK_LINE}\n")
    assert parsed.unscannable == []
    assert [c for _, _, c in parsed.added] == [_LEAK_LINE]


def test_an_empty_diff_is_not_reported_as_headerless() -> None:
    """A merge commit against its first parent can legitimately produce nothing at all."""
    assert guard.parse_diff("").unscannable == []
    assert guard.parse_diff("\n").unscannable == []


def test_the_headerless_marker_reaches_the_verdict_and_prints_without_its_sigil() -> None:
    """The marker is only worth having if it survives to the operator, legibly."""
    findings, blind = guard.scan_added("abc1234567", guard.parse_diff(_HEADERLESS_DIFF), COMPILED)
    assert findings == []
    assert len(blind) == 1, blind
    assert "external diff driver" in blind[0], blind[0]
    assert guard._MARKER_SIGIL not in blind[0], "the NUL sigil must be stripped for display"


def test_the_sigil_is_stripped_from_a_FINDING_line_too_not_only_the_unscannable_list() -> None:
    """`_shown` is applied at two sites and only one was covered — deleting it from the findings
    line left the whole suite green. A raw NUL reaching a git hook's cp1252 stdout is exactly the
    class `_ascii` exists for, so the guard would die while announcing a leak."""
    marker = guard._unparsed_header("a/x b/y")
    findings, _ = guard.scan_added("abc1234567", guard.ParsedDiff([(marker, 1, _LEAK_LINE)], []),
                                   COMPILED)
    assert len(findings) == 1, findings
    assert guard._MARKER_SIGIL not in findings[0], (
        "the NUL sigil leaked into a finding line, which a cp1252 pipe cannot print")


@pytest.mark.timeout(300)
def test_an_unattributable_marker_survives_ALONGSIDE_a_binary_needing_a_rediff(
    tmp_path: Path,
) -> None:
    """The combined case, which the early return hides.

    `added_lines` returns `unattributable` in two places: the `if not pending` early return, and
    the accumulator that follows it. Only the first was covered, so blanking the second
    (`still_unreadable = []`) left every test green — a diff carrying BOTH an unattributable
    marker and a binary file needing a re-diff would have dropped the marker on the floor.

    Driven through the pure function because git with `--no-ext-diff` pinned will not produce a
    headerless diff, which is the point of the pin.
    """
    repo = tmp_path / "combined"
    _seeded(repo)
    (repo / "asset.bin").write_bytes(b"\x00\x01" + _LEAK_LINE.encode() + b"\n")
    _commit(repo, "add asset")
    sha = _git(repo, "rev-parse", "HEAD").strip()

    real = guard.parse_diff

    def both(diff: str, _o=real):
        parsed = _o(diff)
        # Only the PRIMARY parse gets the extra marker; the re-diff's own result is untouched.
        if "--text" not in diff and parsed.unscannable:
            return guard.ParsedDiff(parsed.added, [*parsed.unscannable, guard._NO_HEADER])
        return parsed

    guard.parse_diff = both
    try:
        got = guard.added_lines(repo, sha)
    finally:
        guard.parse_diff = real

    assert any(guard._is_marker(u) for u in got.unscannable), (
        f"the unattributable marker was dropped once a re-diff also had to run: {got.unscannable}")


def test_a_marker_is_never_mistaken_for_a_skipped_path() -> None:
    """A marker that `_skipped` swallowed would be a fail-open with extra steps.

    ⚠️ ONLY PATH-LESS THINGS MAY BE MARKERS, and an earlier version of this test asserted the
    opposite — that a NUL-bearing PATH wrapped as a marker also escaped `_skipped`. That pinned a
    defect in the direction that made it look correct. A value that HAS a path must go through the
    normal filters; see
    `test_a_skipped_suffix_stays_skipped_by_BOTH_scans_when_its_content_has_a_NUL`.
    """
    for marker in (guard._NO_HEADER, guard._unparsed_header("a/x b/y")):
        assert guard._is_marker(marker)
        assert not guard._skipped(marker), marker


@pytest.mark.timeout(300)
def test_a_skipped_suffix_stays_skipped_by_BOTH_scans_when_its_content_has_a_NUL(
    tmp_path: Path,
) -> None:
    """⛔ A FALSE RED WITH NO AVAILABLE FIX — the worst shape a guard can take.

    Wrapping a NUL-bearing path in a marker made it inherit the marker exemption from `_skipped`,
    so a `.pdf` — already in SKIP_SUFFIXES — whose first NUL falls PAST git's 8000-byte binary
    window was SKIPPED by the tree scan and REFUSED by the range scan. The two scans disagreeing
    about which files count is precisely what sharing `_skipped` exists to prevent.

    And the printed remedy was inert: "add a binary suffix to SKIP_SUFFIXES" for a suffix already
    in SKIP_SUFFIXES. `.githooks/pre-push` blocked the push with nothing the operator could change.

    Both directions asserted, so this cannot be satisfied by skipping everything: the skipped
    suffix passes, and a byte-identical file at a NON-skipped suffix is still refused.
    """
    repo = tmp_path / "pdfnul"
    base = _seeded(repo)
    body = b"%PDF-1.4\n" + b"x" * 9000 + b"\n" + _LEAK_LINE.encode("utf-16-le") + b"\n"
    (repo / "doc.pdf").write_bytes(body)
    (repo / "doc.dat").write_bytes(body)
    _commit(repo, "add assets")

    # The premise: git must serve these as TEXT, or the primary-path NUL check is never reached.
    assert "Binary files" not in _git(repo, "diff", "--unified=0", "--no-color", base, "HEAD"), (
        "premise broken: git called it binary, so this exercises the fallback, not the case named")

    proc = _run_guard(repo, "--range", f"{base}..HEAD")
    out = proc.stdout + proc.stderr
    assert "doc.pdf" not in out, (
        f"a suffix already in SKIP_SUFFIXES was refused by the range scan while the tree scan "
        f"skips it, and the printed remedy cannot clear it: {out}")
    assert "doc.dat" in out, f"the non-skipped twin must still be refused: {out}"


@pytest.mark.timeout(300)
def test_an_external_diff_driver_cannot_blind_the_range_scan(tmp_path: Path) -> None:
    """#250 end to end, with a real driver configured — the ONLY way to test this.

    ⚠️ LOCAL-ONLY IN PRACTICE, and that is exactly why it matters: a CI runner has no
    `diff.external`, so the layer this disables is `.githooks/pre-push` — the layer that exists
    for the add-then-delete case, on the machine of whoever happens to like difftastic.
    """
    driver = tmp_path / "driver.sh"          # OUTSIDE the repo, so it is never committed
    driver.write_text('#!/bin/sh\nprintf "1 %s\\n" "$(cat "$5")"\n',
                      encoding="utf-8", newline="\n")
    repo = tmp_path / "extdiff"
    base = _seeded(repo)
    _git(repo, "config", "diff.external", "sh " + driver.as_posix())
    (repo / "cfg.txt").write_text(_LEAK_LINE + "\n", encoding="utf-8")
    _commit(repo, "add cfg")
    (repo / "cfg.txt").unlink()
    _commit(repo, "drop cfg")               # the tree is clean; only history holds it now

    assert _run_guard(repo).returncode == 0, "precondition: the tree really is clean"
    proc = _run_guard(repo, "--range", f"{base}..HEAD")
    assert proc.returncode == 1, (
        f"an external diff driver blinded the range scan: {proc.stdout}{proc.stderr}")
    assert "private lan domain" in proc.stdout, proc.stdout


@pytest.mark.timeout(300)
def test_a_textconv_filter_cannot_hide_a_leak_from_the_range_scan(tmp_path: Path) -> None:
    """The `.gitattributes` route to the same place, which `--no-textconv` closes.

    ⚠️ DECLARED LIMIT: this exercises the PRIMARY diff call. The `--text` re-diff cannot be
    reached with a textconv configured — git treats the converted blob as text and never emits
    `Binary files` — so that call site is covered by SHARING `_DIFF_FLAGS` with this one rather
    than by its own end-to-end case. The mutation matrix strips the flag from the shared tuple.
    """
    conv = tmp_path / "conv.sh"
    conv.write_text('#!/bin/sh\necho "nothing to see here"\n', encoding="utf-8", newline="\n")
    repo = tmp_path / "textconv"
    base = _seeded(repo)
    _git(repo, "config", "diff.hide.textconv", "sh " + conv.as_posix())
    (repo / ".gitattributes").write_text("*.dat diff=hide\n", encoding="utf-8")
    (repo / "cfg.dat").write_text(_LEAK_LINE + "\n", encoding="utf-8")
    _commit(repo, "add cfg")
    (repo / "cfg.dat").unlink()
    _commit(repo, "drop cfg")

    proc = _run_guard(repo, "--range", f"{base}..HEAD")
    assert proc.returncode == 1, f"a textconv filter hid the leak: {proc.stdout}{proc.stderr}"


@pytest.mark.timeout(300)
def test_EVERY_git_diff_the_scan_runs_carries_the_pinned_config_and_flags(tmp_path: Path) -> None:
    """⛔ ASSERTS WHAT THE CODE PASSES TO GIT, not what the constants contain.

    The first version of this test inspected `_DIFF_FLAGS`/`_DIFF_CONFIG` and nothing else. That
    restated the constants instead of executing the call sites: reverting the RE-DIFF invocation to
    its old unpinned argv left the whole suite green, and that re-diff is the very call site the
    textconv test explicitly delegates to this one. It also carried a genuine tautology —
    `assert pinned is not None` on a freshly-built dict, which cannot fail.

    So this captures every `git diff` argv the scan actually issues and checks each one, which
    covers the sharing without depending on how it is implemented.
    """
    repo = tmp_path / "argv"
    # The seed SHA is not needed here — this test scans HEAD directly — but the call is what
    # BUILDS the repository, so it is not removable.
    _seeded(repo)
    (repo / ".gitattributes").write_text("*.lock binary\n", encoding="utf-8")
    (repo / "deps.lock").write_text("resolved = 1\n", encoding="utf-8")   # forces the re-diff
    (repo / "plain.txt").write_text("hello\n", encoding="utf-8")
    _commit(repo, "add both")
    sha = _git(repo, "rev-parse", "HEAD").strip()

    seen: list[list[str]] = []
    real_run, real_git = subprocess.run, guard._git

    def spy_run(cmd, *a, **kw):
        if isinstance(cmd, list) and "diff" in cmd:
            seen.append(list(cmd))
        return real_run(cmd, *a, **kw)

    def spy_git(root, *args, **kw):
        if "diff" in args:
            seen.append(["git", *args])
        return real_git(root, *args, **kw)

    guard.subprocess.run, guard._git = spy_run, spy_git
    try:
        guard.added_lines(repo, sha)
    finally:
        guard.subprocess.run, guard._git = real_run, real_git

    assert len(seen) >= 2, f"expected the primary diff AND the --text re-diff, saw {len(seen)}"
    assert any("--text" in c for c in seen), "the re-diff call site was never exercised"
    for cmd in seen:
        flat = " ".join(cmd)
        for flag in ("--no-ext-diff", "--no-textconv", "--no-renames", "--unified=0"):
            assert flag in cmd, f"{flag} missing from a real git diff call: {flat}"
        for key in ("core.quotePath=false", "diff.noprefix=false",
                    "diff.srcPrefix=a/", "diff.dstPrefix=b/", "diff.mnemonicPrefix=false"):
            assert key in cmd, f"{key} unpinned on a real git diff call: {flat}"


# ------------------------------------------------------------- #241 path derivation / `+++ `


@pytest.mark.parametrize("path", [
    "cfg.txt",
    "dir/cfg.txt",
    "a file with spaces.txt",
    "x b/y.lock",                 # contains " b/" — the split-based derivation's first victim
    "b/b.txt",
    "notes.png ",                 # trailing space — what `.strip()` used to eat
    " leading.txt",
    "café.bin",              # non-ASCII, quoted by git unless core.quotePath is pinned off
    "a/b b/c d/e.bin",
])
def test_the_header_path_is_reconstructed_byte_exactly(path: str) -> None:
    """#241, member 1. Reconstruction by LENGTH, then verified — never a delimiter guess."""
    assert guard._header_path(f"a/{path} b/{path}") == path


@pytest.mark.parametrize("tail", [
    '"a/caf\\303\\251.bin" "b/caf\\303\\251.bin"',   # a quoted pair the pin should prevent
    "a/only-one-side",
    "a/x b/y",                                        # the two sides disagree: a rename slipped in
    "",
    "a/",
])
def test_an_unreconstructable_header_fails_CLOSED_and_names_what_it_saw(tail: str) -> None:
    """A tail this cannot explain must never produce a wrong-but-plausible path.

    And the marker must NAME the header: a fail-closed report that says only "unparsed" leaves
    the operator nothing to act on, which is how the sibling repo's attempt made every binary
    file in a commit unactionable.
    """
    got = guard._header_path(tail)
    assert guard._is_marker(got), f"{tail!r} produced a path rather than a refusal: {got!r}"
    assert repr(tail) in got, "the marker must quote the header it could not parse"


def test_the_plus_line_strips_one_tab_and_never_whitespace() -> None:
    """#241, member 2. git appends a TAB after a path carrying trailing whitespace — measured:
    `+++ b/notes.png \\t` — so `.strip()` removed the tab AND the meaningful space with it."""
    assert guard._plus_path("b/notes.png \t") == "b/notes.png "
    assert guard._plus_path("b/cfg.txt") == "b/cfg.txt"
    assert guard._plus_path("b/two.txt\t\t") == "b/two.txt\t", "at most ONE tab"
    assert guard._plus_path("b/ leading.txt") == "b/ leading.txt"


def test_the_plus_line_cannot_OVERRIDE_a_path_the_header_already_gave() -> None:
    """#241, member 2, at the seam. Two sources for one fact is how the bypass happened: the
    header produced the path byte-exactly and the very next line threw it away.

    ⚠️ THE TWO SOURCES MUST DISAGREE HERE, or this proves nothing. The first version of this test
    used a trailing-space path, where `_plus_path` and `_header_path` compute the SAME answer —
    so it passed with the precedence rule reverted, and the mutation matrix caught it as a
    survivor. It was satisfied by a predicate other than the one it names. Making the two sources
    name different files isolates the precedence rule as the only thing that can decide it, and
    the decoy is a SKIPPED suffix so a wrong answer drops the leak rather than merely mislabelling
    it.
    """
    parsed = guard.parse_diff(
        "diff --git a/real.txt b/real.txt\n--- /dev/null\n+++ b/decoy.png\n"
        f"@@ -0,0 +1 @@\n+{_LEAK_LINE}\n")
    assert [p for p, _, _ in parsed.added] == ["real.txt"], (
        "the `+++ ` line was allowed to replace the header's path")
    assert _hits("abc", parsed.added), "and the leak must therefore still be found"


def test_the_plus_line_does_not_override_even_when_the_two_sources_agree() -> None:
    """The original trailing-space case, kept as a regression pin for `_plus_path` itself."""
    parsed = guard.parse_diff(
        "diff --git a/notes.png  b/notes.png \nnew file mode 100644\n"
        "--- /dev/null\n+++ b/notes.png \t\n"
        f"@@ -0,0 +1 @@\n+{_LEAK_LINE}\n")
    assert [p for p, _, _ in parsed.added] == ["notes.png "]


def test_the_plus_line_IS_the_path_source_when_there_is_no_header() -> None:
    """⛔ The fallback is NOT redundant — deleting it is what turned #241 into #250 in the
    sibling repo. It is the only path source when git emits no `diff --git` line."""
    parsed = guard.parse_diff(f"--- /dev/null\n+++ b/cfg.txt\n@@ -0,0 +1 @@\n+{_LEAK_LINE}\n")
    assert [p for p, _, _ in parsed.added] == ["cfg.txt"]


def test_a_deleted_file_is_still_recognised_by_dev_null_on_the_plus_line() -> None:
    """The behaviour the `+++ ` branch must keep: a deletion adds nothing."""
    parsed = guard.parse_diff(
        "diff --git a/cfg.txt b/cfg.txt\ndeleted file mode 100644\n"
        f"--- a/cfg.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-{_LEAK_LINE}\n")
    assert parsed.added == []
    assert parsed.unscannable == []


def test_an_unparseable_header_is_reported_ONCE_not_once_per_branch() -> None:
    """The `Binary files ` branch must not re-report a marker the header line already added.

    Reverting that guard leaves two identical entries, inflating the "in N added file(s)" count
    the operator reads. Asserted on the LIST, so it pins the behaviour rather than the wording.
    """
    parsed = guard.parse_diff(
        'diff --git "a/bad\\"q.bin" "b/bad\\"q.bin"\n'
        'Binary files "a/bad\\"q.bin" and "b/bad\\"q.bin" differ\n')
    assert len(parsed.unscannable) == 1, (
        f"an unparseable binary header was reported once per branch: {parsed.unscannable}")
    assert guard._is_marker(parsed.unscannable[0])


def test_DELETING_a_file_whose_path_cannot_be_parsed_is_not_reported() -> None:
    """⛔ A DELETION PUBLISHES NOTHING, and that has to hold for an unparseable path too.

    The header marker is appended at the `diff --git` line, which is read BEFORE
    `deleted file mode `, so the deletion guard could never apply to it: a commit that merely
    DELETED a file whose path git C-quotes failed the range scan. The advice the marker carries —
    rename it, or review that commit by hand — cannot be acted on for a commit already written, so
    it was a red with no way out. That is the trap the `Binary files ` branch already documents
    for the parseable case; there is no reason the unparseable case should be treated worse.

    Both spellings, because git emits a different body for a text and a binary deletion.
    """
    text_deletion = guard.parse_diff(
        'diff --git "a/note\\\\draft.md" "b/note\\\\draft.md"\n'
        "deleted file mode 100644\n"
        '--- "a/note\\\\draft.md"\n+++ /dev/null\n@@ -1 +0,0 @@\n-perfectly clean text\n')
    assert text_deletion.unscannable == [], (
        f"deleting an unparseable TEXT path was reported: {text_deletion.unscannable}")

    binary_deletion = guard.parse_diff(
        'diff --git "a/bad\\"q.bin" "b/bad\\"q.bin"\n'
        "deleted file mode 100644\n"
        'Binary files "a/bad\\"q.bin" and /dev/null differ\n')
    assert binary_deletion.unscannable == [], (
        f"deleting an unparseable BINARY path was reported: {binary_deletion.unscannable}")

    # ⚠️ THE NEGATIVE DIRECTION, or this "fix" is just a bypass: ADDING one is still refused.
    addition = guard.parse_diff(
        'diff --git "a/bad\\"q.bin" "b/bad\\"q.bin"\n'
        "new file mode 100644\n"
        'Binary files /dev/null and "b/bad\\"q.bin" differ\n')
    assert len(addition.unscannable) == 1, (
        f"adding an unparseable path must still be refused: {addition.unscannable}")

    # ⛔⛔ PRECISION, NOT MERELY REMOVAL — and this is the assertion that earns its keep.
    # Everything above passes just as happily if the pop is replaced by `unscannable.clear()`,
    # which is a REAL fail-open: one commit that deletes an unparseable path while ADDING a
    # binary blob wipes the binary's entry off the list too, and the run exits 0 with the added
    # value still recoverable from history. A test that pins "something was removed" cannot see
    # that; only one that pins WHICH entry survives can.
    mixed = guard.parse_diff(
        "diff --git a/asset.dat b/asset.dat\nnew file mode 100644\n"
        "Binary files /dev/null and b/asset.dat differ\n"
        'diff --git "a/bad\\\\q.md" "b/bad\\\\q.md"\n'
        "deleted file mode 100644\n"
        '--- "a/bad\\\\q.md"\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n')
    assert mixed.unscannable == ["asset.dat"], (
        f"the deletion's pop took an unrelated entry that was already reported: "
        f"{mixed.unscannable}")


@pytest.mark.timeout(300)
def test_BOTH_halves_of_241_are_closed_a_half_fix_leaves_the_other_open(tmp_path: Path) -> None:
    """⭐ ONE test, BOTH members — deliberately.

    The work package is explicit that #241 has two members and that closing only one leaves the
    other open. Split across two tests, a half-fix shows as one red and one green and reads like
    partial progress; asserted together it reads as what it is.

      (1) the `diff --git` header parse — a BINARY file whose path contains " b/". There is no
          `+++ ` line to correct it, the re-diff matches nothing, git exits 0 empty, and the
          file is announced CLEAN.
      (2) the `+++ ` `.strip()` — a TEXT file at `<the guard's own path> ` strips to exactly that
          path and inherits its single self-exemption.

    ⭐ MEMBER 2 IS RUN AGAINST A VENDORED COPY, because it is the only posture in which the
    exemption it steals exists at all. Run from an installed package the guard is not inside the
    scanned repository, recognises no file there as itself, and the theft has nothing to take —
    so a version of this test that ran `_SCRIPT` would pass while proving nothing.
    """
    repo = tmp_path / "both241"
    base = _seeded(repo)
    vendored = _vendor_guard(repo)
    _git(repo, "add", _VENDORED_REL)
    _git(repo, "commit", "-q", "-m", "vendor the guard, so its self-exemption is live here")

    (repo / "x b").mkdir()
    (repo / "x b" / "y.lock").write_bytes(_NUL_BLOB)
    # ⚠️ ORDER IS LOAD-BEARING. `git add -A` sees a tracked path that is absent from the worktree
    # and stages its DELETION — so running it AFTER `_stage_literal` silently undid the literal
    # path, and this test passed its first assertion while the second half was never committed at
    # all. A setup that quietly no-ops is the vacuous-premise failure, which is why the tree is
    # asserted below rather than assumed.
    _git(repo, "add", "-A")
    _stage_literal(repo, _VENDORED_REL + " ", (_LEAK_ADDR_LINE + "\n").encode())
    add_sha = _commit_index(repo, "add both")

    listed = _git(repo, "ls-tree", "-r", "--name-only", add_sha)
    assert "x b/y.lock" in listed, f"member 1's file never reached the commit: {listed!r}"
    assert _VENDORED_REL + " \n" in listed or _VENDORED_REL + ' ' in listed, (
        f"member 2's file never reached the commit — the premise is vacuous: {listed!r}")

    (repo / "x b" / "y.lock").unlink()
    _git(repo, "add", "-A")
    _unstage_literal(repo, _VENDORED_REL + " ")
    _commit_index(repo, "drop both")

    assert _run_vendored(vendored, repo).returncode == 0, "precondition: the tree really is clean"
    proc = _run_vendored(vendored, repo, "--range", f"{base}..HEAD")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"both halves of #241 still pass silently: {out}"
    assert "x b/y.lock" in out, f"member 1 (' b/' in the header path) still open: {out}"
    assert _VENDORED_REL + " " in out, f"member 2 (`+++ `.strip()) still open: {out}"


@pytest.mark.timeout(300)
def test_a_non_ascii_path_is_resolved_rather_than_quoted_away(tmp_path: Path) -> None:
    """#241, member 1(b) — the realistic one: any binary file with an accented filename."""
    repo = tmp_path / "quoted"
    base = _seeded(repo)
    (repo / "café.bin").write_bytes(_NUL_BLOB)
    _commit(repo, "add asset")
    (repo / "café.bin").unlink()
    _commit(repo, "drop asset")

    proc = _run_guard(repo, "--range", f"{base}..HEAD")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"a git-quoted path scanned clean: {out}"
    # ⚠️ RESOLVED, NOT MERELY REFUSED. `"caf" in out` alone cannot tell the two apart: with the
    # `core.quotePath=false` pin removed, the output still contains "caf" — inside the escaped RAW
    # HEADER of an unparsed-header refusal (`'"a/caf\303\251.bin" ...'`). The test would have kept
    # its name while proving the opposite of what it claims, so it must assert that the path was
    # reconstructed rather than that the bytes appear somewhere.
    assert "cannot resolve to one path" not in out, (
        f"the pin is not holding: the path was REFUSED as unparseable rather than resolved: {out}")
    # `_ascii` escapes it for a cp1252 pipe, so match the stem rather than the accent.
    assert "caf" in out and ".bin" in out, out


@pytest.mark.timeout(300)
def test_a_redi_ff_that_matches_NOTHING_leaves_the_path_unreadable(tmp_path: Path) -> None:
    """The check that closes the CLASS rather than the two reported header shapes.

    Every path mis-parse — including any future one — ends in the same place: `git diff` exits 0
    with no output, the strict decode of "" succeeds, and the path drops silently out of
    `still_unreadable`. Driven here through the pure function, since inventing a NEW mis-parse is
    exactly what this is meant to survive.
    """
    parsed = guard.ParsedDiff([], ["no/such/file.bin"])
    # A path that is tracked nowhere: the re-diff can only come back empty.
    repo = tmp_path / "empty-rediff"
    _seeded(repo)
    (repo / "real.bin").write_bytes(_NUL_BLOB)
    _commit(repo, "add real")
    sha = _git(repo, "rev-parse", "HEAD").strip()

    original = guard.parse_diff
    try:
        # Force the primary parse to claim a path the commit does not contain, which is precisely
        # what a mis-parse produces. Everything after that is the real code.
        guard.parse_diff = lambda d, _o=original: (
            parsed if d.startswith("diff --git") else _o(d))
        got = guard.added_lines(repo, sha)
    finally:
        guard.parse_diff = original
    assert got.unscannable == ["no/such/file.bin"], (
        "an empty re-diff was treated as a clean one — the shape every mis-parse ends in")


@pytest.mark.timeout(300)
def test_a_path_containing_glob_characters_is_matched_literally(tmp_path: Path) -> None:
    """A glob-shaped filename is scanned correctly through the `--text` re-diff path.

    ⚠️ SCOPED CLAIM, because the mutation matrix disproved the stronger one. This test does NOT
    demonstrate that `:(literal)` is load-bearing: dropping it leaves the suite green, and the
    reason is measured, not assumed — git's pathspec matching tries an EXACT match before treating
    the string as a glob, so `-- 'sprite[1].bin'` resolves the literal file either way. Windows
    cannot create a name containing `*` or `?` at all, so the remaining glob metacharacters are
    not reachable here.

    `:(literal)` is kept because it is free and removes the dependence on that git behaviour, but
    it is belt-and-braces rather than a proven guard — and the honest statement of what a guard
    does NOT catch ages better than a strength claim. What this test DOES pin is the behaviour
    that matters: a `.gitattributes`-binary text file at such a path is resolved and cleared
    (no false red), and a leak inside one is still caught (it read the real content).
    """
    repo = tmp_path / "globpath"
    base = _seeded(repo)
    (repo / ".gitattributes").write_text("*.bin binary\n", encoding="utf-8")
    (repo / "sprite[1].bin").write_text("frames = 12\n", encoding="utf-8")
    _commit(repo, "add sprite")

    # The premise: git really does refuse to diff it, so the re-diff path IS the one under test.
    assert "Binary files" in _git(repo, "diff", "--unified=0", "--no-color", base, "HEAD"), (
        "premise broken: git served it as text, so the pathspec is never exercised")

    proc = _run_guard(repo, "--range", f"{base}..HEAD")
    assert proc.returncode == 0, (
        f"a glob-shaped path was not matched literally, so correct work went red: {proc.stdout}")

    (repo / "sprite[1].bin").write_text(f"frames = 12\n{_LEAK_LINE}\n", encoding="utf-8")
    _commit(repo, "leak in the sprite")
    proc = _run_guard(repo, "--range", f"{base}..HEAD")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"a leak at a glob-shaped path scanned clean: {out}"
    assert "private lan domain" in out, out


# ------------------------------------------------------------------------ #242 NUL / UTF-16


def test_a_NUL_bearing_added_line_is_reported_rather_than_counted_as_scanned() -> None:
    """#242 on the PRIMARY diff path — not only in the `--text` re-diff fallback.

    git flags a blob binary only if a NUL falls in its first 8000 bytes, so a UTF-16 payload
    further in gets an ordinary TEXT diff and the re-diff never runs. "git called it binary" and
    "it contains a NUL" are different questions.
    """
    payload = _LEAK_LINE.encode("utf-16-le").decode("utf-8")
    parsed = guard.parse_diff(
        f"diff --git a/report.txt b/report.txt\n--- /dev/null\n+++ b/report.txt\n"
        f"@@ -0,0 +1 @@\n+{payload}\n")
    assert parsed.added == [], "NUL-separated content is not text and must not count as scanned"
    # ⚠️ The PLAIN PATH, not a marker: it has a path, so it must stay subject to `_skipped`.
    assert parsed.unscannable == ["report.txt"], parsed.unscannable
    assert not guard._is_marker(parsed.unscannable[0])


def test_a_NUL_bearing_file_is_reported_ONCE_not_once_per_line() -> None:
    """A UTF-16 file is NUL-bearing on every line; a thousand reports read as a thousand faults."""
    payload = _LEAK_LINE.encode("utf-16-le").decode("utf-8")
    parsed = guard.parse_diff(
        f"diff --git a/report.txt b/report.txt\n--- /dev/null\n+++ b/report.txt\n"
        f"@@ -0,0 +3 @@\n+{payload}\n+{payload}\n+{payload}\n")
    assert len(parsed.unscannable) == 1, parsed.unscannable


def test_ordinary_non_ascii_utf8_is_still_SCANNED_not_refused() -> None:
    """⭐ THE DIRECTION THAT MATTERS MOST. A rule that refuses too much is a false red, and a
    guard that reddens correct work gets switched off — which is a net LOSS of safety."""
    parsed = guard.parse_diff(
        f"diff --git a/notes.md b/notes.md\n--- /dev/null\n+++ b/notes.md\n"
        f"@@ -0,0 +1 @@\n+café naïve 日本語 {_LEAK_LINE}\n")
    assert parsed.unscannable == [], "accented and CJK UTF-8 must not be mistaken for binary"
    assert len(parsed.added) == 1
    assert _hits("abc", parsed.added), "and the leak inside it must still be caught"


@pytest.mark.timeout(300)
def test_bomless_utf16le_is_not_cleared_by_the_TREE_scan(tmp_path: Path) -> None:
    """#242, decode site 1. UTF-16LE of ASCII is `A\\x00G\\x00E\\x00…` — every byte under 0x80,
    so `read_text(encoding="utf-8")` SUCCEEDS and the file was counted in the scanned total."""
    repo = tmp_path / "u16tree"
    _seeded(repo)
    (repo / "report.txt").write_bytes(_LEAK_LINE.encode("utf-16-le"))
    _git(repo, "add", "report.txt")

    proc = _run_guard(repo)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"BOM-less UTF-16LE was reported as scanned and clean: {out}"
    assert "NUL" in out and "report.txt" in out, out


@pytest.mark.timeout(300)
def test_bomless_utf16le_is_not_cleared_through_the_STAGED_blob(tmp_path: Path) -> None:
    """#242, decode site 2 — `staged_blob`, reached by the same content one step later: stage the
    file, delete it from the worktree, and the tree scan reads the index blob instead.

    This is the site the sibling repo's attempt missed, which is why the check is placed on what
    the reads PRODUCE rather than being written at each read.
    """
    repo = tmp_path / "u16staged"
    _seeded(repo)
    (repo / "report.txt").write_bytes(_LEAK_LINE.encode("utf-16-le"))
    _git(repo, "add", "report.txt")
    (repo / "report.txt").unlink()

    proc = _run_guard(repo)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"a staged UTF-16 blob was decoded and cleared: {out}"
    assert "NUL" in out and "report.txt" in out, out


@pytest.mark.timeout(300)
def test_bomless_utf16le_past_gits_binary_window_is_not_cleared_by_the_RANGE_scan(
    tmp_path: Path,
) -> None:
    """#242, decode site 3, in the place that actually matters.

    ⚠️ The payload sits PAST 8000 bytes deliberately. git only calls a blob binary when a NUL is
    in its first 8000, so this gets an ordinary text diff and never reaches the `--text` re-diff —
    which is where the sibling repo put its check, covering only the case git had already refused.
    """
    repo = tmp_path / "u16range"
    base = _seeded(repo)
    (repo / "report.txt").write_bytes(
        b"# header\n" * 1200 + _LEAK_LINE.encode("utf-16-le") + b"\n")
    _commit(repo, "add report")
    (repo / "report.txt").unlink()
    _commit(repo, "drop report")

    # The premise: git really does serve this as TEXT, so the fallback path is not involved.
    raw = _git(repo, "diff", "--unified=0", "--no-color", f"{base}", "HEAD~0")
    assert "Binary files" not in raw, "premise broken: git called it binary, wrong case under test"

    proc = _run_guard(repo, "--range", f"{base}..HEAD")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"UTF-16LE past the 8000-byte window scanned clean: {out}"
    assert "report.txt" in out, out
    assert "NOT CLEARED" in out and "NUL byte" in out, (
        f"the verdict must say what was refused and why: {out}")


@pytest.mark.timeout(300)
def test_a_gitattributes_binary_TEXT_file_is_still_resolved_and_scanned(tmp_path: Path) -> None:
    """The false red the NUL rule must not create.

    Marking a generated lockfile `binary` is standard practice. git emits `Binary files … differ`,
    the `--text` re-diff must resolve it, and a CLEAN one must stay clean while a leaking one is
    still caught. Both directions, because only asserting the clean one proves nothing.
    """
    repo = tmp_path / "lockclean"
    base = _seeded(repo)
    (repo / ".gitattributes").write_text("*.lock binary\n", encoding="utf-8")
    (repo / "deps.lock").write_text("resolved = 1\nname = 'left-pad'\n", encoding="utf-8")
    _commit(repo, "add lock")
    assert _run_guard(repo).returncode == 0
    assert _run_guard(repo, "--range", f"{base}..HEAD").returncode == 0, (
        "a `.gitattributes`-binary TEXT file was refused — that is a false red on correct work")

    (repo / "deps.lock").write_text(f"resolved = 1\n{_LEAK_LINE}\n", encoding="utf-8")
    _commit(repo, "leak in the lock")
    proc = _run_guard(repo, "--range", f"{base}..HEAD")
    assert proc.returncode == 1, f"and a leak inside one must still be caught: {proc.stdout}"


# ----------------------------------------------------- the false "these repos are public" claim


def test_the_guard_does_not_claim_the_fleet_is_public() -> None:
    """⭐ A FALSE CLAIM IS A DEFECT, and this one is the argument for switching the guard off.

    Stating a rationale that holds only for public repositories hands the next reader a reason to
    ignore the guard everywhere else — and this file is the fleet's shared source for it.

    ⚠️ The correction deliberately does NOT quote the sentence it refutes, so this assertion can
    be a plain absence check rather than one that has to reason about context.

    ⭐ THE OVER-CORRECTION IS A DEFECT TOO, which is why this test now pins BOTH directions. The
    replacement claim — that one named repository was the only public one — was true when it was
    written and false by the time this module shipped, because the second public repository is the
    one PUBLISHING this file to everyone who installs the library. A guard's docstring that is
    wrong about who can read it is wrong about the premise its own rules rest on.
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "repos are public" not in src.lower(), (
        "the false fleet-wide 'these repos are public' claim is back")
    assert "wrong TWICE" in src, (
        "the record of the over-correction is gone; a correction that is deleted rather than kept "
        "is a correction somebody makes again")
    assert "`kw-common`, the repository this module" in src, (
        "the docstring no longer says that the repository shipping this file is itself public")


def test_the_accurate_public_references_are_KEPT() -> None:
    """The other half of the same edit: this repo IS public, and the statements that say so are
    load-bearing — they are why a real-literal denylist cannot live here."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "cannot live in a public repo" in src, (
        "the accurate rationale for a shapes-only denylist was removed along with the false claim")


# ------------------------------------- line splitting: git's definition, not Python's
# Found by an adversarial agent during this package's verification gate, and it is the most
# serious of the four: a REAL leak passed BOTH scans with exit 0 while staying recoverable from
# history, and it is reachable by accident rather than only by intent.


# The nine characters `str.splitlines()` breaks on and `git` does not.
_EXTRA_BREAKS = ["\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "", " ", " "]


@pytest.mark.parametrize("ch", _EXTRA_BREAKS, ids=[repr(c) for c in _EXTRA_BREAKS])
def test_a_line_is_split_only_on_newline_never_on_pythons_extra_breaks(ch: str) -> None:
    """⛔ THE ROOT CAUSE. `str.splitlines()` breaks on nine characters git treats as content."""
    assert guard._lines(f"a{ch}b") == [f"a{ch}b"], f"{ch!r} was treated as a line break"
    assert guard._lines("a\nb") == ["a", "b"], "and `\\n` must still split"


@pytest.mark.parametrize("ch", _EXTRA_BREAKS, ids=[repr(c) for c in _EXTRA_BREAKS])
def test_a_leak_after_one_of_those_characters_is_still_found_in_an_added_line(ch: str) -> None:
    """The fail-open itself, at the pure-function level.

    `splitlines` cut `+harmless<ch>LEAK` into `+harmless` and `LEAK` — and the remainder no longer
    began with `+`, so it fell past the added-line branch and was dropped. Not scanned, not
    reported, not counted as unscannable: the run simply said clean.
    """
    parsed = guard.parse_diff(
        f"diff --git a/cfg.txt b/cfg.txt\n--- /dev/null\n+++ b/cfg.txt\n"
        f"@@ -0,0 +1 @@\n+harmless{ch}{_LEAK_LINE}\n")
    assert len(parsed.added) == 1, f"{ch!r} split one added line into fragments: {parsed.added}"
    assert _hits("abc", parsed.added), f"the leak after {ch!r} was dropped"


@pytest.mark.timeout(300)
def test_a_CR_split_leak_is_caught_end_to_end_by_the_range_scan(tmp_path: Path) -> None:
    """The full bypass: added, then deleted, so only the range scan can see it.

    A carriage return is not exotic — any file with mixed line endings carries one, and `\\x0c`
    is an ordinary page break in real source.
    """
    repo = tmp_path / "crsplit"
    base = _seeded(repo)
    (repo / "cfg.txt").write_bytes(f"harmless\n\r{_LEAK_LINE}\n\r{_LEAK_ADDR_LINE}\n".encode())
    leak = _commit(repo, "add cfg")
    (repo / "cfg.txt").unlink()
    _commit(repo, "drop cfg")

    assert _run_guard(repo).returncode == 0, "precondition: the tree really is clean"
    # ...and the premise: the value really is still recoverable, or there is nothing to catch.
    assert _HOST in _git(repo, "show", f"{leak}:cfg.txt")

    proc = _run_guard(repo, "--range", f"{base}..HEAD")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"a CR-split leak passed the range scan: {out}"
    assert "private lan domain" in out and "private IPv4" in out, out


@pytest.mark.timeout(300)
def test_file_content_cannot_FORGE_a_diff_header_and_steal_the_self_exemption(
    tmp_path: Path,
) -> None:
    """⛔ THE ESCALATION, and the reason this is a production defect rather than a miss.

    `diff --git` is the one branch checked unconditionally — correctly, because in git's real
    grammar every content line carries a `+`/`-`/space prefix and so can never look like a header.
    Splitting on `\\r` broke that guarantee: content could start a line, forge a header, and
    re-attribute everything after it to any path — including this guard's own, the single file
    both scans skip. That is #241's self-exemption theft reached by another route.

    ⭐ VENDORED, for the same reason as the #241 test: the forged path has to be one the running
    guard would really exempt, and an installed guard exempts nothing in the repository it scans.
    """
    repo = tmp_path / "forge"
    base = _seeded(repo)
    vendored = _vendor_guard(repo)
    _git(repo, "add", _VENDORED_REL)
    _git(repo, "commit", "-q", "-m", "vendor the guard, so there is an exemption worth stealing")
    (repo / "planted.txt").write_bytes(
        f"X\rdiff --git a/{_VENDORED_REL} b/{_VENDORED_REL}\n"
        f"Y\r@@ -0,0 +1,1 @@\n{_LEAK_ADDR_LINE}\n".encode())
    _commit(repo, "plant")

    proc = _run_vendored(vendored, repo, "--range", f"{base}..HEAD")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"a forged header stole the self-exemption: {out}"
    assert "planted.txt" in out, (
        f"the leak was attributed to the FORGED path rather than the real file: {out}")


@pytest.mark.timeout(300)
def test_a_CR_split_leak_is_caught_through_the_binary_rediff_path_too(tmp_path: Path) -> None:
    """The same flaw defeated the #241/#242 fail-closed machinery, so it is pinned there as well.

    With the file marked binary, it reaches the `--text` re-diff. That output was NON-empty (so
    the empty-re-diff refusal did not fire) and carried no NUL (so the not-text refusal did not
    fire) — while the leak line had already been dropped by the split. The path then fell out of
    `still_unreadable` and was reported clean: every new guard satisfied, nothing scanned.
    """
    repo = tmp_path / "crbinary"
    base = _seeded(repo)
    (repo / ".gitattributes").write_text("*.dat -diff\n", encoding="utf-8")
    (repo / "cfg.dat").write_bytes(f"ok\n\r{_LEAK_LINE}\n".encode())
    _commit(repo, "add dat")

    proc = _run_guard(repo, "--range", f"{base}..HEAD")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"a CR-split leak passed through the re-diff path: {out}"


@pytest.mark.timeout(300)
def test_both_scans_agree_on_the_line_number_of_a_finding_in_a_CR_bearing_file(
    tmp_path: Path,
) -> None:
    """The reason `scan_text` shares the rule even though its split was not a bypass.

    Every fragment was still scanned there, so nothing leaked — but the two scans counted lines
    differently, so the same finding was reported at two different line numbers depending on which
    half of the guard found it. A guard whose halves disagree is telling one of them wrong.
    """
    repo = tmp_path / "linenos"
    base = _seeded(repo)
    (repo / "cfg.txt").write_bytes(f"one\ntwo\rstill two\n{_LEAK_LINE}\n".encode())
    _commit(repo, "add cfg")

    tree = _run_guard(repo)
    rng = _run_guard(repo, "--range", f"{base}..HEAD")
    assert tree.returncode == 1 and rng.returncode == 1

    # git's own count: the leak is on line 3, because `\r` is content.
    assert "cfg.txt:3:" in tree.stdout, tree.stdout
    assert "cfg.txt:3:" in rng.stdout, rng.stdout


# ------------------------------------------ log.showSignature: the identity call site
# Found by an adversarial agent during this package's verification gate. It was previously
# DISMISSED here on a measurement taken against UNSIGNED commits, where the setting is genuinely
# inert (see #35). Signing is what makes it bite, and signing is ordinary.


def _ssh_signing_available() -> bool:
    """Can this machine make an ssh-signed commit? Probed, not assumed."""
    return shutil.which("ssh-keygen") is not None


# ⚠️ ASSEMBLED AT RUNTIME, like every other deny case here — and this one caught me out. Written
# whole, it made the guard fail on its own test suite, at the very line that exists to prove the
# guard reports such a path. The file never needs to EXIST: git's "Unable to open allowed keys
# file <path>" warning quotes it back, and that warning text IS the payload.
_SIGNERS_WINPATH = "C:/Users/" + "jsmith" + "/.ssh/allowed_signers"


def _sign_with_ssh(repo: Path, allowed_signers: str = _SIGNERS_WINPATH) -> None:
    _git(repo, "config", "gpg.format", "ssh")
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(repo / "k")],
                   cwd=repo, capture_output=True, check=True, timeout=120)
    _git(repo, "config", "user.signingkey", str(repo / "k.pub"))
    _git(repo, "config", "commit.gpgsign", "true")
    _git(repo, "config", "log.showSignature", "true")
    _git(repo, "config", "gpg.ssh.allowedSignersFile", allowed_signers)


@pytest.mark.timeout(300)
@pytest.mark.skipif(not _ssh_signing_available(), reason="ssh-keygen not available")
def test_a_signed_commit_with_log_showSignature_does_not_fabricate_a_finding(
    tmp_path: Path,
) -> None:
    """⛔ A FALSE RED THE OPERATOR CANNOT CLEAR, which is the worst kind.

    With `log.showSignature=true` git prepends the signature-VERIFICATION block to stdout, ahead
    of the `--format` output — and the warning naming the allowed-signers file contains a Windows
    profile path. Glued onto the first field, that made a spotless commit report its "author name"
    as publishing `C:\\Users\\<name>`. The advice printed with it (rewrite history, fix
    `user.email`) cannot touch the real cause.

    The field COUNT stays 4, so `commit_identity`'s fail-closed guard cannot catch this: the
    response has the right shape and the wrong content.
    """
    repo = tmp_path / "signed"
    _seeded(repo)
    _sign_with_ssh(repo)   # need not exist; the WARNING is the payload
    (repo / "f.txt").write_text("hi\n", encoding="utf-8")
    _commit(repo, "signed")

    # The premise: git really is emitting the signature block into the identity output, or this
    # test passes vacuously against a machine that quietly did not sign.
    raw = _git(repo, "show", "-s", "--format=%an", "HEAD")
    assert "signature" in raw.lower() or "principal" in raw.lower(), (
        f"premise broken: the commit was not signed, so nothing is under test here: {raw!r}")

    proc = _run_guard(repo, "--range", "HEAD --not --remotes")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"a signed commit fabricated an identity finding: {out}"


@pytest.mark.timeout(300)
@pytest.mark.skipif(not _ssh_signing_available(), reason="ssh-keygen not available")
def test_a_REAL_leak_in_the_author_name_is_still_caught_when_signatures_are_shown(
    tmp_path: Path,
) -> None:
    """The other direction, and the reason this is not merely cosmetic: the fabricated text also
    BURIES a genuine leak among the signature output. Suppressing the block must not suppress the
    finding with it."""
    repo = tmp_path / "signedleak"
    _seeded(repo)
    _sign_with_ssh(repo)
    _git(repo, "commit", "-q", "--allow-empty",
         f"--author={_HOST} admin <admin@example.com>", "-m", "second")

    proc = _run_guard(repo, "--range", "HEAD --not --remotes")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"a leak in the author name went unreported: {out}"
    assert "author name" in out and "private lan domain" in out, out


@pytest.mark.timeout(300)
def test_an_unparseable_header_prints_a_remediation_that_matches_its_CAUSE(
    tmp_path: Path,
) -> None:
    """A fail-closed report that names the WRONG cause sends the operator after a problem that
    does not exist — the same defect class as any other false claim in this file.

    git C-quotes a path containing a quote, a backslash or a control byte REGARDLESS of
    `core.quotePath` (which governs non-ASCII only), so such a header genuinely cannot be
    reconstructed. The file is neither binary nor mis-encoded, so "add a binary suffix" and
    "commit it as UTF-8 text" are both wrong.

    ⚠️ Built with plumbing, because Windows cannot create such a name — but every CI runner is
    Linux, where a commit carrying one is perfectly legal.
    """
    repo = tmp_path / "quotedhdr"
    base = _seeded(repo)
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo,
                          input=b"hello world\n", capture_output=True, check=True,
                          timeout=120).stdout.decode().strip()
    tree = subprocess.run(["git", "mktree", "-z"], cwd=repo,
                          input=f'100644 blob {blob}\tquo"te.txt\0'.encode(),
                          capture_output=True, check=True, timeout=120).stdout.decode().strip()
    sha = _git(repo, "commit-tree", tree, "-p", base, "-m", "quoted path").strip()
    _git(repo, "update-ref", "refs/heads/main", sha)

    proc = _run_guard(repo, "--range", f"{base}..{sha}")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"an unattributable diff was cleared: {out}"
    assert "cannot resolve to one path" in out, out
    # The cause-specific advice must travel WITH the marker...
    assert "C-quotes" in out and "rename it" in out, (
        f"the marker did not carry advice matching its own cause: {out}")
    # ...and the generic decode advice must be scoped so it no longer claims to apply to it.
    assert "For an ordinary path above" in out, out


def test_the_required_status_check_names_are_recorded_in_the_workflow() -> None:
    """Renaming a required check leaves every PR green and permanently unmergeable, and the
    required contexts are invisible from inside the repo — so they are written down here.

    ⚠️ THE COUNT IS THE MECHANISM. Each name must appear at least TWICE: once as the job's own
    `name:`, and once in the comment block that records what to protect with. One occurrence means
    the job exists and nothing wrote it down; the comment is the half a rename silently breaks.

    The names are this repository's, not an inherited list — a copied suite asserting another
    repository's job names is a test that fails for a reason nobody here can act on.
    """
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for context in ("No internal info (leak guard)",
                    "Lint, type-check and test (py",
                    "The built wheel installs and imports"):
        assert ci.count(context) >= 2, (
            f"{context!r} is a status-check context but is not recorded as one")

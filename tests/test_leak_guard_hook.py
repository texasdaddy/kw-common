"""The pre-push hook: what it DOES, driven end to end against a real `git push`.

⭐ WHY THIS FILE EXISTS, AND WHAT IT DELIBERATELY CANNOT SAY (#16, #10).

`test_the_shipped_guard_names_no_private_repository` used to live in
`tests/test_leak_guard_extraction.py`. It asked ONE file — the guard's own source — whether it
named a private repository, and it carried the list of those names as a plain tuple. Both halves
were wrong:

  * every OTHER published file was unchecked. Four private service names reached the template, the
    setup document, the CHANGELOG and a module during 1.2.0 and were caught by a person reading,
    not by any check. `MANIFEST.in` puts `tests/`, `docs/` and the template in the sdist attached
    to every public Release, and the modules are in the wheel.
  * the LIST ITSELF SHIPPED. The inventory the check existed to keep out of published artifacts
    was published inside the check, and had been since 1.1.0. Widening that test's reach while
    the list stayed would have made the disclosure worse, not better; hashing or splitting it
    in-repo is the same disclosure with extra steps.

So the list moved OUT, into a guard that is committed to nothing, and the check moved from a
pytest assertion to `.githooks/pre-push`. That is a real trade and this file states it rather than
implying coverage it does not have:

  ⚠️ CI CANNOT VERIFY A LIST IT MUST NOT HAVE. Nothing here — and nothing in CI — can assert that
     the hook RAN on somebody's machine, or that the names are absent from this tree. What this
     file asserts is that the hook exists, is installed the way it says it is, and REFUSES the
     push when the guard it delegates to finds something. The other half is a local preflight and
     is honest about being one.

⭐ EVERY END-TO-END TEST BELOW DRIVES A REAL `git push` TO A REAL BARE REMOTE, and asserts what
HAPPENED — the remote ref moved, or it did not. A wiring assertion ("`--public-repo` appears in
the hook file") holds while the hook is unreachable, while the interpreter probe fails, and while
the exit code is discarded; #10 names that shape explicitly as the thing not to write.

The guard the hook calls is STUBBED here. It has to be: the real one is not in this repository,
which is the entire point of #16. The stub takes the same command line and records the arguments
it was handed, so the `--not --remotes` case — the brand-new branch that would otherwise scan zero
commits — is pinned by what the hook actually passed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".githooks" / "pre-push"

# The one string the stub guard treats as a leak. Deliberately not a real anything.
SENTINEL = "PLANTED-PRIVATE-NAME"

# A stand-in for the project-side guard. Same command line, same exit codes: 0 clean, 1 finding.
# It also APPENDS its argv to a log the tests read, which is how the range form is asserted.
#
# ⭐ IT HONOURS `--public-repo` RATHER THAN IGNORING IT, and that is not decoration. The real
# guard's private-name class is OFF unless that flag is passed — a sibling's own name is ordinary
# content in its own private repository — so a hook that dropped the flag would run a scan that
# structurally cannot see the thing it exists to catch, and report clean. A stub that treats the
# flag as noise turns every assertion about it into a wiring assertion; this one makes dropping it
# change the OUTCOME.
#
# ⛔ AND IT READS NO STDIN, which is a requirement the hook's own header states. A guard that read
# stdin would consume git's ref list and the commit scan would silently not run.
STUB_GUARD = '''\
import subprocess, sys
from pathlib import Path

argv = sys.argv[1:]
Path(sys.argv[0]).with_suffix(".log").open("a", encoding="utf-8").write(" ".join(argv) + "\\n")

def value(flag):
    return argv[argv.index(flag) + 1] if flag in argv else None

repo = value("--repo")
rng = value("--range")
sentinel = "SENTINEL_PLACEHOLDER"

if "--public-repo" not in argv:
    print("stub: the private-name class is OFF without --public-repo")
    sys.exit(0)

if rng is None:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, check=True)
    for rel in out.stdout.decode().split("\\0"):
        if not rel:
            continue
        p = Path(repo) / rel
        if p.is_file() and sentinel in p.read_text(encoding="utf-8", errors="replace"):
            print("STUB FINDING (tree): " + rel)
            sys.exit(1)
else:
    shas = subprocess.run(["git", "rev-list", *rng.split()], cwd=repo,
                          capture_output=True, check=True).stdout.decode().split()
    for sha in shas:
        diff = subprocess.run(["git", "show", sha], cwd=repo,
                              capture_output=True, check=True).stdout.decode(errors="replace")
        if sentinel in diff:
            print("STUB FINDING (range): " + sha)
            sys.exit(1)
print("stub clean")
sys.exit(0)
'''


# =================================================================================================
# helpers
# =================================================================================================
def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git with an identity injected PER CALL.

    ⚠️ NOT via a helper that some call sites skip, and not from a global config. A CI runner has no
    global git identity, so a `commit` that needs one silently no-ops there — and a test whose
    SETUP no-ops passes vacuously while looking like coverage.
    """
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, check=False)


def _in_a_git_repo() -> bool:
    res = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=REPO_ROOT, capture_output=True,
                         timeout=60, check=False)
    return res.returncode == 0


def _scratch(tmp_path: Path, *, configure_guard: bool = True) -> tuple[Path, Path, Path]:
    """A work repo with the REAL hook installed, a bare remote, and the stub guard.

    Returns (work, remote, guard-log). The hook is COPIED from this repository, not rewritten, so
    what the tests drive is the file that ships.
    """
    remote = tmp_path / "remote.git"
    remote.mkdir()
    assert _git(remote, "init", "--bare").returncode == 0

    work = tmp_path / "work"
    work.mkdir()
    assert _git(work, "init").returncode == 0

    guard = tmp_path / "stubguard.py"
    guard.write_text(STUB_GUARD.replace("SENTINEL_PLACEHOLDER", SENTINEL), encoding="utf-8")

    # ⭐ THE INSTALL FORM THE DOCUMENTS PRESCRIBE, not a convenient one. The README and the hook's
    # own header say `git config core.hooksPath .githooks` — a RELATIVE path, resolved inside the
    # working tree — so that is what these tests drive. An earlier version pointed `core.hooksPath`
    # at an absolute directory outside the repository, which is a different install with different
    # properties, and testing it would have proved nothing about the one an operator runs.
    hooks = work / ".githooks"
    hooks.mkdir()
    shutil.copyfile(HOOK, hooks / "pre-push")
    os.chmod(hooks / "pre-push", 0o755)  # noqa: S103 - a hook git will not run otherwise

    assert _git(work, "config", "core.hooksPath", ".githooks").returncode == 0
    if configure_guard:
        # ⚠️ The hook runs `python3`/`python`/`py -3`, not this interpreter. The stub is plain
        # stdlib so any of them runs it; what matters is that the PATH has one, which the probe
        # inside the hook establishes and `test_the_hook_refuses_when_no_interpreter...` pins.
        assert _git(work, "config", "kw.privateGuard", str(guard)).returncode == 0
    assert _git(work, "remote", "add", "origin", str(remote)).returncode == 0

    (work / "README.md").write_text("clean\n", encoding="utf-8")
    assert _git(work, "add", "README.md").returncode == 0
    assert _git(work, "commit", "-m", "first").returncode == 0
    return work, remote, guard.with_suffix(".log")


def _remote_head(remote: Path, ref: str = "refs/heads/main") -> str | None:
    res = _git(remote, "rev-parse", "--verify", "-q", ref)
    return res.stdout.strip() or None


needs_sh = pytest.mark.skipif(shutil.which("sh") is None,
                              reason="no POSIX sh on PATH, so git cannot run the hook")
needs_repo = pytest.mark.skipif(not _in_a_git_repo(),
                                reason="not a git repository (an sdist or an archive extraction)")


# =================================================================================================
# 1. the file itself
# =================================================================================================
def test_the_hook_is_shipped_at_the_path_the_install_command_names() -> None:
    assert HOOK.is_file(), (
        f"{HOOK} is missing. `core.hooksPath .githooks` finds nothing, and a hooks path that "
        f"resolves to no hook is indistinguishable from a passing check.")


@needs_repo
def test_the_hook_is_committed_EXECUTABLE() -> None:
    """⭐ THE BIT LIVES IN THE INDEX, NOT ON DISK.

    `core.filemode` is false on Windows, so `chmod +x` changes nothing that gets committed, and
    git-for-windows runs a non-executable hook anyway — so the workstation that authors the hook
    never notices. Linux silently SKIPS it. A hook that is silently skipped is the failure mode
    this whole file exists to remove, so the mode is asserted where it is actually stored.
    """
    res = subprocess.run(["git", "ls-files", "-s", ".githooks/pre-push"], cwd=REPO_ROOT,
                         capture_output=True, text=True, timeout=60, check=True)
    assert res.stdout.startswith("100755 "), (
        f"the hook is committed as {res.stdout.split()[0] if res.stdout else '(untracked)'}, not "
        f"100755. git SKIPS a non-executable hook on Linux without a word. Fix with "
        f"`git update-index --chmod=+x .githooks/pre-push`.")


def test_the_hook_is_pure_ASCII_with_LF_endings_and_a_sh_shebang() -> None:
    """A hook is read by whatever `sh` git found, on three operating systems.

    Non-ASCII in a script that some shell decodes as the locale codepage is the mojibake class
    that has killed `.ps1` files here; CRLF makes a `#!/bin/sh` shebang fail with "bad
    interpreter" on Linux, which reads as "the hook is broken" rather than "the line endings are".
    `.gitattributes` normalises the checkout, and this is what says so out loud.
    """
    raw = HOOK.read_bytes()
    assert raw.startswith(b"#!/bin/sh\n"), "the hook does not start with a POSIX sh shebang"
    assert b"\r\n" not in raw, (
        "the hook has CRLF line endings; `#!/bin/sh\\r` is not an interpreter")
    assert all(byte < 128 for byte in raw), "the hook is not pure ASCII"


@needs_sh
def test_the_hook_parses_as_posix_sh() -> None:
    res = subprocess.run(["sh", "-n", str(HOOK)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60, check=False)
    assert res.returncode == 0, f"the hook is not valid POSIX sh:\n{res.stdout}{res.stderr}"


def test_the_hook_probes_the_interpreter_by_RUNNING_it_not_by_command_v() -> None:
    """⛔ `command -v python3` FINDS THE MICROSOFT STORE STUB on Windows.

    The alias resolves, so the probe passes; the stub then refuses to run and every push aborts
    with "Python was not found" and no mention of the guard. `python3 -c ""` asks the question
    that matters — does this thing execute — and both hooks in the sibling repository this pattern
    came from had the wrong one.
    """
    text = HOOK.read_text(encoding="utf-8")
    assert 'python3 -c ""' in text, "the hook no longer probes the interpreter by running it"
    # ⚠️ COMMENTS STRIPPED FIRST. The hook EXPLAINS why `command -v` is wrong, so a scan of the
    # whole file finds the forbidden string in the sentence saying not to use it — the same
    # self-match that trips a leak guard reading its own denylist. Assert on the CODE.
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "command -v python" not in code, (
        "the hook probes with `command -v`, which resolves the Windows Store stub")


def test_the_hook_states_the_config_key_and_the_install_command() -> None:
    """The refusal has to be actionable. A hook that says only "refused" is a wall."""
    text = HOOK.read_text(encoding="utf-8")
    assert "core.hooksPath .githooks" in text, "the hook does not carry its own install command"
    assert text.count("kw.privateGuard") >= 2, (
        "the hook no longer names the config key in both the install line and the refusal")


# =================================================================================================
# 2. what it DOES — a real push, every time
# =================================================================================================
@needs_sh
@pytest.mark.timeout(300)
def test_a_CLEAN_push_is_NOT_refused(tmp_path: Path) -> None:
    """⭐ THE FALSE-RED DIRECTION, FIRST. A guard that reddens a correct tree gets switched off,
    and then it protects nothing at all. Every refusal asserted below is worthless without this."""
    work, remote, log = _scratch(tmp_path)
    res = _git(work, "push", "origin", "main")
    assert res.returncode == 0, f"a clean push was refused:\n{res.stdout}\n{res.stderr}"
    assert _remote_head(remote) is not None, "the push reported success but the remote has no main"
    assert log.exists() and log.read_text(encoding="utf-8").strip(), (
        "the guard was never invoked, so this push proves nothing about the hook")


@needs_sh
@pytest.mark.timeout(300)
def test_a_COMMIT_carrying_a_finding_is_REFUSED_and_the_remote_does_not_move(
        tmp_path: Path) -> None:
    """The whole point: the refusal is measured by the REMOTE, not by the hook's own chatter."""
    work, remote, _log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0
    before = _remote_head(remote)

    (work / "notes.md").write_text(f"deploy to {SENTINEL}\n", encoding="utf-8")
    assert _git(work, "add", "notes.md").returncode == 0
    assert _git(work, "commit", "-m", "add notes").returncode == 0

    res = _git(work, "push", "origin", "main")
    assert res.returncode != 0, f"the push was NOT refused:\n{res.stdout}\n{res.stderr}"
    assert "REFUSED" in res.stderr, f"the refusal did not say why:\n{res.stderr}"
    assert _remote_head(remote) == before, "the leaking commit reached the remote anyway"


@needs_sh
@pytest.mark.timeout(300)
def test_a_WORKTREE_finding_is_REFUSED_even_though_no_commit_carries_it(tmp_path: Path) -> None:
    """⭐ THE TREE SCAN IS NOT REDUNDANT WITH THE RANGE SCAN, and this is the difference.

    The wheel and the sdist are built from the WORKING TREE. A value sitting in a tracked file
    that no commit has published yet is still what `python -m build` would package, so the tree
    scan is the half that answers #16 and the range scan is the half that answers "does this push
    publish it". Losing either one loses a whole question.
    """
    work, remote, _log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0
    before = _remote_head(remote)

    # Committed clean, then edited. Nothing in any commit carries the sentinel.
    (work / "extra.md").write_text("clean\n", encoding="utf-8")
    assert _git(work, "add", "extra.md").returncode == 0
    assert _git(work, "commit", "-m", "extra").returncode == 0
    (work / "extra.md").write_text(f"{SENTINEL}\n", encoding="utf-8")

    res = _git(work, "push", "origin", "main")
    assert res.returncode != 0, f"a worktree finding did not refuse the push:\n{res.stderr}"
    assert "TREE" in res.stderr, f"the refusal did not name the tree scan:\n{res.stderr}"
    assert _remote_head(remote) == before


@needs_sh
@pytest.mark.timeout(300)
def test_an_UNCONFIGURED_guard_REFUSES_rather_than_passing_quietly(tmp_path: Path) -> None:
    """⛔ FAIL CLOSED. `kw.privateGuard` unset must not mean "skip the private-name check".

    A guard that is silently not running is worse than one that was never installed, because its
    absence looks exactly like a pass — #10's own words. The refusal names the key, because a
    refusal nobody can act on gets worked around with `--no-verify`.
    """
    work, remote, _log = _scratch(tmp_path, configure_guard=False)
    res = _git(work, "push", "origin", "main")
    assert res.returncode != 0, f"an unconfigured guard let the push through:\n{res.stderr}"
    assert "kw.privateGuard" in res.stderr, f"the refusal did not name the key:\n{res.stderr}"
    assert _remote_head(remote) is None, "the push landed despite the refusal"


@needs_sh
@pytest.mark.timeout(300)
def test_a_guard_path_that_is_not_a_file_REFUSES(tmp_path: Path) -> None:
    """Configured-but-wrong is the same hazard as unconfigured, one typo away."""
    work, remote, _log = _scratch(tmp_path)
    assert _git(work, "config", "kw.privateGuard",
                str(tmp_path / "no-such-guard.py")).returncode == 0
    res = _git(work, "push", "origin", "main")
    assert res.returncode != 0, f"a missing guard let the push through:\n{res.stderr}"
    assert "not a file" in res.stderr, res.stderr
    assert _remote_head(remote) is None


@needs_sh
@pytest.mark.timeout(300)
def test_a_BRAND_NEW_branch_is_scanned_with_not_remotes_rather_than_against_nothing(
        tmp_path: Path) -> None:
    """⭐ THE CASE THAT WOULD OTHERWISE SCAN ZERO COMMITS AND REPORT CLEAN.

    A branch that has never been pushed has no remote counterpart, so git hands the hook an
    all-zero `remote_sha`. Diffing against that scans nothing — and a first push is the most
    likely moment for a leak, not the least. `<sha> --not --remotes` is what makes it scan the
    new history instead.

    Asserted TWICE, from both sides: the form the hook passed (from the stub's argv log) AND the
    outcome (a leak on that new branch is refused). The argv alone would pass if the guard
    ignored the argument; the outcome alone would pass if some other scan caught it.
    """
    work, remote, log = _scratch(tmp_path)
    assert _git(work, "checkout", "-b", "feature/new").returncode == 0
    (work / "clean.md").write_text("clean\n", encoding="utf-8")
    assert _git(work, "add", "clean.md").returncode == 0
    assert _git(work, "commit", "-m", "clean work").returncode == 0

    res = _git(work, "push", "origin", "feature/new")
    assert res.returncode == 0, f"a clean first push was refused:\n{res.stdout}\n{res.stderr}"
    calls = log.read_text(encoding="utf-8")
    assert "--not --remotes" in calls, (
        f"the hook did not use `--not --remotes` for a branch with no upstream, so the range "
        f"scan saw nothing:\n{calls}")

    # And the same branch, now leaking, must be refused.
    (work / "leak.md").write_text(f"{SENTINEL}\n", encoding="utf-8")
    assert _git(work, "add", "leak.md").returncode == 0
    assert _git(work, "commit", "-m", "leak").returncode == 0
    before = _remote_head(remote, "refs/heads/feature/new")
    res = _git(work, "push", "origin", "feature/new")
    assert res.returncode != 0, f"a leak on a new branch was not refused:\n{res.stderr}"
    assert _remote_head(remote, "refs/heads/feature/new") == before


@needs_sh
@pytest.mark.timeout(300)
def test_deleting_a_remote_branch_is_not_scanned_as_if_it_added_something(tmp_path: Path) -> None:
    """A deletion publishes nothing, and git sends an all-zero LOCAL sha for it.

    Without the zero check the hook would build the range `<remote>..0000000` and the guard would
    exit non-zero on a range git cannot resolve — refusing a push that adds nothing at all. That
    is the false-red direction again, in the one case that looks like an edge and is not.
    """
    work, remote, _log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0
    assert _git(work, "checkout", "-b", "doomed").returncode == 0
    assert _git(work, "push", "origin", "doomed").returncode == 0
    assert _remote_head(remote, "refs/heads/doomed") is not None

    res = _git(work, "push", "origin", "--delete", "doomed")
    assert res.returncode == 0, f"deleting a branch was refused:\n{res.stdout}\n{res.stderr}"
    assert _remote_head(remote, "refs/heads/doomed") is None


@needs_sh
@pytest.mark.timeout(300)
def test_a_DELETION_is_allowed_even_when_the_worktree_carries_a_finding(tmp_path: Path) -> None:
    """⭐ THE ONE FALSE-RED THAT BLOCKS THE REMEDY ITSELF.

    You find a leak, you want the remote branch gone. The value is still sitting in your working
    tree, because you have not finished cleaning up — and the tree scan, which ran unconditionally,
    refused the deletion. A push that only deletes refs PUBLISHES NOTHING, so there is nothing for
    the tree scan to be protecting, and refusing it is the shape that gets a hook switched off.

    ⚠️ Asserted by the REMOTE, and by the guard log: the deletion must go through AND the tree scan
    must not have run at all, or a later change could satisfy this test by refusing more quietly.
    """
    work, remote, log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0
    assert _git(work, "checkout", "-b", "doomed").returncode == 0
    assert _git(work, "push", "origin", "doomed").returncode == 0
    assert _git(work, "checkout", "main").returncode == 0
    log.write_text("", encoding="utf-8")

    (work / "README.md").write_text(f"{SENTINEL}\n", encoding="utf-8")   # tracked, and leaking

    res = _git(work, "push", "origin", "--delete", "doomed")
    assert res.returncode == 0, (
        f"a branch DELETE was refused because the worktree carries a finding — which is the "
        f"cleanup gesture itself:\n{res.stdout}\n{res.stderr}")
    assert _remote_head(remote, "refs/heads/doomed") is None
    assert log.read_text(encoding="utf-8").strip() == "", (
        "the guard ran on a push that publishes nothing")


@needs_sh
@pytest.mark.timeout(300)
def test_a_leak_that_a_LATER_COMMIT_REMOVED_is_still_refused(tmp_path: Path) -> None:
    """⭐⭐ THE CASE THE RANGE SCAN EXISTS FOR, AND THE ONLY ONE THAT PROVES IT RUNS.

    Every other refusal test here leaves the value in the worktree, so the TREE scan alone produces
    the refusal they measure — measured: deleting the hook's entire commit-scanning loop left them
    all green. This one commits the value and then removes it in a second commit, so the worktree
    is clean and only the range scan can see it.

    That is not a contrived case: "I committed a secret, I deleted it in the next commit, it's
    fine now" is the single most common wrong belief about git. Pushing publishes HISTORY, and the
    value stays readable at the commit that added it forever.
    """
    work, remote, _log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0
    before = _remote_head(remote)

    (work / "notes.md").write_text(f"deploy to {SENTINEL}\n", encoding="utf-8")
    assert _git(work, "add", "notes.md").returncode == 0
    assert _git(work, "commit", "-m", "oops").returncode == 0
    (work / "notes.md").write_text("deploy notes\n", encoding="utf-8")
    assert _git(work, "add", "notes.md").returncode == 0
    assert _git(work, "commit", "-m", "removed it again").returncode == 0
    assert SENTINEL not in (work / "notes.md").read_text(encoding="utf-8")

    res = _git(work, "push", "origin", "main")
    assert res.returncode != 0, (
        f"a value committed and then removed was not refused — the worktree is clean, so the tree "
        f"scan cannot see it and only the commit scan can:\n{res.stdout}\n{res.stderr}")
    assert _remote_head(remote) == before


@needs_sh
@pytest.mark.timeout(300)
def test_EVERY_ref_in_a_multi_ref_push_is_scanned_not_just_the_first(tmp_path: Path) -> None:
    """⭐ THE LOOP, EXERCISED PAST ONE ITERATION.

    `git push origin a b` hands the hook two lines on stdin. A loop that stops after the first —
    because something consumed the rest of stdin, because a `break` crept in, because the guard
    itself read stdin — would scan `a`, report clean, and publish `b` unexamined. Nothing else in
    this file pushes more than one ref, so nothing else could tell.

    The leaking branch is deliberately the SECOND one, and the clean one is checked out, so the
    worktree is clean and the tree scan cannot be what produces the refusal.
    """
    work, remote, log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0

    assert _git(work, "checkout", "-b", "aaa").returncode == 0
    (work / "a.md").write_text("clean\n", encoding="utf-8")
    assert _git(work, "add", "a.md").returncode == 0
    assert _git(work, "commit", "-m", "a").returncode == 0

    assert _git(work, "checkout", "-b", "zzz", "main").returncode == 0
    (work / "z.md").write_text(f"{SENTINEL}\n", encoding="utf-8")
    assert _git(work, "add", "z.md").returncode == 0
    assert _git(work, "commit", "-m", "z").returncode == 0
    # Back to the clean branch: the worktree no longer holds the value.
    assert _git(work, "checkout", "aaa").returncode == 0
    assert not (work / "z.md").exists()
    log.write_text("", encoding="utf-8")

    res = _git(work, "push", "origin", "aaa", "zzz")
    assert res.returncode != 0, (
        f"a two-ref push was accepted although the SECOND ref leaks:\n{res.stdout}\n{res.stderr}")
    assert _remote_head(remote, "refs/heads/zzz") is None, "the leaking ref reached the remote"
    calls = [ln for ln in log.read_text(encoding="utf-8").splitlines() if "--range" in ln]
    assert len(calls) >= 2, (
        f"the commit scan ran {len(calls)} time(s) for a two-ref push, so at least one ref went "
        f"unexamined:\n{calls}")


@needs_sh
@pytest.mark.timeout(300)
def test_the_range_excludes_only_THIS_remotes_refs_not_every_remotes(tmp_path: Path) -> None:
    """⛔ `--not --remotes` SPANS EVERY REMOTE, AND THAT IS A BYPASS.

    A branch pushed to a private remote and then fetched back leaves `refs/remotes/<other>/<branch>`
    reaching those commits. A brand-new branch on THIS remote is then scanned as
    `<sha> --not --remotes`, every commit is excluded by the other remote's ref, the range is
    EMPTY, and the guard reports clean on history it never read. Measured: a leaking branch landed
    on the public remote, exit 0.

    `--remotes=<remote>` scopes the exclusion to the remote actually being pushed to. git hands the
    hook that remote's name as its first argument, which is where it comes from.
    """
    work, remote, log = _scratch(tmp_path)
    other = tmp_path / "other.git"
    other.mkdir()
    assert _git(other, "init", "--bare").returncode == 0
    assert _git(work, "remote", "add", "private", str(other)).returncode == 0

    assert _git(work, "checkout", "-b", "secret").returncode == 0
    (work / "s.md").write_text(f"{SENTINEL}\n", encoding="utf-8")
    assert _git(work, "add", "s.md").returncode == 0
    assert _git(work, "commit", "-m", "secret").returncode == 0
    (work / "s.md").write_text("tidied\n", encoding="utf-8")
    assert _git(work, "add", "s.md").returncode == 0
    assert _git(work, "commit", "-m", "tidied").returncode == 0

    # It reaches the PRIVATE remote (whose hook, in this fixture, is the same one — the push is
    # forced through by pointing the guard away for that one call).
    assert _git(work, "config", "--unset", "kw.privateGuard").returncode == 0
    assert _git(work, "config", "core.hooksPath", "no-such-hooks").returncode == 0
    assert _git(work, "push", "private", "secret").returncode == 0
    assert _git(work, "fetch", "private").returncode == 0
    assert _git(work, "config", "core.hooksPath", ".githooks").returncode == 0
    guard = tmp_path / "stubguard.py"
    assert _git(work, "config", "kw.privateGuard", str(guard)).returncode == 0
    log.write_text("", encoding="utf-8")

    res = _git(work, "push", "origin", "secret")
    assert res.returncode != 0, (
        f"a branch already fetched from another remote was published to this one unscanned — "
        f"`--not --remotes` excluded every commit in it:\n{res.stdout}\n{res.stderr}")
    assert _remote_head(remote, "refs/heads/secret") is None
    calls = log.read_text(encoding="utf-8")
    assert "--remotes=origin" in calls, (
        f"the exclusion is not scoped to the remote being pushed to:\n{calls}")


@needs_sh
@pytest.mark.timeout(300)
def test_a_guard_that_reads_STDIN_cannot_swallow_the_ref_list(tmp_path: Path) -> None:
    """⛔ THE HOOK'S OWN STDIN IS THE REF LIST, and a child that reads it eats the rest.

    Measured before the fix: a guard beginning `sys.stdin.read()` left the hook with ONE
    invocation instead of three, the whole commit scan silently did not run, and a two-ref push
    landed with the leaking ref unexamined. The hook now reads the list in full before running
    anything AND redirects each guard's stdin to /dev/null, so neither half depends on the
    goodwill of a program that lives outside this repository.

    ⚠️ TWO REFS, AND THE LEAKING ONE IS SECOND. With a single ref this passes on the buffering
    alone, because by the time any guard runs there is nothing left on the hook's stdin to eat —
    so a one-ref version of this test is satisfied by half the defence and says nothing about the
    other half. INSIDE the loop the guard inherits the HEREDOC as its stdin, so a greedy guard
    there consumes the remaining ref lines and every later ref goes unscanned. Measured: with the
    `< /dev/null` removed from the range call, this exact scenario published the leaking ref.
    """
    work, remote, log = _scratch(tmp_path)
    guard = tmp_path / "stubguard.py"
    guard.write_text("import sys\nsys.stdin.read()\n" + guard.read_text(encoding="utf-8"),
                     encoding="utf-8")
    assert _git(work, "push", "origin", "main").returncode == 0

    assert _git(work, "checkout", "-b", "aaa").returncode == 0
    (work / "a.md").write_text("clean\n", encoding="utf-8")
    assert _git(work, "add", "a.md").returncode == 0
    assert _git(work, "commit", "-m", "a").returncode == 0

    assert _git(work, "checkout", "-b", "greedy", "main").returncode == 0
    (work / "g.md").write_text(f"{SENTINEL}\n", encoding="utf-8")
    assert _git(work, "add", "g.md").returncode == 0
    assert _git(work, "commit", "-m", "leak").returncode == 0
    assert _git(work, "checkout", "aaa").returncode == 0
    assert not (work / "g.md").exists(), "the worktree still holds it, so the TREE scan would do"
    log.write_text("", encoding="utf-8")

    res = _git(work, "push", "origin", "aaa", "greedy")
    assert res.returncode != 0, (
        f"a guard that read stdin swallowed the ref list and the commit scan did not run:\n"
        f"{res.stdout}\n{res.stderr}")
    assert _remote_head(remote, "refs/heads/greedy") is None
    ranges = [ln for ln in log.read_text(encoding="utf-8").splitlines() if "--range" in ln]
    assert len(ranges) >= 2, (
        f"the commit scan ran {len(ranges)} time(s) for a two-ref push, so a greedy guard ate the "
        f"rest of the list:\n{ranges}")


@needs_sh
@pytest.mark.timeout(300)
def test_an_UP_TO_DATE_push_is_not_refused_for_what_is_in_the_worktree(tmp_path: Path) -> None:
    """⭐ THE OTHER PUSH THAT PUBLISHES NOTHING, and it is the one nobody thinks of.

    git runs `pre-push` for a push it then reports as "Everything up-to-date" — with the remote
    name in `$1` and an EMPTY ref list on stdin. Measured. Under a tree scan that ran whenever the
    ref list was empty, that push was refused for a value sitting in the local worktree: the
    operator is mid-cleanup, types `git push` out of habit, and is told their tree leaks by a
    push that would have moved nothing. Same false-red class as refusing a deletion, and the same
    remedy — an empty list WITH a remote means there is nothing to publish.

    ⚠️ Asserted by the guard log as well as the exit code. "It was not refused" is also true of a
    hook that stopped running at all, and the previous test in this file is what would catch that.
    """
    work, remote, log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0
    head = _remote_head(remote)
    log.write_text("", encoding="utf-8")

    (work / "README.md").write_text(f"{SENTINEL}\n", encoding="utf-8")   # tracked, and leaking
    res = _git(work, "push", "origin", "main")
    assert res.returncode == 0, (
        f"a push with nothing to publish was refused for the WORKTREE:\n{res.stdout}\n{res.stderr}")
    assert "up-to-date" in (res.stdout + res.stderr).lower(), (
        f"this push was not the no-op the test needs it to be:\n{res.stdout}\n{res.stderr}")
    assert _remote_head(remote) == head
    assert log.read_text(encoding="utf-8").strip() == "", (
        "the guard ran on a push that publishes nothing")


@needs_sh
@pytest.mark.timeout(300)
def test_a_ref_list_the_hook_cannot_PARSE_still_scans_the_tree(tmp_path: Path) -> None:
    """⛔ FAIL CLOSED ON INPUT IT CANNOT READ, which is the half the two arms above do not cover.

    "Empty ref list with a remote" means nothing is being published. A ref list that is NOT empty
    but yields no fields — whitespace, or a shape git does not send today — means the opposite:
    something arrived and this could not read it. Skipping the scan there would turn a misread
    into a silent all-clear, and it is the difference between the two conditions rather than
    either one alone, so it needs its own case.

    Unreachable through `git push`, which always sends four fields; driven directly.
    """
    work, _remote, log = _scratch(tmp_path)
    hook = work / ".githooks" / "pre-push"
    (work / "README.md").write_text(f"{SENTINEL}\n", encoding="utf-8")

    res = subprocess.run(["sh", str(hook), "origin", str(work)], cwd=work, input="   \n",
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=120, check=False)
    assert res.returncode != 0, (
        f"a ref list this cannot parse was treated as 'nothing to publish':\n"
        f"{res.stdout}{res.stderr}")
    assert log.read_text(encoding="utf-8").strip(), "no scan ran at all"


@needs_sh
@pytest.mark.timeout(300)
def test_the_hook_run_BY_HAND_with_no_refs_still_scans_the_tree(tmp_path: Path) -> None:
    """The arm that exists for "how anybody tests one", asserted rather than assumed.

    Invoked outside a push there is no ref list at all, and the hook has no way to know whether
    anything would be published. The answer to that is to scan — the alternative is a hook that
    silently does nothing the one time somebody runs it to see what it does. `$1` is absent too,
    which is why `${1:-}` is written that way.
    """
    work, _remote, log = _scratch(tmp_path)
    hook = work / ".githooks" / "pre-push"

    (work / "README.md").write_text(f"{SENTINEL}\n", encoding="utf-8")
    res = subprocess.run(["sh", str(hook)], cwd=work, input="", capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120, check=False)
    assert res.returncode != 0, (
        f"a hand-run over a leaking tree exited 0:\n{res.stdout}{res.stderr}")
    assert "REFUSED" in res.stderr, res.stderr
    assert log.read_text(encoding="utf-8").strip(), "no scan ran at all"

    (work / "README.md").write_text("clean\n", encoding="utf-8")
    res = subprocess.run(["sh", str(hook)], cwd=work, input="", capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120, check=False)
    assert res.returncode == 0, (
        f"a hand-run over a clean tree was refused:\n{res.stdout}{res.stderr}")


@needs_sh
@pytest.mark.timeout(300)
def test_the_hook_passes_public_repo_on_BOTH_scans(tmp_path: Path) -> None:
    """The flag is what turns the private-name class on. Without it on BOTH invocations the hook
    runs a scan that cannot see the thing it exists to catch, and reports clean."""
    work, _remote, log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0
    calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(calls) >= 2, f"expected a tree scan and a range scan, got:\n{calls}"
    assert all("--public-repo" in line for line in calls), (
        f"a scan ran without --public-repo, so the private-name class was off:\n{calls}")
    assert any("--range" in line for line in calls), f"no range scan ran:\n{calls}"
    assert any("--range" not in line for line in calls), f"no tree scan ran:\n{calls}"


# =================================================================================================
# 3. the shipped set is a SUBSET of what the guard can see
# =================================================================================================
@needs_repo
def test_everything_the_package_ships_is_a_TRACKED_file() -> None:
    """⭐ #16(a) ASKED IN-REPO, WITHOUT THE LIST.

    The guard the hook runs scans `git ls-files`. That covers every published file only while
    every published file is TRACKED — a file setuptools packages anyway, from the FILESYSTEM
    rather than from the index, would be inside an artifact and invisible to the scan.

    ⭐⭐ `recursive-include` IS THE HALF THAT MATTERS, and the first version of this test skipped
    it. `include` names one path each and there are a handful; `recursive-include` GLOBS A
    DIRECTORY,
    and `recursive-include tests *.py` is the exact directive that put seventeen private service
    names into every published sdist while one test looked at one module. A scratch file dropped in
    `tests/` or `docs/` is picked up by the glob, ships, and is not in `git ls-files` — so the
    guard never reads it. Checking only the `include` lines would have left the measured hole open
    and looked like coverage.

    ⚠️ It does NOT build the artifacts. Building needs the network for build dependencies and takes
    seconds; CI builds both and runs the shipped suite against them. The property this pins is the
    one that would make the whole hook mechanism blind, and it is cheap.
    """
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True,
                            timeout=120, check=True)
    tracked = {p for p in listed.stdout.decode("utf-8").split("\0") if p}
    assert tracked, "git ls-files returned nothing — this test would pass vacuously"

    def is_build_output(path: Path) -> bool:
        # `.egg-info/` is metadata setuptools regenerates on every build and `.gitignore` excludes;
        # `__pycache__` and `.pyc` likewise. None is in an artifact, so demanding they be tracked
        # would redden a correct tree — which is how a check gets switched off.
        return (path.suffix == ".pyc"
                or any(p == "__pycache__" or p.endswith(".egg-info") for p in path.parts))

    untracked: list[str] = []
    checked = 0
    # The package directory: `packages.find` discovers the package here and setuptools takes its
    # `.py` files plus whatever `package-data` names, all read off the filesystem.
    for path in sorted((REPO_ROOT / "src").rglob("*")):
        if not path.is_file() or is_build_output(path):
            continue
        checked += 1
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel not in tracked:
            untracked.append(rel)

    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    named = 0
    globbed = 0
    for line in manifest.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "include":
            for rel in parts[1:]:
                named += 1
                checked += 1
                if rel not in tracked:
                    untracked.append(rel)
        elif parts[0] == "recursive-include" and len(parts) >= 3:
            globbed += 1
            base = REPO_ROOT / parts[1]
            for pattern in parts[2:]:
                for path in sorted(base.rglob(pattern)):
                    if not path.is_file() or is_build_output(path):
                        continue
                    checked += 1
                    rel = path.relative_to(REPO_ROOT).as_posix()
                    if rel not in tracked:
                        untracked.append(rel)
    assert named >= 4, f"MANIFEST.in no longer names files with `include` ({named} found)"
    assert globbed >= 3, (
        f"MANIFEST.in has {globbed} `recursive-include` directive(s) — this test is no longer "
        f"looking at the mechanism that actually shipped the names")
    assert checked >= 20, f"only {checked} shipped path(s) examined; this proves nothing"
    assert sorted(set(untracked)) == [], (
        f"these files ship but are not tracked, so the guard's tree scan never reads them: "
        f"{sorted(set(untracked))}")


# =================================================================================================
# 4. the two workflows, which keep diverging
# =================================================================================================
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

# The packaging assertions that must exist on BOTH paths, as (a fragment of the step's `name:`,
# what it is for). Matched on the name because the two copies differ in wording elsewhere.
_PACKAGING_STEPS = (
    ("sdist carries its fixtures", "what the tarball must and must not contain"),
    ("sdist's own test suite passes", "the check the file list cannot be"),
    ("py.typed", "PEP 561's marker, in the artifact rather than in the tree"),
)


def test_the_PUBLISHING_workflow_carries_every_packaging_assertion_the_PR_workflow_does() -> None:
    """⭐⭐ THE MISS THAT KEEPS HAPPENING, MADE INTO A TEST INSTEAD OF A COMMENT.

    `release.yml` is self-contained on purpose — it does not call `ci.yml`, because a `v*` tag can
    point at a commit that never went through a pull request, and that is precisely the case the
    duplication exists for. The cost of that design is that every packaging check has to be added
    TWICE, and twice now it was added once: `.githooks/pre-push` to one sdist list, then the
    `py.typed` wheel assertion to one workflow. `release.yml` already carries a comment warning
    about exactly this, and the comment did not stop the next occurrence — prose does not fail.

    ⚠️ NAMES, NOT BODIES. Comparing the step scripts verbatim would fail on wording that is
    legitimately different (the release job asserts the tag matches the version; the PR job does
    not), and a test that fails for the wrong reason gets deleted. What must not diverge is WHICH
    questions the publishing path asks.
    """
    ci = CI_WORKFLOW.read_text(encoding="utf-8")
    rel = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    def step_names(text: str) -> list[str]:
        return [ln.split("- name:", 1)[1].strip()
                for ln in text.splitlines() if ln.strip().startswith("- name:")]

    ci_names, rel_names = step_names(ci), step_names(rel)
    assert len(ci_names) >= 5 and len(rel_names) >= 5, (
        f"step names are no longer being found ({len(ci_names)}, {len(rel_names)}) — this test "
        f"would pass over anything")

    missing = []
    for fragment, why in _PACKAGING_STEPS:
        in_ci = [n for n in ci_names if fragment.lower() in n.lower()]
        in_rel = [n for n in rel_names if fragment.lower() in n.lower()]
        assert in_ci, f"ci.yml no longer has a step about {fragment!r} ({why})"
        if not in_rel:
            missing.append(f"{fragment!r} ({why})")
    assert missing == [], (
        f"release.yml — the workflow that PUBLISHES — is missing the packaging assertion(s) "
        f"{missing}. A tag cut on a commit that never went through a PR would publish an artifact "
        f"nobody asked those questions of.")

    # The sdist required-file lists are the other half, and they diverged the same way.
    #
    # ⚠️ `"; do"`, NOT `"do"`. Splitting on the bare word truncated the list at
    # `/docs/alerting-setup.md` — the `do` inside `docs` — so the comparison silently ran on four
    # of six entries and the vacuity guard below is what caught it.
    def required_paths(text: str) -> set[str]:
        body = text.split("for required in", 1)[1].split("; do", 1)[0]
        return set(re.findall(r"'(/[^']+)'", body))

    ci_required, rel_required = required_paths(ci), required_paths(rel)
    assert len(ci_required) >= 5, f"the sdist required-file list is no longer parsed: {ci_required}"
    assert ci_required == rel_required, (
        f"the two sdist checks require different files. Only in ci.yml: "
        f"{sorted(ci_required - rel_required)}; only in release.yml: "
        f"{sorted(rel_required - ci_required)}")


def test_the_py_typed_marker_exists_and_is_declared_as_package_data() -> None:
    """⛔ WITHOUT IT A CONSUMER'S MYPY IGNORES EVERY ANNOTATION IN THIS PACKAGE, SILENTLY.

    Nothing in the suite mentioned `py.typed` at all: deleting it left every test green, and the
    only thing that would have noticed was a CI step which, at the time, existed on one of the two
    workflows. `mypy` here reads the SOURCE, so this repository's own type-checking proves nothing
    about what an installed copy exposes.

    ⚠️ WHAT THIS CAN AND CANNOT SAY. It asks that the file exists and that the packaging metadata
    declares it — the two things a source tree can answer. Whether the marker actually reaches the
    built wheel is a question about an artifact, and both workflows now ask it of the zip.
    """
    marker = REPO_ROOT / "src" / "kw_common" / "py.typed"
    assert marker.is_file(), (
        "src/kw_common/py.typed is missing. PEP 561 makes it the switch that tells a consumer's "
        "type checker the annotations in this package may be used; without it they are ignored.")
    assert marker.read_bytes() == b"", "PEP 561's marker is an EMPTY file"

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = ("[tool.setuptools.package-data]" in pyproject
                and 'kw_common = ["py.typed"]' in pyproject)
    assert declared, "pyproject.toml no longer declares py.typed as package data"
    assert "include src/kw_common/py.typed" in (REPO_ROOT / "MANIFEST.in").read_text(
        encoding="utf-8"), "MANIFEST.in no longer carries py.typed into the sdist"

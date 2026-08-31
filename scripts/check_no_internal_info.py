#!/usr/bin/env python3
r"""Fail the build if any tracked file contains internal identifying information.

⚠️ RAW STRING, deliberately. This docstring quotes regexes and Windows paths, so `\U` (in
`\Users`), `\b`, `\w` and friends appear in it as text. Without the `r` prefix Python reads them
as escapes: `\Users` is a truncated `\UXXXXXXXX` and is a hard SyntaxError that stops the guard
from importing at all, and `\b` silently becomes a literal backspace character in the text.

WHY THIS EXISTS
    An internal IP, an appdata pool path or a personal address committed anywhere is PERMANENT:
    removing it in a later commit does not remove it, because it stays in the history and in
    whatever already mirrored the repo. Cleaning it up afterwards costs a full history rewrite
    and a force-push across every clone. This guard is what stops it coming back.

    ⛔ WHICH REPOSITORIES ARE PUBLIC — the previous answer here was WRONG, and wrong in the
    direction that gets a guard switched off. It generalized "public" across the whole fleet,
    including the images built from it. The truth: every CODE repo is PRIVATE — `tape`,
    `keystone`, `cef-tracker`, `reauth-bot`, `gambit`, `the-desk`. Only `unraid-templates` is
    PUBLIC (the GHCR *packages* are public too).

    (The false sentence is deliberately NOT reproduced here. A correction that quotes the claim
    it is refuting leaves the claim greppable in the file, so the next reader — and any test
    asserting it is gone — finds it either way.)

    ⚠️ Why a docstring error is worth this much space: this file is the fleet's shared guard, and
    a blanket claim of publicness is precisely the premise someone reasons FROM when they want to
    relax it — "this one is private, so the check does not really apply here". It does apply. A
    private repo is not a secret store: it is readable by every collaborator and every token with
    `repo` scope, it gets cloned onto laptops and CI runners, and making it public later — or
    forking it, or extracting it — publishes the whole history at once. The guard's value does
    not come from the repository's visibility, and stating a reason that only holds for public
    repositories hands the next reader an argument for ignoring it everywhere else.

⭐ THIS GUARD MATCHES SHAPES, NOT VALUES — AND THAT IS DELIBERATE.
    Every pattern below describes a CLASS of value (an RFC1918 address, a `/mnt/<pool>` path, a
    `.lan` host, a freemail address, a UUID). Not one of them names a real host, a real domain,
    a real address or a real person.

    The reason is that a denylist of REAL LITERALS cannot live in a public repo. Written out
    here, the codenames and the infra domain would be readable on GitHub code search forever —
    the guard would BE the leak, republishing in one greppable place exactly what it exists to
    remove. Worse, this is the one file the scan skips (SELF_PATH, below), so it could never
    catch itself doing it. Encoding the list (base64) was tried and is not a fix: it defeats a
    grep, but the values are still shipped, still decodable in one line, and still there.

    ⚠️ SO THIS GUARD IS NOT THE WHOLE CONTROL, AND MUST NOT BE TREATED AS ONE. It catches the
    shapes. The REAL-LITERAL check — the one that knows the actual host codenames, the actual
    infra domain and the operator's name — lives OUTSIDE every repo, in the project working
    directory, and is run against the staged tree before every push. A value that is real but
    shape-less (a bare codename such as a machine's pet name, with no domain or address around
    it) is invisible HERE and is caught THERE. Both layers are required; neither is sufficient.

WHAT COUNTS HERE
    RFC1918 and CGNAT addresses, `*.ts.net` tailnet names, `.lan`/`.local` host names, Unraid
    `/mnt/<pool>` paths, freemail personal addresses, and bare UUIDs (Cloudflare
    Access policy ids, tenant ids and app ids all take that shape, and all identify the estate).
    Documentation ranges (RFC5737) and `example.com`/`.example` are the ALLOWED way to write an
    address or a host in a doc, a comment, or a test.

    ⚠️ EVERY EXAMPLE IN THIS FILE IS SYNTHETIC — `.invalid` hosts (RFC 2606), out-of-range
    octets and reserved documentation ranges exercise the same patterns and leak nothing.

KNOWN LIMITS (state them; do not pretend to coverage)
    Line-based and literal: a value split across lines (implicit string concatenation, a YAML
    folded scalar) or encoded (base64, percent-encoding) is not detected. The guard raises the
    cost of an accidental leak; it is not a control against a determined one.

    Shape-based, per the note above: it cannot see a real value that has no recognisable shape.

    ⚠️ THE SPECIFIC GAPS, listed because three separate pattern comments said "stated in KNOWN
    LIMITS" while this section did not in fact state them. A cross-reference to a promise nobody
    kept is the same claim-inflation the guard exists to keep out:
      * `.lan`/`.local` with a TRAILING DOT (`the agent lives at host-a.lan.`, or the absolute-DNS
        `http://host-a.lan./mcp`) is NOT matched. The obvious repair `(?![\w-]|\.[\w-])` was tried
        and REVERTED: it false-fires on `settings.local.*`, `config.local.${ENV}` and `.gitignore`
        globs, which are ordinary clean lines. `<label>.local.` at a sentence end and
        `<label>.lan.` at a sentence end are the same string shape and no regex separates them,
        so this takes the fail-quiet side deliberately. Do not "fix" it.
      * A `.gitignore` glob ending immediately after the label — `*config.local*` — DOES fire, a
        real false positive (issue #32). The right bound rejects a following LABEL, and `*` is not
        one. Not repaired here because the repair touches the frozen pattern above; pinned as
        known behaviour by a test so it is discoverable rather than folklore.
      * THE TREE SCAN READS THE WORKTREE, and that is still true — but it is no longer a GAP,
        because it is no longer the only thing the commit-time layer runs. `git add cfg.txt` while
        it holds a leak, then overwrite cfg.txt with a clean version and do not re-stage: the tree
        scan is honestly clean and the index — and so the commit — still carries the leak (issue
        #33). `--staged` is the scan that answers the index's question, and `.githooks/pre-commit`
        runs both. The two shapes tried and REMOVED before it stay recorded so they are not
        re-attempted: reading each staged blob made the hook take 78 s on a 1000-file worktree,
        and one `cat-file --batch` DESYNCHRONISED on a gitlink — `:<path>` on a submodule returns
        a COMMIT object whose body the parser must skip — mis-attributing one file's content to
        another and exiting 0 on a staged leak it never read. `--staged` is neither: it is
        `git diff --cached --unified=0` through the existing, well-tested `parse_diff`.
        ⚠️ WHAT REMAINS, stated rather than claimed away: `--staged` sees what the commit ADDS
        relative to HEAD. A leak already present in HEAD is not "staged" and is not reported by
        it — that is the tree scan's question, which is why both still run.
      * A CUSTOM Unraid pool name (`/mnt/tank`, `/mnt/nvme`) has no shape here — the pool list is
        a fixed set of stock names. Widening to `/mnt/[\w-]+/` would fire on ordinary container
        paths. Custom names are the project-side guard's job.
      * `@me.com` / `@mac.com` are deliberately absent from the freemail alternation: `me`
        collides with too much ordinary text.
      * Windows: UNC (`\\<host>\<share>`), drive-RELATIVE (`\Users\<name>`) and percent-encoded
        (`C:%5CUsers%5C…`) spellings are not matched. See the pattern comment for why each would
        cost more in false positives than it closes.

    `--range` reads each commit's diff against its FIRST parent. For an ordinary commit that is
    exactly "what this commit added". For a merge it is "everything the merged side brought in",
    which is redundant when those commits are themselves in the range and is the safe direction
    to be wrong in — a guard may re-scan, it may not skip. What it does NOT see is a value that
    was never added as a whole line by any commit in the range (the same line-based limit as
    above), or history OUTSIDE the range you hand it.

WHY --range EXISTS (the working-tree scan is not enough)
    Scanning the working tree answers "is the leak here NOW". Pushing publishes HISTORY. A value
    committed in one commit and deleted in the next is absent from the tree, passes the tree
    scan, and is still permanently readable at the earlier commit once the branch is on the
    remote — which is the expensive case this whole file exists to prevent. So the tree scan and
    the range scan answer two different questions and BOTH are run: the tree scan on every
    commit, the range scan on everything about to reach the remote.

    ⚠️ AND THE TREE SCAN IS BLIND TO A BRAND-NEW FILE. It reads `git ls-files`, which lists
    TRACKED files only, so a file not yet in the index is not scanned at all — it is not a hit,
    it is not "unreadable", it is simply absent from the run that prints "no internal info
    found". That is how a synthetic internal hostname once reached a public remote: the guard
    ran BEFORE `git add`, reported clean on a tree that did not yet contain the new file, and
    the very next commit published it. Two consequences, both load-bearing:
      1. ALWAYS run the tree scan AFTER `git add`, never before. The pre-commit hook is on the
         right side of that line by construction; a human running it by hand is not.
      2. The range scan does not care either way — it reads what each COMMIT added, and a new
         file's first commit adds every one of its lines. It is the layer that actually closes
         this, which is why it runs on the push path and in CI rather than only locally.

WHAT A SCAN LOOKS AT (five surfaces, not one)
    A push publishes a commit OBJECT, and every field of it is permanent. The scans read:
      * FILE CONTENT — the tracked worktree, the index, and the lines each commit adds;
      * FILE PATHS — a leak in a filename or a directory name is published on every file listing
        and in every clone, and was read by nothing at all until keystone#20;
      * COMMIT IDENTITY — author and committer name/email;
      * COMMIT MESSAGES — subject and body, rendered on every commit page (keystone#21 / #39);
      * ANNOTATED TAGS the push NAMES — the whole tag OBJECT, which carries the tag's name, its
        tagger and its message together. Scoped by the REF being pushed, not by what its commit
        reaches: a tag cut at an already-pushed commit is the ordinary release gesture and covers
        zero new commits, while a purely local scratch tag must not block a branch push that never
        sends it.
        ⚠️ A LIGHTWEIGHT tag has no object, so a leak that exists only as a ref NAME — a
        lightweight tag, or a branch called after a host — is read by NO layer. That is issue #49,
        and it is a deliberate revert rather than an omission; `refs_being_published` carries the
        reasoning and the three designs that failed.
    Three of the last four can be removed only by rewriting history, which is why they belong on
    the same push-path gate as a leaked line rather than in a checklist. A TAG is the exception —
    it is a ref, and `git tag -d` plus `git push --delete` clears it — but it is still published,
    and catching it before it goes out is still cheaper than after.

USAGE
    python scripts/check_no_internal_info.py            # scan tracked files, exit 1 on a hit
    python scripts/check_no_internal_info.py --selftest # prove the patterns still bite
    python scripts/check_no_internal_info.py --staged   # scan the INDEX - what a commit records
    python scripts/check_no_internal_info.py --range origin/main..HEAD  # scan the COMMITS

    As a pre-commit + pre-push hook (one-time, per clone):
        git config core.hooksPath .githooks

IF IT FIRES ON SOMETHING LEGITIMATE
    Add the exact literal to ALLOW_LITERALS below, in the same commit, with a comment saying
    why. That edit is visible in review -- which is the point. Do not loosen a pattern, and
    never add a blanket per-file skip: this file's whole value is that it cannot be satisfied
    by looking away.
"""

from __future__ import annotations

import bisect
import functools
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# --------------------------------------------------------------------------- the denylist
# SHAPES ONLY. See the module docstring: a real literal in this list would make this file the
# leak. Every entry MUST have at least one deny case in `_MUST_FAIL` and at least one near-miss
# in `_MUST_PASS` — a shape that looks like it and must NOT fire. What `selftest` actually
# enforces: it fails on a pattern with no deny case (nothing would then prove the pattern still
# bites), and it fails on any `_MUST_PASS` sample that trips (which is how a pattern widened
# until it matches ordinary text gets caught). It CANNOT check that a given pattern has a
# near-miss, because `_MUST_PASS` is a flat list with no pattern labels — that half is
# convention, and it is the half that catches widening, so add it with the pattern, not later.
PATTERNS: list[tuple[str, str]] = [
    # RFC1918. Left-bounded so a decimal doesn't trip it; the right bound must still reject a
    # 5th octet while ALLOWING a sentence-final period — `the host is <addr>.` is the most
    # natural way to write one in prose, and was invisible to an earlier `(?![\w.])`.
    ("private IPv4 (RFC1918)",
     (r"(?<![\w.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
      r"|192\.168\.\d{1,3}\.\d{1,3}"
      r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\w]|\.\d)")),
    # Carrier-grade NAT 100.64.0.0/10 — the range Tailscale assigns from.
    ("cgnat address",
     r"(?<![\w.])100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}(?![\w]|\.\d)"),
    # ⚠️ Left-bounded, and with `(?<![\w-])` specifically — NOT `(?<![\w.-])`.
    # `\b[\w-]+\.` was an instance of the unbounded-leading-class defect this file has been bitten
    # by three times: `-` is in the class but is not a word character, so `\b` held at every
    # hyphen, giving one restart per position. Measured on hyphenated input: 0.12/0.48/1.96/7.27 s
    # at 4k/8k/16k/32k. Not theoretical — base64url is exactly `[\w-]`, and a 128 KB token line
    # made a real `git push` take 2m06s. Bounded, the same input takes 0.0015 s.
    # Including `.` in the lookbehind would be the obvious copy-paste and it BREAKS the pattern:
    # a match must be able to start right after a dot, or `foo.bar.ts.net` stops being caught.
    ("tailnet name", r"(?<![\w-])[\w-]+\.ts\.net\b"),
    # Private/LAN search domains. The trailing `(?![\w.-])` is LOAD-BEARING and is not the `\b`
    # the other patterns use: `\b` matches before a `.`, so `[\w-]+\.local\b` fires on
    # `settings.local.json` / `config.local.yml` / `.claude/settings.local.json`, which exist in
    # ordinary repos and would redden CI on a file that leaks nothing. Requiring that NO further
    # label follows keeps the hostname reading (`http://nas-a.lan/`, `ping printer.local`) and
    # drops the filename one.
    #
    # ⚠️ `.internal` IS DELIBERATELY NOT IN THIS LIST, and re-adding it is a regression. ICANN
    # reserved `.internal` in 2024 precisely so it could be used freely, which made it the
    # conventional way to spell a SYNTHETIC host in a fixture — `db.internal` in a test config is
    # a placeholder, not an estate. Denying it produced a false positive on exactly that, and a
    # guard that fires on the approved placeholder convention is a guard people switch off. There
    # is a `_MUST_PASS` case pinning this. If an estate ever genuinely adopts `.internal`, it
    # belongs in the project-side real-literal guard, not here.
    ("private lan domain", r"(?<![\w-])[\w-]+\.(?:lan|local)(?![\w.-])"),
    # Unraid share/pool roots. The PATH is what identifies an estate's storage layout; the bare
    # words are ordinary technical English (`__pycache__`, `--not --remotes`) and matching those
    # produced false failures, so this is anchored to `/mnt/`.
    #
    # ⚠️ A FIXED LIST OF STOCK NAMES, NOT `/mnt/<pool>`. `disk\d+` is here because
    # `/mnt/disk1`…`/mnt/diskN` are the canonical Unraid ARRAY mounts and the plural `disks` did
    # not cover them — a one-character-off gap that let the most ordinary array path through. A
    # CUSTOM pool name (`/mnt/tank`, `/mnt/nvme`) still has no shape here and is the project-side
    # guard's job; that limit is stated in KNOWN LIMITS rather than papered over by widening this
    # to `/mnt/[\w-]+/`, which would fire on ordinary container paths.
    ("unraid pool path", r"/mnt/(?:apps|user|cache|remotes|disks|disk\d+)\b"),
    # A personal address on a consumer mail provider. Shape, not a specific mailbox — the
    # operator's own address is a literal and lives in the project-side guard, not here.
    #
    # ⚠️ `proton(?:mail)?` and the legacy Microsoft/Google domains are NOT decoration. The old
    # alternation named `proton` and required a `.` straight after it, so `@protonmail.com` —
    # Proton's original and still-dominant domain — did NOT match while `@proton.me` did. Same
    # shape for Outlook: `@live.com` and `@msn.com` are the same mailboxes under older names, and
    # `@googlemail.com` is `@gmail.com`. A provider NAMED in the list whose main domain walks
    # through is a bug, not a scoping choice. `@me.com`/`@mac.com` are deliberately NOT here —
    # `me` collides with too much ordinary text — and that gap is stated in KNOWN LIMITS.
    # ⚠️ LEFT-BOUNDED with `(?<![\w.+-])`. Without it `[\w.+-]+@` can start at every position in a
    # run of word characters and rescan to the end of the line, so the cost is quadratic in line
    # length: measured at 0.10/0.41/1.62/6.34 s for 4k/8k/16k/32k characters, a clean 4x per
    # doubling. Bounded, 64k takes 0.005 s. The two forms are equivalent, not merely close: over
    # 60 014 strings they agreed on match/no-match in every case — the bound only pins where a
    # match may START, and any match that exists has a leftmost start that no `[\w.+-]` precedes.
    ("personal mail address",
     r"(?<![\w.+-])[\w.+-]+@(?:gmail|googlemail|outlook|live|msn|hotmail|yahoo|icloud"
     r"|proton(?:mail)?|aol)\."),
    # Any UUID. Cloudflare Access policy ids look like this, and so do tenant/app ids — all of
    # which identify the estate. A legitimate one (a fixture, a migration revision) is meant to
    # be added to ALLOW_LITERALS deliberately rather than waved through by a looser pattern.
    # ⚠️ Bounded on the HEX CLASS, not with `\b` and not with `(?<![\w-])`. `_` is a word
    # character, so `\b` does NOT hold after it and `app_id_11111111-2222-...` walked straight
    # through — `<KEY>_<uuid>` is an ordinary config idiom and was the likeliest way for one of
    # these to be written. `(?<![\w-])` is the opposite error: it is STRICTER than `\b` and
    # rejects that same case. What the bound actually needs to prevent is a UUID being read out
    # of a longer HEX run (a git sha, a hash), so exclude hex on both sides and nothing else.
    ("uuid (access policy / tenant id)",
     r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])"),
    # A Windows profile path. The IDENTIFYING part is the account name in the third segment —
    # `C:\Users\<name>\…` names the operator, and an AppData path under it names their machine
    # layout as surely as `/mnt/user/…` names the array.
    #
    # ⛔ ANCHORED TO `Users`, NOT TO A DRIVE LETTER. `[A-Za-z]:\…` on its own fires on every
    # ordinary Windows path an instruction can contain — `C:\dev\<repo>`, `C:\Program Files\…`,
    # `D:\data\export.csv` — none of which identifies anybody. A guard that reddens on the
    # command it is telling you to run is one that gets switched off.
    #
    # ⚠️ THE PLACEHOLDER FORMS MUST PASS, and they do so structurally rather than by exception:
    # `<you>` and `%USERNAME%` contain characters outside `[\w.-]`, so the account-name segment
    # simply fails to match. The four Windows built-in profiles are excluded explicitly because
    # they ARE spelled with ordinary word characters and name no one.
    #
    # ⚠️ `(?:\\{1,2}|/)` because a path written INSIDE a string literal is escaped —
    # `"C:\\Users\\operator"` in JSON or Python source is the same leak as the bare form, and a
    # single-character separator class misses every one of them.
    #
    # ⚠️ THREE PREFIXES, not one. The same profile path is written three ways on this platform and
    # only the first was matched at first: the WSL view `/mnt/c/Users/<name>/…` (which also slips
    # `unraid pool path`, whose pool list is fixed) and the environment-variable form
    # `%SystemDrive%\Users\<name>` are the same leak in different clothes.
    #
    # ⚠️ `[\\/]+` FOR THE SEPARATOR — one class, both characters, any run length. This was got
    # wrong TWICE in the same place, each repair closing only the half in front of it:
    #   1. `\\{1,2}` capped backslashes at two, so `C:\\\Users\\\operator` (a JSON string inside a
    #      shell string) walked through.
    #   2. `(?:\\+|/)` fixed exactly that and left the forward-slash side at ONE, and forbade
    #      mixing — so `C://Users/operator`, `/mnt/c//Users/operator` and
    #      `file:///C://Users//operator` all walked through instead.
    # A separator is a RUN of either character; writing it as one class is the form that has no
    # remaining half. (Doubled slashes are not exotic: they come from naive path joining and from
    # `file://` URLs.)
    #
    # KNOWN LIMITS, stated rather than half-covered (all three are also in the module docstring):
    #   * a UNC path (`\\<host>\<share>`) names a host and is NOT matched. Its shape — two
    #     backslashes, a label, a backslash — is also an ordinary regex fragment (`"\\w\\d"`), so
    #     matching it false-fires on source code, and a false-red costs more than the gap.
    #   * the drive-RELATIVE form (`\Users\<name>`) is not matched, for the same reason: with no
    #     prefix to anchor on it fires inside ordinary escaped text.
    #   * percent-encoded separators (`C:%5CUsers%5Coperator`) are not matched — the same
    #     encoding limit the docstring already declares for base64 and percent-encoding.
    #
    # ⚠️ `runneradmin` IS IN THE EXCLUSION LIST FOR THE SAME REASON THE FOUR BUILT-INS ARE: it is
    # the GitHub-hosted Windows runner's account, it names nobody, and it turns up in ordinary CI
    # output — a workflow log, a stack trace, a commit message describing a CI fix. Once commit
    # MESSAGES became a scanned surface it produced a false red on exactly that
    # ("ci: fix path handling for C:\\Users\\runneradmin\\AppData\\Local\\Temp"), which is a red
    # nobody can clear without rewriting an already-reviewed commit. Same class as the built-ins,
    # same carve-out, one more spelling.
    ("windows profile path",
     r"(?<![\w])(?:[A-Za-z]:|/mnt/[a-z]|%\w+%)[\\/]+Users[\\/]+"
     r"(?!(?:Public|Default|Default User|All Users|runneradmin)(?![\w.-]))[\w.-]+"),
]

# ⭐⭐ THE DENYLIST ABOVE IS WRITTEN FOR FILE CONTENT, AND TWO OF THE FIVE SURFACES ARE NOT CONTENT.
# A repo-relative PATH and a free-prose COMMIT MESSAGE have different grammar from a line of code,
# and three of the patterns rely on bounds that only hold in the grammar they were written for.
# Applied verbatim they reddened trees that leak nothing — measured, all of these:
#
#   * `zshrc.local`, `vimrc.local`, `gitconfig.local`, `tmux.conf.local`, `Makefile.local`,
#     `packages/app.local/` — the standard machine-local-override convention. `private lan domain`
#     rejects a following LABEL (`settings.local.json` passes) but a `/` or end-of-string satisfies
#     its bound, which is exactly what a segment-terminal `.local` filename is. A dotfiles repo
#     became permanently red, and `git commit` was refused.
#   * `db/migrations/<uuid>.sql`, `test/fixtures/<uuid>.json`, `cassettes/<uuid>.yml` — a UUID
#     FILENAME is a naming convention. The pattern exists for a Cloudflare policy or tenant id,
#     which is a value written INSIDE a file, and the content scan still catches it there.
#   * `docs/mnt/user/notes.md` — a repo-relative path can never BE an absolute pool path. The
#     pattern can only match one directory deep, where it means a doc or fixture tree, not an
#     estate's storage layout.
#   * a commit message naming `config.local`, one citing `printer.local` as an mDNS example, one
#     quoting a fixture UUID. Worse than an ordinary false red: a message cannot be edited without
#     rewriting history, and a push is blocked over commits the pusher did not write.
#
# So each surface gets an explicit, documented override set. `None` REMOVES a pattern from that
# surface; a string REPLACES its regex. Nothing is loosened for file content, which is the surface
# the bounds were designed for and where they are correct.
#
# ⛔ THIS IS NOT A BLANKET SKIP, and the difference matters: every removal below is a pattern whose
# SHAPE cannot occur meaningfully on that surface, never a decision to look away from a place a
# leak can hide. The `.lan` half of `private lan domain` is KEPT on both surfaces, because
# `<host>.lan` has no filename convention behind it and is the exact shape the path scan exists
# for (`docs/<host>.lan/`, `<host>.lan.conf`).
_LAN_ONLY = r"(?<![\w-])[\w-]+\.lan(?![\w-])"

# ⭐ THE RIGHT BOUND IS `(?![\w-])`, DELIBERATELY WIDER THAN THE CONTENT PATTERN'S `(?![\w.-])`.
# Content must reject a following dot so `settings.local.json` passes; with `.local` gone there is
# nothing left for a following dot to protect, so allowing one CLOSES two cases the content
# pattern is documented to miss: the filename `<host>.lan.conf`, and the sentence-final
# `deployed from <host>.lan.` in a commit message.
PATH_PATTERN_OVERRIDES: dict[str, str | None] = {
    "private lan domain": _LAN_ONLY,
    "unraid pool path": None,
    "uuid (access policy / tenant id)": None,
}
# ⛔ AND THE POOL PATH IS LEFT-BOUNDED ON THIS SURFACE. The content pattern needs only a LEADING
# SLASH, which any nested repo-relative path supplies — so a commit message that merely NAMED one
# of the paths `_MUST_PASS_PATHS` blesses ("docs: add docs/mnt/user/notes.md") blocked the push,
# while the file and its filename both passed. That is the same defect corrected on the path
# surface, one dict down. Requiring that nothing path-like precede `/mnt/` keeps the real case —
# "moved appdata to /mnt/user/appdata/svc" — and drops the mention of a repo path.
# ⚠️ THE BOUND REJECTS `[\w.-]`, AND DELIBERATELY NOT `/`. Excluding `/` as well looked tidier and
# cost a real catch: `file:///mnt/user/...` and a bare `//mnt/user/...` are ABSOLUTE pool paths, and
# the character before them is a slash. What the bound is actually for is a path SEGMENT
# continuation — `docs/mnt/user/notes.md`, where the preceding character is a word character.
# (`host/mnt/user/...` is genuinely indistinguishable from `docs/mnt/user/...` and stays missed;
# that ambiguity is irreducible in text and is stated rather than papered over.)
_ABS_POOL_PATH = r"(?<![\w.-])/mnt/(?:apps|user|cache|remotes|disks|disk\d+)\b"

MESSAGE_PATTERN_OVERRIDES: dict[str, str | None] = {
    "private lan domain": _LAN_ONLY,
    "unraid pool path": _ABS_POOL_PATH,
    "uuid (access policy / tenant id)": None,
}

# ⚠️ WHAT THE OVERRIDES REMOVE, STATED IN FULL — because this file's own KNOWN LIMITS block exists
# precisely because three pattern comments once said "stated in KNOWN LIMITS" while that section
# did not in fact state them. Beyond the two whole patterns dropped from paths, `_LAN_ONLY` drops
# a THIRD thing on BOTH new surfaces: the `.local` half of `private lan domain`. So a genuine mDNS
# host — `printer-b.local` — is NOT caught in a filename, a directory name, a commit message or a
# tag. That is deliberate and it is not separable: `zshrc.local` and `printer-b.local` are the same
# string shape, and the corpora require the first to pass. It is caught in file CONTENT, which is
# where such a host is actually configured. Do not read the surviving LABEL as coverage.

# Per-PATH exemptions for a single pattern, as (pattern label, path regex).
#
# EMPTY HERE, AND THAT IS THE EXPECTED STATE for a shape-based denylist: every pattern above
# describes a value that has no business appearing anywhere in this repo, including in build
# output. The mechanism exists because the engine is shared verbatim with the project-side
# real-literal guard, which DOES need one (a scan for the operator's given name false-positives
# on the home-directory path baked into `__pycache__` bytecode). Keeping the two engines
# byte-identical except for their data is what stops them drifting into disagreeing about what
# a repository contains.
#
# ⚠️ This is a per-PATTERN, per-PATH carve-out and never a blanket file skip. Adding a whole
# file here would recreate exactly the "satisfied by looking away" hole the docstring forbids.
PATH_EXEMPT: tuple[tuple[str, str], ...] = ()

# Literals that LOOK like a hit but are allowed. Keep each one justified.
# ⚠️ As of today all three are INERT: none of them matches any current pattern, so removing the
# neutralisation would change no verdict. They are kept as a deliberate carve-out for the
# functional owner path in case a future pattern would catch it — stated here so nobody reads
# this list as evidence that the carve-out is exercised. It is pinned DIRECTLY, by
# `test_the_allow_literals_are_recognised_as_permitted_spans`, and not through a scan verdict that
# would pass either way.
ALLOW_LITERALS: tuple[str, ...] = (
    # The GitHub/GHCR owner path is functional — neutralizing it breaks image pulls and the
    # icon URLs the Unraid templates point at. It is the account name, not infrastructure, and
    # it is in this repository's own clone URL.
    "github.com/texasdaddy",
    "githubusercontent.com/texasdaddy",
    "ghcr.io/texasdaddy",
)

# The documented ways to write an address or a host. A span here SUPPRESSES A HIT IT CONTAINS
# (see `scan_text`); it never deletes text and it never skips the line. Skipping the line meant
# one `.env.example` mention could hide a real leak sitting beside it, and `.example` matches
# `.env.example`, which appears in every repo's docs.
#
# ⛔ NO SPAN MAY CONSUME ANYTHING TO ITS LEFT. A span's only power is to suppress a hit it
# CONTAINS, so any text a span can grow over is text it can grant amnesty to. This entire file
# is the second half of a fix; the first half is `scan_text` no longer deleting these spans.
#
# THE BUG THIS CLOSES, exactly. The leading-label group `(?:[\w-]+\.)*` in front of
# `example\.(?:com|org|net)` was unbounded, so on `AGENT=<rfc1918-addr>.example.com` the span
# matched the WHOLE host — the address included — and the delete-then-match pass left `AGENT=`
# with nothing to find. Exit 0, "no internal info found". ONE suffix defeated EVERY dotted
# pattern at once: RFC1918, CGNAT, tailnet, UUID and freemail, plus `/mnt/user.example.com` for
# the pool path. Measured on this guard before the fix: 6 of 6 shapes walked through.
#
# ⚠️ TWO REPAIRS THAT LOOK SAFE AND ARE NOT (recorded by the sibling repo that tried both, so
# they are not re-attempted here):
#   1. Keeping the group but capping it — it still walks back over labels, just fewer.
#   2. "ONE label, bounded" (`[\w-]+\.example`) — `[\w-]` INCLUDES the hyphen, and a UUID is
#      hyphen-separated hex, i.e. exactly ONE `[\w-]` run. So `CF_APP=<uuid>.example` was still
#      swallowed, and the UUID pattern is precisely the one that exists for Cloudflare Access
#      policy and tenant ids.
# One DNS label is not one CHARACTER-CLASS run, and that gap is where both bugs lived.
#
# So NO entry carries a leading label at all. `your-domain.example` is permitted by matching the
# `.example` SUFFIX; the label in front is simply not part of the span.
#
# ⚠️ AND THE ALLOWLIST IS NOT INERT. This comment used to say these spans "suppress nothing
# whatsoever (no deny pattern can match INSIDE an RFC5737 address or an `example.*` suffix)" —
# which enumerates two of the three span families and silently omits the third: `.env[.q].local`
# contains a `private lan domain` match BY CONSTRUCTION, and suppression genuinely fires there.
# That false half is exactly what made an unbounded version of that span read as harmless. The
# same sentence appeared in `scan_text`'s docstring and was corrected there first; leaving the
# copy here is how a corrected claim un-corrects itself. Treat the carve-out as live machinery.
#
# `_MUST_FAIL_ADJACENT` pins the class, one case per label, and
# `test_no_permitted_span_overlaps_the_leak_in_any_curated_amnesty_case` asserts the invariant
# behind it — that no permitted span OVERLAPS the leak — over exactly those curated cases. Stated
# that precisely on purpose: it is not a proof over all possible inputs, and a comment claiming
# one would be the kind of unverified strength claim this file exists to keep out.
#
# ⚠️ The host spans are LEFT-BOUNDED, rather than leading with a bare `[\w.-]*` as this file used
# to. An unbounded leading `[\w.-]*` overlaps the `\.` that follows it AND can start at every
# position in a run of word characters, so the cost is quadratic in line length: 2 000 chars
# 0.06 s, 4 000 chars 0.21 s, 8 000 chars 0.84 s, 16 000 chars 3.40 s — a clean 4x per doubling.
# Bounded, 16 000 chars complete in 0.0005 s.
#
# (This paragraph used to add "and anchor each label with `(?:[\w-]+\.)*`", describing an
# "anchored body" that no longer exists anywhere in the file — the leading-label group was
# REMOVED entirely, which is what the block above says. A leftover half-sentence from a
# superseded design reads as a description of the current one; the surviving lesson is the LEFT
# BOUND, not the anchoring.)
#
# ⚠️ THE LEFT BOUND IS WHAT MATTERS, and it is easy to mis-attribute: adding `(?<![\w.-])` to the
# OLD flat body also makes it linear, so a "revert" that keeps the bound looks like evidence that
# the anchoring was pointless. It is not the same form. The rule to carry away is that any
# unbounded leading character class is quadratic.
#
# The anchored body is not match-for-match identical to the flat one: it REPORTS cases the flat
# form swallowed (the `<label>-example.tld` shape) and none in the other direction — so the
# change is strictly FAIL-CLOSED, which is the property that matters for a leak guard.
ALLOW_SPANS: tuple[str, ...] = (
    r"(?<![\w.-])192\.0\.2\.\d{1,3}",         # RFC5737 TEST-NET-1
    r"(?<![\w.-])198\.51\.100\.\d{1,3}",      # RFC5737 TEST-NET-2
    r"(?<![\w.-])203\.0\.113\.\d{1,3}",       # RFC5737 TEST-NET-3
    r"(?<![\w-])example\.(?:com|org|net)\b",  # example.com and friends, the token ITSELF
    r"\.example\b",                           # the `.example` SUFFIX, NOT the label before it
    r"\.env\.example\b",
    # `.env.local` is as universal as `.env.example` and is a FILENAME, not a `.local` host.
    # Without it `cp .env.local .env` is a standing false positive on an ordinary line.
    #
    # ⛔ THE MIDDLE SEGMENT IS AN ENUMERATION, NOT A CHARACTER CLASS — the same lesson as above,
    # learned twice more by the sibling repo that added this span:
    #   * `[\w-]+` here swallowed `.env.<uuid>.local` (hyphen-separated hex is one `[\w-]` run).
    #   * Capping it to `[\w-]{1,20}` fixed only the UUID and left the general case open:
    #     `.env.host-a.local` still hid a real `.local` LAN host, because a hostname label is
    #     SHORT. The cap treated the symptom (36 characters) instead of the property.
    # A dotenv qualifier is one of a small known set, so enumerate it. A qualifier OUTSIDE this
    # list is matched — a visible false positive with a documented escape hatch, which is the
    # right way round for a leak guard.
    # ⛔⛔ THE LEFT BOUND `(?<![\w-])` IS THE WHOLE SAFETY OF THIS ENTRY, and it was MISSING when
    # this span was first ported here. Without it the span starts at a bare `\.`, so it matches
    # the TAIL of a hostname as happily as a filename — and `nas-a.env.local` then contains the
    # `env.local` deny match, which containment duly suppressed. ALL TWELVE qualifier forms were
    # exploitable, the scan exited 0, and it masked a leak in COMMIT IDENTITY too:
    #     AGENT_URL=http://nas-a.env.local:9999/mcp        -> "no internal info found"
    # That is the amnesty class this whole file was rewritten to close, reintroduced in the one
    # span nobody re-checked. A span with no left bound can grow leftward over its neighbour; the
    # rule at the top of this block is not a style note.
    r"(?<![\w-])\.env(?:\.(?:local|development|staging|production|preview|test|dev|prod|ci|qa"
    r"|sandbox))?\.local\b",
)

# Text-bearing formats are NEVER skipped — an SVG is XML and carries <title>/<desc>/href, and
# the leak an earlier batch removed was literally an icon URL inside one.
#
# ⭐ THIS IS A HINT ABOUT WHERE NOT TO WASTE A READ, NOT AN EXEMPTION (keystone#22). It was the
# latter, and a file named `deploy-notes.pdf` holding an ordinary ASCII runbook — a LAN host, an
# appdata path and an RFC1918 address, all in plain text — was therefore read by NEITHER scan and
# exited 0. Renaming a text file must not defeat a guard. What each scan does now:
#   * the tree scan asks `_looks_binary` of the BYTES and only skips if they really are binary;
#   * the range scan scans added lines regardless of suffix — git already refused a text diff for
#     anything it judged binary, which is a content check stricter than any filename.
# What the list still earns, stated per scan rather than rounded up to "both". In the RANGE and
# `--staged` scans it stops a 100 MB `.png` being re-diffed with `--text`, and it stops an ordinary
# image being REPORTED as "unreadable, so not cleared". In the TREE scan it does neither: that scan
# reads every tracked file's bytes before deciding anything, so the suffix only chooses which
# QUESTION is asked of them (`_looks_binary`, or the #242 NUL refusal). Reading an asset in full on
# every tree scan is a real cost the suffix used to avoid; it is a slowdown, not a defect, and
# batching it is left as its own change rather than smuggled in here.
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".ico", ".gif", ".pdf", ".zip", ".gz",
                 ".woff", ".woff2", ".ttf", ".db", ".sqlite")

# ⛔ THE EXACT PATH, not a substring of the file NAME. This file carries synthetic deny cases by
# design and so cannot be scanned; nothing else earns that exemption. Matching `SELF in name` —
# which this file and its siblings used to do — silently exempts any file whose name merely
# CONTAINS it, `tests/test_check_no_internal_info.py` being the obvious one, and
# `scripts/__pycache__/check_no_internal_info.cpython-312.pyc` being the one that actually bit:
# a tracked, binary, literal-bearing copy the scan skipped in silence. A `.pyc` is deliberately
# NOT in SKIP_SUFFIXES so it surfaces as unreadable-and-therefore-not-cleared instead.
#
# ⚠️ BOTH SCANS USE THIS, and they must agree. The range scan gets its paths from the
# `diff --git a/X b/X` header, which is already repo-relative and posix-separated — the same
# shape as the tree scan's `path.relative_to(root).as_posix()`. If the two disagreed about which
# single file is exempt, one of them would be lying about its coverage.
#
# ⚠️ The project-side real-literal guard deliberately does NOT skip this path. Scanning THIS file
# for real values is the check that keeps the shapes-only promise above honest, and a guard that
# skipped it could not perform that check.
SELF_PATH = "scripts/check_no_internal_info.py"

_ALLOW_SPAN_RX = re.compile("|".join(ALLOW_SPANS), re.IGNORECASE)
_PATH_EXEMPT_RX: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(rx)) for label, rx in PATH_EXEMPT)

# EVERY git call this makes is bounded — `tracked_files`, the per-commit diff and the fallback
# re-diff alike. A guard that can hang on the push path is the failure the no-silent-hangs
# directive forbids, and a wedged git is indistinguishable from a slow one. These are local
# operations with no network, so the risk is small but not nil: `git diff` can invoke a
# `.gitattributes` textconv filter that shells out. Generous enough that a large repository is
# never the reason it fires.
_GIT_TIMEOUT_S = 120


@functools.cache
def _self_rel_path(root: Path | None = None) -> str:
    """This file's OWN repo-relative path — resolved, not assumed.

    ⚠️ `SELF_PATH` is a constant, so the exemption used to land on whatever sits at that path
    rather than on this file. Copy the guard to `tools/check_no_internal_info.py` and run it, and
    it scans ITSELF (every synthetic deny case above becomes a finding) while exempting the
    unrelated file at `scripts/…` — the exemption on the one file that does not need it, and gone
    from the one that does. Deriving it from `__file__` makes the SELF_PATH comment true wherever
    the guard is run from; the constant stays as the fallback for an exotic loader with no
    resolvable path, and for the project-side guard, which reads it to know what NOT to skip.

    ⚡ CACHED: `_is_self` calls this once per tracked file, and each call makes two
    `Path.resolve()` syscalls — ~1.7 ms per file against ~0.8 us for a constant compare, so a
    large repository would pay seconds for an answer that cannot change during a run. (This used
    to name `_skipped` as the per-file caller. `_skipped` is not called by the tree scan at all;
    the caching is still right, the named caller was not.)
    """
    if root is None:
        return SELF_PATH
    try:
        return Path(__file__).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return SELF_PATH


def _is_self(rel_path: str, root: Path | None = None) -> bool:
    """Is this the scanner's own source? The ONE file whose CONTENT no scan reads.

    ⛔ THE ONLY UNCONDITIONAL CONTENT EXEMPTION IN THIS FILE, and it is deliberately separate from
    the binary-suffix question now. They used to be one predicate (`_skipped`) answering two very
    different questions: "this file carries synthetic deny cases by design" versus "this file is
    probably an image, so reading it is pointless". The second is a GUESS FROM A FILENAME, and
    treating a guess as an exemption is what let an ASCII leak in a file named `.pdf` walk past
    both scans (keystone#22). One is a fact about a known path; the other is a prediction, and a
    prediction now has to be CHECKED against the bytes — see `_looks_binary`.

    This exempts the file's CONTENT only. Its PATH is scanned like every other path (`scan_path`):
    renaming this guard to something that names the estate is a leak whatever the file contains.
    """
    return rel_path == _self_rel_path(root)


def _binary_suffix(rel_path: str) -> bool:
    """Does the NAME claim this is a binary asset? A HINT, never a verdict — see `_looks_binary`."""
    return rel_path.lower().endswith(SKIP_SUFFIXES)


def _skipped(rel_path: str, root: Path | None = None) -> bool:
    """Files neither scan will REPORT AS UNREADABLE: declared binary assets, and this own source.

    ⚠️ WHAT THIS STILL DOES, AND WHAT IT NO LONGER DOES. It still answers "is it pointless to
    complain that this file could not be decoded" — a `.png` git serves as a binary diff, or a
    blob the range scan would otherwise re-diff byte by byte. It is NO LONGER the gate on whether
    CONTENT gets scanned: a skipped suffix whose bytes turn out to be text is now scanned by both
    scans (`_looks_binary` in the tree scan; `scan_added` no longer filtering added lines by
    suffix). The suffix list stops the guard WASTING a read; it no longer stops it LOOKING at text.

    ⚠️ WHERE IT IS ACTUALLY CALLED, stated because the previous version of this docstring named the
    wrong callers and rested an argument on them. Its call sites are `resolve_unscannable` and
    `scan_added` — the RANGE scan and the `--staged` scan. **The tree scan does not call it at
    all**: it asks `_is_self` and `_binary_suffix` directly, because it holds the bytes and can ask
    the better question. So the read-avoidance above is real in `resolve_unscannable` (where it
    stops a 100 MB `.png` being re-diffed) and is NOT true of the tree scan, which reads every
    tracked file before deciding anything.

    `root` is threaded through the range scan for the same reason the tree scan resolves it: the
    self-exemption has to land on THIS file wherever the guard is run from.
    """
    return _binary_suffix(rel_path) or _is_self(rel_path, root)


def _looks_binary(data: bytes) -> bool:
    """Is this blob genuinely not UTF-8 TEXT? Asked of the BYTES, never of the filename.

    ⭐ THIS IS THE HALF THAT MAKES `SKIP_SUFFIXES` SAFE (keystone#22). The suffix list used to be
    trusted outright, so `deploy-notes.pdf` holding an ordinary ASCII runbook — a LAN host, an
    appdata path and an RFC1918 address, all in plain text — was read by neither scan and exited
    0. Renaming a text file must not be a way to defeat a guard.

    TWO tests. The NUL one comes first because it is what git itself uses to call a blob binary,
    and because it is the cheap answer for every real image:
      * a NUL byte anywhere — no legitimate UTF-8 source contains one;
      * it does not decode as UTF-8 at all — a real PNG/JPEG/PDF stream, a latin-1 file.

    ⚠️ SCOPED, deliberately: this is consulted ONLY for a path whose suffix already claims to be
    binary. A NUL-bearing blob at such a path is skipped SILENTLY, where a NUL-bearing blob at any
    other path is REFUSED and reported (the #242 BOM-less-UTF-16 posture, unchanged). So a UTF-16
    payload hidden in a file named `.pdf` is still not read — exactly as before this change,
    no better and no worse. Widening that means re-litigating which suffixes are assets, which is
    issue #38's design call and not this one's.
    """
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def gitlinks(root: Path) -> set[str]:
    """Repo-relative paths of SUBMODULE entries (git mode 160000).

    ⚠️ Asked of git rather than inferred from the filesystem, because the two states a gitlink can
    be in look like two DIFFERENT errors to a plain `read_text`: checked out it is a DIRECTORY
    (`IsADirectoryError` on POSIX, `PermissionError` on Windows), and in an ordinary clone without
    `--recurse-submodules` it does not exist at all (`FileNotFoundError`) — which is otherwise
    indistinguishable from the staged-but-deleted FILE that branch now exists to report. Without
    this, adding a submodule makes the repository permanently RED on a path whose contents are a
    different repository's business and are scanned by that repository's own guard.
    """
    out = subprocess.run(["git", "ls-files", "-s", "-z"], cwd=root, capture_output=True,
                         check=True, timeout=_GIT_TIMEOUT_S)
    found: set[str] = set()
    for entry in out.stdout.decode("utf-8", errors="replace").split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        if meta.startswith("160000"):
            found.add(path)
    return found


def compile_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """The denylist, compiled the ONE way the scan uses it.

    ⛔ EVERY CALLER MUST USE THIS — do not re-compile `PATTERNS` by hand. `main` used to inline
    `re.compile(rx, re.IGNORECASE)` and the test module inlined the identical line, so the tests
    held their OWN correct copy of the flags: dropping `re.IGNORECASE` from the shipped scan would
    leave every selftest case, the whole suite and CI green, while a real repository containing an
    uppercase LAN host, an uppercase UUID or a mixed-case freemail address scanned clean and
    exited 0. Uppercase GUIDs turn up routinely in Windows contexts (WMI hardware UUIDs, registry
    CLSID keys), so that is ordinary input rather than an exotic one. A single definition means
    the tests exercise what ships.
    """
    return [(label, re.compile(rx, re.IGNORECASE)) for label, rx in PATTERNS]


def compile_for(overrides: dict[str, str | None]) -> list[tuple[str, re.Pattern[str]]]:
    """`compile_patterns()` with a surface's overrides applied — see PATH_PATTERN_OVERRIDES.

    ⛔ DERIVED FROM `PATTERNS`, never a second hand-written list. A parallel copy would drift the
    moment a pattern is added, and the drift would be SILENT and in the fail-open direction: the
    new pattern would simply never reach the path or message surface. Deriving it means a pattern
    added tomorrow applies everywhere unless someone writes down an override and says why.

    ⚠️ AN OVERRIDE KEY THAT NAMES NO PATTERN IS AN ERROR, not a no-op. A renamed label would
    otherwise leave the override silently inert — the content bound would come back on a surface
    it was measured to false-red, and nothing would say so.
    """
    known = {label for label, _ in PATTERNS}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(
            f"override(s) naming no pattern: {sorted(unknown)} - a renamed label would leave the "
            f"override inert, so this is an error rather than a silent no-op")
    # ⛔ AN EMPTY STRING IS THE SAME SILENT INERTNESS, one step further in. `"" or rx` falls back
    # to the CONTENT pattern, so spelling an override `""` — a plausible way to write "disable
    # this" — quietly restored the exact bound the surface was measured to false-red on, and
    # nothing said so. Use `None` to remove a pattern; there is no third meaning.
    empty = sorted(k for k, v in overrides.items() if v is not None and not v)
    if empty:
        raise ValueError(
            f"empty override(s) for {empty} - an empty pattern silently falls back to the content "
            f"one. Use None to REMOVE a pattern from this surface")
    return [(label, re.compile(overrides.get(label, rx) or rx, re.IGNORECASE))
            for label, rx in PATTERNS if overrides.get(label, rx) is not None]


def _exempt(label: str, rel_path: str) -> bool:
    """Is this PATTERN excused on this PATH? See PATH_EXEMPT — normally nothing is."""
    return any(lb == label and rx.search(rel_path) for lb, rx in _PATH_EXEMPT_RX)


def _ascii(s: str) -> str:
    """A form safe to `print` from a git hook.

    ⚠️ STDOUT IS A PIPE THERE, so Python falls back to the locale encoding (cp1252 on these
    workstations) and a non-ASCII character raises UnicodeEncodeError mid-verdict. The file's own
    rule is "ASCII ONLY in anything PRINTED" — but that was applied to the hand-written messages
    and not to the interpolated PATHS, so one tracked file with an accented or CJK name crashed
    the scan just as it was listing the finding. Exit stayed 1, so it failed closed; the operator
    simply never got to see WHICH file leaked.
    """
    return s.encode("ascii", "backslashreplace").decode("ascii")


def tracked_files(root: Path) -> list[Path]:
    """Every tracked path, DEDUPLICATED.

    `git ls-files -z` lists an unmerged (conflicted) path once per index stage, so a file in a
    conflict was scanned two or three times and every finding in it printed as many times — which
    reads as several separate leaks. `dict.fromkeys` keeps first-seen order.
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True,
                         check=True, timeout=_GIT_TIMEOUT_S)
    # `errors="replace"`, matching `gitlinks` and `_git`. This decode was STRICT while both its
    # peers were lenient, so a tracked path that is not valid UTF-8 — ordinary on Linux, where a
    # filename is bytes — crashed the scan with a traceback instead of producing a verdict.
    seen = dict.fromkeys(p for p in out.stdout.decode("utf-8", errors="replace").split("\0") if p)
    return [root / p for p in seen]


def _permitted_spans(line: str) -> list[tuple[int, int]]:
    """(start, end) of every span on this line that is allowed to look like a hit."""
    spans = [m.span() for m in _ALLOW_SPAN_RX.finditer(line)]
    for lit in ALLOW_LITERALS:
        start = line.find(lit)
        while start != -1:
            spans.append((start, start + len(lit)))
            start = line.find(lit, start + 1)
    return spans


def _containment_index(spans: list[tuple[int, int]]) -> tuple[list[int], list[int]]:
    """Sorted starts plus a running maximum of the ends, for O(log n) containment queries.

    ⚠️ SPANS ARE NOT MERGED, and that is deliberate. Merging overlapping spans into one wider
    interval would let TWO permitted tokens jointly cover a leak that NEITHER of them contains —
    a fresh amnesty hole opened in the name of speed. Containment must always be satisfied by a
    SINGLE span, so the query stays "is there one span with start <= match.start AND end >=
    match.end", which the running maximum answers exactly.
    """
    ordered = sorted(spans)
    starts = [s for s, _ in ordered]
    best: list[int] = []
    run = -1
    for _, end in ordered:
        run = max(run, end)
        best.append(run)
    return starts, best


def _contained(starts: list[int], best: list[int], lo: int, hi: int) -> bool:
    """Is [lo, hi) entirely inside at least one permitted span?"""
    i = bisect.bisect_right(starts, lo) - 1
    return i >= 0 and best[i] >= hi


# ⛔ `_neutralize` — which DELETED the permitted spans from a line so the remainder could be
# matched — used to live here and is deliberately GONE. It WAS the amnesty bug: deletion is what
# let a permitted token consume its neighbours, because whatever a span could grow over, it could
# erase. Do not reintroduce a delete-then-match pass, and do not keep one "so the allowlist can be
# inspected directly" — the sibling repo kept exactly that and it was dead code carrying the shape
# of the defect in a safety-critical file. What replaced its one test is
# `test_a_permitted_span_still_suppresses_what_it_actually_contains`, which pins the containment
# rule itself.


def _lines(text: str) -> list[str]:
    r"""Split text into lines the way GIT does — on `\n`, and on nothing else.

    ⛔⛔ NEVER `str.splitlines()` HERE. Python splits on NINE more characters than git does —
    `\r`, `\x0b`, `\x0c`, `\x1c`, `\x1d`, `\x1e`, ``, ` `, ` ` — and git treats
    every one of them as ordinary CONTENT inside a diff line. In `parse_diff` that was a
    FAIL-OPEN, not a cosmetic difference:

        an added line `+harmless\rAGENT=<rfc1918-addr>` arrives from git as ONE line. `splitlines`
        cut it into `+harmless` and `AGENT=<rfc1918-addr>` — and the remainder no longer starts
        with `+`, so it fell past the added-line branch and was DROPPED. Not scanned, not
        reported, not counted: both scans exited 0 with the value still recoverable from history.

    Reachable by accident, not just by intent: any file with mixed line endings carries `\r`, and
    `\x0c` (form feed) is an ordinary page-break character in real source.

    It also let file CONTENT forge a `diff --git` header — the one branch that is checked
    unconditionally, because in git's real grammar a content line always carries a `+`/`-`/space
    prefix and so can never look like one. A forged header re-attributed the following lines to
    any path the attacker chose, INCLUDING this guard's own path, which is the single file both
    scans skip. That is the self-exemption theft #241 exists to close, reached by a different
    route.

    ⚠️ SHARED BY BOTH SCANS ON PURPOSE. `scan_text` had the same call. There it was not a bypass —
    every fragment still got scanned — but it made the two scans disagree about what line a
    finding is ON, and a guard whose two halves count lines differently is telling one of them
    wrong. One definition, so they cannot drift apart again.
    """
    return text.split("\n")


def scan_text(text: str, compiled: list[tuple[str, re.Pattern[str]]],
              rel_path: str = "") -> list[tuple[int, str, str]]:
    """(line number, label, matched text) for every hit in `text`.

    ⭐ MATCH FIRST, THEN SUPPRESS ONLY WHAT IS FULLY CONTAINED IN A PERMITTED SPAN. This is
    deliberately NOT the "delete the allowed spans, then match the remainder" form this guard used
    to have, because that form is FAIL-OPEN and was demonstrated to be so on this very file: with
    the old unbounded leading-label group, `AGENT=<rfc1918-addr>.example.com` had the whole host
    deleted and scanned CLEAN, and 6 of the 7 shapes fell to the same one-suffix trick.

    Containment is the property that actually expresses the intent. A documented RFC5737 address
    or an `example.com` host may not itself be reported; it may NOT grant amnesty to anything
    outside its own extent. A hit that merely TOUCHES a permitted span still counts.

    `rel_path` is only consulted for PATH_EXEMPT and defaults to "" so the function stays callable
    on a bare string — which is what `selftest` and most of the tests do.

    ⚠️ THE ALLOWLIST IS NOT INERT, and an earlier version of this docstring claimed it was ("no
    deny pattern can match inside an RFC5737 address or an `example.*` suffix"). That enumerated
    two of the three span families and quietly omitted the third: `.env[.<qualifier>].local`
    CONTAINS a `private lan domain` match by construction, so suppression genuinely fires there.
    The false half of that claim is exactly what made an unbounded version of that span look
    harmless. The carve-out is exercised; treat it as live machinery, not decoration.

    ⚡ The containment query is indexed (`_containment_index`) rather than a linear scan over the
    spans. The naive form is O(matches x spans) per pattern, and a single 176 KB minified line
    dense with permitted tokens took 31 s — on the push path, which is the hang this guard's own
    no-silent-hangs rule forbids.
    """
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(_lines(text), start=1):
        permitted = _permitted_spans(line)
        starts, best = _containment_index(permitted) if permitted else ([], [])
        for label, rx in compiled:
            if rel_path and _exempt(label, rel_path):
                continue
            if not permitted:
                # ⚡ THE ORDINARY CASE — no allowlist token on this line, so nothing can be
                # suppressed and the first match is the answer. Kept as its own branch because the
                # general path below is O(matches x spans): with `finditer` no longer stopping at
                # the first match, a line of many suppressible matches goes quadratic. Real lines
                # almost never carry a permitted token, so this branch is what actually runs.
                m = rx.search(line)
                if m:
                    hits.append((lineno, label, m.group(0)))
                continue
            for m in rx.finditer(line):
                if _contained(starts, best, m.start(), m.end()):
                    continue  # entirely inside something the allowlist permits
                hits.append((lineno, label, m.group(0)))
                break
    return hits


@functools.cache
def path_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """The denylist as it applies to a PATH. See PATH_PATTERN_OVERRIDES for why it differs."""
    return tuple(compile_for(PATH_PATTERN_OVERRIDES))


@functools.cache
def message_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """The denylist as it applies to a commit MESSAGE or a TAG object."""
    return tuple(compile_for(MESSAGE_PATTERN_OVERRIDES))


def scan_path(rel_path: str) -> list[tuple[str, str]]:
    """(label, matched text) for every hit in a repo-relative PATH STRING.

    ⛔ TAKES NO `compiled` ARGUMENT, deliberately. Handing this function the CONTENT pattern set is
    exactly the defect that made it redden `zshrc.local`, `db/migrations/<uuid>.sql` and
    `docs/mnt/user/notes.md`, and an optional parameter is an invitation to reintroduce it. There
    is one correct pattern set for a path; it is derived from `PATTERNS` by `compile_for`, so a
    pattern added tomorrow reaches this surface unless someone writes an override and says why.

    ⭐ A PATH IS PUBLISHED CONTENT (keystone#20). Neither scan looked at one: the tree scan read
    `git ls-files` only to open the file, and the range scan read a path only to decide what to
    skip. So a tracked `192.168.77.77.conf`, or a `docs/<host>.lan-runbook/` directory, reached a
    public remote with BOTH scans exiting 0 on perfectly clean file contents. A filename is
    rendered on every GitHub file listing and in every clone; it is not metadata the guard may
    look past.

    ⚠️ THE SAME PATTERNS AND THE SAME ALLOWLIST, via `scan_text`, rather than a second matcher.
    Two matchers would drift, and the allow-spans are needed here for real: `.env.local` is a
    FILENAME containing a `private lan domain` match by construction, and `config.local.yml` and
    `.claude/settings.local.json` are ordinary paths in ordinary repositories. They pass for the
    same structural reason they pass as file content, which is the property worth having — one
    definition of "permitted", not two that agree today.

    ⛔ NO SELF-EXEMPTION AND NO SUFFIX SKIP HERE. `_is_self` and `_binary_suffix` are about
    READING a file; nothing about `icons/` or about this scanner's own source makes its NAME
    exempt. A path is a short string, so scanning every one of them costs nothing.

    KNOWN LIMITS OF THIS SURFACE, stated rather than implied (see `_MUST_PASS_PATHS` /
    `_MUST_FAIL_PATHS`, which `selftest` runs):
      * `<host>.lan-runbook/` is NOT matched — the `.lan` bound rejects a following hyphen, and
        loosening it would fire on ordinary hyphenated names.
      * A four-component VERSION directory in the `10.` range (`docs/10.0.0.1/`) IS matched, and
        that is a known over-match: `10.0.0.1` as a version and as an address are the same string.
        `192.168.*` and `172.16-31.*` have no such collision. Rename the directory, or add the
        literal to `ALLOW_LITERALS`.
      * `uuid` and `unraid pool path` do not apply here at all — see PATH_PATTERN_OVERRIDES.

    Line numbers are dropped: a path is one line by construction, and reporting `:1` on every
    finding would read as a line inside the file, which is exactly what this is not.
    """
    return [(label, match)
            for _, label, match in scan_text(rel_path, list(path_patterns()), rel_path)]


# --------------------------------------------------------------- scanning the COMMITS
# The empty tree. `git diff <EMPTY_TREE> <sha>` is how the very first commit in a repository is
# diffed, since it has no parent to diff against and `<sha>^` simply fails there.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

_HUNK_RX = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# ⛔⛔ EVERY `git diff` THIS FILE RUNS GOES THROUGH BOTH OF THESE. A guard whose VERDICT depends on
# the developer's git config is not a guard: the same commit passes on one machine and fails on
# another, and the machine it passes on is whichever one happens to have the setting.
#
# ⚠️ PIN THE CLASS, NOT THE MEMBERS YOU HAPPEN TO KNOW. Each entry below is a setting that changes
# what `git diff` EMITS, which is the only input this scanner has:
#   * `diff.external` / `GIT_EXTERNAL_DIFF` - difftastic's and delta's documented setup is exactly
#     `[diff] external = difft`. git then emits NEITHER a `diff --git` header NOR a `+++ ` line;
#     the output is just the driver's stdout, so `parse_diff` set no path, dropped every line, and
#     returned empty-added AND empty-unscannable. A REAL LEAK EXITED 0 (issue #250). `--no-ext-diff`
#     is the fix; `-c diff.external=` is NOT - git tries to spawn the empty string and dies 128.
#   * `--no-textconv` - the `.gitattributes` `diff=<driver>`/`textconv` route to the same place.
#   * `core.quotePath` - with the default `true`, a non-ASCII path is emitted C-quoted
#     (`"a/caf\303\251.bin"`), which no path derivation here can reconstruct (issue #241).
#   * `diff.noprefix`, `diff.srcPrefix`, `diff.dstPrefix`, `diff.mnemonicPrefix` - all four change
#     the `a/`/`b/` prefixes the header path is derived FROM. `mnemonicPrefix` was measured not to
#     apply to a two-tree diff, and is pinned anyway: a pin costs one argument, whereas relying on
#     that measurement means re-verifying it against every future git release.
# The one setting deliberately NOT pinned here is `log.showSignature`, which affects `git log`
# rather than `git diff` - a separate call site, and a separate issue.
_DIFF_CONFIG = ("-c", "core.quotePath=false",
                "-c", "diff.noprefix=false",
                "-c", "diff.srcPrefix=a/",
                "-c", "diff.dstPrefix=b/",
                "-c", "diff.mnemonicPrefix=false")
_DIFF_FLAGS = ("--unified=0", "--no-color", "--no-renames", "--no-ext-diff", "--no-textconv")

# ------------------------------------------------------------------ unattributable MARKERS
# Some things `parse_diff` must report are NOT paths: "I could not read this file" needs a file,
# but "I could not work out WHICH file this is" has none to give.
#
# ⛔ THE SIGIL IS A NUL, and that is the whole reason this is safe. A git path can never contain
# one — git itself uses NUL to delimit paths in `ls-files -z` — so a marker is distinguishable
# from every real path by construction, rather than by a prefix convention a real filename could
# collide with. That matters because a marker must NEVER reach a `git diff -- <pathspec>`: it
# would match nothing, git would exit 0 with empty output, and the run would clear a diff it
# never read. Checked by `_is_marker`, stripped for display by `_shown`.
#
# ⚠️ A MARKER MUST NAME SOMETHING THE OPERATOR CAN ACT ON. A fail-closed report that says only
# "unparsed" names no file and cannot be acted on, so `_unparsed_header` quotes the raw header.
_MARKER_SIGIL = "\x00"

_NO_HEADER = _MARKER_SIGIL + (
    "<the whole diff carried hunks but no 'diff --git' header, so not one of its lines could be "
    "attributed to a file - is an external diff driver (diff.external / GIT_EXTERNAL_DIFF) "
    "configured?>")


def _unparsed_header(tail: str) -> str:
    # ⚠️ THE REMEDIATION TRAVELS WITH THE MARKER. This is not a file that failed to decode, so the
    # generic "add a binary suffix / commit it as UTF-8" advice printed for unreadable files is
    # wrong for it in both halves, and an operator following it stays red with nothing to change.
    # git C-quotes any path containing a quote, a backslash or a control byte REGARDLESS of
    # `core.quotePath` (which governs non-ASCII only), and such a header cannot be reconstructed.
    return _MARKER_SIGIL + (
        f"<a 'diff --git' header this cannot resolve to one path, so nothing in that diff was "
        f"attributed or scanned: {tail!r} - if the path contains a quote, a backslash or a "
        f"control character, git C-quotes it and it cannot be read here: rename it, or review "
        f"that commit by hand before pushing>")


def _is_marker(p: str) -> bool:
    return p.startswith(_MARKER_SIGIL)


def _shown(p: str) -> str:
    return p[len(_MARKER_SIGIL):] if _is_marker(p) else p


def _header_path(tail: str) -> str:
    """The ONE path out of a `diff --git a/X b/X` tail, byte-exact — or a marker.

    ⛔ NOT `tail.split(" b/", 1)`, which is what this used to be and which two ordinary shapes
    defeat (issue #241). A path CONTAINING ` b/` splits at the wrong place —
    `a/x b/y.lock b/x b/y.lock` yielded `y.lock b/x b/y.lock`, a path that does not exist — and a
    path git QUOTES has no ` b/` at all, so the whole tail became the path. Neither is a cosmetic
    mis-naming: for a BINARY file there is no `+++ ` line to correct it afterwards, the re-diff
    that follows matches nothing, git exits 0 empty, and the file is reported CLEAN rather than
    unscannable. The fail-closed posture inverted.

    ⭐ RECONSTRUCTION, NOT SPLITTING. `--no-renames` and the pinned prefixes (`_DIFF_CONFIG`) mean
    both sides are the SAME path, so `a/{p} b/{p}` determines `p` by LENGTH — no delimiter has to
    be guessed. The reconstruction is then verified byte-for-byte, which is what makes a tail this
    cannot explain fail CLOSED instead of producing a wrong-but-plausible answer.
    """
    if len(tail) < 5 or (len(tail) - 5) % 2:
        return _unparsed_header(tail)
    n = (len(tail) - 5) // 2
    p = tail[2:2 + n]
    return p if tail == f"a/{p} b/{p}" else _unparsed_header(tail)


def _plus_path(rest: str) -> str:
    """The path out of a `+++ ` line — stripping AT MOST ONE trailing TAB, never whitespace.

    ⛔ `.strip()` HERE WAS A FULL BYPASS (issue #241). git appends a TAB after a path that carries
    trailing whitespace — measured: `+++ b/notes.png \\t` — so stripping removed the tab AND the
    meaningful trailing space with it. `notes.png ` became `notes.png`, whose suffix is SKIPPED,
    and every added line in the real file was dropped. The worse half: `<SELF_PATH> ` stripped to
    exactly `SELF_PATH` and inherited this guard's single self-exemption, so an arbitrary file
    could be handed the one exemption in the file.
    """
    return rest[:-1] if rest.endswith("\t") else rest


def _git(root: Path, *args: str) -> str:
    """Run git and return stdout as text.

    `errors="replace"` and not strict: a diff can carry a byte sequence that is not valid UTF-8
    (a latin-1 file, a half-binary blob git decided to treat as text), and a scanner that DIES on
    the content it is meant to inspect fails open — the push goes through unscanned. Replacement
    characters cannot create a false hit: every pattern is ASCII.
    """
    out = subprocess.run(["git", *args], cwd=root, capture_output=True, check=True,
                         timeout=_GIT_TIMEOUT_S)
    return out.stdout.decode("utf-8", errors="replace")


def resolves(root: Path, rev: str) -> bool:
    """Does `rev` name a commit that exists in THIS clone?

    `^{commit}` and not a bare `--verify`: a 40-hex string that is not an object still parses as
    a SHA-1 for some git subcommands, and the question here is whether the object is present.
    """
    try:
        _git(root, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}")
        return True
    except subprocess.CalledProcessError:
        return False


def commits_in_range(root: Path, rev_range: str) -> list[str]:
    """The commits the range covers, oldest first. Empty is a legitimate answer (nothing new).

    Whitespace-split so multi-token selectors work as well as `A..B` — the pre-push hook needs
    `<sha> --not --remotes` for a BRAND-NEW branch, which has no remote counterpart to diff
    against and would otherwise be scanned as zero commits.
    """
    return list(reversed(_git(root, "rev-list", *rev_range.split()).split()))


def widen_unreachable_base(root: Path, rev_range: str) -> tuple[str, str | None]:
    """Return (range to actually scan, message) — repairing an `A..B` whose BASE is gone.

    ⚠️ THE CASE THIS EXISTS FOR. After a history rewrite, the next push sends
    `github.event.before` = the OLD tip, which no longer exists in the rewritten history. The
    workflow already handles the null sha (branch creation) but not an unreachable one, so the
    range failed to resolve and the guard exited 1 — a RED on the first push after every rewrite,
    for a repository that is in fact clean. Treating it as a hard failure trains people to
    ignore this check, which is worse than any single leak it might catch.

    ⭐ THE REPAIR IS DELIBERATELY FAIL-CLOSED: an unreachable base widens the scan to ALL of the
    head's history, which is strictly MORE than `A..B` would have covered, never less. It is the
    same answer the workflow's branch-creation branch already gives, for the same reason —
    "I could not work out a range" must never be a pass.

    What is NOT repaired, on purpose:
      * an unresolvable HEAD — there is nothing to widen TO, so it stays an error;
      * a multi-token selector (`<sha> --not --remotes`) — it has no single base to lose;
      * a typo'd base that happens to be a valid ref — indistinguishable from a real one, and
        widening is safe anyway.
    A base that DOES resolve is left completely alone, so the ordinary path is untouched.
    """
    if ".." not in rev_range or " " in rev_range.strip():
        return rev_range, None
    base, sep, head = rev_range.partition("...")
    if not sep:
        base, _, head = rev_range.partition("..")
    if not base or not head or resolves(root, base):
        return rev_range, None
    if not resolves(root, head):
        # Nothing to widen to. Fall through unchanged so the normal failure path reports it.
        return rev_range, None
    # ⚠️ ASCII ONLY in anything PRINTED. Every other message in this file is ASCII too, and that
    # is not an accident: this runs from a pre-push hook, where stdout is a pipe and Python falls
    # back to the locale encoding (cp1252 on these workstations). A stray non-ASCII character
    # there raises UnicodeEncodeError and the guard dies mid-verdict. Comments and docstrings are
    # free to use whatever they like; the print path is not.
    return head, (
        f"BASE {base!r} is not present in this clone - almost certainly a history rewrite, "
        f"where the previous tip no longer exists.\n"
        f"WIDENING to {head!r}: every commit reachable from the head is scanned. That is MORE "
        f"than {rev_range!r} asked for, never less, so this is not a way to pass unscanned.")


class ParsedDiff(NamedTuple):
    """What one commit's diff contributes: lines we CAN scan, and paths we cannot."""

    added: list[tuple[str, int, str]]   # (path, new-file line number, content)
    unscannable: list[str]              # paths git served as binary — see `parse_diff`


def parse_diff(diff: str) -> ParsedDiff:
    """Parse a `--unified=0` diff into added lines plus the paths that could not be read.

    Pure, so the parsing can be tested without a repository — the git side of this is two
    commands and the fiddly part is all here.

    Added lines are the whole question: a value enters the permanent history at the commit that
    added it, and stays readable there no matter what a later commit does. Removed lines are
    ignored for the same reason — deleting a leak is exactly what makes it invisible to the tree
    scan while leaving it in the history.

    ⚠️ THE PATH COMES FROM `diff --git`, NOT FROM `+++ `. Keying on the `--- `/`+++ ` header pair
    looks sufficient and is not: a REMOVED content line beginning `-- ` renders as `--- …` and an
    ADDED one beginning `++ ` renders as `+++ …`, so an ordinary pair of prose lines is
    byte-identical to a file header. Proven against real git output — an added line reading
    `++ logo.png` made every following line attribute to `logo.png`, which `scan_added` then
    skipped as a binary asset, and a genuine leak on the next line scanned CLEAN. `in_hunk`
    closes it structurally: once `@@` has been seen, nothing until the next `diff --git` can be a
    header, which is exactly git's own grammar.

    ⚠️ A BINARY DIFF CARRIES NO LINES, so it must be REPORTED rather than passed over. The tree
    scan already refuses to vouch for what it cannot decode; a UTF-16 file is invisible to both
    scans otherwise, which is a hole rather than a limit.

    ⛔⛔ "GIT CALLED IT BINARY" AND "IT CONTAINS A NUL" ARE DIFFERENT QUESTIONS, and treating them
    as one was issue #242. git flags a blob binary only if a NUL falls in its FIRST 8000 BYTES, so
    a BOM-less UTF-16LE payload further in gets an ordinary TEXT diff — and UTF-16LE of ASCII
    (`A\\x00G\\x00E\\x00…`) is *valid UTF-8*, so the decode succeeds, the content is NUL-separated
    so no pattern can match, and the file was counted as SCANNED. The guard reported it read and
    cleared when it had read nothing of the sort.

    So the NUL check lives HERE, on the added lines of the PRIMARY diff, and not only in the
    `--text` re-diff fallback (which runs only for what git ALREADY refused — the case that was
    already being reported). One rule covers UTF-16LE, UTF-16BE and UTF-32 without enumerating
    encodings, because a NUL does not occur in legitimate UTF-8 source.

    ⚠️ SCOPED, not a strength claim: this catches content whose NULs land in ADDED lines. Ordinary
    non-ASCII UTF-8 — accents, CJK — has no NUL and is still scanned normally, which is the
    direction that matters most, since a guard that reddens correct work gets switched off.

    ⛔⛔ "I SAW A DIFF BUT NO HEADER I RECOGNISE" IS A THIRD ANSWER, and its absence was a
    fail-open (issue #250). With an external diff driver configured, git emits neither a
    `diff --git` header nor a `+++ ` line — just the driver's stdout. `path` therefore stayed
    empty, `if not path: continue` dropped every line INCLUDING the added ones the driver had
    printed under a `@@` header, and this returned empty-added AND empty-unscannable, which reads
    identically to "there was nothing to scan". A real leak exited 0.

    `--no-ext-diff` (see `_DIFF_CONFIG`) stops git doing that in the first place. This state is
    the second half, and it is the more important half: the flag closes the ONE member of the
    class that is known, whereas a parser that cannot say "unrecognised" turns every FUTURE member
    into a silent pass too. Reported loud, and fail-closed, exactly like an undecodable file.
    """
    added: list[tuple[str, int, str]] = []
    unscannable: list[str] = []
    path = ""
    lineno = 0
    in_hunk = False
    deleted = False
    saw_header = False
    saw_hunk = False
    nul_seen: set[str] = set()
    for line in _lines(diff):
        if line.startswith("diff --git "):
            saw_header = True
            path = _header_path(line[len("diff --git "):])
            if _is_marker(path):
                # Nothing after this can be trusted to belong to a file, so say so HERE rather
                # than waiting for a `Binary files` line that a text file never produces.
                unscannable.append(path)
            in_hunk = False
            deleted = False
            continue
        if not in_hunk and line.startswith("+++ "):
            raw = _plus_path(line[4:])
            if raw == "/dev/null":
                # `/dev/null` on the new side means the file was DELETED — a deletion adds nothing.
                path = ""
            elif not saw_header:
                # ⭐ THE HEADER WINS WHEN THERE IS ONE, and this branch is the FALLBACK — not the
                # override it used to be. Two sources for one fact is how #241's second half
                # happened: `diff --git` had already produced the path byte-exactly and the very
                # next line threw it away. Making `+++ ` authoritative-only-when-alone leaves the
                # two unable to disagree.
                #
                # ⛔ AND IT IS NOT REDUNDANT — do NOT delete it. It is the ONLY path source when
                # git emits no `diff --git` header, and removing it is what turned #241 into #250
                # in the sibling repo (tape#241, "what the next attempt should know", item 1).
                path = raw.removeprefix("b/")
            continue
        if line.startswith("deleted file mode "):
            # `GIT binary patch` (only emitted under `--binary`, which this never passes) carries
            # no `/dev/null` marker, so the deletion has to be recognised from the mode line.
            # `parse_diff` is a documented pure function with its own tests; it should not depend
            # on its one caller's flags for correctness.
            deleted = True
            # ⛔ A DELETION PUBLISHES NOTHING — INCLUDING AN UNPARSEABLE ONE. The header marker is
            # appended at the `diff --git` line, which is read BEFORE this one, so the `deleted`
            # guard on the `Binary files ` branch below never got the chance to apply to it. A
            # commit that merely DELETES a file whose path git C-quotes therefore failed the range
            # scan — and the advice the marker carries ("rename it, or review that commit by
            # hand") cannot be acted on for a commit already written. A red with no way out is
            # exactly what the comment on that branch forbids for the parseable case, and there is
            # no reason the unparseable case should be treated worse: either way the deletion adds
            # nothing to the history.
            #
            # Popped by identity from the tail: nothing else appends between the header line and
            # this one, so the marker for THIS file section is necessarily the last entry.
            if _is_marker(path) and unscannable and unscannable[-1] == path:
                unscannable.pop()
                path = ""
            continue
        if not in_hunk and (line.startswith("Binary files ") or line == "GIT binary patch"):
            # ⚠️ A DELETION PUBLISHES NOTHING. `Binary files a/x and /dev/null differ` is a
            # removal, and reporting it would make every commit that deletes a binary — or the
            # delete half of a binary rename — fail forever. That is the same trap the
            # removed-lines rule above avoids: a guard that punishes cleanup gets switched off.
            # `not _is_marker(path)`: an unparsed header was already reported at the `diff --git`
            # line, and reporting it again here made a binary file with such a path appear TWICE,
            # inflating the "in N added file(s)" count the operator reads.
            if (path and not deleted and not _is_marker(path)
                    and not line.rstrip().endswith("and /dev/null differ")):
                unscannable.append(path)
            path = ""
            continue
        # ⛔ THE HUNK IS RECOGNISED BEFORE THE PATH GATE, and the order is the whole point. With
        # `if not path: continue` first, a diff with no header dropped its `@@` lines here too, so
        # `saw_hunk` could never become true on precisely the diffs it exists to detect — the
        # check would have been dead code that looked like a fix (`_worker-common`: a mutation
        # must PERFORM the failure, and a guard whose pattern matches 0x is broken, not passing).
        # Setting `in_hunk` unconditionally also strengthens the header/prose ambiguity above:
        # once `@@` has been seen, nothing until the next `diff --git` is treated as a header,
        # which is git's own grammar rather than a heuristic.
        m = _HUNK_RX.match(line)
        if m:
            lineno = int(m.group(1))
            in_hunk = True
            saw_hunk = True
            continue
        if not path:
            continue
        if in_hunk and line.startswith("+"):
            content = line[1:]
            if "\x00" in content:
                # ⛔ THE PLAIN PATH, NOT A MARKER, and the difference was a false red nobody could
                # clear. Markers are steered AROUND `_skipped` on purpose — `_NO_HEADER` and an
                # unparsed header have no path, so no suffix rule can apply to them. This case is
                # the opposite: it HAS a path, and wrapping it made it inherit that exemption. A
                # `.pdf` whose first NUL falls past git's 8000-byte window was then SKIPPED by the
                # tree scan and REFUSED by the range scan — the two scans disagreeing about which
                # files count, which is exactly what `_skipped` exists to prevent. Worse, the
                # printed remedy was inert: the suffix was already in SKIP_SUFFIXES, so the
                # operator was told to do the thing they had already done, with no way to pass.
                #
                # Reported ONCE per path, not once per line: a UTF-16 file is NUL-bearing on
                # every line, and a report repeated a thousand times reads as a thousand faults.
                if path not in nul_seen:
                    nul_seen.add(path)
                    # An unparsed-header marker is ALREADY in `unscannable` from the header line.
                    if not _is_marker(path):
                        unscannable.append(path)
            else:
                added.append((path, lineno, content))
            lineno += 1
    if saw_hunk and not saw_header:
        # ⚠️ The `if not path: continue` above already dropped every one of those hunk lines. This
        # is what stops that from being SILENT. Checked after the loop rather than per line so it
        # is reported once for the diff, not once per hunk.
        unscannable.append(_NO_HEADER)
    return ParsedDiff(added, unscannable)


def first_parent(root: Path, sha: str) -> str:
    """`<sha>^`, or the empty tree for a root commit.

    Extracted because TWO callers need it — `added_lines` and `changed_paths` — and they must
    diff against the same base or they would disagree about what one commit contributed.
    """
    try:
        return _git(root, "rev-parse", "--verify", f"{sha}^").strip() or _EMPTY_TREE
    except subprocess.CalledProcessError:
        return _EMPTY_TREE  # the root commit has no parent


def changed_paths(root: Path, *revs: str) -> list[str]:
    """New-side paths a diff introduces or modifies — DELETIONS EXCLUDED.

    ⭐ `--name-only -z`, NOT the paths `parse_diff` derives from the `diff --git` header. That is
    two sources for one fact only in appearance: they answer different questions and one of them
    is byte-exact. `-z` makes git emit RAW, NUL-terminated paths, so the entire C-quoting class
    (issue #37 — a path holding a quote, a backslash or a control byte, which the header form
    cannot reconstruct at all) simply does not arise here. A path scan built on the header would
    have inherited that gap on day one.

    ⛔ `--diff-filter=d` — A DELETION PUBLISHES NOTHING NEW, and its path was already published by
    whichever commit added it. Reporting it would make the commit that REMOVES a badly-named file
    fail forever, which is the same trap the removed-lines rule avoids: a guard that punishes
    cleanup gets switched off. Modified paths ARE included: re-scanning a path is the safe
    direction to be wrong in, and a guard may re-scan where it may not skip.
    """
    out = _git(root, *_DIFF_CONFIG, "diff", *_DIFF_FLAGS, "--name-only", "-z",
               "--diff-filter=d", *revs)
    return [p for p in out.split("\0") if p]


def scan_paths(shown: str, paths: list[str]) -> list[str]:
    """Findings in a set of PATH strings, labelled so they read as paths and not as file lines."""
    return [f"{shown}<path> {rel}: {label}: {match!r}"
            for rel in paths for label, match in scan_path(rel)]


def added_lines(root: Path, sha: str) -> ParsedDiff:
    """`parse_diff` over this commit's diff against its FIRST parent, then RESOLVE the paths git
    served as binary by actually reading them.

    First parent, so a merge reports what its side brought in rather than nothing at all —
    `git diff <merge> <merge>` is empty, and a merge would otherwise always scan clean.

    ⚠️ "git called it binary" IS NOT "it is unreadable". git says that for a `.gitattributes`
    `-diff`/`binary` attribute too — standard practice for a generated lockfile — and for UTF-8
    text carrying one stray NUL. Reporting those as "not scanned" reddens ordinary work with
    remediation advice that is simply wrong ("commit the file as UTF-8 text" — it already IS).

    So each flagged path is RE-DIFFED with `--text`, which forces git to emit real hunks, and the
    result is decoded strictly. Decodes → those are the lines this commit added, scanned like any
    other. Fails → the content genuinely is not UTF-8, and it stays unscannable so the run
    refuses to clear it.

    ⚠️ RE-DIFF, NOT RE-READ. The first version of this fetched the whole blob and scanned every
    line of it. That costs 15.9 s per commit on a 24 MB `-diff` blob, so a 200-commit PR touching
    a large lockfile extrapolates to ~50 MINUTES, on the push path and in CI — the hang the
    no-silent-hangs directive forbids, re-entering through a door opened while closing another.
    It also attributed a PRE-EXISTING leak to whichever innocent commit last touched the file.
    Both faults are the same mistake: the unit of this scan is what a commit ADDED, and reading
    the blob threw that away. The `--text` diff restores it, and bounds the cost by the change
    rather than by the file.

    Skipped suffixes are filtered FIRST, so a 100 MB `.png` is never fetched only to be discarded.
    """
    parent = first_parent(root, sha)
    parsed = parse_diff(_git(root, *_DIFF_CONFIG, "diff", *_DIFF_FLAGS, parent, sha))
    return resolve_unscannable(root, parsed, (parent, sha))


def resolve_unscannable(root: Path, parsed: ParsedDiff, revs: tuple[str, ...]) -> ParsedDiff:
    """Re-diff with `--text` whatever git served as binary, and keep whatever still cannot be read.

    ⭐ EXTRACTED SO `--staged` GETS IT TOO. This was inline in `added_lines`, so the range scan
    resolved a `.gitattributes`-marked lockfile correctly while the new index scan would have
    REPORTED the same ordinary file as "not scanned, so NOT CLEARED" and blocked the commit — a
    false red on correct work, in the layer a developer meets most often. One implementation, both
    callers; `revs` is what distinguishes them (`(parent, sha)` versus `("--cached", base)`).
    """
    # ⚠️ A MARKER IS NOT A PATH and must never reach a pathspec — it is already the final answer.
    # Feeding one to the re-diff below would match nothing, git would exit 0 with empty output,
    # and the run would clear a diff it could not read at all.
    unattributable = [p for p in parsed.unscannable if _is_marker(p)]
    parsed = ParsedDiff(parsed.added, [p for p in parsed.unscannable if not _is_marker(p)])
    # ⚠️ `root` IS LOAD-BEARING HERE, and leaving it off was a live fail-open. `_skipped(p)` with
    # no root falls back to the SELF_PATH CONSTANT instead of resolving this file from `__file__`
    # — the exact defect `_self_rel_path` exists to remove, one line away from its own fix. The
    # consequence is worse than a mismatched exemption: a path wrongly judged "skipped" drops out
    # of `pending`, and an empty `pending` returns early DISCARDING the whole unscannable list, so
    # a blob the scan never read is reported as clean on the push path.
    pending = [p for p in parsed.unscannable if not _skipped(p, root)]
    if not pending:
        return ParsedDiff(parsed.added, unattributable)

    added = list(parsed.added)
    still_unreadable: list[str] = list(unattributable)
    for path in pending:
        try:
            # ⚠️ `:(literal)` — WITHOUT IT THE PATH IS A GLOB. A perfectly ordinary asset name
            # containing `[`, `*` or `?` is pathspec MAGIC, so `-- 'sprite[1].bin'` matches
            # nothing, and "matches nothing" is the exact state that reads as clean below.
            raw = subprocess.run(
                ["git", *_DIFF_CONFIG, "diff", *_DIFF_FLAGS, "--text",
                 *revs, "--", f":(literal){path}"],
                cwd=root, capture_output=True, check=True, timeout=_GIT_TIMEOUT_S).stdout
            text = raw.decode("utf-8")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, UnicodeDecodeError):
            # Cannot read it → cannot vouch for it. Fail closed, exactly like the tree scan.
            still_unreadable.append(path)
            continue
        # ⛔⛔ AN EMPTY RE-DIFF IS NOT A CLEAN ONE — it is the shape every path mis-parse ends in
        # (issue #241). git exits 0 with no output when the pathspec matched nothing, the strict
        # decode of "" succeeds, `parse_diff("")` adds nothing, and the path silently drops out of
        # `still_unreadable` — so a file the scan never opened is reported CLEAN. This is the
        # check that closes the CLASS rather than the two header shapes that were reported: any
        # future way of deriving a wrong path ends here, fail-closed, instead of exiting 0.
        #
        # ⚠️ EMPTY OUTPUT, not "no ADDED lines". A binary whose change only REMOVES bytes yields
        # real hunks with no `+` line, and reddening that would be a false red on correct work.
        if not text.strip():
            still_unreadable.append(path)
            continue
        sub = parse_diff(text)
        # ⛔ THE RE-DIFF'S OWN VERDICT WAS BEING DISCARDED — only `.added` was read. So a path git
        # flagged binary, re-diffed with `--text`, that came back NUL-bearing (a BOM-less UTF-16
        # blob) contributed no added lines, raised nothing, and quietly dropped out of
        # `still_unreadable` — reported CLEAN. Whatever the re-diff could not read stays unread.
        if sub.unscannable:
            still_unreadable += sub.unscannable
            continue
        added += sub.added
    return ParsedDiff(added, still_unreadable)


def scan_added(sha: str, parsed: ParsedDiff, compiled: list[tuple[str, re.Pattern[str]]],
               root: Path | None = None) -> tuple[list[str], list[str]]:
    """(findings, unscannable) for one commit.

    ⚠️ THIS NO LONGER SKIPS "the same files the tree scan skips", and the old summary line saying
    so was left in place while the paragraph below contradicted it. What the two scans share is the
    RULE — content decides, not the filename — not an identical file list: the tree scan asks
    `_looks_binary` of bytes it has, and this asks nothing, because git already refused to serve a
    text diff for a blob it judged binary. Same answer, reached from what each scan can see.

    ⭐ `_is_self`, NOT `_skipped`, ON THE ADDED LINES (keystone#22). git only serves a TEXT diff
    for a blob it judged to be text, so lines arriving here have already passed a content check
    stricter than any filename — and dropping them because the file is called `.pdf` is the
    filename-trust half of #22 on the range side. The tree scan's twin is `_looks_binary`. The
    `unscannable` list below still uses `_skipped`: that one is about not complaining that a
    genuine `.png` could not be decoded, which is a question about assets, not about text.
    """
    findings: list[str] = []
    for path, lineno, content in parsed.added:
        if _is_self(path, root):
            continue
        for _, label, match in scan_text(content, compiled, path):
            findings.append(f"{sha[:10]} {_shown(path)}:{lineno}: {label}: {match!r}")
    # `_shown` strips the marker sigil for display. `_skipped` is still asked of the RAW value: a
    # marker is not a path, so it matches no skip suffix and no self-exemption, which is the
    # answer wanted — an unattributable diff is never skipped.
    return findings, [f"{sha[:10]} {_shown(p)}" for p in parsed.unscannable
                      if not _skipped(p, root)]


# ------------------------------------------------------- the COMMIT'S OWN identity
# ⭐ A COMMIT PUBLISHES MORE THAN ITS DIFF. Author and committer name/email are part of the
# commit object: they are pushed, they are rendered on every GitHub commit page, and NO scan in
# this fleet looked at them. A repo whose files are spotless still publishes the operator's
# personal address on every commit if a local `user.email` — or a one-off `-c user.email=…`
# override, which beats the global config silently — was wrong.
#
# This is caught HERE and nowhere else. The tree scan cannot see it (it is not file content) and
# no later commit can remove it: fixing it means rewriting the commits, exactly like a leaked
# line, which is why it belongs on the same push-path gate rather than in a checklist.
#
# The full denylist is applied rather than the freemail pattern alone. A name field is free text
# — it has held a hostname and a path before now — and running every shape costs one regex pass
# over four short strings.
_IDENT_FORMAT = "%an%x00%ae%x00%cn%x00%ce"
_IDENT_FIELDS = ("author name", "author email", "committer name", "committer email")


def commit_identity(root: Path, sha: str) -> list[tuple[str, str]]:
    """[(field, value)] for this commit's author and committer identity.

    NUL-separated rather than newline-separated: a name may legitimately contain almost anything
    except NUL, and a newline in a name would otherwise shift every following field by one.

    ⛔⛔ `--no-show-signature` IS LOAD-BEARING, and leaving it off was a live false red. With
    `log.showSignature=true` — an ordinary setting for anyone who signs commits — git prepends the
    signature-VERIFICATION block to stdout, ahead of the `--format` output:

        Good "git" signature with ED25519 key SHA256:...
        Unable to open allowed keys file "C:/Users/<name>/.ssh/allowed_signers": ...
        No principal matched.
        <author name>\0<author email>\0<committer name>\0<committer email>

    That text is glued onto the FIRST field, so a spotless commit was reported as publishing a
    Windows profile path (from the key path in the warning) or a freemail address (from the
    signer principal) in its "author name". The field count is still 4, so the guard below cannot
    catch it — the response has the right SHAPE and the wrong CONTENT.

    Both directions are wrong, which is why this is not cosmetic. The false red is unfixable by
    the operator: the message says to rewrite history and correct `user.email`, and neither
    touches the real cause. And in the other direction the fabricated text BURIES a genuine leak
    in the author name among the signature output.

    This is the same class as `_DIFF_CONFIG` — a setting on the developer's machine deciding the
    guard's verdict — one call site over. It was previously dismissed here as "a separate call
    site, and a separate issue" (#35) on a measurement taken against UNSIGNED commits, where the
    setting is genuinely inert. Signing is what makes it bite.
    """
    raw = _git(root, "show", "-s", "--no-show-signature", f"--format={_IDENT_FORMAT}", sha)
    parts = raw.rstrip("\n").split("\0")
    if len(parts) != len(_IDENT_FIELDS):
        # ⚠️ NOT a silent `zip` truncation. `zip` stops at the shorter side, so a malformed or
        # short git response would quietly scan only the fields that happened to arrive and
        # report the commit clean on the rest — a guard reporting on less than it claims. If the
        # response is not the shape asked for, the identity is UNVERIFIED, and unverified fails
        # closed like everything else here.
        raise ValueError(
            f"commit {sha[:10]}: expected {len(_IDENT_FIELDS)} identity fields from git, got "
            f"{len(parts)} - refusing to report on an identity that was not fully read")
    return [(field, value) for field, value in zip(_IDENT_FIELDS, parts) if value]


def scan_identity(sha: str, ident: list[tuple[str, str]],
                  compiled: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    """Findings in a commit's own author/committer metadata."""
    findings: list[str] = []
    for field, value in ident:
        for _, label, match in scan_text(value, compiled):
            findings.append(f"{sha[:10]} <{field}>: {label}: {match!r}")
    return findings


# ------------------------------------------------------- the COMMIT'S OWN message, and TAGS
# ⭐ A COMMIT PUBLISHES ITS MESSAGE (keystone#21 / #39). The range scan read the commit's IDENTITY
# and its DIFF and nothing else, so a leak written into a commit BODY — "deployed from <host>.lan,
# policy <uuid>" — was published by `git push`, rendered on the commit page, and both scans exited
# 0. It is exactly as permanent as a leaked line and costs the same history rewrite to remove,
# which is why it belongs on this gate rather than in a checklist.
#
# ⛔ A SEPARATE CALL, NOT A FIFTH `_IDENT_FORMAT` FIELD. `%B` is multi-line, and `commit_identity`
# fails CLOSED on a field count that is not exactly four — appending it would make every commit
# with a two-line message raise "refusing to report on an identity that was not fully read".
# The identity read stays NUL-delimited and exact; the message is read on its own.


_MESSAGE_FORMAT = "%B"


def commit_message(root: Path, sha: str) -> str:
    """This commit's full message, subject and body.

    `--no-show-signature` for the same reason `commit_identity` passes it: with
    `log.showSignature=true` git prepends the signature-verification block to stdout, and that
    text carries a key path (`C:/Users/<name>/.ssh/allowed_signers`) and a signer principal —
    a FABRICATED finding attributed to a commit message that is in fact clean.
    """
    return _git(root, "show", "-s", "--no-show-signature", f"--format={_MESSAGE_FORMAT}", sha)


def scan_message(sha: str, message: str) -> list[str]:
    """Findings in a commit's own message. Uses the MESSAGE pattern set — see `scan_path` for why
    a surface that is not file content does not get the content bounds."""
    return [f"{sha[:10]} <commit message>:{lineno}: {label}: {match!r}"
            for lineno, label, match in scan_text(message, list(message_patterns()))]


def _rev_tokens(rev_range: str) -> list[str]:
    """The REVISION names in a range selector, with the options and the `..`/`...` stripped.

    `<sha> --not --remotes`, `A..B` and `A...B` are all forms this is handed, and only the
    revision halves name an object.
    """
    out: list[str] = []
    for token in rev_range.split():
        if token.startswith("-"):
            continue
        for part in re.split(r"\.{2,3}", token):
            if part:
                out.append(part)
    return out


def refs_being_published(root: Path, rev_range: str) -> list[tuple[str, str, str]]:
    """[(kind, sha, text)] for every TAG OBJECT the range NAMES.

    ⛔⛔ REF **NAMES** ARE NOT SCANNED HERE, AND THAT IS A DELIBERATE REVERT RATHER THAN AN
    OVERSIGHT — see issue #49. A version of this also matched every `refs/`-bearing token as a
    NAME, and a `--ref-name` argument carried the name a push publishes (which is `<remote_ref>`,
    not `<local_ref>`, because a refspec may rename). It was withdrawn after producing a production
    bypass in three consecutive verification rounds:

      1. scoping tags by commit-reachability missed the ordinary tag push entirely;
      2. reading only the LOCAL ref let `git push origin clean:refs/tags/<host>.lan` publish a
         leaking name under a clean one;
      3. and once both were repaired, `refs/heads/<host>.lan-deploy` (one trailing hyphen), a
         UUID-named ref and a `.local`-named ref all still published clean, while the LOCAL name
         produced a false red whose printed remedy was inert against it.

    Ref-name scanning is not in this package's acceptance criteria — those name a leak in a commit
    MESSAGE and in an annotated tag MESSAGE — and an addition that cannot converge inside a package
    does not get to hold it. What survives is what the criteria actually ask for, and it is
    unaffected by the revert: an annotated tag's OBJECT, which carries its name, its tagger and its
    message together.

    ⚠️ THE GAP THAT REMAINS, stated rather than left to be discovered: a LIGHTWEIGHT tag has no
    object, so a leak that exists only as a ref name — `refs/tags/<host>.lan` — is NOT caught by
    any layer. #49 carries the measured repros and the failed designs.

    ⛔⛔ SCOPED BY WHAT IS BEING PUSHED, NOT BY COMMIT REACHABILITY — and that correction is the
    whole of this function. The first version asked "does this tag point at a commit in the range",
    which is wrong in BOTH directions and was measured wrong in both:

      * IT MISSED EVERY REAL TAG PUSH. Cutting a tag at an already-pushed commit is this repo's
        documented release workflow, and there the range resolves to ZERO commits — so the tag
        object, its name, its tagger and its message all reached the remote with the guard printing
        `no internal info added across 0 commit(s)` and exiting 0. A tag pointing at a BLOB was
        skipped too, even with the commit in range, because `%(*objectname)` peels to the blob.
      * IT REDDENED PUSHES THAT PUBLISH NO TAG AT ALL. `git push origin main` does not send tags —
        only `--tags`/`--follow-tags` do — so a purely local scratch tag on an in-range commit
        blocked an ordinary branch push, and the remediation the guard printed ("rewrite the
        offending commits") was not even the right fix (`git tag -d` was).

    Both disappear if the question is "what refs is this push sending", which the caller already
    knows: `.githooks/pre-push` is handed `<local_ref>` on stdin, and CI has `GITHUB_REF`. Both now
    put the REF in the range they pass, so the range NAMES the tag and this reads it.

    ⭐ NESTED TAGS ARE WALKED. `git tag -a outer -m … inner` produces a chain, and reading only the
    outermost object left the inner one — with its own message — published and unread once its own
    ref was deleted. The loop follows `object <sha>` while the type is still `tag`, so every level
    is read rather than the one that happens to be on top.
    """
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for token in _rev_tokens(rev_range):
        try:
            sha = _git(root, "rev-parse", "--verify", "-q", token).strip()
        except subprocess.CalledProcessError:
            continue
        # Walk the tag chain. Bounded by `seen` so a malformed cycle cannot loop forever.
        while sha and sha not in seen:
            seen.add(sha)
            try:
                if _git(root, "cat-file", "-t", sha).strip() != "tag":
                    break
                body = _git(root, "cat-file", "tag", sha)
            except subprocess.CalledProcessError:
                break
            found.append(("tag object", sha, body))
            nxt = ""
            for line in _lines(body):
                if line.startswith("object "):
                    nxt = line[len("object "):].strip()
                    break
                if not line.strip():
                    break   # the header ended; there is no `object` line to follow
            sha = nxt
    return found


def scan_tags(tags: list[tuple[str, str, str]]) -> list[str]:
    """Findings in tag objects. Uses the MESSAGE pattern set."""
    return [f"{sha[:10]} <{kind}>:{lineno}: {label}: {match!r}"
            for kind, sha, body in tags
            for lineno, label, match in scan_text(body, list(message_patterns()))]


class RangeResult(NamedTuple):
    findings: list[str]
    unscannable: list[str]
    commits: int


def scan_range(root: Path, rev_range: str,
               compiled: list[tuple[str, re.Pattern[str]]]) -> RangeResult:
    """Everything each commit in `rev_range` PUBLISHES: its added lines, the PATHS it introduces,
    its author/committer identity, its MESSAGE, and any tag pointing at it.

    ⭐ FIVE SURFACES, not one. Pushing publishes a commit OBJECT, and every field of it is
    permanent and rendered on the commit page. Reading only the diff — which is all this did —
    left three of the five unread: a leak in a filename (keystone#20), a leak in the commit
    message (keystone#21 / #39), and a leak in a tag's name, tagger or message.

    ⚠️ "PERMANENT" IS TRUE OF FOUR OF THE FIVE, and the fifth is worth naming rather than rounding
    up. A commit's diff, its paths, its identity and its message can only be removed by rewriting
    history. A TAG is a ref: `git tag -d` plus `git push --delete` removes it with no rewrite. It
    is on this gate because it is still PUBLISHED and still cheap to catch first, not because it is
    equally unrecoverable.
    """
    findings: list[str] = []
    unscannable: list[str] = []
    commits = commits_in_range(root, rev_range)
    for sha in commits:
        parent = first_parent(root, sha)
        hits, blind = scan_added(sha, added_lines(root, sha), compiled, root)
        findings += hits
        findings += scan_paths(f"{sha[:10]} ", changed_paths(root, parent, sha))
        findings += scan_identity(sha, commit_identity(root, sha), compiled)
        findings += scan_message(sha, commit_message(root, sha))
        unscannable += blind
    findings += scan_tags(refs_being_published(root, rev_range))
    return RangeResult(findings, unscannable, len(commits))


# Deny cases are SYNTHETIC by construction — reserved documentation ranges (RFC5737), the top of
# the CGNAT block, `.invalid` hosts (RFC 2606) and invented labels. None of them is a value from
# any real estate, and none needs to be: these patterns match SHAPES, so a synthetic instance of
# the shape exercises them exactly as well as a real one would.
_MUST_FAIL: list[tuple[str, str]] = [
    ("private IPv4 (RFC1918)", "DATABASE_URL=postgresql://u:p@192.168.77.77:5432/db"),
    ("private IPv4 (RFC1918)", "allowlist = '10.99.99.0/24'"),
    ("private IPv4 (RFC1918)", "peer 172.31.255.254 is not allowed"),
    # The bare-prose form: a sentence-final period must not hide it.
    ("private IPv4 (RFC1918)", "The database lives at 192.168.77.77."),
    # 100.127.255.254 is the very top of the CGNAT block rather than any host anywhere.
    ("cgnat address", "agent reachable on 100.127.255.254:9999"),
    ("tailnet name", "https://host-a.tailnet-example.ts.net/"),
    ("private lan domain", "AGENT_URL=http://host-a.lan:9999/mcp"),
    ("private lan domain", "ping printer-b.local"),
    ("unraid pool path", 'Default="/mnt/apps/appdata/svc/data"'),
    ("unraid pool path", "Run from: cd /mnt/user/appdata/svc"),
    # The canonical Unraid ARRAY mount. The plural `disks` did not cover `/mnt/disk1`.
    ("unraid pool path", "mover moved it to /mnt/disk3/appdata/svc"),
    ("personal mail address", "_UA = 'Research someone@gmail.invalid'"),
    # Provider domains that the old alternation NAMED and still let through: `proton` required a
    # dot straight after it, so Proton's original domain walked past the pattern written for it.
    ("personal mail address", "owner = 'someone@protonmail.invalid'"),
    ("personal mail address", "cc: someone@live.invalid"),
    ("uuid (access policy / tenant id)", "access_app = '11111111-2222-3333-4444-555555555555'"),
    # `<KEY>_<uuid>` — an ordinary config idiom, and `\b` does not hold after `_`, so the old
    # bound let the single likeliest spelling of this shape through.
    ("uuid (access policy / tenant id)",
     "app_id_11111111-2222-3333-4444-555555555555 = 1"),
    ("windows profile path", r"cache = 'C:\Users\operator\AppData\Local\svc'"),
    # The same path as it is actually written in source: escaped inside a string literal.
    ("windows profile path", r'{"cache": "D:\\Users\\operator\\AppData\\Roaming"}'),
    ("windows profile path", "posix-separated too: C:/Users/operator/Documents"),
]

_MUST_PASS: list[str] = [
    "host='198.51.100.5'  # RFC5737",
    "peer 192.0.2.1 rejected",
    "doc range 203.0.113.9 is fine",
    "APP_BASE_URL=https://svc.your-domain.example",
    "ntfy example: https://ntfy.example.com",
    "see https://github.com/texasdaddy/tape/issues/31",
    "icon: https://raw.githubusercontent.com/texasdaddy/unraid-templates/main/icons/tape.png",
    "version 10.16.2 of the driver",              # not an address
    "the 172.315 basis-point spread",             # not an address
    "bind 0.0.0.0:5000",
    "copy .env.example and fill it in",
    # ⭐ The `.local` FILENAME cases the `(?![\w.-])` bound exists for. All three occur in
    # ordinary repositories, and `[\w-]+\.local\b` reddens CI on every one of them.
    'cp .claude/settings.local.json.tmpl .claude/settings.local.json',
    "load_config('config.local.yml')",
    "docker compose -f compose.local.yaml up",
    # ⭐ Pins the deliberate `.internal` exclusion documented on the pattern above. This exact
    # string is a real fixture in one of the fleet repos; if someone re-adds `.internal` to the
    # denylist, this case fails and says why rather than reddening that repo's CI mysteriously.
    'DATABASE_URL=postgresql://u:p@db.internal:5432/app',
    # near-misses for the five patterns that had none. Each is a shape that LOOKS like its
    # pattern and must not fire, which is what fails if that pattern is ever widened.
    "cgnat neighbours 100.63.255.254 and 100.128.0.1 are outside the range",
    'import helper from "./net.ts" then re-export',
    "store it under /mnt/POOL/appdata/runner-REPO/docker",
    "contact noreply@example.com or support@github.com",
    "pinned action SHA 3d3c42e5aac5ba805825da76410c181273ba90b1",
    # The reason ALLOW_SPANS suppresses a SPAN and not the LINE: a permitted token must not
    # grant amnesty to a real leak sharing the line with it.
    # ⭐ `.env.local` is a FILENAME, not a `.local` host. Without its allow-span this is a
    # standing false positive on a line that appears in ordinary repositories.
    "cp .env.local .env",
    "vite reads .env.local and .env.production.local",
    # ⭐ The `windows profile path` near-misses. Every one of these occurs in ordinary Windows
    # instructions, and the pattern is anchored to `Users` precisely so none of them fires.
    r"clone it to C:\dev\unraid-templates",
    r"gh lives at C:\Program Files\GitHub CLI\gh.exe",
    r"export to D:\data\report.csv",
    # The two documented placeholder spellings: both fail to match structurally, because `<` and
    # `%` are outside the account-name class rather than being special-cased.
    r"put it in C:\Users\<you>\AppData\Local\app",
    r"set CACHE=C:\Users\%USERNAME%\AppData\Local\app",
    # The Windows built-in profiles, which name nobody.
    r"shared drop: C:\Users\Public\Documents\shared.csv",
    r"template hive: C:\Users\Default\NTUSER.DAT",
]

_MUST_FAIL_COMBINED = "# see example.com; real host is host-a.private-example.lan"

# ...nor one it is written flush AGAINST. ⭐ THESE ARE THE AMNESTY BYPASS, and they are the
# reason `_neutralize` is gone. Under the old delete-then-match design with an unbounded leading
# LABEL group, the allow-span matched the LEAK TOO and deleting it left nothing to find — ONE
# suffix defeated every dotted pattern at once. Measured on this file before the fix: 6 of 6.
# `scan_text` now matches first and suppresses only what a permitted span CONTAINS, and every
# case below must still be caught.
_MUST_FAIL_ADJACENT: list[tuple[str, str]] = [
    ("private IPv4 (RFC1918)", "AGENT=192.168.77.77.example.com"),
    ("cgnat address", "agent on 100.127.255.254.example.com"),
    ("tailnet name", "https://host-a.tailnet-example.ts.net.example.net/"),
    ("unraid pool path", "/mnt/user.example.com"),
    ("personal mail address", "mail someone@gmail.invalid.example.org"),
    # ⭐ THE UUID CASES. A UUID is hyphen-separated hex — one `[\w-]` run — so it is the ONE shape
    # that fits inside even a "single label, bounded" allow-span, and it survived that repair in
    # the sibling repo while every other shape was closed. It is also the shape the pattern exists
    # for. Pinned in every masking form that worked.
    ("uuid (access policy / tenant id)",
     "CF_APP=11111111-2222-3333-4444-555555555555.example.com"),
    ("uuid (access policy / tenant id)", "TENANT=11111111-2222-3333-4444-555555555555.example"),
    ("uuid (access policy / tenant id)",
     "POLICY=11111111-2222-3333-4444-555555555555-your-domain.example"),
    # ⭐ The `.env.<qualifier>.local` span, whose middle segment is an ENUMERATION for exactly
    # this reason: as a character class it swallowed first a UUID and then an ordinary LAN host.
    ("uuid (access policy / tenant id)",
     "TENANT_FILE=.env.11111111-2222-3333-4444-555555555555.local"),
    ("private lan domain", "cp .env.host-a.local .env"),
    ("private lan domain", "cp .env.printer-b.local .env"),
    # ⭐⭐ THE `.env...local` SPAN WITH NO LEFT BOUND — the bypass this file shipped and an
    # adversarial round found. The span started at a bare `\.`, so it matched the TAIL of a
    # hostname as readily as a filename and suppressed the `.local` host inside it. All twelve
    # qualifier forms were exploitable; two are pinned here and the general property is asserted
    # by `test_a_permitted_span_may_only_start_mid_word_if_it_cannot_contain_a_leak`.
    ("private lan domain", "AGENT_URL=http://nas-a.env.local:9999/mcp"),
    ("private lan domain", "PRINTER=printer-b.env.production.local"),
]
# ⚠️ `private lan domain` has no `<host>.lan.example.com` case, and that is correct rather than an
# omission: its right bound rejects any following label, so `host-a.lan.example.com` is not a
# `.lan` host at all — it is a host UNDER example.com. Requiring `.lan`/`.local` to be the final
# label is the same rule that keeps `settings.local.json` out. Recorded because it was considered,
# so nobody re-adds it as a "missing" case.
# ⚠️ `windows profile path` has none either: it contains no dot-separated host, so there is no
# domain suffix a permitted span could be appended to.


# ⭐⭐ THE PATH AND MESSAGE SURFACES GET THEIR OWN CORPORA, and they are not decoration: those two
# surfaces run a DIFFERENT pattern set (PATH_PATTERN_OVERRIDES / MESSAGE_PATTERN_OVERRIDES), so
# `_MUST_PASS` — a list of CONTENT lines — proves nothing about either. Without these, the whole
# new surface had zero coverage in `selftest`, which is the layer `pre-commit` and CI actually run,
# and every false red below was found by an adversarial sweep instead of by the guard's own tests.
#
# ⚠️ THE MUST-PASS HALF IS THE POINT. Each entry is a path that occurs in ordinary repositories and
# that DID redden before the overrides existed. They are the regression test for the overrides.
_MUST_PASS_PATHS: list[str] = [
    "README.md",
    "templates/service.xml",
    "icons/service.png",
    ".github/workflows/ci.yml",
    ".env.example",
    # The `.local` FILENAME family. The content bound rejects a following LABEL, so
    # `settings.local.json` was always fine — but a segment-TERMINAL `.local` satisfies it, and
    # these are the standard machine-local-override convention. Every one blocked a commit.
    ".env.local",
    "home/zshrc.local",
    "home/vimrc.local",
    "home/gitconfig.local",
    "home/tmux.conf.local",
    "Makefile.local",
    "packages/app.local/index.js",
    ".claude/settings.local.json",
    "src/config.local.yml",
    "compose.local.yaml",
    "nginx/conf.d/site.local.conf",
    # A UUID FILENAME is a naming convention, not a tenant id.
    "db/migrations/11111111-2222-3333-4444-555555555555.sql",
    "test/fixtures/11111111-2222-3333-4444-555555555555.json",
    # A repo-relative path can never BE an absolute pool path.
    "docs/mnt/user/notes.md",
    "tests/fixtures/mnt/cache/appdata/svc/config.yml",
    "mnt-user-backup.sh",
    # Ordinary near-misses that must stay clean.
    "docs/1.2.3/index.html",
    "src/net.ts",
    "locales/en-US/messages.json",
    "vendor/example.com/pkg/x.go",
]

_MUST_FAIL_PATHS: list[tuple[str, str]] = [
    ("private IPv4 (RFC1918)", "192.168.77.77.conf"),
    ("private IPv4 (RFC1918)", "docs/192.168.77.77/index.md"),
    ("cgnat address", "100.127.255.254.conf"),
    # ⭐ `.lan` KEEPS ITS BOUND LOOSE ON THIS SURFACE, so a following EXTENSION does not hide it.
    # `.local` cannot have the same treatment: `zshrc.local` above is the same string shape.
    ("private lan domain", "docs/host-a.lan/readme.md"),
    ("private lan domain", "host-a.lan.conf"),
    # ⚠️ AN ACCEPTED OVER-MATCH, pinned so it is a decision rather than a surprise. In file CONTENT
    # `host-a.lan.example.com` is correctly NOT a `.lan` host (a further label follows); the path
    # surface's looser bound reports it. Same trade as the four-part version directory: the bound
    # exists so an ordinary EXTENSION cannot hide a host, and that is worth one contrived miss.
    ("private lan domain", "docs/host-a.lan.example.com/x.md"),
    ("tailnet name", "docs/host-a.tailnet-example.ts.net.md"),
    ("personal mail address", "inbox/someone@gmail.invalid.txt"),
    ("windows profile path", "docs/C:/Users/operator/notes.md"),
]

# Ordinary commit messages. Every one of these blocked a PUSH — and a message cannot be edited
# without rewriting history, so a false red here is far more expensive than one on a file.
_MUST_PASS_MESSAGES: list[str] = [
    "Merge pull request #1 from texasdaddy/feature",
    'Revert "add the thing"\n\nThis reverts commit 3d3c42e5aac5ba805825da76410c181273ba90b1.',
    "cherry-pick the fix\n\n(cherry picked from commit 3d3c42e5aac5ba805825da76410c181273ba90b1)",
    "Rename config.local to config.defaults",
    "chore: load tmux.conf.local from the user home",
    "feat: support Avahi hostnames like printer.local on the LAN",
    "test: add a fixture for policy id 11111111-2222-3333-4444-555555555555",
    r"ci: fix path handling for C:\Users\runneradmin\AppData\Local\Temp",
    "docs: the example host is svc.your-domain.example at 198.51.100.5",
    # ⭐ A message that merely NAMES a repo path. The content pattern needs only a leading slash,
    # which any nested path supplies, so these blocked the push while the file and the filename
    # both passed — the regression test for the `_ABS_POOL_PATH` left bound.
    "docs: add docs/mnt/user/notes.md",
    "test: fixtures now live under tests/fixtures/mnt/cache/appdata/svc",
    "fix: close the issue at https://github.com/texasdaddy/tape/issues/31\n\n"
    "Co-Authored-By: Someone <1234+someone@users.noreply.github.com>",
]

_MUST_FAIL_MESSAGES: list[tuple[str, str]] = [
    ("private lan domain", "release cut on host-a.lan"),
    # The sentence-final form the CONTENT pattern is documented to miss; the message bound catches
    # it, because with `.local` gone there is nothing for a trailing-dot rejection to protect.
    ("private lan domain", "deployed from host-a.lan."),
    ("private IPv4 (RFC1918)", "point the agent at 192.168.77.77 for now"),
    ("unraid pool path", "moved appdata to /mnt/user/appdata/svc"),
    # ⭐ THE URL FORMS. Excluding `/` from the left bound (to stop `docs/mnt/user/...`) also
    # excluded these, which ARE absolute pool paths — the character in front is merely a slash.
    ("unraid pool path", "docs: see file:///mnt/user/appdata/svc for the runbook"),
    ("unraid pool path", "docs: see //mnt/user/appdata/svc"),
    ("personal mail address", "reported by someone@gmail.invalid"),
    (r"windows profile path", r"cache now lives in C:\Users\operator\AppData\Local"),
]


def selftest(compiled: list[tuple[str, re.Pattern[str]]]) -> int:
    bad: list[str] = []
    exercised: set[str] = set()
    for want_label, s in _MUST_FAIL:
        hits = scan_text(s, compiled)
        if not hits:
            bad.append(f"SHOULD have been caught ({want_label}): {s!r}")
            continue
        labels = {label for _, label, _ in hits}
        exercised |= labels
        if want_label not in labels:
            bad.append(f"caught by the WRONG pattern ({sorted(labels)}, "
                       f"wanted {want_label}): {s!r}")
    for s in _MUST_PASS:
        hits = scan_text(s, compiled)
        if hits:
            bad.append(f"false positive on {s!r}: {hits}")
    if not scan_text(_MUST_FAIL_COMBINED, compiled):
        bad.append("an allowed token on the same line hid a real leak: "
                   f"{_MUST_FAIL_COMBINED!r}")
    for want_label, s in _MUST_FAIL_ADJACENT:
        labels = {label for _, label, _ in scan_text(s, compiled)}
        exercised |= labels
        if want_label not in labels:
            bad.append(f"AMNESTY BYPASS - a permitted token written flush against a leak hid "
                       f"it (wanted {want_label}, got {sorted(labels)}): {s!r}")
    missing = {label for label, _ in PATTERNS} - exercised
    if missing:
        bad.append(f"pattern(s) with no deny case, so nothing proves they still work: "
                   f"{sorted(missing)}")

    # ⭐ THE TWO SURFACES THAT DO NOT RUN THIS PATTERN SET. `_MUST_FAIL`/`_MUST_PASS` above are
    # CONTENT lines and prove nothing about a path or a message, which run the override sets. Both
    # directions are checked for both surfaces, because the must-PASS half is what pins the
    # overrides and the must-FAIL half is what stops an override quietly gutting the surface.
    for rel in _MUST_PASS_PATHS:
        hits = scan_path(rel)
        if hits:
            bad.append(f"false positive on the PATH {rel!r}: {hits}")
    for want_label, rel in _MUST_FAIL_PATHS:
        labels = {label for label, _ in scan_path(rel)}
        if want_label not in labels:
            bad.append(f"PATH not caught (wanted {want_label}, got {sorted(labels)}): {rel!r}")
    for msg in _MUST_PASS_MESSAGES:
        hits = scan_text(msg, list(message_patterns()))
        if hits:
            bad.append(f"false positive on the MESSAGE {msg!r}: {hits}")
    for want_label, msg in _MUST_FAIL_MESSAGES:
        labels = {label for _, label, _ in scan_text(msg, list(message_patterns()))}
        if want_label not in labels:
            bad.append(f"MESSAGE not caught (wanted {want_label}, got {sorted(labels)}): {msg!r}")

    if bad:
        print("SELFTEST FAILED:")
        for b in bad:
            print("  " + b)
        return 1
    print(f"selftest ok: {len(_MUST_FAIL) + len(_MUST_FAIL_ADJACENT) + 1} denied shapes caught "
          f"across {len(PATTERNS)} patterns, {len(_MUST_PASS)} allowed shapes passed")
    print(f"  paths: {len(_MUST_FAIL_PATHS)} denied, {len(_MUST_PASS_PATHS)} allowed; "
          f"messages: {len(_MUST_FAIL_MESSAGES)} denied, {len(_MUST_PASS_MESSAGES)} allowed")
    return 0


# ------------------------------------------------------------------------ the command line
USAGE = """usage: check_no_internal_info.py
           [--selftest | --staged | --range <revision-range>] [--repo <path>]

  (no arguments)              scan the tracked working TREE
  --selftest                  prove the denylist patterns still bite
  --staged                    scan what is in the INDEX - what `git commit` would record
  --range <A..B>              scan the lines ADDED by every commit in the range
  --range=<A..B>              the same, joined form
  --repo <path>               the repository to scan (default: the one containing $PWD)
  -h, --help                  this message

exit codes: 0 clean | 1 a finding, something unreadable, or a range git could not resolve
            2 a usage error (this message)"""


class UsageError(Exception):
    """An argument this scanner does not understand.

    Its own exception type so `main` cannot accidentally swallow it with the git failures, and so
    a caller embedding this module gets something catchable rather than a bare exit.
    """


class Args(NamedTuple):
    selftest: bool
    rev_range: str | None
    repo: str | None
    help: bool
    staged: bool = False


def _value_for(flag: str, argv: list[str], i: int) -> str:
    """The argument after `flag`, rejecting a missing one and a following flag alike.

    `--range --selftest` would otherwise swallow the flag as a revision range and fail later with
    "could not scan '--selftest'" — fail-closed, but it reports the wrong problem, and a contract
    headed "every argument is parsed" should not have a hole where an argument is silently
    consumed as a value.
    """
    if i + 1 >= len(argv):
        raise UsageError(f"{flag} needs a value")
    value = argv[i + 1]
    if value.startswith("-"):
        raise UsageError(f"{flag} takes a value, not another flag (got {value!r})")
    return value


def parse_args(argv: list[str]) -> Args:
    """Parse the command line STRICTLY.

    Every token is either recognised or an error. There is no fall-through, because the
    fall-through WAS the bug: a version of this scanner tested `"--selftest" in argv` and IGNORED
    everything else, so the documented `--range origin/dev..HEAD` scan was a TREE scan run twice,
    reporting clean regardless of the commits being pushed. An unrecognised argument leaving the
    caller believing a commit-range scan had run is the one failure mode a guard cannot have.
    """
    selftest = False
    rev_range: str | None = None
    repo: str | None = None
    want_help = False
    staged = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            want_help = True
        elif arg == "--selftest":
            selftest = True
        elif arg == "--staged":
            staged = True
        elif arg == "--range":
            rev_range = _value_for("--range", argv, i)
            i += 1
        elif arg.startswith("--range="):
            rev_range = arg[len("--range="):]
            if not rev_range:
                raise UsageError("--range needs a revision range, e.g. --range origin/main..HEAD")
            # The same check the spaced form makes. Leaving it out of this branch was the "fixed
            # the instance, not the class" shape all over again, one `elif` away from the fix.
            if rev_range.startswith("-"):
                raise UsageError(
                    f"--range takes a revision range, not another flag (got {rev_range!r})")
        elif arg == "--repo":
            repo = _value_for("--repo", argv, i)
            i += 1
        elif arg.startswith("--repo="):
            repo = arg[len("--repo="):]
            if not repo:
                raise UsageError("--repo needs a path")
        else:
            # Includes a BARE revision range. `check_no_internal_info.py origin/main..HEAD` reads
            # like it would scan the range and would otherwise have run a tree scan and called it
            # clean — the same silent substitution this whole change exists to remove.
            raise UsageError(f"unrecognised argument: {arg!r}")
        i += 1
    # ⛔ EVERY PAIR, not just the pair that existed first. Three scan modes make three pairs, and
    # writing only the one that used to be there is the "fixed the instance, not the class" shape
    # this file has been bitten by repeatedly. Each combination would otherwise run ONE of the two
    # scans the caller asked for and report success — the silent substitution `parse_args` exists
    # to make impossible.
    if sum((selftest, rev_range is not None, staged)) > 1:
        raise UsageError("--selftest, --range and --staged do different things; "
                         "run them one at a time")
    if want_help and (selftest or staged or rev_range is not None):
        # `--range A..B --help` printed the usage and exited 0 — an accepted argument combination
        # that substitutes "no scan" for a scan and reports success. That is the same shape as the
        # ignored-argument defect, just harder to reach, so it is an error rather than a silent
        # preference.
        raise UsageError("--help does not combine with a scan; run one or the other")
    return Args(selftest, rev_range, repo, want_help, staged)


def _scan_tree(root: Path, compiled: list[tuple[str, re.Pattern[str]]]) -> int:
    findings: list[str] = []
    undecodable: list[str] = []
    scanned = 0
    # Submodule entries, resolved once. See `gitlinks` for why they cannot be told apart from a
    # staged-but-deleted file by catching exceptions.
    submodules = gitlinks(root)
    tracked = tracked_files(root)
    for path in tracked:
        rel = path.relative_to(root).as_posix()
        # ⭐ THE PATH IS SCANNED FIRST, AND FOR EVERY TRACKED FILE — before any skip, any
        # submodule check and any read (keystone#20). A filename is published content: it needs no
        # decoding, it cannot be binary, and neither the self-exemption nor the binary-suffix hint
        # has anything to say about it. Doing it here rather than inside the read means an
        # `icons/<host>.lan.png` and a `docs/<addr>/` directory are caught even though the file
        # itself is never opened. (`<host>.lan.png` is caught because the PATH surface runs a
        # LOOSER `.lan` bound than file content does — see PATH_PATTERN_OVERRIDES. It was NOT
        # caught when this comment first asserted it; the claim came before the behaviour.)
        findings += [f"{rel}: <path>: {label}: {match!r}"
                     for label, match in scan_path(rel)]
        if _is_self(rel, root):
            continue
        # ⛔ A GITLINK IS SKIPPED ONLY IF IT IS NOT A READABLE FILE. Trusting the index mode alone
        # would be a bypass: `git update-index --cacheinfo 160000,<sha>,<path>` marks ANY tracked
        # path as a gitlink without a real submodule, a `.gitmodules` entry, or even a written
        # blob — and the file goes on sitting in the worktree, readable and published. A genuine
        # submodule is a DIRECTORY (checked out) or absent (plain clone); either way it is not a
        # file, so this keeps real submodules skipped while a spoofed entry falls through and is
        # scanned like anything else. Fail-closed on ambiguity.
        if rel in submodules and not path.is_file():
            continue
        raw: bytes | None = None
        text: str | None = None
        absent_from_worktree = False
        try:
            # ⚠️ READ BYTES AND DECODE — do NOT use `read_text`, which applies UNIVERSAL NEWLINES
            # and translates `\r\n` and a lone `\r` to `\n` BEFORE any splitting. That left
            # `_lines` powerless here: the tree scan counted a CR-bearing file's lines differently
            # from the range scan (raw bytes from git) and from `staged_blob` (which reads a
            # blob and translates nothing) — three reads, three answers, for one file. Reading
            # bytes makes all three agree instead of leaving one definition of "line" in `_lines`
            # and a second hidden in the reader.
            #
            # ⚠️ NOT `read_text(newline="")` either: `Path.read_text` only accepts `newline` from
            # Python 3.13, and CI pins 3.12, so that spelling raises TypeError on every file.
            #
            # ⭐ THE DECODE MOVED OUT of this `try` when the suffix stopped being trusted: the
            # answer to "it did not decode" now depends on WHICH file it is, and a bare
            # `except UnicodeDecodeError` here could not ask that. `read_bytes` still raises the
            # same FileNotFoundError and OSError subclasses (a checked-out submodule is still
            # IsADirectoryError / PermissionError), which is all this `try` ever needed to cover.
            raw = path.read_bytes()
        except FileNotFoundError:
            # ⚠️ NOT SILENT, and not `continue`. A tracked file missing from the worktree is
            # STAGED-BUT-DELETED (or mid-rebase): its content is still in the INDEX and still goes
            # into the commit, so passing over it quietly let a staged leak commit clean — exit 0,
            # and the file not even counted. Real submodules are filtered out above, so this
            # branch does not fire on an ordinary layout.
            #
            # SCAN THE STAGED BLOB rather than merely refusing to vouch for it: that reports a
            # staged leak precisely (file and line) AND stops an unstaged `rm` of a clean file
            # from reddening the commit. See `staged_text`.
            #
            # ⚠️ ONE SUBPROCESS PER ABSENT FILE, ~74 ms each (issue #34). Irrelevant for the
            # handful of files normally in this state, and ~77 s if a 1000-file tracked directory
            # is deleted without staging the deletion. Left per-file DELIBERATELY: the batched
            # form is what shipped an exit-0 bypass in this same package (a non-blob response
            # carries a body, and not consuming it desynchronised the stream — see #33), and
            # adding a third batch reader to fix a SLOWDOWN rather than a correctness defect was
            # the wrong trade at the end of that package. #34 carries the design.
            #
            # ⛔⛔ BYTES, NOT TEXT, AND THE SAME BYTES-DECIDE RULE AS THE WORKTREE READ. This
            # branch used to decode here and report anything that would not decode — and once the
            # suffix stopped gating the loop, an ordinary tracked `icons/logo.png` DELETED from
            # the worktree without staging the deletion (an everyday mid-edit gesture) reached
            # this line, failed to decode, and REDDENED the commit. Worse, the remedy printed with
            # it was inert: it said "add its suffix to SKIP_SUFFIXES", and `.png` already is one.
            # Handing the bytes to the shared block below means one rule decides "is this an
            # asset" for both sources, which is what `_skipped`'s two-scans-must-agree note has
            # always been about.
            absent_from_worktree = True
            raw = staged_blob(root, rel)
            if raw is None:
                # ⚠️ DO NOT NAME A CAUSE THIS DOES NOT KNOW. The remaining reason `git cat-file`
                # refuses `:<path>` is that there is no stage-0 entry — an UNMERGED path. The
                # encoding half of this message moved to where the decode now happens, rather than
                # being asserted here about a step that no longer decodes anything.
                undecodable.append(
                    f"{rel} (absent from the worktree, and git could not read its staged content: "
                    f"the path is probably unmerged and has no stage-0 entry)")
                continue
        except OSError as exc:
            # Anything else unreadable — a permission problem, a broken symlink. Reported rather
            # than raised, because a traceback here aborts the scan part-way and every file after
            # it goes unexamined.
            undecodable.append(f"{rel} (unreadable: {type(exc).__name__})")
            continue
        if raw is not None:
            # ⭐⭐ THE SUFFIX IS A HINT, NOT A VERDICT (keystone#22). `SKIP_SUFFIXES` used to end
            # the matter before the file was opened, so `deploy-notes.pdf` holding a plain ASCII
            # runbook — LAN host, appdata path, RFC1918 address — was never read by either scan and
            # exited 0. The bytes now decide: an asset that really is one is skipped exactly as
            # before, and one that is text is scanned exactly like any other text file.
            #
            # ⚠️ THE TWO BRANCHES DIFFER IN WHAT A FAILED DECODE MEANS, and that asymmetry is
            # deliberate rather than an oversight. At a `.png` it means "yes, an image" — skip it
            # silently, which is the whole point of the suffix list and keeps issue #38 exactly
            # where it was. At any other path it means "I could not read this", which is REPORTED,
            # because a file this scanner cannot read is a file it cannot vouch for.
            if _binary_suffix(rel):
                if _looks_binary(raw):
                    continue
                # `_looks_binary` already proved this decodes; it cannot raise here.
                text = raw.decode("utf-8")
            else:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    # NOT silent: a file this scanner cannot read is a file it cannot vouch for.
                    undecodable.append(
                        f"{rel} (absent from the worktree, and its staged content is not UTF-8)"
                        if absent_from_worktree else rel)
                    continue
        # ⛔⛔ A SUCCESSFUL DECODE IS NOT PROOF IT IS TEXT (issue #242). BOM-less UTF-16LE of ASCII
        # is `A\x00G\x00E\x00…` — every byte under 0x80, so it IS valid UTF-8. `read_text`
        # succeeded, the decoded content was NUL-separated so no pattern could match, and the file
        # was counted in the "scanned" total. The guard vouched for a file it had not read.
        #
        # ⭐ PLACED AFTER THE `try`, DELIBERATELY, so it covers BOTH sources of `text` — the
        # worktree read AND the staged blob from `staged_blob`. Those are two of the three decode
        # sites in this file, and the sibling repo's attempt at this reached only one of them
        # because it was written at the reads rather than at what they produce (tape#242).
        # (`text` is necessarily a str here: both sources set `raw`, and every path that leaves it
        # unset has already `continue`d. Asserted by construction rather than re-checked, because a
        # defensive `text is None` branch could only print a NUL message about something that is
        # not a NUL problem.)
        if "\x00" in text:
            # `absent_from_worktree` threaded in here too: this is the one branch that could
            # report a staged blob without saying the file is not on disk, which sent the operator
            # looking for a file that is not there.
            where = " (absent from the worktree)" if absent_from_worktree else ""
            undecodable.append(
                f"{rel}{where} (contains a NUL byte, so it is not UTF-8 text - BOM-less "
                f"UTF-16/UTF-32 decodes as valid UTF-8 and would scan as nothing)")
            continue
        scanned += 1
        findings += [f"{rel}:{n}: {label}: {match!r}"
                     for n, label, match in scan_text(text, compiled, rel)]

    if undecodable:
        print(f"UNREADABLE as UTF-8 ({len(undecodable)}) - not scanned, so not cleared:")
        for u in undecodable:
            print("  " + _ascii(u))
        print("If it is binary, add its suffix to SKIP_SUFFIXES; if it is text, fix the "
              "encoding; if it is staged-but-deleted, re-stage it so the scan sees what the "
              "commit will contain.\n")
    if findings:
        print(f"INTERNAL INFO FOUND in {len(findings)} place(s) - this repo is public:\n")
        for f in findings:
            print("  " + _ascii(f))
        print("\nReplace with a placeholder (<your-unraid-host>, your-domain.example, "
              "/mnt/POOL/..., RFC5737 addresses) or take the value from an env Variable.")
        print("If a hit is genuinely legitimate, add the literal to ALLOW_LITERALS in "
              f"{SELF_PATH} with a comment saying why.")
    if findings or undecodable:
        return 1
    if tracked and scanned == 0:
        # ⛔⛔ A RUN THAT SCANNED NOTHING IS NOT A CLEAN RUN (keystone#22, second half). This
        # printed `no internal info found (0 tracked text files scanned)` and exited 0 — a
        # cheerful pass, in the same words as a real one, for a scan that opened no file at all.
        # Every way of getting here is a failure worth stopping on: `--repo` aimed at the wrong
        # directory, a tree whose every file is an asset, or someone widening SKIP_SUFFIXES until
        # nothing is left to read.
        #
        # ⚠️ IT IS NOT THE SAME QUESTION AS "were there findings". Findings and unreadable files
        # are both reported ABOVE and both already exit 1; this is the third state neither of them
        # covers — no findings BECAUSE there was no input. The count was printed all along, which
        # is what makes this cheap: the number was right there in the success message and nothing
        # acted on it.
        #
        # ⛔ `tracked and` IS LOAD-BEARING, AND ITS ABSENCE BLOCKED A LEGITIMATE ACTION. An EMPTY
        # tracked list is not "a scan that skipped everything", it is a scan with nothing to do —
        # and `git commit --allow-empty -m initial`, the standard way to start a repository, has
        # exactly that shape. Without this the pre-commit hook refused it and the repository could
        # not be bootstrapped at all. The acceptance this exists for says "a 0-files-scanned run
        # against a NON-EMPTY tree", and the empty tree is the case it deliberately does not name.
        print(f"REFUSING to report clean: {len(tracked)} tracked path(s), but ZERO were read as "
              "text, so no file CONTENT was scanned.")
        print("A scan that opened no file cannot clear a tree. Check that --repo points at the "
              "right repository and that SKIP_SUFFIXES has not grown to cover everything. If this "
              "really is an assets-only tree, every path was still checked - add one text file "
              "(a README) so the content scan has something to vouch for.")
        return 1
    if not tracked:
        # ⚠️ NAME THE ROOT WHEN THERE WAS NOTHING TO SCAN. An empty tracked list is legitimate (a
        # fresh repository, `git commit --allow-empty`), so it is not an error — but it is also
        # what a mis-aimed `--repo` looks like, and `no internal info found (0 ...)` is
        # indistinguishable from a real clean run. Saying WHICH repository was empty makes the
        # wrong-directory case visible without reddening the right one.
        print(f"no internal info found (0 tracked text files scanned; {_ascii(str(root))} has no "
              f"tracked files at all - check --repo if that is a surprise)")
        return 0
    print(f"no internal info found ({scanned} tracked text files scanned)")
    return 0


def staged_blob(root: Path, rel: str) -> bytes | None:
    """The BYTES `git commit` would record for a tracked file that is NOT in the worktree, or None.

    ⭐ THIS IS THE ANSWER TO THE STAGED-BUT-DELETED CASE, and it is strictly better than the
    "cannot read it, cannot vouch for it" report it replaces — in BOTH directions:

      * SECURITY. A staged leak is now IDENTIFIED, with its file and line, instead of being
        reported as an unreadable path the operator has to go and inspect by hand.
      * FALSE-RED. `rm <tracked-file>` without staging the deletion is an ordinary thing to do
        mid-edit, and the index still holds the file, so the blanket report BLOCKED THE COMMIT on
        a tree that publishes nothing internal — and the advice it printed ("re-stage it") was
        the wrong instruction for someone whose intent was to keep the deletion. Reading the blob
        answers the real question, which is what the COMMIT will contain.

    ⛔ RETURNS BYTES, NOT TEXT, and that is the second half of the same false-red story. It used to
    decode here and return None for a blob that is not UTF-8 — so once the binary SUFFIX stopped
    gating the loop, `rm icons/logo.png` (without staging the deletion) reddened the commit, with a
    remedy that was inert because `.png` was already in SKIP_SUFFIXES. Handing the caller the bytes
    lets ONE rule decide "is this an asset", for the worktree read and the staged blob alike.

    `:<path>` is the index revision of the file. None now means only that git refused the path —
    in practice an UNMERGED path, which has no stage-0 entry.
    """
    try:
        return subprocess.run(["git", "cat-file", "blob", f":{rel}"], cwd=root,
                              capture_output=True, check=True, timeout=_GIT_TIMEOUT_S).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def staged_diff(root: Path) -> tuple[ParsedDiff, list[str]]:
    """(what the INDEX would commit, the paths it introduces) — `git diff --cached`.

    ⭐ THIS IS THE ANSWER TO #33 / keystone#23. The tree scan reads the WORKTREE, so a leak that
    is `git add`ed and then tidied in the worktree without re-staging is invisible to the
    pre-commit layer: the index — and therefore the commit — still carries it, and the hook exits
    0 while `git show HEAD:<file>` returns the leak. The range scan on the push path did catch it,
    so it could not reach the remote; the gap was that the layer running at COMMIT time answered a
    question about a different tree from the one being committed.

    ⛔ `git diff --cached` THROUGH `parse_diff`, and NOT a per-file `cat-file` of every staged
    blob. Both of the other shapes were written and REMOVED from this file already, and their
    failures are recorded in the module docstring: reading each blob made the hook take 78 s on a
    1000-file worktree, and a batched `cat-file --batch` DESYNCHRONISED on a gitlink — `:<path>`
    on a submodule returns a COMMIT object whose body the parser must skip — mis-attributing one
    file's content to another and exiting 0 on a staged leak it never read. `--cached` asks git
    exactly "what will this commit ADD" and needs no new protocol parser: it reuses the
    well-tested `parse_diff`, the allow-spans, the binary/NUL posture and the marker machinery
    that the range scan has been exercising all along.

    ⚠️ THE BASE IS THE EMPTY TREE WHEN THERE IS NO HEAD. On the very first commit of a repository
    `git diff --cached` has no `HEAD` to resolve and dies 128 — and the initial commit is exactly
    when a whole tree is staged at once, so failing there would leave the first and largest commit
    unscanned by this layer.
    """
    base = "HEAD" if resolves(root, "HEAD") else _EMPTY_TREE
    parsed = parse_diff(_git(root, *_DIFF_CONFIG, "diff", *_DIFF_FLAGS, "--cached", base))
    # ⛔ THE SAME `--text` RESOLUTION THE RANGE SCAN DOES, and not an optional refinement: without
    # it an ordinary `.gitattributes`-marked lockfile — git reports `Binary files … differ` for a
    # `-diff` attribute — would be reported "not scanned, so NOT CLEARED" and BLOCK the commit,
    # a false red on correct work in the layer a developer meets most often.
    return resolve_unscannable(root, parsed, ("--cached", base)), changed_paths(
        root, "--cached", base)


def _scan_staged(root: Path, compiled: list[tuple[str, re.Pattern[str]]]) -> int:
    """Scan the INDEX. Same reporting posture as the range scan: unread is never cleared."""
    parsed, paths = staged_diff(root)
    findings, unscannable = scan_added("", parsed, compiled, root)
    # `scan_added` prefixes each finding with `sha[:10] ` and there is no sha here, so the prefix
    # is empty and the lines read `<file>:<line>: ...`. Same for the path findings below.
    findings += scan_paths("", paths)
    if unscannable:
        print(f"BINARY / UNREADABLE in {len(unscannable)} staged file(s) - not scanned, so NOT "
              f"CLEARED:")
        for u in unscannable:
            print("  " + _ascii(u.strip()))
        print("Add a binary suffix to SKIP_SUFFIXES if that is what it is, or stage the file as "
              "UTF-8 text.\n")
    if findings:
        print(f"INTERNAL INFO FOUND in {len(findings)} place(s) STAGED FOR COMMIT - this repo is "
              "public:\n")
        for f in findings:
            print("  " + _ascii(f.strip()))
        print("\nThis is what the INDEX holds, which is what the commit will record - tidying the "
              "working copy without re-staging does NOT remove it.")
        print("Replace with a placeholder (<your-unraid-host>, your-domain.example, "
              "/mnt/POOL/..., RFC5737 addresses), then `git add` the corrected file.")
        print("A `<path>` finding is the FILE NAME, not its contents: `git mv` it to a neutral "
              "name and stage the rename.")
    if findings or unscannable:
        return 1
    print(f"no internal info staged ({len(parsed.added)} added line(s), "
          f"{len(paths)} path(s) checked)")
    return 0


def is_shallow(root: Path) -> bool:
    """Is this a truncated clone? `--is-shallow-repository` prints `true`/`false`."""
    try:
        return _git(root, "rev-parse", "--is-shallow-repository").strip() == "true"
    except subprocess.CalledProcessError:  # pragma: no cover - git predating the flag
        return False


def _scan_commits(root: Path, rev_range: str,
                  compiled: list[tuple[str, re.Pattern[str]]]) -> int:
    if is_shallow(root):
        # ⛔ A SHALLOW CLONE DOES NOT FAIL LOUDLY ON ITS OWN. `git rev-list` succeeds against the
        # truncated history and returns the truncated count, and the boundary commit's missing
        # parent falls into the empty-tree branch — so the scan degenerates to "this one commit's
        # whole tree", prints a cheerful `no internal info added across 1 commit(s)`, and exits 0
        # having never seen the history the range names. The workflows pin `fetch-depth: 0`, so
        # this is not live; the point is that the safety net has to exist rather than be asserted,
        # or the day someone drops that line the guard fails OPEN and silently.
        print("REFUSING to scan a SHALLOW clone: the history is truncated, so a range scan here "
              "would report on commits it cannot see.")
        print("Fetch the full history first (CI: `fetch-depth: 0`, locally: "
              "`git fetch --unshallow`).")
        return 1
    rev_range, widened = widen_unreachable_base(root, rev_range)
    if widened:
        print(_ascii(widened))
    try:
        result = scan_range(root, rev_range, compiled)
    except subprocess.CalledProcessError as exc:
        # A range git cannot resolve is NOT a pass. A shallow clone, an unfetched base or a typo
        # would otherwise scan zero commits and print a clean result — the one failure mode a
        # guard must never have.
        print(_ascii(f"could not scan {rev_range!r}: git exited {exc.returncode}. ")
              + "Fetch the base ref (CI needs fetch-depth: 0) or check the range.")
        return 1
    if result.unscannable:
        # Same posture as the tree scan's `undecodable` list: a blob this cannot read is a blob
        # it cannot vouch for.
        print(f"BINARY / UNREADABLE in {len(result.unscannable)} added file(s) - not "
              f"scanned, so NOT CLEARED:")
        for u in result.unscannable:
            print("  " + _ascii(u))
        # ⚠️ THE ADVICE MUST MATCH THE CAUSE, and this one line covered two unrelated ones. For an
        # UNPARSEABLE HEADER both halves of it are wrong — the file is neither binary nor
        # mis-encoded — so it left the operator chasing a problem that does not exist. Each marker
        # now CARRIES its own remediation (see `_unparsed_header`) rather than
        # having this site try to recognise one after the sigil has been stripped for display.
        print("For an ordinary path above: add a binary suffix to SKIP_SUFFIXES if that is what "
              "it is, or commit the file as UTF-8 text. A file that DECODES but carries a NUL "
              "byte is refused here too - BOM-less UTF-16/UTF-32 is valid UTF-8 and would scan "
              "as nothing, so it is not cleared.\n")
    if result.findings:
        # ⚠️ `_ascii` HERE TOO. This is the line that ANNOUNCES a real leak, and with a non-ASCII
        # branch name in `rev_range` it died mid-sentence under a hook's cp1252 stdout — the guard
        # crashing at the exact moment it had something to say. The finding LIST was made
        # ASCII-safe and these three `rev_range` sites were missed: the instance, not the class.
        print(_ascii(f"INTERNAL INFO FOUND in {len(result.findings)} place(s) published by "
                     f"{rev_range} ")
              + "- this repo is public and pushing publishes HISTORY:\n")
        for f in result.findings:
            print("  " + _ascii(f))
        # ⚠️ THE ADVICE MUST MATCH THE CAUSE — the same rule `_unparsed_header` exists for, applied
        # to the surfaces added since. "Rewrite the offending commits" is right for a leaked LINE
        # and for the identity, and WRONG for the other two: a `<tag object>` finding is cleared by
        # deleting the tag, and a `<path>` finding by renaming the file. Printing one remedy for
        # five surfaces sent the operator to rewrite history over a scratch tag.
        print("\nRemoving it in a LATER commit does not help: the value stays readable at "
              "the commit that added it. Rewrite the offending commits (git rebase -i / "
              "filter-repo) BEFORE pushing, then re-run this.")
        print("A `<author email>` / `<committer email>` finding is the COMMIT'S OWN identity, "
              "not its diff: fix `git config user.email` (the fleet identity is the GitHub "
              "noreply address), then rewrite the commits so the metadata is corrected too.")
        print("A `<commit message>` finding is the message, not the diff - it needs the same "
              "rewrite (`git commit --amend` for the tip, `git rebase -i` further back).")
        print("A `<tag object>` finding needs NO history rewrite: the tag is a ref. "
              "`git tag -d <name>` (and `git push origin --delete <name>` if it is already out) "
              "clears it, or re-cut it with `git tag -a -f`.")
        print("A `<path>` finding is the FILE NAME, not its contents: `git mv` it to a neutral "
              "name in the offending commit.")
    if result.findings or result.unscannable:
        return 1
    # ⚠️ `_ascii` HERE TOO — this is the FOURTH `rev_range` site and the only one on the SUCCESS
    # path. The first repair covered the three failure/finding messages and missed this, so a
    # perfectly clean range scan on a branch with a non-ASCII name still died with a traceback
    # under a hook's cp1252 stdout. A guard that crashes when it has nothing to report is the
    # purest form of the false-red it exists to avoid. The instance, not the class, twice.
    print(_ascii(f"no internal info added across {result.commits} commit(s) in {rev_range}"))
    return 0


def repo_root(start: str | None) -> Path:
    """The top level of the repository to scan."""
    return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                               capture_output=True, check=True, text=True,
                               timeout=_GIT_TIMEOUT_S).stdout.strip())


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
    except UsageError as exc:
        print(f"{exc}\n\n{USAGE}", file=sys.stderr)
        return 2
    if args.help:
        print(USAGE)
        return 0

    compiled = compile_patterns()
    if args.selftest:
        return selftest(compiled)

    root = repo_root(args.repo)
    if args.rev_range is not None:
        return _scan_commits(root, args.rev_range, compiled)
    if args.staged:
        return _scan_staged(root, compiled)
    return _scan_tree(root, compiled)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

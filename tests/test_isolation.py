"""The generic-code contract, enforced as tests rather than as a promise.

A module in this library earns its place by satisfying rules a reviewer cannot reliably eyeball.
These are the ones a green functional suite says NOTHING about:

1. **It imports cleanly in isolation.** Copy the module alone into an empty directory and import
   it: no consumer module, and no other `kw_common` module, may appear in `sys.modules`.
2. **Configuration is INJECTED, never assumed.** The module names no environment variable, no
   default path and no service name — the caller passes what it uses. This is checked against the
   AST rather than the text, so an explanatory comment cannot fail it and a real `os.environ`
   read cannot hide behind one.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "kw_common" / "alerting.py"

# ⛔ DELIBERATELY NO LIST OF CONSUMER MODULE NAMES HERE. An earlier version enumerated the
# adopting services by name, which published a private deployment's inventory into a PUBLIC
# repository — the exact class of disclosure `scripts/check_no_internal_info.py` exists to stop,
# and one it structurally cannot catch, because a bare project name has no shape to match.
#
# The enumeration was never needed. Two positive properties replace it, stated below and scoped
# honestly: after importing this module alone, (a) nothing outside the standard library is in
# `sys.modules`, and (b) the sandbox directory supplied nothing but `alerting` itself. Neither
# needs a list to maintain, and together they cover consumers that do not exist yet.
#
# ⚠️ (a) ALONE IS NOT A SUPERSET of the list it replaced — a consumer whose top-level name
# collides with a standard-library one would be invisible to it. That is what (b) is for: it asks
# where a module was loaded FROM, which is a question a name cannot answer. See the note on the
# assertions themselves.


def test_the_module_source_is_where_the_test_thinks_it_is() -> None:
    """A vacuity guard: every check below reads this file, and a `shutil.copy` of a path that
    does not exist would fail loudly — but a path that resolved somewhere ELSE would not."""
    assert MODULE_PATH.is_file()
    assert "def notify(" in MODULE_PATH.read_text(encoding="utf-8")


def _run_isolated(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    """Run `script` in a directory holding ONLY a copy of the module, with the repo off the path.

    ⚠️ `encoding=`/`errors=` explicitly, never a bare `text=True`: that decodes the child's output
    with the LOCALE codec, which on a Windows workstation is cp1252 — so a harness capturing a
    child whose output carries a non-ASCII assertion message crashes only when a test FAILS. It
    works on the green control and dies on the first real result, which is exactly the path this
    harness exists to exercise.
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    shutil.copy(MODULE_PATH, sandbox / "alerting.py")
    # `-E` ignores every PYTHON* variable (PYTHONPATH, PYTHONHOME); `-s` drops the user site
    # directory; `-S` drops site-packages entirely. What is left on `sys.path` is the standard
    # library and the sandbox directory, and NOTHING else — which is what makes "it imported
    # alone" mean what it says rather than "it found everything it needed already installed".
    #
    # ⛔ NOT `-I`. That looks like the right flag and is the obvious first reach, but `-I` also
    # removes the CURRENT DIRECTORY from `sys.path`, so the child cannot see the copied module at
    # all and the run fails with `ModuleNotFoundError: alerting` — a false RED that reads exactly
    # like the property being violated.
    # `-B` (not PYTHONDONTWRITEBYTECODE, which `-E` would ignore) keeps a `__pycache__` out of
    # the sandbox, so a second run cannot import a stale copy.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    return subprocess.run(
        [sys.executable, "-E", "-s", "-S", "-B", "-c", script],
        cwd=sandbox,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        env=env,
    )


def test_the_module_imports_alone_with_no_consumer_in_sys_modules(tmp_path: Path) -> None:
    """⭐ Copy it into an empty directory and import it. Nothing from any consuming service, and
    nothing from the rest of this library, may come along."""
    script = (
        "import sys, json\n"
        "import alerting\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    result = _run_isolated(tmp_path, script)

    assert result.returncode == 0, f"the module did not import alone:\n{result.stderr}"
    loaded = set(json.loads(result.stdout))

    # Stated positively and without naming anyone: nothing outside the standard library came
    # along. It needs no list to maintain and it covers consumers that do not exist yet.
    #
    # ⚠️ SCOPED HONESTLY — this is NOT an unconditional superset of the name list it replaced.
    # A module whose TOP-LEVEL name collides with a standard-library one (`array`, `platform`,
    # `queue`, `select`, `stat`, `code` are all plausible names in a fleet) would be invisible
    # here. Measured: none of the ten names previously listed collides, so nothing was lost in
    # practice — but the earlier wording claimed this "covers every consumer module, named or
    # not", and that was an overstatement. The second assertion below is what closes the
    # collision case, because it asks WHERE a module came from rather than what it is called.
    foreign = sorted(m for m in loaded
                     if m.partition(".")[0] not in sys.stdlib_module_names
                     and m not in ("alerting", "__main__"))
    assert foreign == [], f"importing alerting dragged in non-stdlib modules: {foreign}"
    kw = sorted(m for m in loaded if m.startswith("kw_common"))
    assert kw == [], f"the module is not standalone — it pulled in {kw}"


def test_nothing_but_the_module_itself_is_imported_from_the_sandbox(tmp_path: Path) -> None:
    """The origin check, which a name-based one cannot make.

    Asks where every loaded module was loaded FROM, and requires the sandbox directory to have
    supplied exactly one: `alerting` itself. A consumer module sitting beside it is caught here
    whatever it is called — including when its name collides with a standard-library module,
    which is precisely the case the name-based filter above cannot see.
    """
    # ⚠️ No decoy file is planted, and an earlier version of this comment claimed one was. What
    # the check actually rests on is the ORIGIN filter below: `sys.modules` is filtered to the
    # modules whose `__file__` lives in the sandbox directory, and exactly one may. A future edit
    # that made `alerting` import a sibling is caught by that whatever the sibling is called —
    # no decoy needed, and planting one named after a stdlib module would shadow the real one on
    # `sys.path` and break the import it is supposed to be observing.
    script = (
        "import sys, json, os\n"
        "import alerting\n"
        "here = os.getcwd()\n"
        "from_sandbox = sorted(\n"
        "    name for name, mod in list(sys.modules.items())\n"
        "    if getattr(mod, '__file__', None)\n"
        "    and os.path.realpath(os.path.dirname(mod.__file__)) == os.path.realpath(here)\n"
        ")\n"
        "print(json.dumps(from_sandbox))\n"
    )
    result = _run_isolated(tmp_path, script)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["alerting"], (
        f"the sandbox supplied more than the module under test: {result.stdout}")


def test_the_module_needs_no_third_party_package(tmp_path: Path) -> None:
    """Stdlib-first is a contract: every third-party dependency here is a supply-chain surface
    across the WHOLE fleet at once. `-S` above puts site-packages out of reach, so a third-party
    import would already fail the run; this states the property directly as well, so the failure
    NAMES the offending package instead of arriving as a bare ImportError."""
    # ⛔ NOT a `sysconfig.get_paths()['stdlib']` prefix test. The C extension modules alerting
    # legitimately pulls in — `_ssl`, `_socket`, `_hashlib`, `_bz2`, `_lzma`, `select` — live in
    # `DLLs/` or `lib-dynload/`, OUTSIDE that directory, so a path-prefix check reports six
    # standard-library modules as third-party. `sys.stdlib_module_names` (3.10+) is the frozen
    # set of names the interpreter itself considers standard, which is the question being asked.
    script = (
        "import sys, json\n"
        "import alerting\n"
        "outside = sorted(\n"
        "    name for name in list(sys.modules)\n"
        "    if name.partition('.')[0] not in sys.stdlib_module_names\n"
        "    and name not in ('alerting', '__main__')\n"
        ")\n"
        "print(json.dumps(outside))\n"
    )
    result = _run_isolated(tmp_path, script)

    assert result.returncode == 0, result.stderr
    outside = json.loads(result.stdout)
    assert outside == [], f"alerting imported non-stdlib modules: {outside}"


def test_importing_the_package_does_not_import_any_submodule() -> None:
    """Contract rule 5, from the other side: `import kw_common` must stay free.

    A convenience re-export in `__init__.py` would make every consumer of every future module pay
    for alerting's `smtplib`/`ssl`/`urllib` import whether or not it alerts.
    """
    result = subprocess.run(
        [sys.executable, "-c",
         "import kw_common, sys;"
         "print([m for m in sys.modules if m.startswith('kw_common.')])"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        f"importing kw_common pulled in submodules: {result.stdout.strip()}")


# ------------------------------------------------------------------ configuration is injected
def _module_ast() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))


def _environment_reads(tree: ast.Module) -> list[str]:
    """Every place `tree` reads the process environment.

    ⭐ THE SHARED IMPLEMENTATION, and that sharing is the point. The positive test below and the
    negative one after it MUST run the same code: an earlier version re-implemented this walk
    inline in the negative test, so gutting the real one left the "would it catch anything?"
    test still passing. It validated a copy, not the guard.
    """
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                offenders.append(f"os.{node.attr} at line {node.lineno}")
        elif isinstance(node, ast.Name) and node.id in ("getenv", "environ"):
            offenders.append(f"{node.id} at line {node.lineno}")
    return offenders


def test_the_module_never_reads_the_environment() -> None:
    """⭐ AST, not grep — the docstrings explain that config is injected INSTEAD of read from the
    environment, so a text search would trip on the very sentence documenting the rule.

    An environment variable NAME is configuration the consumer would have to match exactly, which
    makes it a hardcoded contract by another spelling. The caller passes values; this module reads
    none of its own.
    """
    offenders = _environment_reads(_module_ast())
    assert offenders == [], (
        f"the module reads the environment: {offenders}. Configuration is INJECTED via "
        f"AlertSettings; a consumer passes what it uses.")


@pytest.mark.parametrize("source", [
    pytest.param("import os\n\n\ndef f() -> str:\n    return os.environ.get('SECRET', '')\n",
                 id="os.environ.get"),
    pytest.param("import os\nX = os.environ['SECRET']\n", id="os.environ-subscript"),
    pytest.param("import os\n\n\ndef f():\n    return os.getenv('SECRET')\n", id="os.getenv"),
    pytest.param("from os import getenv\nX = getenv('SECRET')\n", id="bare-getenv"),
])
def test_the_guard_above_would_actually_catch_an_environment_read(source: str) -> None:
    """⭐ THE NEGATIVE DIRECTION, through the REAL guard.

    "No `os.environ` node was found" is equally true of a module that has none and of a walk that
    looks in the wrong place. This calls `_environment_reads` — the same function the positive
    test calls — on modules that DO read the environment, so gutting the walk turns this test red
    as well. Four spellings, because a guard that only recognises one spelling of the thing it
    forbids is an arms race it loses.
    """
    assert _environment_reads(ast.parse(source)), (
        "the guard cannot see this environment read, so the positive test proves nothing")


def test_no_module_level_constant_holds_an_absolute_path() -> None:
    """A default path is a promise this library cannot keep — see the module docstring. The
    predecessor defaulted its error log into a volume its adopters did not all have, and got
    `os.makedirs` SUCCEEDING inside the container's disposable layer: no error, no warning,
    records unreachable from the host."""
    offenders: list[str] = []
    for node in _module_ast().body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        text = value.value
        if text.startswith("/") or (len(text) > 2 and text[1] == ":" and text[2] in "\\/"):
            offenders.append(f"line {node.lineno}: {text!r}")
    assert offenders == [], f"module-level absolute paths must be injected instead: {offenders}"


def test_every_exported_callable_is_defined_at_module_level() -> None:
    """Every exported FUNCTION OR CLASS is a real top-level definition in this file.

    Scoped honestly: this reads `__all__` and checks the names that resolve to a function or a
    class, which is what "a real top-level definition" can be asserted about via the AST. The
    exported CONSTANTS are covered by `test_everything_in_dunder_all_exists` (which resolves
    every one of them) and by ruff's `F822`, which fails an `__all__` entry that does not exist
    at all. An earlier version of this test was parametrised over a hardcoded seven of the
    twenty-eight names while its docstring claimed it covered every one.
    """
    defined = {
        node.name
        for node in _module_ast().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    import kw_common.alerting as mod

    expected = {name for name in mod.__all__
                if callable(getattr(mod, name, None)) and not isinstance(getattr(mod, name), str)}
    missing = sorted(expected - defined)
    assert missing == [], f"exported but not defined at module level here: {missing}"
    assert len(expected) >= 7, "the check stopped finding exported callables — it proves nothing"

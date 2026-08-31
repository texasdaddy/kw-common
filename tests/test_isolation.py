"""The generic-code contract, enforced as tests rather than as a promise.

A module in this library earns its place by satisfying rules a reviewer cannot reliably eyeball.
These are the two that a green functional suite says NOTHING about:

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

# Anything importing one of these has stopped being a shared library. `reauth` is the module this
# code was extracted FROM and is the specific regression this guards; the rest are the sibling
# services that will adopt it.
CONSUMER_MODULES = ("reauth", "reauth_bot", "app", "tape", "keystone", "cef_tracker", "gambit",
                    "the_desk", "config", "settings")


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

    leaked = sorted(loaded & set(CONSUMER_MODULES))
    assert leaked == [], f"importing alerting dragged in consumer modules: {leaked}"
    kw = sorted(m for m in loaded if m.startswith("kw_common"))
    assert kw == [], f"the module is not standalone — it pulled in {kw}"


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


def test_the_module_never_reads_the_environment() -> None:
    """⭐ AST, not grep — the docstrings explain that config is injected INSTEAD of read from the
    environment, so a text search would trip on the very sentence documenting the rule.

    An environment variable NAME is configuration the consumer would have to match exactly, which
    makes it a hardcoded contract by another spelling. The caller passes values; this module reads
    none of its own.
    """
    offenders: list[str] = []
    for node in ast.walk(_module_ast()):
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                offenders.append(f"os.{node.attr} at line {node.lineno}")
        elif isinstance(node, ast.Name) and node.id in ("getenv", "environ"):
            offenders.append(f"{node.id} at line {node.lineno}")
    assert offenders == [], (
        f"the module reads the environment: {offenders}. Configuration is INJECTED via "
        f"AlertSettings; a consumer passes what it uses.")


def test_the_guard_above_would_actually_catch_an_environment_read(tmp_path: Path) -> None:
    """The negative direction. "No `os.environ` node was found" is equally true of a module that
    has none and of a walk that looks in the wrong place — feed it a module that DOES read the
    environment and require the same walk to find it."""
    planted = tmp_path / "planted.py"
    planted.write_text("import os\n\n\ndef f() -> str:\n    return os.environ.get('SECRET', '')\n",
                       encoding="utf-8")
    tree = ast.parse(planted.read_text(encoding="utf-8"))
    found = [n for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and n.attr in ("environ", "getenv")
             and isinstance(n.value, ast.Name) and n.value.id == "os"]
    assert found, "the AST walk cannot see an environment read, so the guard above proves nothing"


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


@pytest.mark.parametrize("name", ["notify", "warn_if_unconfigured", "configure", "AlertSettings",
                                  "AlertConfig", "Alerter", "smtp_port_fault"])
def test_the_public_names_are_defined_at_module_level(name: str) -> None:
    """`__all__` is the semver contract, so every name in it has to be a real top-level
    definition — not something a star-import happens to supply."""
    defined = {
        node.name
        for node in _module_ast().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert name in defined

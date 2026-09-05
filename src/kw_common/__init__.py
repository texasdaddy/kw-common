"""kw-common — the fleet's shared Python library.

⭐ IMPORTING THIS PACKAGE IMPORTS NOTHING ELSE, ON PURPOSE.

Every module here is INDEPENDENTLY IMPORTABLE: `kw_common.alerting` must not drag in anything
else, and nothing else may drag in `kw_common.alerting`. A consumer takes what it needs:

    from kw_common.alerting import AlertSettings, configure, notify, OK, WARN, ERROR

So this file deliberately re-exports NOTHING. A convenience `from . import alerting` here would
make every consumer of every future module pay for the alerting import (and its `smtplib`, `ssl`
and `urllib` cost) whether or not it alerts — and would quietly break the isolation property the
whole library is built on.

`__version__` is the only name here, and it is the SINGLE SOURCE of the version:
`pyproject.toml` declares `dynamic = ["version"]` and reads it from this attribute, so there is
no second literal to forget. The release workflow additionally asserts the git TAG agrees.

Consumers pin an exact git tag, never a branch:

    pip install git+https://github.com/texasdaddy/kw-common@v1.4.0
"""

__all__ = ["__version__"]

__version__ = "1.4.0"

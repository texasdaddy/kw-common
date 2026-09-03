# Setting up ops alerting

One page, owned once, pointed at from everywhere. Every service that uses `kw_common.alerting_env`
reads the same shared file and declares the same three variables, so this document is written once
here rather than copied into each consumer's README — a prose copy per consumer is the same
duplication problem relocated into documentation.

**A consumer README carries a short prerequisite block and a link to this page. Never a copy.**

---

## Before you download anything

The commands below fetch one plain-text template file over HTTPS and write it to disk. Nothing is
executed, and **nothing is piped into a shell** — that is deliberate, and it is the difference
between a download you can inspect and one you cannot.

> ⚠️ **Review any file you download before you use it, and proceed at your own risk.** Open
> `alerting.env.template` in an editor and read it before you fill it in. It is a configuration
> template and should contain nothing but comments and `KEY=VALUE` lines. If it contains anything
> else, stop.

---

## 1. The three variables

Every alerting app declares exactly these, and nothing else about alerting:

| variable | mode | what it is |
|---|---|---|
| `SHARED_ROOT` | read-only | the shared root. Carries `configs/alerting.env` and nothing else. |
| `CONFIG_PATH` | read-write | the app's **own** directory. It writes its boot marker here. |
| `DEPLOY_ENV` | — | `prod` or `dev`. Case-insensitive; `kw-common` lowercases it. |

There is **no default for any of them**. A missing variable is an error at boot, on purpose: a
default would point at the wrong deployment silently, and the symptom of that is an incident that
produced no page.

> ⚠️ `CONFIG_PATH` is a **directory the app owns**, not the config file. The shared root is mounted
> read-only and the app must never try to write into it.

There are no per-service ntfy, SMTP or email variables. Every such value comes from the shared
file, and `DEPLOY_ENV` selects which ntfy topic applies.

## 2. The folder structure

```
<SHARED_ROOT>/
└── configs/
    └── alerting.env        <- the one file; holds an SMTP password
```

`configs/` is the only thing under the shared root. Nothing else belongs there.

## 3. Create it, and fetch the template

Run these **in the directory you want to become `SHARED_ROOT`**. Each pair creates the structure
and downloads the template beside it; neither runs anything it downloaded.

### Windows (PowerShell)

```powershell
New-Item -ItemType Directory -Force -Path .\configs
Invoke-WebRequest -Uri https://raw.githubusercontent.com/texasdaddy/kw-common/v1.2.0/alerting.env.template -OutFile .\configs\alerting.env
```

### macOS and Linux

```sh
mkdir -p ./configs
curl -fLo ./configs/alerting.env https://raw.githubusercontent.com/texasdaddy/kw-common/v1.2.0/alerting.env.template
```

`-f` makes `curl` fail on an HTTP error rather than writing the error page into your config file —
measured: on a 404 it exits 22 and creates nothing. `-L` follows a redirect if one is ever served;
today `raw.githubusercontent.com` answers 200 directly, so the flag costs nothing and covers a
change you would otherwise discover as a truncated file.

The URL names a **tag**, not a branch, for the same reason a consumer pins a tag: what you download
today and what you download next month should be the same file unless you chose otherwise.

## 4. Restrict it, THEN fill it in

Lock the file down **before** you type a password into it. It holds an SMTP credential, and the
file arrives from the download world-readable:

```sh
chmod 600 ./configs/alerting.env
```

`600` grants the file's **owner** — whoever ran the download — and nobody else. If the account your
services run as is a different user, `chown` the file to that account (or to a group both are in,
and use `640`). Getting that wrong is not silent: the service refuses to boot and says the file
could not be read.

On Windows, open the file's Properties → Security, disable inheritance, and grant only the account
the services run as.

Now open `configs/alerting.env` and replace every placeholder — including `SMTP_PASSWORD`, which
ships as `CHANGE-ME` and which boot validation refuses. The template documents each key; the short
version:

* **email** — `EMAIL_TO`, `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_PASSWORD` are required.
  `SMTP_USER` is optional and defaults to `EMAIL_FROM` **only when `EMAIL_FROM` is a plain
  `local@domain`**. A From header carrying a display name (`Alerts <box@host>`) or non-ASCII text
  is a valid header and is not a credential, so it will not be used as one — such a deployment
  must set `SMTP_USER` explicitly, and boot refuses until it does.
* **ntfy** — `NTFY_URL_DEV` and `NTFY_URL_PROD` are the topics services share. Each is a **full
  URL**, never a bare topic name.
* **a dedicated topic** — optional, one service, `NTFY_URL_<SERVICE>`. It wins over the shared
  topic and turns the title prefix off, because the topic is already the identifier. The key is
  the service name upper-cased with each RUN of characters that are not an **ASCII** letter or
  digit replaced by a single `_` and the ends trimmed, so `backup-agent` is
  `NTFY_URL_BACKUP_AGENT`, `feed--poller` is `NTFY_URL_FEED_POLLER` (a run becomes ONE `_`), and
  `café-poller` is `NTFY_URL_CAF_POLLER` (an accented letter is not an ASCII letter). There is exactly one
  spelling: a key spelled any other way is ignored and the service quietly lands back on the
  shared topic. A service may not be named `dev` or `prod` — that would derive a shared
  environment key — and `kw_common.alerting_env.ntfy_key("<service>")` returns the answer if you
  would rather not derive it by hand.

## 5. Set the three variables

On the container (or in whatever starts the service), declare exactly:

| variable | value |
| --- | --- |
| `SHARED_ROOT` | the path the shared root is mounted at, **read-only** |
| `CONFIG_PATH` | a directory the app can WRITE, its own, for the boot marker |
| `DEPLOY_ENV` | `prod` or `dev` |

None of them has a default. If one is missing or blank the service refuses to boot and names it.

## 6. Use it from an app

```python
from dataclasses import replace

from kw_common.alerting import configure, warn_if_unconfigured
from kw_common.alerting_env import load_alert_settings_from_env, validate_boot_from_env

settings = replace(
    load_alert_settings_from_env("my-service"),
    state_file="/data/alerts.json",
    error_log="/data/logs/my-service-errors.log",
)
alerter = configure(settings)
warn_if_unconfigured("my-service")
validate_boot_from_env(settings, alerter=alerter)
```

`load_alert_settings_from_env` reads `SHARED_ROOT` and `DEPLOY_ENV` and raises `AlertEnvError` if
either is unset. `validate_boot_from_env` additionally reads `CONFIG_PATH` and checks:

* the shared root is mounted and carries `configs/`;
* the file reads and is UTF-8;
* every key this environment requires carries a value, and none of them is still `CHANGE-ME`;
* every configured channel is one the library can actually send on — a bare topic instead of a
  full ntfy URL, an unusable `SMTP_PORT`, or an `EMAIL_FROM` that is not a plain mailbox **and no
  `SMTP_USER` to stand in for it** all refuse here rather than failing silently on the first real
  alert. (With `SMTP_USER` set explicitly, a display-name `EMAIL_FROM` is perfectly fine.)

Then it sends **one** confirmation alert and writes an empty marker into `CONFIG_PATH`. Later boots
skip the check while that marker is newer than the config file, so editing the file is what makes
the next boot re-validate. The marker is **per environment**, so promoting a service from `dev` to
`prod` re-validates even though the shared file did not change.

`state_file` and `error_log` are the app's own paths under its own volume, not fleet settings, so
they are not in the shared file and the loader has nothing to say about them — hence the
`replace`.

**Let `AlertEnvError` stop the process.** An app that cannot alert must not come up pretending it
can, and it must not try to report the problem through the channel that is broken. Every refusal is
logged before it is raised, so the reason is in the process log even if you catch it.

## 7. What good looks like

The first boot after any change to `configs/alerting.env` delivers one confirmation to email and to
the ntfy topic, titled:

```
[OK] [<env>][<service>] Alerting configuration validated
```

— or without the `[<env>][<service>]` part if that service has its own topic. That message **is**
the proof the channel works: a channel that never fires is indistinguishable from a broken one.

If it does not arrive, the process log says which of the two channels was skipped and why; nothing
is silent.

⚠️ One limit worth knowing: the marker is compared by **timestamp**. A config file restored at an
older timestamp — `rsync -a`, `cp -p`, `tar -x`, a volume restore, all of which preserve mtime —
leaves the marker still newer, so that change is not re-validated. `touch` the file after a restore.

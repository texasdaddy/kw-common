# Setting up ops alerting

One page, owned once, pointed at from everywhere. Every service that uses `kw_common.alerting_env`
reads the same shared file and declares the same three variables, so this document is written once
here rather than copied into each consumer's README — eight prose copies is the same duplication
problem relocated into documentation.

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

`-f` makes `curl` fail on an HTTP error rather than writing the error page into your config file;
`-L` follows the redirect GitHub serves for raw content.

The URL names a **tag**, not a branch, for the same reason a consumer pins a tag: what you download
today and what you download next month should be the same file unless you chose otherwise.

## 4. Fill it in

Open `configs/alerting.env` and replace every placeholder. The template documents each key; the
short version:

* **email** — `EMAIL_TO`, `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_PASSWORD` are required.
  `SMTP_USER` is optional and defaults to `EMAIL_FROM`; set it only when the auth identity and the
  From header genuinely differ.
* **ntfy** — `NTFY_URL_DEV` and `NTFY_URL_PROD` are the topics services share. Each is a **full
  URL**, never a bare topic name.
* **a dedicated topic** — optional, one service, `NTFY_URL_<SERVICE>`. It wins over the shared
  topic and turns the title prefix off, because the topic is already the identifier. The key is
  the service name upper-cased with every non-alphanumeric character replaced by `_`, so
  `reauth-bot` is `NTFY_URL_REAUTH_BOT`. There is exactly one spelling: a key spelled any other
  way is ignored and the service quietly lands back on the shared topic.

Then restrict the file. It holds an SMTP password:

```sh
chmod 600 ./configs/alerting.env
```

On Windows, remove inherited access and grant only the account the services run as, from the
file's Properties → Security dialog.

## 5. Use it from an app

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
either is unset. `validate_boot_from_env` additionally reads `CONFIG_PATH`; it checks the mount,
the structure, the file and every key this environment requires, then sends **one** confirmation
alert and writes an empty marker. Later boots skip the check while that marker is newer than the
config file, so editing the file is what makes the next boot re-validate.

`state_file` and `error_log` are the app's own paths under its own volume, not fleet settings, so
they are not in the shared file and the loader has nothing to say about them — hence the
`replace`.

**Let `AlertEnvError` stop the process.** An app that cannot alert must not come up pretending it
can, and it must not try to report the problem through the channel that is broken.

## 6. What good looks like

The first boot after any change to `configs/alerting.env` delivers one `[OK] Alerting
configuration validated` message to email and to the ntfy topic. That message **is** the proof the
channel works — a channel that never fires is indistinguishable from a broken one.

If it does not arrive, the process log says which of the two channels was skipped and why; nothing
is silent.

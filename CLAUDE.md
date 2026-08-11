# CLAUDE.md

Context for Claude Code (and any AI assistant) working on Herald. Read this
before making changes - it captures the working agreement, architecture map,
and conventions that span sessions.

Herald is part of the **luadch-ng** project; where a rule here is silent, the
[luadch-ng CLAUDE.md](https://github.com/luadch-ng/luadch-ng/blob/master/CLAUDE.md)
is the parent reference.

User communication is in **German**; all written artifacts (this file, code,
comments, commits, PRs, issues) stay in **English** so other contributors can
read them.

---

## 1. Working agreement (non-negotiable)

These rules are set by the maintainer and apply to every change.

1. **Security and consistency come first.** Treat any change touching network
   I/O (ADC connection, the GitHub update check), authentication (Tiger/HPAS
   login), config/secret handling, or file I/O as security-sensitive. When you
   fix a pattern in one place, grep for the same pattern across the repo and fix
   it everywhere - divergent code paths are a defect.
2. **No spaghetti code.** Prefer small, focused functions and modules. Keep the
   Qt-free core in `adc/`; if new logic does not have an obvious home, propose a
   new module before writing the code. The GUI (`main.py`) and the CLI
   (`herald_cli.py`) are thin front-ends over that shared core - shared logic
   lives in `adc/`, not copy-pasted between the two.
3. **Deep-dive before implementation.** Analyse the issue/idea from the source
   outward before writing code, even when it costs more tokens. A clean
   implementation pays the tokens back twice over.
4. **An issue/plan is a hypothesis, not ground truth.** Re-derive the root cause
   from the ADC spec + current source before implementing. If the plan is wrong,
   correct the plan - do not implement the wrong thing.
5. **Verify every assumption** against the current code/spec before building on
   it. Recalled memory and old docs are point-in-time; confirm before relying.
6. **Mandatory two-pass pre-merge review.** Before any merge - regardless of how
   small the diff - run (a) an independent reviewer (a subagent / fresh
   perspective) and (b) a maintainer-side spot-check. The review covers
   **security**, **new bugs**, **breaking existing behaviour**, and
   **consistency / anti-spaghetti**. No "it's just a one-liner" exemption. For
   anything non-trivial - and always for a change touching network I/O - prefer
   a **3-agent parallel review** on disjoint axes (correctness / consistency /
   security), then synthesise and fix-then-advance before the merge.
7. **Regression tests must provably fail pre-fix.** A test green on both old and
   new code proves nothing. For every fix, demonstrate the new test FAILS on the
   unpatched code and PASSES patched.
8. **Small reviewable PRs.** One logical change per PR. Reference the issue it
   closes. Never bundle unrelated fixes.
9. **No wall of text.** Chat answers, issues, PR bodies, release notes: minimal,
   technical, complete - result first. Internal artifacts (commit messages, code
   comments, repo docs) stay as detailed as needed.
10. **No em-dashes anywhere.** Use `-` in all written output: chat, commits,
    PRs, issues, docs.

When uncertain whether a change fits, **stop and ask the maintainer**.

---

## 2. Project overview

Herald is a multihub **ADC** release announcer: it watches folders for completed
releases and announces them to one or more DC++/ADC hubs (built for
[luadch-ng](https://github.com/luadch-ng/luadch-ng)). It has two front-ends over
one shared engine:

- a **Qt GUI** (`main.py`, PySide6 / Qt Widgets, dark + light theme), and
- a **headless CLI** (`herald_cli.py`) for servers / Docker - no display, no Qt.

- **Version:** single-sourced in `adc/__init__.py` (`__version__`). The GUI About
  box, the ADC `VE` field sent to hubs, and the release zip names all read it.
  Bump it deliberately per release, never per change.
- **Versioning plan:** internal test builds stay on the `1.x` line while Herald
  is validated with the testhub operator. The **first public GitHub release
  resets to `1.0`** and numbering continues from there. The update check
  (below) only matters from that first release on.
- **License:** GPLv3.0

### Dependencies (intentionally minimal)

- Core (CLI): pure-Python ADC engine; the only dependency is optional
  `watchdog` (file-events for near-live latency; degrades to polling).
  `requirements-core.txt`.
- GUI adds `PySide6` and `numpy` (About-box easter egg only).
  `requirements.txt`.

---

## 3. Architecture

```
adc/            pure-Python ADC core - imports ZERO Qt (shared by GUI + CLI)
  __init__.py     single-source __version__ + core re-exports
  _table.py       Tiger S-box table (verbatim from adclib tiger.cpp)
  tiger.py        Tiger/192 hash (adclib byte order, 0x01 padding)
  base32.py       adclib base32 (NOT RFC 4648)
  hashes.py       CID-from-PID + HPAS password hash
  protocol.py     ADC field escaping + PID/CID generation
  connection.py   connect -> login -> announce + liveness loop, auto-reconnect
  announce.py     release scanner (blacklist/whitelist/hidden/age/... filters)
  tracker.py      per-release completion state machine
  complete.py     SFV / size-stable / CRC completion detection
  config.py       hub + rule model, hubs.json / settings.json persistence
  migrate.py      import an old Lua-announcer cfg (self-contained Lua reader)
  updates.py      GitHub release check (notify-only; urllib, never raises)
ui/
  herald.ui       Qt Designer layout (edit: pyside6-designer ui/herald.ui)
  theme.qss       tokenised stylesheet skeleton (dark/light palettes in main.py)
assets/         icons, logo, chiptune wav (bundled read-only)
main.py         GUI: sidebar of hubs, per-hub Status/Config/Rules/Release tabs
herald_cli.py   headless runner: every enabled hub, one connection per thread
packaging/      build_release.py (PyInstaller onedir -> zips) + READMEs
deploy/         example systemd unit for the headless runner
docs/           CONFIGURATION.md - operator config reference (linked from README)
tests/          plain-script tests: ADC hash/base32 vectors + tracker/completion
```

**Local-only dev aids (gitignored + kept out of the source zip until the first
public 1.0; present in the maintainer's tree, NOT in a public clone):**
`cli_login.py` (no-GUI Tiger-login smoke test), `tools/fake_releases.py` (drops
fake test releases at a watched dir - drives the announce path + GUI), and
`packaging/NOTES.md` (internal per-version changelog; a public CHANGELOG starts
at 1.0). Both `.gitignore` and `packaging/build_release.py` (SKIP_FILES) exclude
them - keep the two lists in sync.

Key design facts (re-verify before building on them):

- **`adc/` is Qt-free.** Importing anything from `adc/` must never pull in
  PySide6, so the CLI runs on a bare server. A Qt import creeping into `adc/`
  is a review-blocking defect.
- **GUI is MULTI-CONNECTION.** `main.Herald._runtime` is keyed by `id(hub)`;
  each hub has its own worker thread, Status log, LED and editor-lock. Worker
  callbacks are hub-bound (`lambda ..., h=hub:`) and delivered to GUI slots via
  queued Qt signals (safe to touch widgets). Only the SELECTED hub's editor is
  locked while it is connected; the hub list stays live.
- **Theming is tokenised.** `ui/theme.qss` is one skeleton with `__TOKEN__`
  placeholders; `main._PALETTES` holds the dark + light colour maps; the theme
  is persisted in `settings.json` and applied live.
- **Update check is notify-only.** `adc/updates.py` queries GitHub
  `releases/latest` for `luadch-ng/herald`, compares `tag_name` to
  `__version__`, and reports. It NEVER downloads or installs. `check()` never
  raises (offline/error -> `None`). Opt-out: Settings checkbox /
  `--no-update-check`. The opened release URL is constrained to
  `https://github.com`.

---

## 4. Build, run, test

```sh
# GUI
pip install -r requirements.txt
python main.py

# headless / CLI (no Qt)
pip install -r requirements-core.txt
python herald_cli.py --list            # configured hubs
python herald_cli.py --check           # validate config + watch paths here
python herald_cli.py                   # run every enabled hub (Ctrl-C stops)

# release build (PyInstaller onedir -> release/*.zip; needs PyPI `packaging`)
python packaging/build_release.py
```

### Testing contract

- **Pure-core tests run headless** as standalone scripts (exactly as CI runs
  them, no pytest dependency): `python tests/test_adc.py`,
  `python tests/test_tracker.py` (ADC Tiger/base32 vectors, PID->CID,
  tracker/completion) and `python tests/test_config_ux.py` (config-model
  persistence contract). Each exits non-zero on failure. No Qt, no network.
- **GUI logic is tested offscreen.** Drive Qt under `QT_QPA_PLATFORM=offscreen`,
  construct `main.Herald()`, and assert on widget state. Monkeypatch
  `config.save_*` / `config.load_*` (so tests never touch real `data/`; point
  `config.HUBS_FILE` at an existing file to force `_first_run=False`), monkeypatch
  `QMessageBox`/`QDialog.exec` (so modals never block), and use `isHidden()` not
  `isVisible()` for visibility (the top-level window is not shown). Grab a widget
  to a PNG (`widget.grab().save(...)`) to eyeball a visual change.
  `tests/test_gui_ux.py` is the offscreen GUI suite; it runs in the dedicated
  `gui-tests` CI job (installs `requirements.txt`, needs `libegl1`/`libxkbcommon0`
  on Linux), separate from the no-Qt core job.
- **`adc/updates.py` network is tested by monkeypatching `urllib.request.urlopen`**
  to a fake response; never hit the real API in a test.
- Every fix ships a regression test that FAILS pre-fix (rule 1.7).

---

## 5. Conventions

- **Commit style:** concise, imperative, optional `fix #NNN` trailer. Match
  `git log`.
- **Branch model (GitFlow A, like luadch-ng):** long-lived `master` (release
  substrate) + `dev` (staging). Every change is its own short-lived branch
  (`feat/X` / `fix/X`) off `dev`, PR'd into `dev`. When validated, PR
  `dev -> master` as a MERGE commit (never squash the dev->master promotion).
  Tags (`v1.0`, `v1.1`, ...) are cut from `master`. Never tag from `dev`.
- **Releases are what the update check sees.** A GitHub Release (with the built
  zips as assets) on a `vX.Y` tag is what `adc/updates.py` reports to users.
  Bump `adc/__init__.py __version__` to match the tag before building.
- **Secret hygiene:** operator data lives in `data/` (hubs.json with hub
  passwords/keyprints, settings.json, announced_*.txt) and is **gitignored** -
  never commit it. The only in-repo credential is the intentional `dummy`/`test`
  demo default (matches luadch-ng's demo login), which is not a secret. The
  `.gitignore` `data/` rule is the guard; there is no CI secret-scanner (the
  gitleaks action needs a paid license on org accounts, so it was dropped).
- **Lua/Python style:** match the file you are editing. Don't reformat unrelated
  lines. Comments explain *why*, not *what*.
- **No drive-by refactors.** Spot something during an unrelated change? Open an
  issue instead of fixing it inline.

### Privacy note (`data/`)

Never distribute a user's `data/` (it holds hub credentials + bot identity). If
asked to publish or share a config, strip `data/` first.

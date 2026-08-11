# Contributing to Herald

Thanks for helping out. Herald is part of the
[luadch-ng](https://github.com/luadch-ng/luadch-ng) project and follows the same
working agreement - the short version is below; the full one is in
[CLAUDE.md](CLAUDE.md).

## Branch model (GitFlow A)

Two long-lived branches:

- **`master`** - release substrate. Tags (`v1.0`, `v1.1`, ...) are cut from here.
- **`dev`** - staging. Everything lands here first for validation.

Every change is its own short-lived branch off `dev`:

```
git checkout dev
git checkout -b feat/my-change      # or fix/my-change
# ... work ...
# open a PR into dev
```

When `dev` is validated, it is promoted to `master` with a **merge commit**
(never squash the `dev -> master` promotion). Releases are tagged on `master`.

- One logical change per PR. Reference the issue it closes.
- Never commit `data/` (it holds hub passwords, keyprints, bot identities - it
  is gitignored). CI runs a secret scan on every push.
- No em-dashes in any written output; use `-`.

## Tests

The pure-Python core runs headless:

```bash
pip install -r requirements-core.txt
python tests/test_adc.py
python tests/test_tracker.py
```

(The tests are standalone scripts - each exits non-zero on failure - so CI runs
them directly; there is no pytest dependency.)

GUI logic is tested offscreen (`QT_QPA_PLATFORM=offscreen`). Every bug fix should
ship a regression test that fails on the unpatched code and passes with the fix.

## Review

Non-trivial changes get an independent review before merge, covering security,
new bugs, breaking existing behaviour, and consistency. Changes touching network
I/O (the ADC connection or the update check) always do.

## Releases

Bump `adc/__init__.py` `__version__` to match the tag, then a tagged release on
`master` builds the zips and publishes them as assets - which is what the in-app
update check reports to users.

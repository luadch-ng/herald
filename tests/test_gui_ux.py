"""Offscreen GUI regression tests for the Sopor UX batch.

Drives the real `main.Herald` window under QT_QPA_PLATFORM=offscreen (per the
CLAUDE.md testing contract) and asserts the widget-level behaviour of:

  - #4 Release-Log tooltip toggle  (release_tooltips gate on the item tooltip)
  - #1 per-hub Autoconnect          (Config-tab checkbox + startup fan-out)
  - #3 "settings saved" status line (logged on every Settings save)

Construction is disk-free: config load/save are monkeypatched so the test never
touches the operator's data/. Modals are neutralised (QDialog.exec forced to
Accepted). Needs PySide6 (run it in a Qt-enabled job, NOT the no-Qt core job).

RED before the fix:
  - tooltip test: _make_release_item set the tooltip unconditionally, so the
    "off" case would still carry the path -> FAIL.
  - autoconnect test: _autoconnect_startup / _connect_hub did not exist -> the
    call raises AttributeError -> FAIL.
  - settings-save test: no generic line was logged -> "settings saved" absent.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QCheckBox, QDialog  # noqa: E402

from adc import config  # noqa: E402
import main  # noqa: E402

FAILED = []


def check(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    if not cond:
        FAILED.append(name)


def _app():
    return QApplication.instance() or QApplication(["test"])


def _build(settings=None, hubs=None):
    """Construct a Herald window with fully controlled, disk-free config."""
    base = config.default_settings()
    # Never spawn the background GitHub update-check QThread in an offscreen test:
    # it would still be running at process exit and Qt aborts ("QThread destroyed
    # while running", exit 134). Update-check logic is covered by test_updates.py.
    base["check_updates"] = False
    if settings:
        base.update(settings)
    config.load_settings = lambda: dict(base)
    config.save_settings = lambda s: None
    config.save_hubs = lambda h: None
    src = hubs if hubs is not None else [config.default_hub("H1")]
    config.load_hubs = lambda: [dict(h) for h in src]
    config.HUBS_FILE = os.path.abspath(__file__)   # exists -> _first_run False
    return main.Herald()


def test_release_tooltip_toggle():
    _app()
    off = _build(settings={"release_tooltips": False})
    it = off._make_release_item("12:00  Rel", "/mnt/rel/Rel", "/mnt/rel/Rel")
    check("tooltips off -> release item carries no tooltip", it.toolTip() == "")

    on = _build(settings={"release_tooltips": True})
    it2 = on._make_release_item("12:00  Rel", "/mnt/rel/Rel", "/mnt/rel/Rel")
    check("tooltips on -> release item tooltip == path",
          it2.toolTip() == "/mnt/rel/Rel")


def test_autoconnect_startup_and_field():
    _app()
    a, b = config.default_hub("A"), config.default_hub("B")
    a["autoconnect"], b["autoconnect"] = True, False
    w = _build(hubs=[a, b])

    # Config-tab checkbox exists and reflects the loaded (selected) hub.
    check("hub_fields has an 'autoconnect' checkbox",
          isinstance(w.hub_fields.get("autoconnect"), QCheckBox))
    check("autoconnect checkbox reflects the loaded hub (A=True)",
          w.hub_fields["autoconnect"].isChecked() is True)

    # Startup fan-out connects only the flagged hubs.
    called = []
    w._connect_hub = lambda hub: called.append(hub.get("name"))
    w._autoconnect_startup()
    check("startup auto-connects only autoconnect hubs", called == ["A"])

    # Round-trip: toggling the checkbox writes back into the hub dict.
    w.hub_fields["autoconnect"].setChecked(False)
    w._save_hub_fields_to(w.hubs[0])
    check("checkbox writes autoconnect back into the hub",
          w.hubs[0].get("autoconnect") is False)


def test_settings_save_logs_generic_line():
    _app()
    w = _build()
    logged = []
    w._log = lambda line: logged.append(line)
    orig_exec = QDialog.exec
    QDialog.exec = lambda self: QDialog.DialogCode.Accepted
    try:
        w._show_settings()
    finally:
        QDialog.exec = orig_exec
    check("Settings save logs '--- settings saved ---'",
          "--- settings saved ---" in logged)


def test_save_rule_creates_when_none_selected():
    """Fresh hub, no rules: filling the rule editor and clicking Save must
    CREATE a rule from the typed values, not silently do nothing (which forced
    the user to click '+' first and re-enter everything). (Sopor)"""
    from PySide6.QtWidgets import QMessageBox
    _app()
    hub = config.default_hub("H")
    hub["rules"] = []
    w = _build(hubs=[hub])
    QMessageBox.warning = staticmethod(lambda *a, **k: None)   # never block
    check("precondition: no rule selected on a fresh hub", w._rule_row == -1)

    w._set(w.rule_fields["rulename"], "My First Rule")
    w._set(w.rule_fields["path"], "/watch/movies")
    w._set(w.rule_fields["command"], "+addrel")
    w._set(w.rule_fields["category"], "Movies")
    w.rule_blacklist.setPlainText("(nuked)")
    w._save_rule()

    rules = w.hubs[0]["rules"]
    check("Save with no rule selected -> one rule created", len(rules) == 1)
    check("  new rule keeps the typed name", rules and rules[0]["rulename"] == "My First Rule")
    check("  new rule keeps the typed path", rules and rules[0]["path"] == "/watch/movies")
    check("  new rule keeps the typed category", rules and rules[0]["category"] == "Movies")
    check("  new rule keeps the exclude term", rules and rules[0]["blacklist"] == ["(nuked)"])
    check("  the new rule becomes the selected row", w._rule_row == 0)
    check("  and it is the only one (no double-add)", w.rule_list.count() == 1)


def test_save_rule_incomplete_active_lands_inactive():
    """Creating a rule via Save that is marked active but still incomplete must
    land INACTIVE (the same guard a normal save runs), not active-but-broken."""
    from PySide6.QtWidgets import QMessageBox
    _app()
    hub = config.default_hub("H")
    hub["rules"] = []
    w = _build(hubs=[hub])
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    w._set(w.rule_fields["rulename"], "Incomplete")
    w.rule_fields["active"].setChecked(True)
    w._set(w.rule_fields["path"], "")        # missing -> incomplete
    w._set(w.rule_fields["category"], "")
    w._save_rule()
    rules = w.hubs[0]["rules"]
    check("incomplete active rule -> created but forced inactive",
          len(rules) == 1 and rules[0]["active"] is False)


def test_empty_hub_editor_shows_defaults_not_stale():
    """Landing on a hub with no rules shows default_rule() values in the editor,
    not the previously-viewed hub's rule (so Save-without-editing can't clone a
    stale rule)."""
    _app()
    a = config.default_hub("A")
    a["rules"] = [dict(config.default_rule(), rulename="A-rule", path="/a")]
    b = config.default_hub("B")
    b["rules"] = []
    w = _build(hubs=[a, b])
    w.hub_list.setCurrentRow(0)              # A: its rule loads into the editor
    check("hub A rule is in the editor", w.rule_fields["rulename"].text() == "A-rule")
    w.hub_list.setCurrentRow(1)              # B: empty -> editor should reset
    check("switching to empty hub clears the stale name",
          w.rule_fields["rulename"].text() == "")
    check("  and resets _rule_row to -1", w._rule_row == -1)
    check("  editor shows default_rule() blacklist, not hub A's",
          w._lines(w.rule_blacklist) == config.default_rule()["blacklist"])


def test_reapply_autoscroll_on_restore():
    """Restoring Herald from the tray/minimize must RE-ASSERT auto-scroll - while
    hidden the deferred scroll aimed at a stale scrollbar maximum and got stuck
    at the top (Sopor). Verify _reapply_autoscroll re-runs both scrolls when the
    toggles are on, and skips them when off. (The actual scroll landing is
    display-dependent and only reproduces on a real screen.)"""
    _app()
    w = _build()
    calls = []
    w._autoscroll_log_to_bottom = lambda: calls.append("log")
    w._autoscroll_release_to_top = lambda: calls.append("rel")
    w.chk_autoscroll_log.setChecked(True)
    w.chk_autoscroll_rel.setChecked(True)
    w._reapply_autoscroll()
    check("restore re-applies both scrolls when toggles on", calls == ["log", "rel"])
    calls.clear()
    w.chk_autoscroll_log.setChecked(False)
    w.chk_autoscroll_rel.setChecked(False)
    w._reapply_autoscroll()
    check("restore skips scrolls when toggles off", calls == [])


def test_changeevent_restore_from_minimize_schedules_reapply():
    """A restore from a taskbar minimize (WindowStateChange: was-minimized ->
    not-minimized) must schedule the auto-scroll re-apply - guards the wiring
    against a future refactor silently dropping it. (review)"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QWindowStateChangeEvent
    _app()
    w = _build()
    called = []
    w._reapply_autoscroll = lambda: called.append(True)
    # oldState = minimized; the window itself is not minimized (never shown), so
    # the restore-from-minimize branch fires and schedules the deferred re-apply.
    w.changeEvent(QWindowStateChangeEvent(Qt.WindowState.WindowMinimized))
    QApplication.processEvents()
    check("changeEvent restore-from-minimize schedules reapply", called == [True])


def main_():
    test_release_tooltip_toggle()
    test_autoconnect_startup_and_field()
    test_settings_save_logs_generic_line()
    test_save_rule_creates_when_none_selected()
    test_save_rule_incomplete_active_lands_inactive()
    test_empty_hub_editor_shows_defaults_not_stale()
    test_reapply_autoscroll_on_restore()
    test_changeevent_restore_from_minimize_schedules_reapply()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("all gui-ux checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main_())

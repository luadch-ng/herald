"""Herald headless runner - the no-GUI way to run the announcer.

Configure your hubs and rules once in the GUI (they persist to
data/hubs.json), then run this on a server, under systemd, in Docker, or in a
terminal - no display, no Qt, no PySide6 required. It starts one ADC
connection per ENABLED hub (the GUI's per-hub "Enabled" checkbox), each on its
own thread with the same connect -> login -> announce loop the GUI drives.

    python herald_cli.py                 # run every enabled hub
    python herald_cli.py --hub "My hub"  # run only named hub(s), repeatable
    python herald_cli.py --list          # list configured hubs and exit
    python herald_cli.py --check         # validate config + watch paths, exit
    python herald_cli.py --log-file herald.log -v

Watch paths must exist on THIS machine (where the engine runs). If you authored
the config on a different OS, set the target runtime in the GUI (Settings ->
Target runtime OS) so it stops validating paths against the wrong filesystem,
and enter the paths as they are on this machine. --check reports any that are
missing here.

Ctrl-C (SIGINT) or SIGTERM shuts every hub down cleanly.
"""

import argparse
import signal
import sys
import threading
import time

from adc import config, updates
from adc.connection import HubConnection


class _Log:
    """Thread-safe timestamped logger to stdout and, optionally, a file."""

    def __init__(self, log_file=None, verbose=False):
        self._lock = threading.Lock()
        self._fh = open(log_file, "a", encoding="utf-8") if log_file else None
        self.verbose = verbose

    def line(self, hub, message):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        text = f"{ts}  [{hub}]  {message}"
        with self._lock:
            print(text, flush=True)
            if self._fh:
                self._fh.write(text + "\n")
                self._fh.flush()

    def close(self):
        if self._fh:
            self._fh.close()


def _select(hubs, names):
    """Enabled hubs, optionally narrowed to --hub NAME (case-insensitive)."""
    enabled = [h for h in hubs if h.get("enabled", True)]
    if not names:
        return enabled
    wanted = {n.lower() for n in names}
    return [h for h in enabled if h.get("name", "").lower() in wanted]


def cmd_list(hubs):
    print(f"{len(hubs)} hub(s) in {config.HUBS_FILE}:\n")
    for h in hubs:
        state = "enabled " if h.get("enabled", True) else "disabled"
        active = sum(1 for r in h.get("rules", []) if r.get("active"))
        total = len(h.get("rules", []))
        print(f"  [{state}]  {h.get('name', '?'):<24} "
              f"{h.get('host', '?')}:{h.get('port', '?')}   "
              f"{active}/{total} active rule(s)")
    return 0


def cmd_check(selected):
    """Validate that every active rule's watch path exists on THIS machine."""
    if not selected:
        print("No enabled hubs selected - nothing to check.")
        return 1
    ok = True
    tgt = config.target_os()
    if not config.target_is_local():
        print(f"NOTE: target runtime is '{tgt}' but this machine is "
              f"'{config.host_os()}'. Paths are being checked against THIS "
              f"machine - run --check where the engine will actually run.\n")
    for h in selected:
        print(f"{h.get('name', '?')}:")
        rows = config.hub_path_status(h)
        if not rows:
            print("  (no active rules)")
        for rulename, path, exists in rows:
            mark = "OK     " if exists else "MISSING"
            if not exists:
                ok = False
            print(f"  [{mark}] {rulename}: {path or '(empty)'}")
        print()
    print("All watch paths present." if ok else
          "Some watch paths are missing on this machine (rules will idle "
          "until they appear).")
    return 0 if ok else 1


def check_updates(log, force=False):
    """Notify (log only) if a newer Herald release is on GitHub. Throttled to
    once/24h via settings unless forced; silent on offline/error. Never blocks
    startup beyond the short HTTP timeout, and never raises."""
    settings = config.load_settings()
    if not force and not updates.should_check(settings):
        return
    info = updates.check()
    if info is None:
        return                       # offline / rate-limited / error -> silent
    updates.mark_checked(settings, info["latest"])
    try:
        config.save_settings(settings)
    except OSError:
        pass
    if info["update_available"]:
        log.line("herald", f"update available: Herald {info['latest']} "
                           f"(you have {info['current']}) - {info['url']}")
    elif force:
        log.line("herald", f"up to date (Herald {info['current']}).")


def run(selected, log):
    """Start one connection per selected hub and block until a stop signal."""
    stop_event = threading.Event()

    def _handle(_signum, _frame):
        log.line("herald", "shutdown signal received, stopping ...")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    try:
        signal.signal(signal.SIGTERM, _handle)   # not always present on Windows
    except (AttributeError, ValueError):
        pass

    workers = []
    for hub in selected:
        name = hub.get("name", "hub")

        # bind the hub name into each callback so concurrent hubs stay labelled
        def on_status(state, detail, _n=name):
            log.line(_n, f"{state.upper()}  {detail}")

        def on_log(line, _n=name):
            log.line(_n, line)

        def on_info(key, value, _n=name):
            if log.verbose:
                log.line(_n, f"{key}: {value}")

        conn = HubConnection(hub, on_status=on_status, on_log=on_log,
                             on_info=on_info)
        thread = threading.Thread(target=conn.run, name=f"hub:{name}",
                                  daemon=True)
        workers.append((conn, thread))

    # report watch-path status once at startup (the engine skips missing ones)
    for hub in selected:
        for rulename, path, exists in config.hub_path_status(hub):
            if not exists:
                log.line(hub.get("name", "hub"),
                         f"watch path not found here: '{path}' "
                         f"(rule '{rulename}' idles until it appears)")

    log.line("herald", f"starting {len(workers)} hub(s) - Ctrl-C to stop")
    for _conn, thread in workers:
        thread.start()

    try:
        while not stop_event.is_set():
            stop_event.wait(0.5)
    except KeyboardInterrupt:
        stop_event.set()

    for conn, _thread in workers:
        conn.stop()
    for _conn, thread in workers:
        thread.join(timeout=5)
    log.line("herald", "stopped.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="herald_cli.py",
        description="Run the Herald announcer headless (no GUI).")
    ap.add_argument("--hub", action="append", metavar="NAME",
                    help="run only this hub (repeatable); default = all enabled")
    ap.add_argument("--list", action="store_true",
                    help="list configured hubs and exit")
    ap.add_argument("--check", action="store_true",
                    help="validate config and watch paths, then exit")
    ap.add_argument("--log-file", metavar="PATH",
                    help="also append log lines to this file")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="log info frames (SID / keyprint) too")
    ap.add_argument("--no-update-check", action="store_true",
                    help="do not check GitHub for a newer Herald release")
    ap.add_argument("--check-updates", action="store_true",
                    help="check GitHub for a newer release now and exit")
    args = ap.parse_args(argv)

    if args.check_updates:
        log = _Log()
        check_updates(log, force=True)
        log.close()
        return 0

    all_hubs = config.load_hubs()
    if args.list:
        return cmd_list(all_hubs)

    selected = _select(all_hubs, args.hub)
    if args.check:
        return cmd_check(selected)

    if not selected:
        which = "matching --hub" if args.hub else "enabled"
        print(f"No {which} hubs in {config.HUBS_FILE}. "
              f"Enable a hub in the GUI or check the name.", file=sys.stderr)
        return 1

    # give EACH selected hub a stable PID/CID and persist if we generated any,
    # so a restart reconnects as the same bot identity. (A short-circuiting
    # any() would stop assigning after the first hub that needed one.)
    changed = False
    for h in selected:
        if config.ensure_identity(h):
            changed = True
    if changed:
        config.save_hubs(all_hubs)

    log = _Log(args.log_file, args.verbose)
    try:
        if not args.no_update_check:
            check_updates(log)
        return run(selected, log)
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())

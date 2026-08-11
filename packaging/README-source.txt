Herald  -  source package
===============================

Runs on Windows, Linux and macOS. Needs Python 3.10+ (tested on 3.12 and
3.14). Use this if you want to run on Linux / a server, or prefer source.

GUI (desktop):
    pip install -r requirements.txt        # includes PySide6 (Qt)
    python main.py

Headless (server, NO GUI / no Qt needed):
    pip install -r requirements-core.txt   # no PySide6
    python herald_cli.py --list            # show configured hubs
    python herald_cli.py --check           # verify watch paths on THIS box
    python herald_cli.py                    # run every enabled hub (Ctrl-C)

Configure once with the GUI (settings persist to data/hubs.json), then run
headless anywhere. If you configure on Windows but run on Linux, set
Settings -> Target runtime OS to "Linux" in the GUI first, then enter the
paths as they are on the server. deploy/herald.service is an example systemd
unit.

Full details (rules, completion detection, migration from the old announcer,
cross-OS paths): see README.md.

Early TEST build - feedback very welcome, thanks!

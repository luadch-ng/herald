# PyInstaller spec: build the Herald GUI (Herald.exe, windowed) and the
# headless runner (herald-cli.exe, console) into ONE folder so they share the
# bundled runtime AND a single data/ directory next to the executables.
#
#   pyinstaller Herald.spec --noconfirm
#
# Result: dist/Herald/  ->  Herald.exe + herald-cli.exe + _internal/
# Config (data/hubs.json, settings.json) is written next to the exe at runtime.

ICON = "assets/herald.ico"
HIDDEN = ["watchdog", "watchdog.observers", "watchdog.events", "numpy"]

a_gui = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("ui", "ui"), ("assets", "assets")],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

a_cli = Analysis(
    ["herald_cli.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# Share dependencies: the CLI reuses whatever the GUI bundle already carries.
MERGE((a_gui, "main", "Herald"), (a_cli, "herald_cli", "herald-cli"))

pyz_gui = PYZ(a_gui.pure)
pyz_cli = PYZ(a_cli.pure)

exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name="Herald",
    console=False,          # windowed GUI, no console window
    icon=ICON,
    disable_windowed_traceback=False,
)

exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="herald-cli",
    console=True,           # console app so --list/--check output shows
    icon=ICON,
)

coll = COLLECT(
    exe_gui,
    a_gui.binaries,
    a_gui.datas,
    exe_cli,
    a_cli.binaries,
    a_cli.datas,
    strip=False,
    upx=False,
    name="Herald",
)

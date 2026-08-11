"""Build + package a Herald release: Windows onedir zip + source zip.

Run from the repo root:
    python packaging/build_release.py            # PyInstaller build + zip
    python packaging/build_release.py --no-build # zip an existing dist/ only

Outputs Herald-<version>-windows-x64.zip and -source.zip into ./release/.
Version is single-sourced from adc/__init__.py __version__ (see version() below).
"""
import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "packaging")

SKIP_DIRS = {"data", "__pycache__", "build", "dist", ".git", "release",
             ".pytest_cache", ".idea", ".vscode", ".claude"}
SKIP_EXT = {".log", ".pyc", ".pyo"}
# Local-only dev aids (kept out of git AND the public source zip until 1.0).
# Paths are relative to ROOT, forward-slash normalised. Keep in sync with the
# matching .gitignore section.
SKIP_FILES = {"cli_login.py", "tools/fake_releases.py", "packaging/NOTES.md"}


def version():
    text = open(os.path.join(ROOT, "adc", "__init__.py"), encoding="utf-8").read()
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def build():
    subprocess.check_call(
        [sys.executable, "-m", "PyInstaller",
         os.path.join(ROOT, "Herald.spec"), "--noconfirm", "--clean"],
        cwd=ROOT)


def package():
    ver = version()
    out = os.path.join(ROOT, "release")
    os.makedirs(out, exist_ok=True)

    dist = os.path.join(ROOT, "dist", "Herald")
    win = os.path.join(out, f"Herald-{ver}-windows-x64.zip")
    with zipfile.ZipFile(win, "w", zipfile.ZIP_DEFLATED) as z:
        for r, _, fs in os.walk(dist):
            for f in fs:
                full = os.path.join(r, f)
                z.write(full, os.path.join("Herald", os.path.relpath(full, dist)))
        z.write(os.path.join(PKG, "README-windows.txt"), "Herald/READ-ME-FIRST.txt")

    top = f"Herald-{ver}-source"
    src = os.path.join(out, f"Herald-{ver}-source.zip")
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
        for r, ds, fs in os.walk(ROOT):
            ds[:] = [d for d in ds if d not in SKIP_DIRS]
            for f in fs:
                if os.path.splitext(f)[1].lower() in SKIP_EXT:
                    continue
                full = os.path.join(r, f)
                rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                if rel in SKIP_FILES:
                    continue
                z.write(full, os.path.join(top, os.path.relpath(full, ROOT)))
        z.write(os.path.join(PKG, "README-source.txt"),
                os.path.join(top, "READ-ME-FIRST.txt"))

    for p in (win, src):
        print(f"{os.path.getsize(p) / 1048576:6.1f} MB   {p}")


if __name__ == "__main__":
    if "--no-build" not in sys.argv:
        build()
    package()

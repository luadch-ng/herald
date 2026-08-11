"""Release-completion primitives: folder snapshots + SFV parsing/verification.

Used by tracker.py to decide when a watched folder is *done* being written, so
Herald never announces a half-uploaded release. Pure functions, testable
without a hub.

Two completion signals:
  - SFV present  -> scene release: complete when every file the SFV lists exists
    (optionally size-stable / CRC-verified). The authoritative "done" marker.
  - no SFV       -> settle: the folder's snapshot is unchanged for N seconds.

Accuracy details baked in:
  - temp / partial upload files are ignored (.tmp, .part, .!ut, ...)
  - 0-byte files never count as "present"
  - Sample/Proof/Subs/Cover subfolders are excluded from SFV discovery so we
    never announce on a sample's SFV
  - multi-disc: every non-excluded SFV in the tree must be satisfied
"""

import os
import zlib

# Partial-upload / temp extensions to ignore when judging completeness.
TEMP_EXT = (".tmp", ".part", ".partial", ".!ut", ".filepart", ".crdownload",
            ".bc!", ".dctmp", ".!qb")
# Subdirectories that are not the main release payload (scene layout).
SKIP_DIRS = {"sample", "proof", "subs", "subtitle", "subtitles",
             "cover", "covers", "screens", "screenshot", "screenshots"}


def is_temp_name(name):
    low = name.lower()
    return low.startswith(".") or low.endswith(TEMP_EXT)


def _walk_files(root, max_depth):
    def walk(d, depth):
        if depth > max_depth:
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            try:
                if e.is_file():
                    if is_temp_name(e.name):
                        continue
                    st = e.stat()
                    if st.st_size == 0:
                        continue   # 0-byte placeholder: not real content yet
                    yield (e.path, st.st_size, st.st_mtime)
                elif e.is_dir():
                    yield from walk(e.path, depth + 1)
            except OSError:
                continue
    yield from walk(root, 0)


def snapshot(root, max_depth=8):
    """(file_count, total_size, max_mtime) over the tree, temp files excluded.

    Comparing this tuple across observations is how we detect 'still being
    written' - immune to NAS clock skew (we compare our own observations)."""
    count = 0
    total = 0
    newest = 0.0
    for _, size, mtime in _walk_files(root, max_depth):
        count += 1
        total += size
        if mtime > newest:
            newest = mtime
    return (count, total, newest)


def file_snapshot(path):
    """Snapshot for a loose file candidate (checkfiles rules)."""
    try:
        st = os.stat(path)
        return (1, st.st_size, st.st_mtime)
    except OSError:
        return (0, 0, 0.0)


def find_sfvs(root, max_depth=3):
    """All .sfv files in the release tree, excluding sample/proof/subs dirs."""
    out = []

    def walk(d, depth):
        if depth > max_depth:
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            try:
                if e.is_dir():
                    if e.name.lower() not in SKIP_DIRS:
                        walk(e.path, depth + 1)
                elif e.is_file() and e.name.lower().endswith(".sfv"):
                    out.append(e.path)
            except OSError:
                continue

    walk(root, 0)
    return out


def parse_sfv(path):
    """Return [(filename, crc32_lowercase), ...] from an SFV file.

    SFV line = '<filename> <8-hex-crc>'; ';' starts a comment. The filename may
    contain spaces, so the CRC is taken as the last whitespace token."""
    entries = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith(";"):
                    continue
                parts = line.rsplit(None, 1)
                if len(parts) != 2:
                    continue
                name, crc = parts[0].strip(), parts[1].strip().lower()
                # reject a path-bearing entry: an SFV lists plain filenames in
                # its own directory; a `../secret` would turn the CRC check into
                # an arbitrary-file read oracle.
                if "/" in name or "\\" in name or name in ("..", "."):
                    continue
                if len(crc) == 8 and all(c in "0123456789abcdef" for c in crc):
                    entries.append((name, crc))
    except OSError:
        pass
    return entries


def _sfv_files_present(sfv_dir, entries):
    for name, _ in entries:
        p = os.path.join(sfv_dir, name)
        try:
            if not os.path.isfile(p) or os.path.getsize(p) == 0:
                return False
        except OSError:
            return False
    return True


def crc32_file(path):
    crc = 0
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(1 << 20)
                if not block:
                    break
                crc = zlib.crc32(block, crc)
    except OSError:
        return None
    return format(crc & 0xFFFFFFFF, "08x")


def _sfv_crc_ok(sfv_dir, entries):
    for name, crc in entries:
        if crc32_file(os.path.join(sfv_dir, name)) != crc:
            return False
    return True


def sfv_status(root):
    """Inspect the release's SFV(s). Returns:
        (has_sfv, all_present, verify_crc_fn)
    has_sfv     - a non-empty SFV was found
    all_present - every listed file of every SFV exists and is non-empty
    verify_crc_fn - callable() -> bool, full CRC check (only call when present)
    """
    sfvs = find_sfvs(root)
    parsed = []
    for sfv in sfvs:
        entries = parse_sfv(sfv)
        if entries:
            parsed.append((os.path.dirname(sfv), entries))
    if not parsed:
        return (False, False, lambda: False)
    all_present = all(_sfv_files_present(d, e) for d, e in parsed)

    def verify():
        return all(_sfv_crc_ok(d, e) for d, e in parsed)

    return (True, all_present, verify)

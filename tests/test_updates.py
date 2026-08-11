"""Pure-core regression tests for the GitHub update check (adc/updates.py).

Mocks urllib so it never touches the network; runs headless in CI (no Qt).

RED before the fix: a 404 (the repo has no published release yet) was caught by
the generic URLError handler and collapsed to None, so a forced check reported
"could not reach GitHub" instead of "up to date". This asserts 404 -> a real
result (update_available False), and only genuine errors -> None.
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adc import updates

FAILED = []


def check(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    if not cond:
        FAILED.append(name)


class _Resp:
    def __init__(self, body):
        self._b = body.encode() if isinstance(body, str) else body

    def read(self, n=-1):
        return self._b[:n] if (n is not None and n >= 0) else self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(fn):
    updates.urllib.request.urlopen = fn


def _ok(body):
    return lambda req, timeout=6: _Resp(body)


def _raise(exc):
    def f(req, timeout=6):
        raise exc
    return f


def _release(tag):
    return json.dumps({
        "tag_name": tag,
        "html_url": f"https://github.com/luadch-ng/herald/releases/tag/{tag}",
        "body": "notes"})


def main():
    _urlopen(_ok(_release("v2.0")))
    r = updates.check(current="1.0.0")
    check("newer release -> update_available True", bool(r) and r["update_available"] is True)
    check("  latest tag carried", r and r["latest"] == "v2.0")

    # HTTP 404 = repo has no release yet -> REACHABLE, up to date (NOT None)
    _urlopen(_raise(urllib.error.HTTPError("u", 404, "Not Found", {}, None)))
    r = updates.check(current="1.0.0")
    check("404 (no release yet) -> a result, not None", r is not None)
    check("  404 -> update_available False", bool(r) and r["update_available"] is False)
    check("  404 -> latest None", bool(r) and r["latest"] is None)

    # genuinely inconclusive -> None (so the UI can honestly say 'unreachable')
    _urlopen(_raise(urllib.error.HTTPError("u", 403, "rate limited", {}, None)))
    check("403 rate-limit -> None", updates.check(current="1.0.0") is None)
    _urlopen(_raise(urllib.error.URLError("offline")))
    check("offline (URLError) -> None", updates.check(current="1.0.0") is None)
    _urlopen(_ok("{ not valid json"))
    check("bad JSON -> None", updates.check(current="1.0.0") is None)

    # version-compare basics + the 1.x internal -> public 1.0 reset
    check("1.8.0 == 1.8 (no false update)", updates.is_newer("1.8.0", "1.8") is False)
    check("2.0 newer than 1.0.0", updates.is_newer("2.0", "1.0.0") is True)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("all updates checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

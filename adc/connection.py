"""Blocking ADC hub connection + login, ported from the Lua announcer's
core/net.lua. Designed to run on a worker thread: call run() to drive the
connect -> login -> liveness loop with auto-reconnect, stop() to end it.

State is reported through two callbacks so a GUI (or CLI) can render it:
  on_status(state, detail)  - state in STATES below; drives the LED colour
  on_log(line)              - human-readable progress, one line at a time

This is deliberately synchronous (one socket, blocking reads) to stay
readable; the multihub design runs one of these per hub on its own thread.
"""

import hashlib
import os
import random
import socket
import ssl
import threading
import time

from . import announce, config, __version__
from .base32 import to_base32
from .hashes import hash_pas
from .protocol import escape
from .tracker import ReleaseTracker

# LED states the frontend maps to colours.
CONNECTING = "connecting"   # orange - handshake / login in progress
ONLINE = "online"           # green  - logged in
RECONNECTING = "reconnecting"  # orange - lost, backing off to retry
ERROR = "error"             # red    - fatal, not retrying

# Login-phase ISTA codes worth retrying (mirrors net.lua RETRY_ISTA):
# hub-full / nick-taken / CID-taken are transient.
_RETRY_ISTA = {"211", "222", "224"}
_MAX_LOGIN_STATUS = 8  # bound the info-frame skip so a chatty hub can't wedge login
_MAX_FRAME_BYTES = 256 * 1024  # cap one ADC frame; a hub streaming more without a newline is hostile/broken


class LoginError(Exception):
    """Raised on a fatal login rejection (do not retry)."""


class HubConnection:
    def __init__(self, config, on_status=None, on_log=None, on_info=None,
                 on_announce=None):
        self.cfg = config
        self._on_status = on_status or (lambda *_: None)
        self._on_log = on_log or (lambda *_: None)
        self._on_info = on_info or (lambda *_: None)
        self._on_announce = on_announce or (lambda *_: None)
        self._stop = False
        self._sock = None
        self._buf = b""
        self._wake = threading.Event()   # set by the watchdog observer

    # -- public control -----------------------------------------------------
    def stop(self):
        self._stop = True
        self._wake.set()   # interrupt the reconnect backoff so quit/Disconnect returns at once
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass

    def run(self):
        """Blocking connect/login/liveness loop with reconnect. Thread entry."""
        # Reconnect delay = base + random jitter, the DC-client convention
        # (AirDC++ 10 + rand(0,30)s, DC++ 20 + rand(0,30)s). The jitter matters:
        # a fixed delay makes every announcer reconnect at the SAME instant
        # after a hub restart and flood it. Fresh jitter per attempt; both
        # cfg-overridable (reconnect_seconds base, reconnect_jitter spread).
        base = self.cfg.get("reconnect_seconds", 10)
        jitter = self.cfg.get("reconnect_jitter", 30)
        while not self._stop:
            delay = base + random.uniform(0, jitter)
            try:
                self._connect()
                self._login()
                self._status(ONLINE, "Login complete.")
                self._log(f"Successfully connected to '{self.cfg.get('name', 'hub')}' "
                          f"as {self.cfg.get('nick', '?')}.")
                self._liveness_loop()
            except LoginError as e:
                self._status(ERROR, str(e))
                self._log(f"Login rejected by hub (fatal): {e}")
                return
            except (OSError, ssl.SSLError, ConnectionError) as e:
                if self._stop:
                    break
                self._status(RECONNECTING, f"{e} | retry in {delay:.0f}s")
                self._log(f"Connection lost: {e}. Reconnecting in {delay:.0f}s ...")
            except Exception as e:
                # Never let an unexpected error (a malformed config value, a
                # latent scanner/tracker bug) kill the worker silently - the hub
                # would go quiet while looking cleanly "disconnected". Log and
                # reconnect instead.
                if self._stop:
                    break
                self._status(RECONNECTING, f"{e} | retry in {delay:.0f}s")
                self._log(f"Unexpected error: {e!r}. Reconnecting in {delay:.0f}s ...")
            finally:
                self._close()
            if self._stop:
                self._log("Connection closed.")
                break
            # Interruptible backoff: stop() sets _wake, so Disconnect / quit
            # returns immediately instead of blocking up to `delay` seconds - a
            # plain time.sleep here hung shutdown (and crashed on exit via a
            # still-running QThread) whenever a hub was offline.
            self._wake.clear()
            self._wake.wait(delay)

    # -- connect + TLS ------------------------------------------------------
    def _connect(self):
        host = self.cfg["host"]
        port = int(self.cfg["port"])
        self._status(CONNECTING, f"Connecting to {host}:{port} ...")
        self._log(f"Connecting to {host}:{port} ...")
        raw = socket.create_connection((host, port),
                                       timeout=self.cfg.get("sockettimeout", 60))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # Hubs use self-signed certs; verify via keyprint (below), not a CA.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self._sock = ctx.wrap_socket(raw, server_hostname=host)
        self._sock.settimeout(self.cfg.get("sockettimeout", 60))
        self._buf = b""

        der = self._sock.getpeercert(binary_form=True)
        kp = to_base32(hashlib.sha256(der).digest())
        self._on_info("keyprint", f"SHA256/{kp}")
        want = (self.cfg.get("keyprint") or "").strip()
        # accept an operator-pasted "SHA256/<base32>" (the copyable form) as well
        # as the bare base32 - otherwise a natural paste never matches, nudging
        # the operator to clear the pin and run unverified.
        if want[:7].upper() == "SHA256/":
            want = want[7:]
        if want:
            if kp != want:
                raise LoginError("keyprint mismatch")
            self._log("TLS handshake OK, keyprint verified.")
        else:
            self._log(f"TLS handshake OK, but NO keyprint is pinned - the server "
                      f"is UNVERIFIED and MITM-exposed. Pin it in the hub config: "
                      f"SHA256/{kp}")

    # -- login state machine (net.lua order) --------------------------------
    def _login(self):
        c = self.cfg
        self._send("HSUP ADBASE ADTIGR ADOSNR ADKEYP ADADCS ADADC0")

        self._recv_login()  # ISUP (hub support) - we don't require OSNR here
        sid_frame = self._recv_login()  # ISID
        if not sid_frame.startswith("ISID "):
            raise LoginError(f"expected ISID, got: {sid_frame}")
        sid = sid_frame[5:9]
        self._log(f"Assigned SID: {sid}")
        self._on_info("sid", sid)
        self._sid = sid

        self._recv_login()  # IINF (hub info)

        # Declare the announcer's share/slots/upload from config. SL is
        # floored to 1: an announcer serves no files, but a hub's usr_slots
        # gate rejects a login declaring 0 slots (ISTA 120 -> IQUI), so <1
        # would lock Herald out of any default-gated luadch hub. SS (bytes)
        # and US come straight from config (0 is fine - share/upload gates are
        # not hit by an empty share).
        slots = max(1, int(c.get("botslots") or 1))
        # botshare is entered in MB (UI label "Bot share (MB)") but the ADC SS
        # field is BYTES, so convert. DC clients render share in binary units,
        # so treat 1 MB as 1 MiB (1024*1024) - a bare value was previously sent
        # as raw bytes (10000 -> 9.77 KiB in AirDC++ instead of ~9.77 GiB).
        # botupload's UI label is already "Upload (bytes/s)" = the raw US unit.
        share = int(c.get("botshare") or 0) * 1024 * 1024
        upload = int(c.get("botupload") or 0)
        binf = (
            f"BINF {sid}"
            f" NI{escape(c['nick'])}"
            f" DE{escape(c.get('description', 'Herald'))}"
            f" AP{escape(c.get('app', 'Herald'))}"
            f" VE{escape(__version__)}"
            f" PD{c['pid']}"
            f" ID{c['cid']}"
            f" SS{share} SL{slots} US{upload} HN0 HR0 HO0 AW2"
            f" SU{escape('ADC0,ADCS,TCP4,UDP4')}"
            f" I40.0.0.0"
        )
        self._send(binf)

        frame = self._recv_login()  # IGPA (password request) or ISTA/IINF
        if "GPA" not in frame:
            # Some hubs admit unregistered nicks with no password step.
            self._finish_login(frame)
            return
        salt = self._match_salt(frame[5:])
        self._log("Password requested, answering ...")
        self._send("HPAS " + hash_pas(c["password"], salt))

        frame = self._recv_login()  # BINF echo = success, or ISTA = failure
        self._finish_login(frame)

    def _finish_login(self, frame):
        if "BINF" not in frame:
            raise LoginError(f"login failed, last frame: {frame}")
        hubcount = "HO1" if any(t in frame for t in ("CT4", "CT8", "CT16", "OP1")) else "HR1"
        self._send(f"BINF {self._sid} {hubcount}")

    # -- announce + liveness loop -------------------------------------------
    def _liveness_loop(self):
        # Fast poll (every POLL s) of a ReleaseTracker that only emits a
        # release once it is fully written (SFV-complete or settled). A
        # watchdog observer wakes the poll on file events for near-live
        # latency; the poll itself is the authoritative, NAS-safe path.
        # Between polls we read the socket for liveness: a read timeout means
        # idle-but-alive, a clean close returns "" -> reconnect.
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        rules = self.cfg.get("rules", [])
        already = config.load_announced(self.cfg)
        tracker = ReleaseTracker(rules, already, log=self._log)

        # Parse the interval config BEFORE starting the watch observer, so a
        # bad value (hand-edited non-numeric sleeptime) raises here - caught by
        # run()'s reconnect handler - without leaking a started observer thread.
        # monotonic(): these timers must be immune to a wall-clock step (an NTP
        # correction must not stall the poll / keepalive schedule).
        POLL = float(self.cfg.get("sleeptime", 10) or 10)
        keepalive_iv = float(self.cfg.get("announceinterval", 300) or 300)
        next_poll = 0.0
        next_keepalive = time.monotonic() + keepalive_iv
        self._sock.settimeout(1.0)
        observer = self._start_watch(rules)
        try:
            while not self._stop:
                if time.monotonic() >= next_poll or self._wake.is_set():
                    self._wake.clear()
                    self._flush_ready(tracker, already)
                    next_poll = time.monotonic() + POLL
                if time.monotonic() >= next_keepalive:
                    self._send(f"BINF {self._sid} AP{escape(self.cfg.get('app', 'Herald'))}")
                    next_keepalive = time.monotonic() + keepalive_iv
                try:
                    line = self._readline()
                    if line == "":  # peer closed
                        raise ConnectionError("hub closed the connection")
                except socket.timeout:
                    continue  # idle but alive
        finally:
            if observer is not None:
                try:
                    observer.stop()
                    observer.join(timeout=2)
                except Exception:
                    pass

    def _flush_ready(self, tracker, already):
        if not any(r.get("active") for r in self.cfg.get("rules", [])):
            return
        ready = tracker.poll()
        for i, rel in enumerate(ready):
            self._send(f"BMSG {self._sid} {escape(announce.build_command(rel))}")
            already.add(rel.name)
            config.append_announced(self.cfg, rel.name)
            self._log(f"Announced: {rel.name}")
            self._on_announce(rel.name, rel.rule.get("category", ""),
                              rel.path, rel.is_dir)
            if i < len(ready) - 1:
                time.sleep(0.3)   # pace bursts so the hub's flood limit is happy

    def _start_watch(self, rules):
        """Optional watchdog observer: file events wake the poll immediately.
        Best-effort - returns None if watchdog is missing or a path can't be
        watched (e.g. a flaky network share); the poll still covers it."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except Exception:
            return None

        wake = self._wake

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                wake.set()

        observer = Observer()
        watched = 0
        seen = set()
        for rule in rules:
            if not rule.get("active"):
                continue
            path = rule.get("path", "")
            if path and path not in seen and os.path.isdir(path):
                try:
                    observer.schedule(_Handler(), path, recursive=True)
                    watched += 1
                    seen.add(path)
                except Exception:
                    pass
        if watched == 0:
            return None
        try:
            observer.start()
        except Exception:
            return None
        self._log(f"Live watch active on {watched} folder(s).")
        return observer

    # -- framing ------------------------------------------------------------
    def _send(self, frame):
        self._sock.sendall((frame + "\n").encode("utf-8"))

    def _readline(self):
        """Read one '\\n'-terminated ADC frame (without the newline)."""
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                return ""  # peer closed
            self._buf += chunk
            if len(self._buf) > _MAX_FRAME_BYTES:
                # a hub streaming newline-less bytes would otherwise grow the
                # buffer until the process is OOM-killed
                raise ConnectionError("ADC frame exceeds size limit")
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode("utf-8", "replace")

    def _recv_login(self):
        """Read a login frame, transparently skipping info-level ISTA and
        classifying rejections (mirrors net.lua recv_login)."""
        for _ in range(_MAX_LOGIN_STATUS):
            frame = self._readline()
            if frame == "":
                raise ConnectionError("hub closed during login")
            if frame.startswith("ISTA "):
                code = frame[5:8]
                sev = code[:1]
                if sev in ("0", "1"):
                    self._log(f"Hub status (info): {frame}")
                    continue
                if code in _RETRY_ISTA:
                    raise ConnectionError(f"transient rejection {code}: {frame}")
                raise LoginError(f"{code}: {frame}")
            return frame
        raise ConnectionError("too many hub status frames")

    @staticmethod
    def _match_salt(rest):
        out = []
        for ch in rest:
            if ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567":
                out.append(ch)
            else:
                break
        return "".join(out)

    # -- helpers ------------------------------------------------------------
    def _close(self):
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        self._sock = None

    def _status(self, state, detail):
        self._on_status(state, detail)

    def _log(self, line):
        self._on_log(line)

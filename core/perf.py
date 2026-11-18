"""Finds what makes the window slow, on any PC.

* watch(app): notices whenever the window stops responding for more than
  STALL seconds and writes how long it lasted and which code was running to
  logs/perf.log - so a "it froze when I clicked X" report comes with the answer.
* mark(name): notes how long each part of starting up took.
* timed(name): `with perf.timed("list mods"):` notes it when it takes long.
* quiet_widgets(): stops labels and buttons redrawing when they're told to
  show what they already show (the status refresh does that every few seconds).

Only file names, line numbers and timings are written - never settings,
server names or anything typed. The log is kept small (old half dropped).
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

STALL = 0.3          # seconds without the window responding before it's noted
SLOW = 0.25          # timed() blocks longer than this are noted
MAX_BYTES = 400_000
MAX_ENTRIES = 300    # per session

_t0 = time.perf_counter()
_last_mark = _t0
_path: Optional[Path] = None
_lock = threading.Lock()
_count = 0
_beat = time.perf_counter()
_watching = False


def set_log(path) -> None:
    global _path
    _path = Path(path)
    try:
        _path.parent.mkdir(parents=True, exist_ok=True)
        if _path.exists() and _path.stat().st_size > MAX_BYTES:
            data = _path.read_bytes()[-MAX_BYTES // 2:]
            _path.write_bytes(data[data.find(b"\n") + 1:])
    except OSError:
        pass
    _write("---- started %s ----" % time.strftime("%Y-%m-%d %H:%M:%S"), force=True)


def _write(line: str, force: bool = False) -> None:
    global _count
    if _path is None:
        return
    with _lock:
        if not force:
            if _count >= MAX_ENTRIES:
                return
            _count += 1
        try:
            with open(_path, "a", encoding="utf-8") as f:
                f.write(line.rstrip() + "\n")
        except OSError:
            pass


def mark(name: str) -> None:
    """Startup step finished: note how long it took."""
    global _last_mark
    now = time.perf_counter()
    _write("startup  %-28s %6.0f ms  (at %.1f s)" % (name, (now - _last_mark) * 1000, now - _t0), force=True)
    _last_mark = now


@contextmanager
def timed(name: str, slow: float = SLOW):
    t = time.perf_counter()
    try:
        yield
    finally:
        took = time.perf_counter() - t
        if took >= slow:
            where = "window" if threading.current_thread() is threading.main_thread() else "background"
            _write("%s  slow     %-28s %6.0f ms  (%s)" % (time.strftime("%H:%M:%S"), name, took * 1000, where))


def _short_stack(frame, limit: int = 14) -> str:
    """The app's own frames (innermost last), as 'file:line function'."""
    root = str(Path(__file__).resolve().parent.parent)
    out = []
    for fs in traceback.extract_stack(frame):
        fn = fs.filename
        if fn.startswith(root):
            fn = fn[len(root):].lstrip("\\/")
        else:
            fn = ".../" + os.path.basename(os.path.dirname(fn)) + "/" + os.path.basename(fn)
        out.append("%s:%s %s" % (fn.replace("\\", "/"), fs.lineno, fs.name))
    return "\n".join("      " + x for x in out[-limit:])


def watch(app, interval_ms: int = 100) -> None:
    """Start noting window freezes. Call once, on the UI thread, after the window exists."""
    global _watching, _beat
    if _watching or _path is None:
        return
    _watching = True
    main_id = threading.main_thread().ident
    _beat = time.perf_counter()

    def beat():
        global _beat
        _beat = time.perf_counter()
        try:
            app.after(interval_ms, beat)
        except Exception:
            pass

    def checker():
        stack, since = None, None
        while _watching:
            time.sleep(interval_ms / 1000.0)
            late = time.perf_counter() - _beat
            if late > STALL:
                if stack is None:                      # first notice: remember what was running
                    since = _beat
                    try:
                        fr = sys._current_frames().get(main_id)
                        stack = _short_stack(fr) if fr is not None else "      (unknown)"
                    except Exception:
                        stack = "      (unknown)"
            elif stack is not None:                    # responding again: write it down
                _write("%s  FROZE    %.2f s while running:\n%s" % (
                    time.strftime("%H:%M:%S"), _beat - since if since else late, stack))
                stack, since = None, None
    try:
        app.after(interval_ms, beat)
        threading.Thread(target=checker, daemon=True, name="perf-watch").start()
    except Exception:
        _watching = False


def stop() -> None:
    global _watching
    _watching = False


def quiet_widgets() -> int:
    """Make CTkLabel / CTkButton skip configure() calls that change nothing.
    customtkinter redraws on every configure, and the status refresh sets the
    same texts and colours every few seconds. Returns how many classes were
    changed (0 if customtkinter isn't there or TOFUSM_NO_QUIET_WIDGETS is set)."""
    if os.environ.get("TOFUSM_NO_QUIET_WIDGETS"):
        return 0
    try:
        import customtkinter as ctk
    except Exception:
        return 0
    done = 0
    for cls in (getattr(ctk, "CTkLabel", None), getattr(ctk, "CTkButton", None)):
        if cls is None or getattr(cls, "_tofusm_quiet", False):
            continue
        cls.configure = _quiet(cls.configure)
        cls._tofusm_quiet = True
        done += 1
    return done


def _quiet(orig):
    def configure(self, require_redraw=False, **kwargs):
        if kwargs and not require_redraw:
            try:
                same = all(_same(self.cget(k), v) for k, v in kwargs.items())
            except Exception:
                same = False
            if same:
                return None
        return orig(self, require_redraw=require_redraw, **kwargs)
    configure.__wrapped__ = orig
    return configure


def _same(cur, new) -> bool:
    if isinstance(new, (str, int, float, bool, tuple)) or new is None:
        return type(cur) is type(new) and cur == new
    return False            # images, fonts, commands, lists: let them through

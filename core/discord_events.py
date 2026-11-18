"""Game nights: "let's play X on Saturday at 8" events for the Discord bot.

Someone runs /event, the bot posts it with "I'm in" / "Can't make it" buttons,
pings everyone who signed up REMIND_MINUTES before it starts, can wake the server
START_MINUTES before so it's ready, and says "starting now" (with how to join) at
the time. Times are shown with Discord timestamps, so everyone sees their own
local time; what people type is read in the server PC's time zone.

The store is a small JSON file (runtime/discord_events.json) so events survive
restarts. Nothing here needs discord.py, so it can be tested.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

REMIND_MINUTES = 30
START_MINUTES = 10
KEEP_HOURS = 12            # events disappear this long after they start
MAX_EVENTS = 50
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# --------------------------------------------------------------------------- #
# reading "when"
# --------------------------------------------------------------------------- #
def parse_when(text: str, now: Optional[float] = None) -> Optional[float]:
    """A local time someone typed -> unix time, or None.

    Understands: "8pm", "20:00", "tonight 8pm", "tomorrow 7:30pm", "sat 8pm",
    "saturday 20:00", "next friday 9pm", "11/8 7pm" (month/day), "2026-11-08 19:30",
    "in 2h", "in 90m", "in 1h30m", "in 2 hours". A time with no day that already
    passed today means tomorrow.
    """
    from core.nightly import parse_clock
    now = time.time() if now is None else now
    base = datetime.fromtimestamp(now)
    t = re.sub(r"\s+", " ", str(text or "").strip().lower().replace(" at ", " "))
    if not t:
        return None
    m = re.fullmatch(r"in (?:(\d+) ?(?:h|hr|hrs|hour|hours))? ?(?:(\d+) ?(?:m|min|mins|minute|minutes))?", t)
    if m and (m.group(1) or m.group(2)):
        mins = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
        return now + mins * 60 if 0 < mins <= 60 * 24 * 60 else None
    day = None
    rest = t
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})[ t]?(.*)$", t)
    if m:
        try:
            day = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        rest = m.group(4)
    else:
        m = re.match(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))? ?(.*)$", t)
        if m:
            year = int(m.group(3)) if m.group(3) else base.year
            year = year + 2000 if year < 100 else year
            try:
                day = datetime(year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                return None
            if not m.group(3) and day.date() < base.date():
                day = day.replace(year=year + 1)
            rest = m.group(4)
        else:
            words = t.split(" ")
            nxt = False
            if words and words[0] == "next":
                nxt, words = True, words[1:]
            if words and words[0] in ("today", "tonight"):
                day, words = base, words[1:]
            elif words and words[0] in ("tomorrow", "tmrw", "tmr"):
                day, words = base + timedelta(days=1), words[1:]
            elif words:
                hit = [i for i, d in enumerate(DAYS) if len(words[0]) >= 3 and d.startswith(words[0])]
                if hit:
                    ahead = (hit[0] - base.weekday()) % 7
                    if nxt and ahead == 0:
                        ahead = 7
                    day, words = base + timedelta(days=ahead), words[1:]
            rest = " ".join(words)
    rest = rest.strip()
    if rest in ("noon",):
        hm = (12, 0)
    elif rest in ("midnight",):
        hm = (0, 0)
    elif not rest:
        hm = (20, 0) if t.startswith("tonight") else None
    else:
        hm = parse_clock(rest.replace(" ", ""))
    if hm is None:
        return None
    if day is None:
        cand = base.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if cand.timestamp() <= now:
            cand += timedelta(days=1)
        return cand.timestamp()
    cand = day.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if cand.timestamp() <= now and re.match(r"(mon|tue|wed|thu|fri|sat|sun)", t):
        cand += timedelta(days=7)        # "sat 8pm" said on Saturday at 9pm -> next week
    return cand.timestamp()


def stamp(ts: float, style: str = "F") -> str:
    """Discord timestamp: shows in each reader's own time zone."""
    return "<t:%d:%s>" % (int(ts), style)


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #
class EventStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> Dict[str, dict]:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f).get("events") or {}
        except Exception:
            return {}

    def _save(self, events: Dict[str, dict]):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"events": events}, f, indent=1)
        os.replace(tmp, self.path)

    def all(self) -> Dict[str, dict]:
        with self._lock:
            return self._load()

    def get(self, eid: str) -> Optional[dict]:
        return self.all().get(eid)

    def upcoming(self, now: Optional[float] = None) -> List[dict]:
        now = time.time() if now is None else now
        evs = [e for e in self.all().values() if e["when"] + KEEP_HOURS * 3600 > now and not e.get("cancelled")]
        return sorted(evs, key=lambda e: e["when"])

    def create(self, title: str, when: float, creator_id: int, creator_name: str, channel_id: int = 0,
               server_id: Optional[str] = None, server_name: Optional[str] = None, auto_start: bool = True,
               now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        title = re.sub(r"[@`]", "", str(title or "").strip())[:100] or "Game night"
        if when <= now + 60:
            raise ValueError("Pick a time that's still ahead.")
        if when > now + 90 * 86400:
            raise ValueError("Pick a time within the next 90 days.")
        with self._lock:
            events = self._load()
            live = [e for e in events.values() if e["when"] + KEEP_HOURS * 3600 > now and not e.get("cancelled")]
            if len(live) >= MAX_EVENTS:
                raise ValueError("There are already %d upcoming events - cancel some first." % MAX_EVENTS)
            eid = uuid.uuid4().hex[:10]
            ev = {"id": eid, "title": title, "when": float(when), "creator": int(creator_id),
                  "creator_name": str(creator_name)[:60], "channel": int(channel_id or 0), "message": 0, "cid": "tofu",
                  "server_id": server_id, "server_name": server_name, "auto_start": bool(auto_start and server_id),
                  "going": [int(creator_id)], "not_going": [], "reminded": False, "woke": False,
                  "announced": False, "created": now}
            events[eid] = ev
            self._save(events)
            return ev

    def update(self, eid: str, **fields) -> Optional[dict]:
        with self._lock:
            events = self._load()
            ev = events.get(eid)
            if not ev:
                return None
            ev.update(fields)
            self._save(events)
            return ev

    def rsvp(self, eid: str, user_id: int, going: bool) -> Optional[dict]:
        with self._lock:
            events = self._load()
            ev = events.get(eid)
            if not ev or ev.get("cancelled"):
                return None
            uid = int(user_id)
            ev["going"] = [u for u in ev["going"] if u != uid]
            ev["not_going"] = [u for u in ev["not_going"] if u != uid]
            (ev["going"] if going else ev["not_going"]).append(uid)
            self._save(events)
            return ev

    def cancel(self, eid: str) -> Optional[dict]:
        return self.update(eid, cancelled=True)

    def prune(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            events = self._load()
            keep = {k: e for k, e in events.items()
                    if e["when"] + KEEP_HOURS * 3600 > now and not (e.get("cancelled") and e["when"] < now)}
            gone = len(events) - len(keep)
            if gone:
                self._save(keep)
            return gone

    def due(self, now: Optional[float] = None) -> Dict[str, List[dict]]:
        """What the bot should do now: {"remind": [...], "wake": [...], "announce": [...]}.
        Each list holds events whose step is due and not done yet (mark them with update)."""
        now = time.time() if now is None else now
        out = {"remind": [], "wake": [], "announce": []}
        for e in self.upcoming(now):
            late = now - e["when"]
            if not e.get("reminded") and now >= e["when"] - REMIND_MINUTES * 60 and late < 15 * 60:
                out["remind"].append(e)
            if e.get("auto_start") and not e.get("woke") and now >= e["when"] - START_MINUTES * 60 and late < 30 * 60:
                out["wake"].append(e)
            if not e.get("announced") and now >= e["when"] and late < 60 * 60:
                out["announce"].append(e)
        return out


def can_manage(ev: dict, user_id: int, is_admin: bool) -> bool:
    return is_admin or int(user_id) == int(ev.get("creator") or 0)


def describe(ev: dict) -> str:
    """The event message body (Discord markdown)."""
    lines = ["\U0001F4C5 %s (%s)" % (stamp(ev["when"], "F"), stamp(ev["when"], "R"))]
    if ev.get("server_name"):
        lines.append("\U0001F3AE **%s**%s" % (ev["server_name"], " - I'll start it %d minutes before" % START_MINUTES
                                              if ev.get("auto_start") else ""))
    going = ["<@%d>" % u for u in ev.get("going") or []]
    lines.append("✅ Going (%d): %s" % (len(going), ", ".join(going) if going else "nobody yet"))
    if ev.get("not_going"):
        lines.append("❌ Can't make it: %d" % len(ev["not_going"]))
    lines.append("Made by %s · I'll ping everyone going %d minutes before." % (ev.get("creator_name") or "someone",
                                                                             REMIND_MINUTES))
    if ev.get("cancelled"):
        lines.insert(0, "~~cancelled~~")
    return "\n".join(lines)

"""Read and write ARK's Game.ini overrides without touching anything else in the file.

ARK keeps creature spawn rules and loot tables in Game.ini under
[/script/shootergame.shootergamemode] as Unreal "struct" lines, e.g.

    NPCReplacements=(FromClassName="Raptor_Character_BP_C",ToClassName="")
    ConfigOverrideSupplyCrateItems=(SupplyCrateClassString="SupplyCrate_Level03_C",...,ItemSets=(...))

parse_struct()/dump_struct() turn those into Python dicts/lists and back. The
friendly models used by the panels:

* spawn rules: [{"from": cls, "to": cls_or_""}]  ("" = never spawns)
* loot: [{"crate", "mode": "add"|"replace", "picks_min", "picks_max",
          "items": [{"cls", "weight", "qty_min", "qty_max", "quality_min",
                     "quality_max", "blueprint"}]}]
  Lines BaoBun can't show in that shape (several item sets, hand-tuned weights
  per class...) come back as {"crate", "advanced": True, "raw": text} and are
  written back unchanged unless the panel replaces them.

Everything else in the file - other sections, other keys, comments, order - stays
exactly as it was, and a .bak copy is written before each save.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Dict, List, Optional, Tuple

SECTION = "/script/shootergame.shootergamemode"
REL = os.path.join("ShooterGame", "Saved", "Config", "WindowsServer", "Game.ini")
GUS_REL = os.path.join("ShooterGame", "Saved", "Config", "WindowsServer", "GameUserSettings.ini")
SPAWN_KEY = "NPCReplacements"
LOOT_KEY = "ConfigOverrideSupplyCrateItems"
NUM_KEYS = ("SupplyCrateLootQualityMultiplier", "FishingLootQualityMultiplier")


def game_ini(directory: str) -> str:
    return os.path.join(directory or "", REL)


def gus_ini(directory: str) -> str:
    return os.path.join(directory or "", GUS_REL)


# --------------------------------------------------------------------------- #
# Unreal struct text <-> python
# --------------------------------------------------------------------------- #
class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0

    def ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            self.i += 1

    def peek(self):
        self.ws()
        return self.s[self.i] if self.i < len(self.s) else ""

    def value(self):
        c = self.peek()
        if c == "(":
            return self.group()
        if c == '"':
            return self.string()
        return self.bare()

    def string(self):
        self.i += 1
        out = []
        while self.i < len(self.s) and self.s[self.i] != '"':
            out.append(self.s[self.i])
            self.i += 1
        self.i += 1
        return Quoted("".join(out))

    def bare(self):
        start = self.i
        depth = 0
        while self.i < len(self.s):
            ch = self.s[self.i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                break
            self.i += 1
        return self.s[start:self.i].strip()

    def group(self):
        """(A=1,B=2) -> dict, (x,y) / ((..),(..)) -> list, () -> []."""
        self.i += 1
        items, named = [], None
        while True:
            c = self.peek()
            if c == ")":
                self.i += 1
                break
            if c == "":
                break
            m = re.match(r"([A-Za-z_][\w]*(?:\[\d+\])?)\s*=", self.s[self.i:])
            if m:
                key = m.group(1)
                self.i += m.end()
                val = self.value()
                named = named if named is not None else []
                named.append((key, val))
            else:
                items.append(self.value())
            if self.peek() == ",":
                self.i += 1
        if named is not None and not items:
            return Struct(named)
        return items


class Quoted(str):
    """A value that was written in double quotes."""


class Struct(list):
    """Ordered (key, value) pairs - keeps order and repeated keys."""

    def get(self, key, default=None):
        for k, v in self:
            if k.lower() == key.lower():
                return v
        return default

    def set(self, key, value):
        for n, (k, _v) in enumerate(self):
            if k.lower() == key.lower():
                self[n] = (k, value)
                return
        self.append((key, value))


def parse_struct(text: str):
    p = _Parser((text or "").strip())
    if p.peek() != "(":
        return p.value()
    return p.group()


def dump_struct(v) -> str:
    if isinstance(v, Struct):
        return "(" + ",".join("%s=%s" % (k, dump_struct(x)) for k, x in v) + ")"
    if isinstance(v, dict):
        return "(" + ",".join("%s=%s" % (k, dump_struct(x)) for k, x in v.items()) + ")"
    if isinstance(v, list):
        return "(" + ",".join(dump_struct(x) for x in v) + ")"
    if isinstance(v, Quoted):
        return '"%s"' % v.replace('"', "")
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return ("%.4f" % v).rstrip("0").rstrip(".") if v != int(v) else "%.1f" % v
    return str(v)


# --------------------------------------------------------------------------- #
# file access (section-aware, keeps everything else)
# --------------------------------------------------------------------------- #
def _read(path: str) -> List[str]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read().splitlines()


def _section_bounds(lines: List[str], section: str) -> Optional[Tuple[int, int]]:
    start = None
    for n, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if start is not None:
                return start, n
            if s[1:-1].strip().lower() == section.lower():
                start = n
    return (start, len(lines)) if start is not None else None


def read_key(path: str, key: str, section: str = SECTION) -> List[str]:
    lines = _read(path)
    b = _section_bounds(lines, section)
    if not b:
        return []
    out = []
    for line in lines[b[0] + 1:b[1]]:
        k, sep, v = line.partition("=")
        if sep and k.strip().lower() == key.lower():
            out.append(v.strip())
    return out


def write_keys(path: str, values: Dict[str, Optional[List[str]]], section: str = SECTION):
    """Replace every line of each key with the given values (None/[] removes the key).
    New lines go where the key used to be, else at the end of the section."""
    lines = _read(path)
    b = _section_bounds(lines, section)
    if not b:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[%s]" % section)
        b = (len(lines) - 1, len(lines))
    start, end = b
    body = lines[start + 1:end]
    wanted = {k.lower(): (k, v or []) for k, v in values.items()}
    out, placed = [], set()
    for line in body:
        k, sep, _v = line.partition("=")
        lk = k.strip().lower()
        if sep and lk in wanted:
            if lk not in placed:
                placed.add(lk)
                name, vals = wanted[lk]
                out.extend("%s=%s" % (name, v) for v in vals)
            continue
        out.append(line)
    # trailing blank lines stay at the end of the section
    tail = []
    while out and not out[-1].strip():
        tail.insert(0, out.pop())
    for lk, (name, vals) in wanted.items():
        if lk not in placed:
            out.extend("%s=%s" % (name, v) for v in vals)
    new = lines[:start + 1] + out + tail + lines[end:]
    if os.path.isfile(path):
        shutil.copy2(path, path + ".bak")
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\r\n" if os.name == "nt" else "\n") as f:
        f.write("\n".join(new) + "\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# creature spawns
# --------------------------------------------------------------------------- #
def read_spawns(path: str) -> List[dict]:
    out = []
    for raw in read_key(path, SPAWN_KEY):
        st = parse_struct(raw)
        if isinstance(st, Struct) and st.get("FromClassName"):
            out.append({"from": str(st.get("FromClassName")), "to": str(st.get("ToClassName") or "")})
    return out


def spawn_lines(rules: List[dict]) -> List[str]:
    seen, out = set(), []
    for r in rules:
        f = _cls(r.get("from"))
        if not f or f.lower() in seen:
            continue
        seen.add(f.lower())
        out.append(dump_struct(Struct([("FromClassName", Quoted(f)), ("ToClassName", Quoted(_cls(r.get("to"), True)))])))
    return out


def _cls(v, allow_empty=False) -> str:
    s = re.sub(r"[^\w\-./]", "", str(v or ""))[:200]
    if not s and not allow_empty:
        return ""
    return s


# --------------------------------------------------------------------------- #
# loot tables
# --------------------------------------------------------------------------- #
def _num(v, default=0.0) -> float:
    try:
        return float(str(v).strip().strip('"'))
    except (TypeError, ValueError):
        return default


def _truth(v) -> bool:
    return str(v).strip().strip('"').lower() in ("true", "1")


def loot_from_struct(raw: str) -> dict:
    st = parse_struct(raw)
    crate = str(st.get("SupplyCrateClassString") or "") if isinstance(st, Struct) else ""
    adv = {"crate": crate, "advanced": True, "raw": raw}
    if not isinstance(st, Struct) or not crate:
        return adv
    sets = st.get("ItemSets") or []
    if not isinstance(sets, list) or len(sets) != 1 or not isinstance(sets[0], Struct):
        return adv
    s0 = sets[0]
    items = []
    for e in s0.get("ItemEntries") or []:
        if not isinstance(e, Struct):
            return adv
        classes = e.get("ItemClassStrings") or []
        if not isinstance(classes, list) or len(classes) != 1:
            return adv
        bp = e.get("ChanceToBeBlueprintOverride")
        items.append({"cls": str(classes[0]), "weight": _num(e.get("EntryWeight"), 1.0),
                      "qty_min": _num(e.get("MinQuantity"), 1), "qty_max": _num(e.get("MaxQuantity"), 1),
                      "quality_min": _num(e.get("MinQuality"), 0), "quality_max": _num(e.get("MaxQuality"), 0),
                      "blueprint": 1.0 if _truth(e.get("bForceBlueprint")) else _num(bp, 0.0)})
    return {"crate": crate, "mode": "add" if _truth(st.get("bAppendItemSets")) else "replace",
            "picks_min": int(_num(s0.get("MinNumItems"), 1)), "picks_max": int(_num(s0.get("MaxNumItems"), 1)),
            "items": items}


def loot_to_struct(r: dict) -> str:
    if r.get("advanced"):
        return str(r.get("raw") or "")
    crate = _cls(r.get("crate"))
    if not crate:
        raise ValueError("Pick a crate")
    items = [i for i in r.get("items") or [] if _cls(i.get("cls"))]
    if not items:
        raise ValueError("Add at least one item to %s" % crate)
    lo = max(1, int(_num(r.get("picks_min"), 1)))
    hi = max(lo, int(_num(r.get("picks_max"), lo)))
    entries = []
    for i in items:
        qmin = max(1.0, _num(i.get("qty_min"), 1))
        qmax = max(qmin, _num(i.get("qty_max"), qmin))
        lmin = max(0.0, _num(i.get("quality_min"), 0))
        lmax = max(lmin, _num(i.get("quality_max"), lmin))
        bp = min(1.0, max(0.0, _num(i.get("blueprint"), 0)))
        entries.append(Struct([
            ("EntryWeight", max(0.01, _num(i.get("weight"), 1.0))),
            ("ItemClassStrings", [Quoted(_cls(i["cls"]))]), ("ItemsWeights", [1.0]),
            ("MinQuantity", qmin), ("MaxQuantity", qmax), ("MinQuality", lmin), ("MaxQuality", lmax),
            ("bForceBlueprint", bp >= 1.0), ("ChanceToBeBlueprintOverride", bp)]))
    item_set = Struct([("SetName", Quoted("BaoBun")), ("MinNumItems", float(lo)), ("MaxNumItems", float(hi)),
                       ("NumItemsPower", 1.0), ("SetWeight", 1.0), ("bItemsRandomWithoutReplacement", True),
                       ("ItemEntries", entries)])
    st = Struct([("SupplyCrateClassString", Quoted(crate)), ("MinItemSets", 1.0), ("MaxItemSets", 1.0),
                 ("NumItemSetsPower", 1.0), ("bSetsRandomWithoutReplacement", True)])
    if r.get("mode") == "add":
        st.append(("bAppendItemSets", True))
    st.append(("ItemSets", [item_set]))
    return dump_struct(st)


def read_loot(path: str) -> List[dict]:
    return [loot_from_struct(raw) for raw in read_key(path, LOOT_KEY)]


def read_numbers(path: str) -> Dict[str, Optional[float]]:
    out = {}
    for k in NUM_KEYS:
        v = read_key(path, k)
        out[k] = _num(v[-1], 1.0) if v else None
    return out


def save(path: str, spawns: Optional[List[dict]] = None, loot: Optional[List[dict]] = None,
         numbers: Optional[Dict[str, Optional[float]]] = None):
    vals: Dict[str, Optional[List[str]]] = {}
    if spawns is not None:
        vals[SPAWN_KEY] = spawn_lines(spawns)
    if loot is not None:
        seen, lines = set(), []
        for r in loot:
            line = loot_to_struct(r)
            c = (r.get("crate") or "").lower()
            if c in seen and not r.get("advanced"):
                raise ValueError("%s is listed twice - put all its items in one entry" % r.get("crate"))
            seen.add(c)
            lines.append(line)
        vals[LOOT_KEY] = lines
    for k, v in (numbers or {}).items():
        if k in NUM_KEYS:
            vals[k] = None if v in (None, "") else [dump_struct(float(v))]
    if vals:
        write_keys(path, vals)

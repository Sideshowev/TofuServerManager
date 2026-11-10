"""Minimal Steam server query (A2S_INFO) client: online players / max players.

Most Steam dedicated servers answer this UDP query on their "query port"
(Source games, Rust, Valheim, 7 Days to Die, Satisfactory, V Rising, ...).
Protocol: https://developer.valvesoftware.com/wiki/Server_queries
"""

from __future__ import annotations

import socket
import struct
from typing import Optional

A2S_INFO = b"\xFF\xFF\xFF\xFFTSource Engine Query\x00"


def _cstr(data: bytes, pos: int):
    end = data.index(b"\x00", pos)
    return data[pos:end].decode("utf-8", errors="replace"), end + 1


def query_info(host: str, port: int, timeout: float = 1.5) -> Optional[dict]:
    """Return {"name", "map", "players", "max_players", "bots"} or None if no answer."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(A2S_INFO, (host, int(port)))
        data, _ = sock.recvfrom(4096)
        if data[:5] == b"\xFF\xFF\xFF\xFFA":          # challenge (anti-spoofing, 2020+)
            sock.sendto(A2S_INFO + data[5:9], (host, int(port)))
            data, _ = sock.recvfrom(4096)
        if data[:5] != b"\xFF\xFF\xFF\xFFI":
            return None
        pos = 6  # header (4) + type (1) + protocol (1)
        name, pos = _cstr(data, pos)
        map_name, pos = _cstr(data, pos)
        _folder, pos = _cstr(data, pos)
        _game, pos = _cstr(data, pos)
        pos += 2  # app id (short)
        players, max_players, bots = struct.unpack_from("<BBB", data, pos)
        return {"name": name, "map": map_name, "players": players,
                "max_players": max_players, "bots": bots}
    except (OSError, ValueError, struct.error):
        return None
    finally:
        sock.close()


A2S_PLAYER = b"\xFF\xFF\xFF\xFFU"


def query_players(host: str, port: int, timeout: float = 1.5):
    """Names of online players: [{"name", "score", "seconds"}] or None if no answer.
    (Some games report empty names; those are skipped.)"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(A2S_PLAYER + b"\xFF\xFF\xFF\xFF", (host, int(port)))
        data, _ = sock.recvfrom(4096)
        if data[:5] == b"\xFF\xFF\xFF\xFFA":          # challenge
            sock.sendto(A2S_PLAYER + data[5:9], (host, int(port)))
            data, _ = sock.recvfrom(65535)
        if data[:5] != b"\xFF\xFF\xFF\xFFD":
            return None
        count = data[5]
        pos = 6
        out = []
        for _ in range(count):
            pos += 1                                   # index
            name, pos = _cstr(data, pos)
            score, seconds = struct.unpack_from("<lf", data, pos)
            pos += 8
            if name.strip():
                out.append({"name": name, "score": score, "seconds": int(seconds)})
        return out
    except (OSError, ValueError, struct.error, IndexError):
        return None
    finally:
        sock.close()

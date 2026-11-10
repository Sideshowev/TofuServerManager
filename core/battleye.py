"""BattlEye RCon client (UDP) - Arma 3, DayZ and Arma Reforger.

These servers don't read their console window either; BattlEye RCon is how admin
tools talk to them. Packet: "BE" + CRC32 (little endian) of the payload, then
payload = 0xFF, type, data. Types: 0 login, 1 command (with a sequence byte),
2 server message (must be acknowledged).
"""

from __future__ import annotations

import socket
import struct
import time
import zlib
from typing import Dict


class BattlEyeError(Exception):
    pass


def _packet(payload: bytes) -> bytes:
    body = b"\xFF" + payload
    return b"BE" + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF) + body


def _parse(data: bytes):
    """(type, rest) of a valid packet, else None."""
    if len(data) < 8 or data[:2] != b"BE" or data[6] != 0xFF:
        return None
    if struct.unpack("<I", data[2:6])[0] != zlib.crc32(data[6:]) & 0xFFFFFFFF:
        return None
    return data[7], data[8:]


def command(host: str, port: int, password: str, cmd: str, timeout: float = 8.0) -> str:
    """Log in, run one command, return the reply text."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        s.send(_packet(b"\x00" + password.encode("utf-8")))
        end = time.time() + timeout
        logged = None
        while time.time() < end and logged is None:
            try:
                p = _parse(s.recv(4096))
            except (socket.timeout, ConnectionResetError, OSError):
                raise BattlEyeError("No answer from BattlEye RCon on port %s (is RConPort set and the "
                                    "server fully started?)" % port)
            if p and p[0] == 0:
                logged = p[1][:1] == b"\x01"
            elif p and p[0] == 2:
                s.send(_packet(b"\x02" + p[1][:1]))
        if not logged:
            raise BattlEyeError("BattlEye RCon login failed (wrong RConPassword?)")
        seq = 0
        s.send(_packet(b"\x01" + bytes([seq]) + cmd.encode("utf-8")))
        parts: Dict[int, bytes] = {}
        total = None
        end = time.time() + timeout
        while time.time() < end:
            try:
                p = _parse(s.recv(65535))
            except socket.timeout:
                break
            if not p:
                continue
            typ, rest = p
            if typ == 2:   # server chat/log message: acknowledge and ignore
                s.send(_packet(b"\x02" + rest[:1]))
                continue
            if typ != 1 or not rest or rest[0] != seq:
                continue
            data = rest[1:]
            if len(data) >= 3 and data[0] == 0:   # multipart: 0x00, total, index
                total = data[1]
                parts[data[2]] = data[3:]
                if len(parts) >= total:
                    break
                continue
            return data.decode("utf-8", "replace").strip()
        if total and parts:
            return b"".join(parts[i] for i in sorted(parts)).decode("utf-8", "replace").strip()
        if total is None:
            # many commands (say, #lock) send an empty acknowledgement or nothing
            return ""
        raise BattlEyeError("Incomplete reply from BattlEye RCon")
    finally:
        s.close()


def parse_players(text: str):
    """Names from the "players" reply."""
    import re
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*\d+\s+\S+:\d+\s+-?\d+\s+\S+\s+(.+?)\s*$", line)
        if m:
            out.append(re.sub(r"\s*\(Lobby\)$", "", m.group(1)).strip())
    return out

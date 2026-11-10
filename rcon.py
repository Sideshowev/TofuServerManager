"""Lightweight Source RCON client (used by ARK, SRCDS games, Squad, Conan, etc.).

Implements the Valve Source RCON protocol with no third-party dependencies so
the control panel can send admin commands over TCP (for servers that don't
expose a writable stdin, or for remote admin).
"""

import socket
import struct


class RCONError(Exception):
    """Raised when an RCON exchange fails (connection/auth/timeout)."""


def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _packet(packet_id, packet_type, body):
    if isinstance(body, str):
        body = body.encode("utf-8")
    # id + type + body + double null terminator
    payload = struct.pack("<ii", packet_id, packet_type) + body + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def _read_packet(sock):
    header = _recv_exact(sock, 4)
    if len(header) < 4:
        return None
    size = struct.unpack("<i", header)[0]
    if size < 10 or size > 65536:
        return None
    data = _recv_exact(sock, size)
    if len(data) < size:
        return None
    pid = struct.unpack("<i", data[0:4])[0]
    ptype = struct.unpack("<i", data[4:8])[0]
    body = data[8:].split(b"\x00", 1)[0]
    return pid, ptype, body


def rcon_command(host, port, password, command, timeout=15, use_marker=True):
    """Send one command over Source RCON and return the response text.

    After the command we send an empty SERVERDATA_RESPONSE_VALUE packet. The
    server answers packets in order, so once its reply to that marker arrives
    every part of the real (possibly multi-packet) response has been received.
    Servers that don't echo the marker (ARK does not) are handled by a short
    quiet-period read instead of waiting for the full timeout.
    """
    sock = socket.create_connection((host, int(port)), timeout=timeout)
    try:
        sock.settimeout(timeout)

        # Authenticate.
        sock.sendall(_packet(0xB0B1, 3, password))
        authed = False
        for _ in range(16):
            pkt = _read_packet(sock)
            if pkt is None:
                break
            pid, ptype, _body = pkt
            if ptype == 2:  # SERVERDATA_AUTH_RESPONSE
                authed = (pid != -1)
                break
            if ptype == 0 and pid == -1:
                authed = False
                break
        if not authed:
            raise RCONError("RCON authentication failed (wrong admin password?)")

        req_id = 0xB0C2
        marker_id = 0xB0C3
        sock.sendall(_packet(req_id, 2, command))
        if use_marker:
            sock.sendall(_packet(marker_id, 0, ""))
        chunks = []
        got_reply = False
        for _ in range(512):
            try:
                pkt = _read_packet(sock)
            except (socket.timeout, TimeoutError):
                if got_reply:
                    break
                raise RCONError("No response from the server (timed out)")
            if pkt is None:
                break
            pid, ptype, body = pkt
            if pid == marker_id:
                break
            if ptype == 0 and pid == req_id:
                chunks.append(body.decode("utf-8", errors="replace"))
                if not got_reply:
                    got_reply = True
                    sock.settimeout(1.5)  # rest of a multi-packet reply arrives quickly
        return "".join(chunks).strip()
    finally:
        sock.close()

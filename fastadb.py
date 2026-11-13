"""ADB ohne adb.exe-Aufruf: spricht direkt mit dem ADB-Server (127.0.0.1:5037). Spart pro Befehl den Start eines
Programms. Klappt etwas nicht, nimmt device.py wieder adb.exe."""
from __future__ import annotations

import socket


class AdbSocketError(RuntimeError):
    pass


def _recv_exact(s, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise AdbSocketError('ADB-Verbindung beendet')
        buf += chunk
    return bytes(buf)


def _send(s, payload: str):
    data = payload.encode('utf-8')
    s.sendall(b'%04x' % len(data) + data)
    st = _recv_exact(s, 4)
    if st != b'OKAY':
        n = 0
        try:
            n = int(_recv_exact(s, 4), 16)
            msg = _recv_exact(s, n).decode('utf-8', 'replace')
        except Exception:
            msg = ''
        raise AdbSocketError(msg or 'ADB: ' + st.decode('latin1'))


def run(serial: str, service: str, timeout: float = 20.0, port: int = 5037) -> bytes:
    """service z. B. 'exec:screencap' oder 'shell:input tap 1 2'. Gibt die Ausgabe zurück."""
    s = socket.create_connection(('127.0.0.1', port), timeout=timeout)
    try:
        s.settimeout(timeout)
        _send(s, 'host:transport:' + serial)
        _send(s, service)
        chunks = []
        while True:
            c = s.recv(1 << 20)
            if not c:
                break
            chunks.append(c)
        return b''.join(chunks)
    except socket.timeout:
        raise AdbSocketError('ADB antwortet nicht')
    finally:
        s.close()

"""Определение основного IPv4 в LAN (интерфейс маршрута по умолчанию)."""

from __future__ import annotations

import socket


def get_lan_ipv4() -> str | None:
    """Возвращает локальный IPv4, если удалось определить без обращения к внешней сети (UDP connect trick)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip and ip != "127.0.0.1" else None
    except OSError:
        return None

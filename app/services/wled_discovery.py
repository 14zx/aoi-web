"""Поиск контроллеров WLED в LAN (как в официальном приложении).

- mDNS: сервис ``_wled._tcp`` (Zeroconf / Bonjour)
- JSON API: ``GET /json/nodes`` на известном устройстве (список соседей)
- Ручной seed: проверка ``GET /json/info`` по указанному адресу

См. https://kno.wled.ge/interfaces/json-api/ (маршрут ``/json/nodes``).
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .esp32_http import Esp32ProbeResult, probe_esp32

logger = logging.getLogger(__name__)

WLED_MDNS_SERVICE = "_wled._tcp.local."


@dataclass(frozen=True)
class WledCandidate:
    base_url: str
    ip: str
    name: str
    source: str


@dataclass
class WledDiscoverResult:
    devices: list[dict[str, Any]] = field(default_factory=list)
    methods_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


def _join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _base_from_ip(ip: str) -> str:
    return f"http://{ip}"


def _normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = f"http://{u}"
    return u


def _fetch_json(url: str, *, timeout_sec: float) -> dict[str, Any] | list[Any] | None:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        if not raw.strip():
            return None
        return json.loads(raw)


def discover_via_mdns(*, timeout_sec: float) -> tuple[list[WledCandidate], str | None]:
    """Обход mDNS ``_wled._tcp`` (как WLED app / Instance List)."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return [], "Библиотека zeroconf не установлена (pip install zeroconf)"

    found: dict[str, WledCandidate] = {}
    lock = threading.Lock()

    class _Listener:
        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name)
            if info is None:
                return
            for addr in info.addresses:
                if len(addr) != 4:
                    continue
                ip = socket.inet_ntoa(addr)
                host = (info.server or name or "").rstrip(".")
                display = host.replace(".local", "").replace("._wled._tcp", "")
                if not display or display == ip:
                    display = f"WLED {ip}"
                base = _base_from_ip(ip)
                with lock:
                    found[ip] = WledCandidate(
                        base_url=base,
                        ip=ip,
                        name=display,
                        source="mdns",
                    )

        def remove_service(self, *_args: object) -> None:
            pass

        def update_service(self, *_args: object) -> None:
            pass

    zc = Zeroconf()
    listener = _Listener()
    try:
        browser = ServiceBrowser(zc, WLED_MDNS_SERVICE, listener)
        deadline = time.monotonic() + max(0.5, float(timeout_sec))
        while time.monotonic() < deadline:
            time.sleep(0.15)
        del browser
    finally:
        zc.close()

    with lock:
        return list(found.values()), None


def discover_via_nodes(
    *,
    seed_base_url: str,
    timeout_sec: float,
) -> tuple[list[WledCandidate], str | None]:
    """``GET {seed}/json/nodes`` — узлы, уже найденные прошивкой WLED."""
    seed = _normalize_base_url(seed_base_url)
    if not seed:
        return [], "Не указан адрес для запроса /json/nodes"
    url = _join_url(seed, "/json/nodes")
    try:
        parsed = _fetch_json(url, timeout_sec=timeout_sec)
    except urllib.error.HTTPError as exc:
        return [], f"/json/nodes HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return [], f"/json/nodes: {exc.reason}"
    except TimeoutError:
        return [], "Таймаут /json/nodes"
    except (json.JSONDecodeError, OSError) as exc:
        return [], str(exc)

    nodes: list[dict[str, Any]] = []
    if isinstance(parsed, dict):
        raw_nodes = parsed.get("nodes")
        if isinstance(raw_nodes, list):
            nodes = [n for n in raw_nodes if isinstance(n, dict)]
    elif isinstance(parsed, list):
        nodes = [n for n in parsed if isinstance(n, dict)]

    out: list[WledCandidate] = []
    for node in nodes:
        ip = str(node.get("ip") or "").strip()
        if not ip:
            continue
        name = str(node.get("name") or f"WLED {ip}")
        out.append(
            WledCandidate(
                base_url=_base_from_ip(ip),
                ip=ip,
                name=name,
                source="json/nodes",
            )
        )
    return out, None


def discover_via_seed_probe(
    *,
    seed_base_url: str,
    timeout_sec: float,
) -> tuple[list[WledCandidate], str | None]:
    seed = _normalize_base_url(seed_base_url)
    if not seed:
        return [], None
    result = probe_esp32(
        base_url=seed,
        health_path="/json/info",
        timeout_sec=timeout_sec,
    )
    if not result.reachable:
        return [], result.message
    ip = ""
    host = ""
    if seed.startswith("http://"):
        host = seed[7:].split("/")[0].split(":")[0]
    if isinstance(result.device_info, dict):
        ip = str(result.device_info.get("ip") or "").strip()
    if not ip and host:
        ip = host
    name = "WLED"
    if isinstance(result.device_info, dict):
        name = str(result.device_info.get("name") or name)
    return [
        WledCandidate(
            base_url=seed,
            ip=ip,
            name=name,
            source="manual",
        )
    ], None


def _merge_candidates(*groups: list[WledCandidate]) -> list[WledCandidate]:
    by_ip: dict[str, WledCandidate] = {}
    for group in groups:
        for c in group:
            key = c.ip or c.base_url
            if key not in by_ip:
                by_ip[key] = c
            elif by_ip[key].source == "manual" and c.source != "manual":
                by_ip[key] = c
    return list(by_ip.values())


def discover_wled_devices(
    *,
    seed_base_url: str | None = None,
    timeout_sec: float = 3.0,
    use_mdns: bool = True,
    use_nodes: bool = True,
) -> WledDiscoverResult:
    """Собирает кандидатов и проверяет каждый через ``GET /json/info``."""
    t0 = time.perf_counter()
    methods: list[str] = []
    errors: list[str] = []
    groups: list[list[WledCandidate]] = []

    seed = _normalize_base_url(seed_base_url or "")

    if use_mdns:
        mdns_list, mdns_err = discover_via_mdns(timeout_sec=timeout_sec)
        if mdns_list:
            methods.append("mdns")
            groups.append(mdns_list)
        if mdns_err:
            errors.append(mdns_err)

    if seed:
        manual, man_err = discover_via_seed_probe(
            seed_base_url=seed,
            timeout_sec=timeout_sec,
        )
        if manual:
            methods.append("manual")
            groups.append(manual)
        if man_err and not manual:
            errors.append(man_err)

        if use_nodes:
            nodes_list, nodes_err = discover_via_nodes(
                seed_base_url=seed,
                timeout_sec=timeout_sec,
            )
            if nodes_list:
                methods.append("json/nodes")
                groups.append(nodes_list)
            if nodes_err:
                errors.append(nodes_err)

    candidates = _merge_candidates(*groups) if groups else []

    devices: list[dict[str, Any]] = []
    for cand in candidates:
        probe: Esp32ProbeResult = probe_esp32(
            base_url=cand.base_url,
            health_path="/json/info",
            timeout_sec=timeout_sec,
        )
        devices.append(
            {
                "base_url": cand.base_url,
                "ip": cand.ip,
                "name": cand.name,
                "source": cand.source,
                "reachable": probe.reachable,
                "latency_ms": probe.latency_ms,
                "message": probe.message,
                "info": probe.device_info,
            }
        )

    devices.sort(
        key=lambda d: (
            0 if d.get("reachable") else 1,
            d.get("latency_ms") if d.get("latency_ms") is not None else 9999.0,
        )
    )

    return WledDiscoverResult(
        devices=devices,
        methods_used=methods,
        errors=errors,
        duration_ms=(time.perf_counter() - t0) * 1000.0,
    )

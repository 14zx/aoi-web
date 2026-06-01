"""Frozen one-click HTTPS entry point for AOI-Web.

Unlike ``run_https_launcher.py``, this entry point is intended to be bundled
with PyInstaller together with the app and Python dependencies.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

import uvicorn

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _configure_stdio_utf8() -> None:
    """Консоль Windows (cp1252) иначе падает на русских print в frozen exe."""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONUTF8", "1")
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass


def _listen_port() -> int:
    """Порт HTTP(S). По умолчанию 8000; иначе ``AOI_WEB_PORT`` или ``PORT``."""
    raw = (os.environ.get("AOI_WEB_PORT") or os.environ.get("PORT") or "8000").strip()
    try:
        p = int(raw, 10)
    except ValueError:
        print(f"Некорректный порт {raw!r}, используется 8000.", flush=True)
        return 8000
    if not (1 <= p <= 65535):
        print(f"Порт {p} вне диапазона 1–65535, используется 8000.", flush=True)
        return 8000
    return p


def _tcp_port_available(host: str, port: int) -> bool:
    """Проверка без SO_REUSEADDR: на Windows иначе возможен ложный «свободен»."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _should_kill_port_occupiers() -> bool:
    """По умолчанию выключено: часто PID из netstat «призрак» или защищён — taskkill бесполезен и спамит."""
    v = os.environ.get("AOI_WEB_KILL_PORT_OCCUPIER", "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return False


def _should_port_fallback() -> bool:
    """Если выбранный порт занят — взять следующий свободный (8001, 8002, …). Отключить: AOI_WEB_PORT_FALLBACK=0."""
    v = os.environ.get("AOI_WEB_PORT_FALLBACK", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return True


def _pids_listening_on_tcp_port(port: int) -> set[int]:
    """PID-ы с TCP LISTEN на ``port`` (Windows ``netstat``)."""
    pids: set[int] = set()
    if sys.platform != "win32":
        return pids
    proc = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return pids
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.upper().startswith("TCP"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local, state, pid_s = parts[1], parts[3], parts[4]
        if state != "LISTENING":
            continue
        try:
            local_port = int(local.rsplit(":", 1)[-1], 10)
        except (ValueError, IndexError):
            continue
        if local_port != port:
            continue
        try:
            pids.add(int(pid_s, 10))
        except ValueError:
            continue
    return pids


def _windows_pid_exists(pid: int) -> bool:
    if sys.platform != "win32" or pid <= 0:
        return False
    proc = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )
    out = ((proc.stdout or "") + (proc.stderr or "")).lower()
    if "no tasks are running" in out:
        return False
    if "отсутствуют" in out or ("не найден" in out and "задач" in out):
        return False
    return str(pid) in (proc.stdout or "")


def _taskkill(pid: int) -> tuple[bool, str]:
    """``(ok, detail)``. Сначала дерево процессов ``/T``, затем без него."""
    if sys.platform != "win32":
        return False, "not windows"
    last = ""
    for args in (
        ["taskkill", "/PID", str(pid), "/F", "/T"],
        ["taskkill", "/PID", str(pid), "/F"],
    ):
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
        last = ((proc.stderr or "").strip() or (proc.stdout or "").strip())
        if proc.returncode == 0:
            return True, ""
    return False, last


def _try_kill_listeners_once(port: int) -> None:
    """Одна попытка снять LISTENERS (только если включено и PID реально существует)."""
    if not _should_kill_port_occupiers() or sys.platform != "win32":
        return
    me = os.getpid()
    protected = {0, 4}
    for pid in sorted(_pids_listening_on_tcp_port(port) - {me} - protected):
        if not _windows_pid_exists(pid):
            print(
                f"Порт {port}: в netstat указан PID={pid}, но процесса нет "
                f"(устаревшая запись или драйвер). taskkill не нужен.",
                flush=True,
            )
            continue
        print(f"Порт {port} занят процессом PID={pid}. Завершаю…", flush=True)
        ok, detail = _taskkill(pid)
        if ok:
            print(f"Процесс {pid} завершён.", flush=True)
        else:
            print(f"taskkill PID={pid} не сработал: {detail or '(пустой вывод)'}", flush=True)


def _pick_listen_port(preferred: int) -> int:
    """Вернуть ``preferred`` или ближайший свободный порт (если разрешён fallback)."""
    if _tcp_port_available("0.0.0.0", preferred):
        return preferred
    _try_kill_listeners_once(preferred)
    time.sleep(0.5)
    if _tcp_port_available("0.0.0.0", preferred):
        return preferred
    if not _should_port_fallback():
        return preferred
    hi = min(preferred + 64, 65535)
    for p in range(preferred + 1, hi + 1):
        if _tcp_port_available("0.0.0.0", p):
            print(
                f"Порт {preferred} занят и не освободился — переключаюсь на свободный порт {p}.",
                flush=True,
            )
            return p
    return preferred


def _resolve_bundle_base() -> Path:
    """Корень данных portable (``_internal``) или корень проекта в dev."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        internal = exe_dir / "_internal"
        return internal if internal.is_dir() else exe_dir
    return Path(__file__).resolve().parent.parent


def _base_dir() -> Path:
    # app.config.BASE_DIR points to PyInstaller's _internal directory in a
    # one-dir build, and to the project root in normal source execution.
    from app.config import BASE_DIR

    return Path(BASE_DIR)


def _ensure_model_weights_env(base: Path) -> None:
    """Если в bundle есть ``aoi_unified.pt``, а .env указывает на отсутствующий файл — исправить."""
    unified = base / "models" / "aoi_unified.pt"
    if not unified.is_file():
        return

    raw = os.environ.get("MODEL_WEIGHTS_PATH", "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = base / candidate
        if candidate.is_file():
            return

    os.environ["MODEL_WEIGHTS_PATH"] = "models/aoi_unified.pt"
    print(f"MODEL_WEIGHTS_PATH={os.environ['MODEL_WEIGHTS_PATH']} ({unified.name} found)", flush=True)


def _ensure_certs(base: Path) -> None:
    cert = base / "certs" / "cert.pem"
    key = base / "certs" / "key.pem"
    if cert.is_file() and key.is_file():
        return
    from scripts.generate_dev_https_certs import main as generate_certs

    print("Generating dev TLS certificates...")
    old_cwd = Path.cwd()
    os.chdir(base)
    try:
        code = generate_certs()
    finally:
        os.chdir(old_cwd)
    if code:
        raise SystemExit(code)


def _set_public_base_url(base: Path, port: int) -> str | None:
    if os.environ.get("PUBLIC_BASE_URL", "").strip():
        return os.environ["PUBLIC_BASE_URL"].strip()

    from scripts.ensure_public_base_url import _public_base_url_from_dotenv

    if _public_base_url_from_dotenv(base / ".env"):
        # app.config will read the same .env value; do not override it here.
        return None

    from scripts.lan_ip import get_lan_ipv4

    lan = get_lan_ipv4()
    if not lan:
        return None
    url = f"https://{lan}:{port}"
    os.environ["PUBLIC_BASE_URL"] = url
    print(f"Using PUBLIC_BASE_URL={url} for this session (phone / QR links).")
    return url


def main() -> int:
    _configure_stdio_utf8()
    if getattr(sys, "frozen", False):
        # При запуске из Проводника cwd часто System32 / профиль — ломает относительные
        # пути и дочерние процессы. Каталог с exe совпадает с layout one-folder (_internal рядом).
        os.chdir(Path(sys.executable).resolve().parent)
        # Чтобы не путать со старыми копиями exe в других папках.
        print(f"Исполняемый файл: {sys.executable}", flush=True)

    port = _listen_port()
    bundle_base = _resolve_bundle_base()
    _ensure_model_weights_env(bundle_base)
    base = _base_dir()
    os.chdir(base)

    port = _pick_listen_port(port)
    _set_public_base_url(base, port)
    _ensure_certs(base)
    if not _tcp_port_available("0.0.0.0", port):
        print(
            f"Порт {port} недоступен. Задайте другой: set AOI_WEB_PORT=8010\n"
            "Либо включите поиск свободного порта (по умолчанию включён): set AOI_WEB_PORT_FALLBACK=1\n"
            "Проверка: netstat -ano | findstr :" + str(port) + "\n"
            "Резерв Windows: netsh interface ipv4 show excludedportrange protocol=tcp",
            flush=True,
        )
        return 1

    public_base_url = os.environ.get("PUBLIC_BASE_URL", "").strip() or None

    print(f"HTTPS: https://localhost:{port}/")
    if public_base_url:
        print(f"Phone/meta: {public_base_url.rstrip('/')}/")
    print("Stop: Ctrl+C or close this window.")
    print(
        "Порты: set AOI_WEB_PORT=… | авто-смена занятого: AOI_WEB_PORT_FALLBACK=1 (по умолчанию) | "
        "принудительно убить слушателя: AOI_WEB_KILL_PORT_OCCUPIER=1",
        flush=True,
    )
    print()

    # Строка ``app.main:app`` заставляет uvicorn импортировать модуль заново; в
    # части сборок это даёт «Could not import module app.main». Объект ASGI
    # передаём напрямую — импорт идёт здесь, с полным traceback при ошибке.
    try:
        from app.main import app as fastapi_app
    except Exception:
        print("Ошибка импорта app.main (полный traceback ниже):", flush=True)
        traceback.print_exc()
        return 1

    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        ssl_keyfile=str(base / "certs" / "key.pem"),
        ssl_certfile=str(base / "certs" / "cert.pem"),
        reload=False,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())

"""Dev uvicorn launcher with Windows-safe --reload excludes.

Uvicorn FileFilter stores directory excludes as relative paths, but watchfiles
reports absolute paths — ``Path('build') in path.parents`` is always false.
We resolve exclude dirs to absolute before watching starts.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from uvicorn.supervisors import watchfilesreload

_orig_file_filter_init = watchfilesreload.FileFilter.__init__


def _file_filter_init_fixed(self, config) -> None:
    _orig_file_filter_init(self, config)
    cwd = Path.cwd()
    resolved: list[Path] = []
    for p in self.exclude_dirs:
        resolved.append(p if p.is_absolute() else (cwd / p).resolve())
    self.exclude_dirs = resolved


watchfilesreload.FileFilter.__init__ = _file_filter_init_fixed  # type: ignore[method-assign]

RELOAD_EXCLUDES = (".venv", ".venv_local", "build", ".git", "storage", "models")


def main() -> None:
    parser = argparse.ArgumentParser(description="AOI-Web dev server (uvicorn + reload)")
    parser.add_argument("--https", action="store_true", help="TLS via certs/key.pem + certs/cert.pem")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    import uvicorn

    kwargs: dict = {
        "app": "app.main:app",
        "host": "0.0.0.0",
        "port": args.port,
        "reload": True,
        "reload_dirs": [str(root / "app"), str(root / "scripts")],
        "reload_excludes": list(RELOAD_EXCLUDES),
    }
    if args.https:
        kwargs["ssl_keyfile"] = str(root / "certs" / "key.pem")
        kwargs["ssl_certfile"] = str(root / "certs" / "cert.pem")

    uvicorn.run(**kwargs)


if __name__ == "__main__":
    main()

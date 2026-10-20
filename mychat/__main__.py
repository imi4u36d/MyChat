"""Run the local web server: ``python -m mychat``."""

from __future__ import annotations

import uvicorn

from .settings import PORT


def main() -> None:
    uvicorn.run("mychat.web:app", host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    main()

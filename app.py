"""Compatibility entrypoint: ``python app.py`` or ``uvicorn app:app``."""

from mychat.web import app

__all__ = ["app"]

if __name__ == "__main__":
    from mychat.__main__ import main

    main()

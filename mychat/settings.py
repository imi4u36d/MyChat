"""Environment and filesystem configuration for MyChat."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent
STATIC_DIR = PACKAGE_DIR / "static"

# Keep data outside the package so it can never be committed by accident.
DATA_DIR = Path(os.environ.get("WECHAT_DATA_DIR") or (ROOT_DIR / "data"))
DB_PATH = DATA_DIR / "messages.db"
CHATS_JSON = DATA_DIR / "chats.json"
JSONL_PATH = DATA_DIR / "messages.jsonl"
ANALYSIS_JSON = DATA_DIR / "analysis.json"
AI_CACHE_DIR = DATA_DIR / "ai"
IMAGES_DIR = DATA_DIR / "images"

# Companion export tool: decrypted databases + chats/config/image_decode.
WECHAT_TOOL = Path(os.environ.get("WECHAT_TOOL") or (Path.home() / "wechat-export-tool"))

AI_BASE_URL = (os.environ.get("AI_BASE_URL") or "http://127.0.0.1:8000/v1").rstrip("/")
AI_MODEL = os.environ.get("AI_MODEL") or "qwen3.8flash"
AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT") or 300)
AI_CALL_CHARS = int(os.environ.get("AI_CALL_CHARS") or 50000)
AI_CHUNK_CHARS = int(os.environ.get("AI_CHUNK_CHARS") or 60000)

PORT = int(os.environ.get("PORT") or 8765)
LIVE_ENABLED = os.environ.get("WECHAT_LIVE", "1") != "0"
LIVE_POLL = float(os.environ.get("WECHAT_LIVE_POLL") or 3)

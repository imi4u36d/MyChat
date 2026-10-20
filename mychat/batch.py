#!/usr/bin/env python3
"""Background batch: run AI perception/tagging over single chats."""
from __future__ import annotations

import threading
import time

from . import ai, service

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_state: dict = {
    "state": "idle",
    "total": 0,
    "done": 0,
    "ok": 0,
    "failed": 0,
    "current": None,
    "errors": [],
    "started_at": None,
    "finished_at": None,
}


def status() -> dict:
    with _lock:
        return dict(_state, errors=list(_state["errors"]))


def _set(**kw):
    with _lock:
        _state.update(kw)


def _pick(scope: str, limit: int, refresh: bool, analysis_scope: str) -> list[dict]:
    data = service.read_analysis() or {}
    chats = [c for c in data.get("chats") or [] if not c.get("is_group")]
    cached = {x["username"]: x for x in ai.list_cache(chats, analysis_scope)}
    if scope == "recent":
        chats = [c for c in chats if (c.get("recent_30d") or 0) > 0]
    if not refresh:
        chats = [c for c in chats
                 if not (cached.get(c["username"], {}).get("fresh"))]
    chats.sort(key=lambda c: ((c.get("recent_30d") or 0), c.get("msg_count") or 0),
               reverse=True)
    if limit > 0:
        chats = chats[:limit]
    return chats


def start(scope: str = "recent", limit: int = 0, refresh: bool = False,
          analysis_scope: str = ai.DEFAULT_SCOPE) -> dict:
    global _thread
    analysis_scope = ai.normalize_scope(analysis_scope)
    with _lock:
        if _thread is not None and _thread.is_alive():
            raise RuntimeError("AI 批量分析正在运行")
        selected = _pick(scope, limit, refresh, analysis_scope)
        if not selected:
            return {"started": False, "reason": "没有需要分析的单聊（都已完成，或范围为空）",
                    "total": 0}
        _stop.clear()
        _state.update(state="running", total=len(selected), done=0, ok=0, failed=0,
                      current=None, errors=[], started_at=int(time.time()),
                      finished_at=None, scope=scope, refresh=refresh,
                      analysis_scope=analysis_scope)
        _thread = threading.Thread(target=_run, args=(selected, analysis_scope), name="ai-batch",
                                   daemon=True)
        _thread.start()
    return {"started": True, "total": len(selected)}


def stop() -> dict:
    _stop.set()
    return status()


def _run(chats: list[dict], analysis_scope: str) -> None:
    for chat in chats:
        if _stop.is_set():
            _set(state="stopped", finished_at=int(time.time()), current=None)
            return
        _set(current=chat.get("name"))
        try:
            ai.perception_sync(chat, refresh=True, scope=analysis_scope)
            with _lock:
                _state["ok"] += 1
                _state["done"] += 1
        except Exception as e:  # keep going; report per chat
            with _lock:
                _state["failed"] += 1
                _state["done"] += 1
                _state["errors"].append({"name": chat.get("name"), "error": str(e)})
                _state["errors"] = _state["errors"][-8:]
        time.sleep(0.2)
    _set(state="done", finished_at=int(time.time()), current=None)

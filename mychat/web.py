#!/usr/bin/env python3
"""WeChat relationship insight web app (local only)."""
from __future__ import annotations

import asyncio
import contextlib
import csv
import datetime as dt
import io
import json
import os
import queue
from urllib.parse import urlparse

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import ai, batch, dataset, service
from .settings import CHATS_JSON, STATIC_DIR
from .source import wechat_send

STATIC = str(STATIC_DIR)


@contextlib.asynccontextmanager
async def lifespan(app):
    service.ensure_index_async()
    service.start_live()
    yield


async def api_live(request):
    q = service.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    ev = q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(1)
                    yield ": ping\n\n"
                    continue
                yield f"event: {ev.get('type', 'message')}\n"
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            service.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


_send_lock = None


def _send_sync(username: str, text: str) -> dict:
    global _send_lock
    import threading
    if _send_lock is None:
        _send_lock = threading.Lock()
    with _send_lock:
        cfg = service.load_config()
        return wechat_send.send_message_tool(username, text, cfg=cfg)


def _allowed_origin(origin: str | None) -> bool:
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")


async def api_send(request):
    if not _allowed_origin(request.headers.get("origin")):
        return JSONResponse({"error": "forbidden origin"}, status_code=403)
    username = request.path_params["username"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求体必须是 JSON"}, status_code=400)
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)
    if len(text) > 2000:
        return JSONResponse({"error": "消息超过 2000 字"}, status_code=400)
    if not service.chat_exists(username):
        return JSONResponse({"error": "聊天不存在"}, status_code=404)
    if wechat_send is None:
        return JSONResponse({"error": "helper_unavailable",
                             "message": "发送助手未安装"}, status_code=503)
    result = await asyncio.to_thread(_send_sync, username, text)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


async def api_send_status(_request):
    if wechat_send is None:
        return JSONResponse({"running": False, "trusted": False,
                             "error": "helper_unavailable"},
                            headers={"Cache-Control": "no-store"})
    result = await asyncio.to_thread(wechat_send.helper_check)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


async def api_live_status(_request):
    return JSONResponse(service.live_status(), headers={"Cache-Control": "no-store"})


async def index(_request):
    return FileResponse(os.path.join(STATIC, "index.html"),
                        headers={"Cache-Control": "no-store"})


async def api_status(_request):
    return JSONResponse(service.status(), headers={"Cache-Control": "no-store"})


async def api_analysis(_request):
    data = service.read_analysis()
    if data is None:
        return JSONResponse({"error": "索引尚未生成", "status": service.status()}, status_code=503)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


def _chat(username: str | None) -> dict | None:
    if not username:
        return None
    data = service.read_analysis() or {}
    return next((c for c in data.get("chats") or [] if c.get("username") == username), None)


async def api_ai_status(_request):
    return JSONResponse(ai.status(), headers={"Cache-Control": "no-store"})


async def api_ai_tags(_request):
    data = service.read_analysis() or {}
    return JSONResponse({"items": ai.list_cache(data.get("chats") or [])},
                        headers={"Cache-Control": "no-store"})


async def api_chat_ai(request):
    username = request.path_params["username"]
    chat = _chat(username)
    if chat is None:
        return JSONResponse({"error": "聊天不存在"}, status_code=404)
    scope = ai.normalize_scope(request.query_params.get("scope"))
    data = ai.cached(username, scope)
    perception = data.get("perception") or {}
    guidance = data.get("guidance") or {}
    stale = bool(perception) and not (
        perception.get("last_ts") == chat.get("last_ts")
        and perception.get("msg_count") == chat.get("msg_count"))
    return JSONResponse({
        "username": username,
        "scope": scope,
        "scopes": ai.SCOPE_LABELS,
        "scope_info": ai.describe_scope(chat, scope),
        "perception": perception or None,
        "guidance": guidance or None,
        "stale": stale,
    }, headers={"Cache-Control": "no-store"})


async def _ai_body(request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def api_ai_perception(request):
    body = await _ai_body(request)
    chat = _chat(body.get("username"))
    if chat is None:
        return JSONResponse({"error": "聊天不存在，或分析索引未就绪"}, status_code=404)
    scope = ai.normalize_scope(body.get("scope"))
    try:
        result = await ai.perception(chat, bool(body.get("refresh")), scope)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except ai.AIError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


async def api_ai_guidance(request):
    body = await _ai_body(request)
    chat = _chat(body.get("username"))
    if chat is None:
        return JSONResponse({"error": "聊天不存在，或分析索引未就绪"}, status_code=404)
    msgs = service.messages(chat["username"], limit=ai.SAMPLE_MESSAGES) or []
    if not msgs:
        return JSONResponse({"error": "没有可分析的消息"}, status_code=400)
    try:
        result = await ai.guidance(chat, msgs, str(body.get("draft") or ""),
                                   str(body.get("goal") or ""),
                                   bool(body.get("refresh")))
    except ai.AIError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


async def api_ai_ask(request):
    body = await _ai_body(request)
    chat = _chat(body.get("username"))
    if chat is None:
        return JSONResponse({"error": "聊天不存在，或分析索引未就绪"}, status_code=404)
    question = str(body.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "问题不能为空"}, status_code=400)
    history = body.get("history") if isinstance(body.get("history"), list) else []
    msgs = service.messages(chat["username"], limit=ai.SAMPLE_MESSAGES) or []
    if not msgs:
        return JSONResponse({"error": "没有可分析的消息"}, status_code=400)
    try:
        result = await ai.ask(chat, msgs, question, history)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except ai.AIError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


async def api_ai_batch(request):
    if request.method == "GET":
        return JSONResponse(batch.status(), headers={"Cache-Control": "no-store"})
    body = await _ai_body(request)
    scope = body.get("scope") if body.get("scope") in ("recent", "all") else "recent"
    analysis_scope = ai.normalize_scope(body.get("analysis_scope"))
    try:
        limit = max(0, min(200, int(body.get("limit") or 0)))
    except (TypeError, ValueError):
        limit = 0
    try:
        result = batch.start(scope, limit, bool(body.get("refresh")), analysis_scope)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    return JSONResponse(result, status_code=202 if result.get("started") else 200)


async def api_ai_batch_stop(_request):
    batch.stop()
    return JSONResponse(batch.status(), status_code=202)


async def api_sync(_request):
    if not service.start_sync():
        return JSONResponse({"error": "同步任务已在运行"}, status_code=409)
    return JSONResponse({"ok": True}, status_code=202)


async def api_messages(request):
    username = request.path_params["username"]
    try:
        limit = max(10, min(300, int(request.query_params.get("limit", 80))))
        before = request.query_params.get("before")
        before = int(before) if before else None
    except ValueError:
        return JSONResponse({"error": "参数无效"}, status_code=400)
    msgs = service.messages(username, limit=limit, before=before)
    if msgs is None:
        return JSONResponse({"error": "聊天不存在或索引未就绪"}, status_code=404)
    return JSONResponse({"messages": msgs}, headers={"Cache-Control": "no-store"})


async def api_avatar(request):
    username = request.path_params["username"]
    av = service.self_avatar() if username == "self" else service.avatar(username)
    if av is None:
        return Response(status_code=404)
    return Response(av["data"], media_type=av["mime"], headers={
        "Cache-Control": "public, max-age=86400",
        "ETag": f'"{av["md5"] or ""}"',
    })


async def api_image(request):
    md5 = request.path_params["md5"].lower()
    path = service.image_path(md5)
    if not path:
        return Response(status_code=404)
    return FileResponse(path, headers={
        "Cache-Control": "public, max-age=604800",
        "ETag": f'"{md5}"',
    })


async def api_dataset_chats(_request):
    path = str(CHATS_JSON)
    if not os.path.exists(path):
        return JSONResponse({"error": "数据集尚未生成"}, status_code=503)
    return FileResponse(path, media_type="application/json",
                        headers={"Cache-Control": "no-store"})


async def api_export(request):
    fmt = request.query_params.get("format", "jsonl").lower()
    chat = request.query_params.get("chat")
    if fmt not in ("jsonl", "csv"):
        return JSONResponse({"error": "format 必须是 jsonl 或 csv"}, status_code=400)

    def rows():
        con = dataset.connect()
        if con is None:
            return
        try:
            names = {r["username"]: r for r in con.execute("SELECT * FROM chats")}
            sql = "SELECT * FROM messages"
            args = ()
            if chat:
                sql += " WHERE chat_username=?"
                args = (chat,)
            sql += " ORDER BY chat_username, ts, id"
            if fmt == "csv":
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["chat", "chat_name", "is_group", "ts", "time",
                            "sender", "is_self", "kind", "text", "md5"])
                yield "\ufeff" + buf.getvalue()
                for r in con.execute(sql, args):
                    m = names.get(r["chat_username"])
                    iso = dt.datetime.fromtimestamp(r["ts"]).isoformat(sep=" ") if r["ts"] else ""
                    buf.seek(0)
                    buf.truncate()
                    w.writerow([r["chat_username"], m["name"] if m else "",
                                int(m["is_group"]) if m else "", r["ts"],
                                iso, r["sender"], r["is_self"], r["kind"], r["text"], r["md5"]])
                    yield buf.getvalue()
            else:
                import json
                for r in con.execute(sql, args):
                    m = names.get(r["chat_username"])
                    yield json.dumps({
                        "chat": r["chat_username"],
                        "chat_name": m["name"] if m else r["chat_username"],
                        "is_group": bool(m["is_group"]) if m else None,
                        "ts": r["ts"],
                        "time": dt.datetime.fromtimestamp(r["ts"]).isoformat(sep=" ") if r["ts"] else None,
                        "sender": r["sender"],
                        "is_self": bool(r["is_self"]),
                        "kind": r["kind"],
                        "text": r["text"],
                        "md5": r["md5"],
                    }, ensure_ascii=False) + "\n"
        finally:
            con.close()

    name = f"wechat_messages.{fmt}"
    return StreamingResponse(rows(), media_type="text/csv" if fmt == "csv" else "application/x-ndjson",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


routes = [
    Route("/", index),
    Route("/api/status", api_status),
    Route("/api/analysis", api_analysis),
    Route("/api/dataset/chats", api_dataset_chats),
    Route("/api/avatars/{username}", api_avatar),
    Route("/api/images/{md5}", api_image),
    Route("/api/export", api_export),
    Route("/api/live", api_live),
    Route("/api/live/status", api_live_status),
    Route("/api/send/status", api_send_status),
    Route("/api/chats/{username}/send", api_send, methods=["POST"]),
    Route("/api/sync", api_sync, methods=["POST"]),
    Route("/api/ai/status", api_ai_status),
    Route("/api/ai/tags", api_ai_tags),
    Route("/api/ai/perception", api_ai_perception, methods=["POST"]),
    Route("/api/ai/guidance", api_ai_guidance, methods=["POST"]),
    Route("/api/ai/ask", api_ai_ask, methods=["POST"]),
    Route("/api/ai/batch", api_ai_batch, methods=["GET", "POST"]),
    Route("/api/ai/batch/stop", api_ai_batch_stop, methods=["POST"]),
    Route("/api/chats/{username}/ai", api_chat_ai),
    Route("/api/chats/{username}/messages", api_messages),
    Mount("/static", app=StaticFiles(directory=STATIC), name="static"),
]

app = Starlette(routes=routes, lifespan=lifespan)



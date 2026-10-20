#!/usr/bin/env python3
"""Runtime service: sync job, index cache and message access."""
from __future__ import annotations

import contextlib
import glob
import io
import json
import os
import queue
import re
import sqlite3
import subprocess
import threading
import time
from collections import deque

from . import dataset, stats
from .settings import (
    ANALYSIS_JSON,
    IMAGES_DIR,
    LIVE_ENABLED,
    LIVE_POLL,
    WECHAT_TOOL,
)
from .source import chats, config, image_decode

TOOL = str(WECHAT_TOOL)
DATA = str(ANALYSIS_JSON)

_log_lock = threading.Lock()
_status_lock = threading.Lock()
_sync_thread: threading.Thread | None = None
_status = {
    "state": "idle",          # idle | indexing | running | done | error
    "log": deque(maxlen=400),
    "started_at": None,
    "finished_at": None,
    "error": None,
}

def _log(line: str):
    line = line.rstrip("\n")
    if not line:
        return
    with _log_lock:
        _status["log"].append(line)


def load_config():
    return config.load_config()


def source_latest_mtime() -> float:
    try:
        db_dir = load_config()["db_dir"]
    except SystemExit:
        return 0
    latest = 0.0
    for dirpath, _dirs, files in os.walk(db_dir):
        for f in files:
            if f.endswith(".db") or f.endswith(".db-wal"):
                try:
                    latest = max(latest, os.path.getmtime(os.path.join(dirpath, f)))
                except OSError:
                    pass
    return latest


def read_analysis() -> dict | None:
    try:
        with open(DATA, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def status() -> dict:
    with _status_lock:
        st = {
            "state": _status["state"],
            "log": list(_status["log"])[-80:],
            "started_at": _status["started_at"],
            "finished_at": _status["finished_at"],
            "error": _status["error"],
        }
    data = read_analysis()
    st["has_data"] = data is not None
    st["generated_at"] = data.get("generated_at") if data else None
    src = source_latest_mtime()
    live_ok = bool(_live_state.get("enabled")) and _live_state.get("state") == "watching"
    st["stale"] = bool(data and src > data.get("generated_at", 0)) and not live_ok
    st["live"] = _live_state.get("state")
    return st


def _set(state: str, **kw):
    with _status_lock:
        _status["state"] = state
        _status.update(kw)


def _run_decrypt():
    _log("执行微信数据库解密（增量）…")
    env = dict(os.environ)
    env["WECHAT_NO_SCAN"] = "1"
    cmd = [os.path.join(TOOL, "wechat"), "decrypt"]
    proc = subprocess.Popen(
        cmd, cwd=TOOL, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    deadline = time.time() + 600
    assert proc.stdout is not None
    for line in proc.stdout:
        _log(line)
        if time.time() > deadline:
            proc.kill()
            raise RuntimeError("解密超时（600 秒）")
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"解密失败，退出码 {code}（密钥缺失时先运行 capture_keys.sh）")
    _log("解密完成")


def _build_index():
    _log("整理格式化数据集（data/messages.db / messages.jsonl）…")
    meta = dataset.build(export_jsonl=True, verbose=True)
    _log(f"数据集完成：{meta['chats']} 个聊天 / {meta['messages']} 条消息")

    _log("重建分析索引…")
    data = stats.build(verbose=True)
    _log(f"索引完成：{data['totals']['singles']} 个单聊 / "
         f"{data['totals']['messages']} 条消息")


def _job(index_only: bool):
    try:
        _set("indexing" if index_only else "running",
             started_at=int(time.time()), finished_at=None, error=None)
        if not index_only:
            _run_decrypt()
        _build_index()
        _set("done", finished_at=int(time.time()))
    except Exception as e:  # noqa: BLE001 - report to UI
        _log(f"ERROR: {e}")
        _set("error", finished_at=int(time.time()), error=str(e))


def ensure_index_async():
    if read_analysis() is not None:
        return
    with _status_lock:
        if _status["state"] in ("indexing", "running"):
            return
    t = threading.Thread(target=_job, args=(True,), daemon=True)
    t.start()


def start_sync() -> bool:
    global _sync_thread
    with _status_lock:
        if _status["state"] in ("indexing", "running"):
            return False
    _sync_thread = threading.Thread(target=_job, args=(False,), daemon=True)
    _sync_thread.start()
    return True


def messages(username: str, limit: int = 80, before: int | None = None) -> list[dict] | None:
    if read_analysis() is None:
        return None
    return dataset.messages(username, limit=limit, before=before)


_image_lock = threading.Lock()
_image_index: dict[str, str] | None = None
_image_index_key = None
_image_keys: tuple | None = None


def self_avatar():
    try:
        uid = config.load_config().get("self_wxid")
    except Exception:
        uid = None
    return avatar(uid) if uid else None


def avatar(username: str):
    con = dataset.connect()
    if con is None:
        return None
    try:
        row = con.execute("SELECT mime, image, md5 FROM avatars WHERE username=?",
                          (username,)).fetchone()
        if not row:
            return None
        return {"mime": row["mime"] or "image/jpeg", "data": bytes(row["image"]),
                "md5": row["md5"]}
    finally:
        con.close()


def image_path(md5: str) -> str | None:
    global _image_index, _image_index_key
    if not re.fullmatch(r"[0-9a-f]{32}", md5 or ""):
        return None
    try:
        key = os.path.getmtime(os.path.join(TOOL, "export"))
    except OSError:
        return None
    with _image_lock:
        if _image_index is None or _image_index_key != key:
            index: dict[str, str] = {}
            root = os.path.join(TOOL, "export")
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    base, ext = os.path.splitext(fn)
                    if ext.lower() not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                        continue
                    if base.endswith("_t"):
                        base = base[:-2]
                    if len(base) != 32:
                        continue
                    prev = index.get(base)
                    # Prefer the full-size file over a thumbnail.
                    if prev is None or ("_t." in os.path.basename(prev) and "_t." not in fn):
                        index[base] = os.path.join(dirpath, fn)
            # Include images decoded on demand by this service.
            for fn in (glob.glob(os.path.join(str(IMAGES_DIR), "*.*"))
                       if os.path.isdir(str(IMAGES_DIR)) else []):
                base = os.path.splitext(os.path.basename(fn))[0]
                if len(base) == 32:
                    index.setdefault(base, fn)
            _image_index, _image_index_key = index, key
        hit = _image_index.get(md5)
    if hit:
        return hit
    return _decode_image(md5)


def _decode_image(md5: str) -> str | None:
    """On-demand decode of a new image message from WeChat's encrypted .dat."""
    global _image_keys
    con = dataset.connect()
    if con is None:
        return None
    try:
        row = con.execute("SELECT chat_username, ts FROM messages "
                          "WHERE md5=? LIMIT 1", (md5,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    try:
        cfg = load_config()
        dat = image_decode.find_image_for_message(
            cfg["wechat_base_dir"], row["chat_username"], md5=md5, create_time=row["ts"])
        if not dat:
            return None
        if _image_keys is None:
            _image_keys = image_decode.get_image_keys(cfg)
        aes_key, xor_key = _image_keys
        data, ext = image_decode.decode_dat(dat, aes_key, xor_key)
        out_dir = str(IMAGES_DIR)
        os.makedirs(out_dir, mode=0o700, exist_ok=True)
        out = os.path.join(out_dir, f"{md5}.{ext}")
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        with _image_lock:
            if _image_index is not None:
                _image_index[md5] = out
        return out
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Live ingestion: watch source message DBs, decrypt changed snapshots, append
# new rows to the dataset, and push an SSE event to open pages.
# ---------------------------------------------------------------------------

_live_state = {
    "enabled": LIVE_ENABLED,
    "state": "off" if not LIVE_ENABLED else "starting",
    "events": 0,
    "decrypted": 0,
    "last_decrypt_at": None,
    "last_event_at": None,
    "last_chat": None,
    "error": None,
}
_subs_lock = threading.Lock()
_subs: set[queue.Queue] = set()
_analysis_write_lock = threading.Lock()


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=200)
    with _subs_lock:
        _subs.add(q)
    return q


def unsubscribe(q: queue.Queue):
    with _subs_lock:
        _subs.discard(q)


def _publish(event: dict):
    with _subs_lock:
        targets = list(_subs)
    for q in targets:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


def live_status() -> dict:
    return dict(_live_state)


def start_live():
    if not LIVE_ENABLED:
        return
    t = threading.Thread(target=_live_loop, name="wechat-live", daemon=True)
    t.start()


def _bump_analysis(updates: list[dict]):
    if not updates:
        return
    with _analysis_write_lock:
        data = read_analysis()
        if not data:
            return
        by = {c.get("username"): c for c in data.get("chats", [])}
        delta = 0
        for u in updates:
            c = by.get(u["username"])
            if not c:
                continue
            c["msg_count"] = u["total"]
            c["last_ts"] = u["last_ts"]
            c["mine"] = c.get("mine", 0) + u["mine"]
            c["theirs"] = c.get("theirs", 0) + u["theirs"]
            delta += u["new"]
        data["totals"]["messages"] = data["totals"].get("messages", 0) + delta
        data["generated_at"] = int(time.time())
        tmp = DATA + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, DATA)


def _import_new(changed_paths: set[str]) -> list[dict]:
    cfg = load_config()
    dec = cfg["decrypted_dir"]
    self_wxid = cfg["self_wxid"]
    contacts = chats.load_contacts(dec)
    room_nicks = chats.load_group_nicknames(dec)
    chat_list = chats.list_chats(dec, contacts=contacts)

    con = sqlite3.connect(dataset.DB_PATH, timeout=8)
    con.execute("PRAGMA busy_timeout=8000")
    updates = []
    try:
        for chat in chat_list:
            paths = {os.path.abspath(p) for p, _t in chat.get("tables", [])}
            if not (paths & changed_paths):
                continue
            old = con.execute(
                "SELECT msg_count, first_ts, last_ts FROM chats WHERE username=?",
                (chat["username"],)).fetchone()
            old_count, old_first, old_last = old if old else (0, None, None)
            if old and old_count == chat["msg_count"] and old_last == chat["last_ts"]:
                continue
            recs = list(chats.iter_messages(chat, dec, self_wxid, contacts, room_nicks,
                                            with_packed_info=True))
            batch = []
            mine = theirs = 0
            for r in recs:
                ts = r.get("ts") or 0
                if ts < (old_last or 0):
                    continue
                kind = r.get("kind") or "other"
                md5 = (image_decode.image_md5_from_packed_info(r.get("packed_info_data"))
                       if kind in ("image", "video") else None)
                is_self = int(bool(r.get("is_self")))
                batch.append((chat["username"], ts, r.get("sender") or "", is_self,
                              kind, r.get("text") or "", md5, r.get("local_id")))
            inserted = 0
            for row in batch:
                cur = con.execute(
                    "INSERT OR IGNORE INTO messages "
                    "(chat_username, ts, sender, is_self, kind, text, md5, local_id) "
                    "VALUES (?,?,?,?,?,?,?,?)", row)
                if cur.rowcount:
                    inserted += 1
                    if row[3]:
                        mine += 1
                    else:
                        theirs += 1
            first_ts = old_first
            if first_ts is None and recs:
                first_ts = min((r.get("ts") or 0) for r in recs)
            con.execute(
                "INSERT INTO chats (username, name, is_group, msg_count, first_ts, last_ts) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET "
                "name=excluded.name, msg_count=excluded.msg_count, "
                "first_ts=excluded.first_ts, last_ts=excluded.last_ts",
                (chat["username"], chat["name"], int(bool(chat.get("is_group"))),
                 chat["msg_count"], first_ts, chat["last_ts"]))
            if inserted:
                updates.append({
                    "username": chat["username"],
                    "name": chat["name"],
                    "is_group": bool(chat.get("is_group")),
                    "new": inserted, "total": chat["msg_count"],
                    "last_ts": chat["last_ts"],
                    "mine": mine, "theirs": theirs,
                })
        con.commit()
    finally:
        con.close()
    return updates


def _live_loop():
    import decrypt_db as crypto

    sig_cache: dict[str, object] = {}
    _live_state.update(state="watching", error=None)

    while True:
        try:
            if _status["state"] in ("running", "indexing"):
                time.sleep(max(1.0, LIVE_POLL))
                continue
            cfg = load_config()
            db_dir = cfg["db_dir"]
            dec = cfg["decrypted_dir"]
            keys = crypto.load_keys()
            if not keys:
                raise RuntimeError("all_keys.json 为空，先运行 capture_keys.sh")
            state = crypto.load_state(dec)
            changed: set[str] = set()

            for src in sorted(glob.glob(os.path.join(db_dir, "message", "message_*.db"))):
                sig = crypto.source_sig(src)
                if sig is None or sig_cache.get(src) == sig:
                    continue
                sig_cache[src] = sig
                rel = os.path.relpath(src, db_dir).replace(os.sep, "/")
                if rel not in keys:
                    continue
                out = os.path.join(dec, rel)
                if crypto.is_current(state, rel, src, out):
                    continue
                key = bytes.fromhex(keys[rel]["enc_key"])
                with contextlib.redirect_stdout(io.StringIO()):
                    got = crypto.decrypt_database(src, out, key)
                if got:
                    crypto.save_state(dec, {rel: got})
                    changed.add(os.path.abspath(out))
                    _live_state["decrypted"] += 1
                    _live_state["last_decrypt_at"] = int(time.time())

            if changed:
                updates = _import_new(changed)
                if updates:
                    _bump_analysis(updates)
                    _live_state["events"] += 1
                    _live_state["last_event_at"] = int(time.time())
                    _live_state["last_chat"] = updates[0]["name"]
                    _publish({
                        "type": "messages",
                        "chats": updates,
                        "new": sum(u["new"] for u in updates),
                        "at": int(time.time()),
                    })
            _live_state["state"] = "watching"
            _live_state["error"] = None
        except Exception as e:  # noqa: BLE001 - keep watching
            _live_state["state"] = "error"
            _live_state["error"] = str(e)
            time.sleep(max(3.0, LIVE_POLL))
        time.sleep(max(1.0, LIVE_POLL))


def chat_exists(username: str) -> bool:
    data = read_analysis()
    if not data:
        return False
    return any(c.get("username") == username for c in data.get("chats", []))

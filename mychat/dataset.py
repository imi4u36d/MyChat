#!/usr/bin/env python3
"""Normalize WeChat data into a stable local dataset.

Outputs (under ./data):
  messages.db    SQLite: chats + messages, canonical schema
  chats.json     chat metadata
  messages.jsonl one formatted message per line (for inspection / re-analysis)

This layer is intentionally independent of the scoring logic: change stats.py
without touching decryption or parsing.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sqlite3
import time

from . import settings
from .source import chats, config, image_decode

DATA = str(settings.DATA_DIR)
DB_PATH = str(settings.DB_PATH)
CHATS_JSON = str(settings.CHATS_JSON)
JSONL = str(settings.JSONL_PATH)

SCHEMA = """
CREATE TABLE chats (
  username TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  is_group INTEGER NOT NULL,
  msg_count INTEGER NOT NULL,
  first_ts INTEGER,
  last_ts INTEGER
);
CREATE TABLE messages (
  id INTEGER PRIMARY KEY,
  chat_username TEXT NOT NULL,
  ts INTEGER NOT NULL,
  sender TEXT,
  is_self INTEGER NOT NULL,
  kind TEXT NOT NULL,
  text TEXT,
  md5 TEXT,
  local_id INTEGER
);
CREATE INDEX idx_messages_chat_ts ON messages(chat_username, ts);
CREATE INDEX idx_messages_md5 ON messages(md5);
CREATE UNIQUE INDEX idx_messages_uid ON messages(chat_username, local_id, ts);
CREATE TABLE avatars (
  username TEXT PRIMARY KEY,
  md5 TEXT,
  mime TEXT,
  image BLOB,
  update_time INTEGER
);
"""


def build(export_jsonl: bool = True, verbose: bool = True) -> dict:
    cfg = config.load_config()
    dec = cfg["decrypted_dir"]
    self_wxid = cfg["self_wxid"]
    contacts = chats.load_contacts(dec)
    room_nicks = chats.load_group_nicknames(dec)
    chat_list = chats.list_chats(dec, contacts=contacts)

    os.makedirs(DATA, exist_ok=True)
    tmp = DB_PATH + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")

    chat_rows = []
    total = 0
    jsonl_f = open(JSONL + ".tmp", "w", encoding="utf-8") if export_jsonl else None
    try:
        for i, chat in enumerate(chat_list, 1):
            recs = list(chats.iter_messages(chat, dec, self_wxid, contacts, room_nicks,
                                            with_packed_info=True))
            if not recs:
                continue
            first_ts = min((r["ts"] for r in recs if r.get("ts")), default=None)
            last_ts = max((r["ts"] for r in recs if r.get("ts")), default=None)
            chat_rows.append((chat["username"], chat["name"], int(bool(chat.get("is_group"))),
                              len(recs), first_ts, last_ts))
            con.execute("INSERT INTO chats VALUES (?,?,?,?,?,?)", chat_rows[-1])
            batch = []
            for r in recs:
                ts = r.get("ts") or 0
                sender = r.get("sender") or ""
                is_self = int(bool(r.get("is_self")))
                kind = r.get("kind") or "other"
                text = r.get("text") or ""
                md5 = None
                if kind in ("image", "video"):
                    md5 = image_decode.image_md5_from_packed_info(r.get("packed_info_data"))
                local_id = r.get("local_id")
                batch.append((chat["username"], ts, sender, is_self, kind, text, md5, local_id))
                if jsonl_f:
                    jsonl_f.write(json.dumps({
                        "chat": chat["username"],
                        "chat_name": chat["name"],
                        "is_group": bool(chat.get("is_group")),
                        "ts": ts,
                        "time": dt.datetime.fromtimestamp(ts).isoformat(sep=" ") if ts else None,
                        "sender": sender,
                        "is_self": bool(is_self),
                        "kind": kind,
                        "text": text,
                        "md5": md5,
                        "local_id": local_id,
                    }, ensure_ascii=False) + "\n")
            con.executemany(
                "INSERT INTO messages (chat_username, ts, sender, is_self, kind, text, md5, local_id) "
                "VALUES (?,?,?,?,?,?,?,?)", batch)
            total += len(batch)
            if verbose and i % 25 == 0:
                print(f"  …{i}/{len(chat_list)} chats, {total} messages", flush=True)

        avatar_count = 0
        head_db = os.path.join(dec, "head_image", "head_image.db")
        if os.path.exists(head_db):
            src = sqlite3.connect(f"file:{head_db}?mode=ro", uri=True)
            try:
                for username, md5, buf, ut in src.execute(
                        "SELECT username, md5, image_buffer, update_time FROM head_image"):
                    if not buf:
                        continue
                    head = bytes(buf[:4])
                    mime = ("image/png" if head.startswith(b"\x89PNG")
                            else "image/gif" if head.startswith(b"GIF8")
                            else "image/webp" if head[:4] == b"RIFF" else "image/jpeg")
                    con.execute("INSERT OR REPLACE INTO avatars VALUES (?,?,?,?,?)",
                                (username, md5, mime, bytes(buf), ut or 0))
                    avatar_count += 1
            finally:
                src.close()
        con.commit()
    finally:
        if jsonl_f:
            jsonl_f.close()
        con.close()

    os.replace(tmp, DB_PATH)
    os.chmod(DB_PATH, 0o600)
    if export_jsonl:
        os.replace(JSONL + ".tmp", JSONL)
        os.chmod(JSONL, 0o600)

    meta = {
        "generated_at": int(time.time()),
        "self_wxid": self_wxid,
        "chats": len(chat_rows),
        "messages": total,
        "avatars": avatar_count,
        "schema": {
            "chats": ["username", "name", "is_group", "msg_count", "first_ts", "last_ts"],
            "messages": ["chat_username", "ts", "sender", "is_self", "kind", "text", "md5", "local_id"],
            "avatars": ["username", "md5", "mime", "image", "update_time"],
        },
    }
    fd = os.open(CHATS_JSON, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "chats": [
            dict(zip(("username", "name", "is_group", "msg_count", "first_ts", "last_ts"), row))
            for row in chat_rows
        ]}, f, ensure_ascii=False, indent=1)
    if verbose:
        print(f"[+] {DB_PATH}: {len(chat_rows)} chats, {total} messages", flush=True)
    return meta


def connect():
    if not os.path.exists(DB_PATH):
        return None
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def messages(chat_username: str, limit: int = 80, before: int | None = None) -> list[dict] | None:
    con = connect()
    if con is None:
        return None
    try:
        exists = con.execute("SELECT 1 FROM chats WHERE username=?", (chat_username,)).fetchone()
        if not exists:
            return None
        sql = ("SELECT ts, sender, is_self, kind, text, md5, local_id FROM messages "
               "WHERE chat_username=?")
        args = [chat_username]
        if before:
            sql += " AND ts < ?"
            args.append(before)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(limit)
        rows = con.execute(sql, args).fetchall()
        return [dict(r) | {"is_self": bool(r["is_self"])} for r in reversed(rows)]
    finally:
        con.close()


def export_jsonl(path: str, chat: str | None = None) -> int:
    src = connect()
    if src is None:
        raise SystemExit("dataset missing; run `python -m mychat.dataset` first")
    n = 0
    try:
        names = {r["username"]: r for r in src.execute("SELECT * FROM chats")}
        sql = "SELECT * FROM messages"
        args = ()
        if chat:
            sql += " WHERE chat_username=?"
            args = (chat,)
        sql += " ORDER BY chat_username, ts, id"
        with open(path, "w", encoding="utf-8") as f:
            for r in src.execute(sql, args):
                meta = names.get(r["chat_username"])
                f.write(json.dumps({
                    "chat": r["chat_username"],
                    "chat_name": meta["name"] if meta else r["chat_username"],
                    "is_group": bool(meta["is_group"]) if meta else None,
                    "ts": r["ts"],
                    "time": dt.datetime.fromtimestamp(r["ts"]).isoformat(sep=" ") if r["ts"] else None,
                    "sender": r["sender"],
                    "is_self": bool(r["is_self"]),
                    "kind": r["kind"],
                    "text": r["text"],
                    "md5": r["md5"],
                }, ensure_ascii=False) + "\n")
                n += 1
    finally:
        src.close()
    return n


def export_csv(path: str, chat: str | None = None) -> int:
    src = connect()
    if src is None:
        raise SystemExit("dataset missing; run `python -m mychat.dataset` first")
    n = 0
    try:
        sql = ("SELECT chat_username, ts, sender, is_self, kind, text, md5 FROM messages")
        args = ()
        if chat:
            sql += " WHERE chat_username=?"
            args = (chat,)
        sql += " ORDER BY chat_username, ts, id"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["chat", "ts", "time", "sender", "is_self", "kind", "text", "md5"])
            for r in src.execute(sql, args):
                w.writerow([r["chat_username"], r["ts"],
                            dt.datetime.fromtimestamp(r["ts"]).isoformat(sep=" ") if r["ts"] else "",
                            r["sender"], int(r["is_self"]), r["kind"], r["text"], r["md5"]])
                n += 1
    finally:
        src.close()
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build/normalize the WeChat dataset")
    ap.add_argument("--no-jsonl", action="store_true")
    ap.add_argument("--jsonl", metavar="PATH", help="export messages to this JSONL and exit")
    ap.add_argument("--csv", metavar="PATH", help="export messages to this CSV and exit")
    ap.add_argument("--chat", help="limit export to one chat username")
    a = ap.parse_args()
    if a.jsonl:
        print(f"[+] {export_jsonl(a.jsonl, a.chat)} messages -> {a.jsonl}")
    elif a.csv:
        print(f"[+] {export_csv(a.csv, a.chat)} messages -> {a.csv}")
    else:
        build(export_jsonl=not a.no_jsonl)


def search(chat_username: str, terms: list[str], limit: int = 24) -> list[dict]:
    """Keyword hits inside one chat, oldest-first, for AI grounding."""
    terms = [t for t in dict.fromkeys(terms) if t][:12]
    if not terms:
        return []
    con = connect()
    if con is None:
        return []
    try:
        where = " OR ".join("text LIKE ?" for _ in terms)
        rows = con.execute(
            "SELECT ts, sender, is_self, kind, text FROM messages "
            f"WHERE chat_username=? AND ({where}) ORDER BY ts DESC LIMIT ?",
            [chat_username] + [f"%{t}%" for t in terms] + [limit]).fetchall()
        return [dict(r) | {"is_self": bool(r["is_self"])} for r in reversed(rows)]
    finally:
        con.close()


def chat_facts(chat_username: str) -> dict:
    """Aggregate facts for grounding AI answers."""
    con = connect()
    if con is None:
        return {}
    try:
        row = con.execute(
            "SELECT COUNT(*) n, MIN(ts) first_ts, MAX(ts) last_ts FROM messages "
            "WHERE chat_username=?", (chat_username,)).fetchone()
        senders = [dict(r) for r in con.execute(
            "SELECT sender, COUNT(*) n FROM messages WHERE chat_username=? "
            "AND is_self=0 GROUP BY sender ORDER BY n DESC LIMIT 5", (chat_username,))]
        months = [dict(r) for r in con.execute(
            "SELECT strftime('%Y-%m', ts, 'unixepoch', 'localtime') m, COUNT(*) n "
            "FROM messages WHERE chat_username=? GROUP BY m ORDER BY m DESC LIMIT 12",
            (chat_username,))]
        return {
            "total": row["n"] if row else 0,
            "first_ts": row["first_ts"] if row else None,
            "last_ts": row["last_ts"] if row else None,
            "senders": senders,
            "months": list(reversed(months)),
        }
    finally:
        con.close()


def _scope_clause(chat_username: str, since: int | None, offset: int):
    where = "chat_username=?"
    args: list = [chat_username]
    if since:
        where += " AND ts >= ?"
        args.append(int(since))
    return where, args, offset


def scope_stats(chat_username: str, since: int | None = None, offset: int = 0) -> dict:
    """Count and time span of a message slice, without loading message bodies."""
    con = connect()
    if con is None:
        return {"count": 0, "first_ts": None, "last_ts": None}
    try:
        where, args, off = _scope_clause(chat_username, since, offset)
        row = con.execute(
            "SELECT COUNT(*) n, MIN(ts) first_ts, MAX(ts) last_ts FROM ("
            f"SELECT ts FROM messages WHERE {where} ORDER BY ts, id LIMIT -1 OFFSET ?)",
            args + [off]).fetchone()
        return {"count": row["n"] if row else 0,
                "first_ts": row["first_ts"] if row else None,
                "last_ts": row["last_ts"] if row else None}
    finally:
        con.close()


def messages_range(chat_username: str, since: int | None = None,
                   offset: int = 0) -> list[dict]:
    """Messages in a time slice / offset, oldest-first, without md5 payloads."""
    con = connect()
    if con is None:
        return []
    try:
        where, args, off = _scope_clause(chat_username, since, offset)
        rows = con.execute(
            "SELECT ts, sender, is_self, kind, text FROM messages "
            f"WHERE {where} ORDER BY ts, id LIMIT -1 OFFSET ?",
            args + [off]).fetchall()
        return [dict(r) | {"is_self": bool(r["is_self"])} for r in rows]
    finally:
        con.close()

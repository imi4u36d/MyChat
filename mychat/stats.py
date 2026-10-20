#!/usr/bin/env python3
"""Build the behaviour index from the normalized SQLite dataset.

Produces data/analysis.json: per-chat behaviour metrics, a relative
behaviour score, dimension breakdown, evidence quotes and a narrative.

Scores are statistical descriptions of chat behaviour, not relationship
judgements; AI results are the source of truth for attitude and labels.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import re
import sqlite3
import statistics
import time

from .settings import ANALYSIS_JSON, DB_PATH

DATASET = str(DB_PATH)
OUT = str(ANALYSIS_JSON)

SESSION_GAP = 6 * 3600          # silence that starts a new conversation
REPLY_WINDOW = 24 * 3600
RECENT_DAYS = 30
MIN_COHORT = 20                 # chats below this are scored but flagged
MAX_EVIDENCE = 4

WARM = [
    "想你", "想我", "喜欢你", "爱你", "亲爱的", "宝贝", "宝宝", "宝子", "么么",
    "亲亲", "抱抱", "心疼", "关心", "担心", "辛苦", "加油", "晚安", "早安",
    "注意身体", "照顾", "开心", "高兴", "可爱", "真好", "嘿嘿", "哈哈", "嘻嘻",
    "好呀", "没问题", "等你", "陪你", "一起", "谢谢你", "感谢", "想见", "见面",
    "在吗", "睡了吗", "吃了吗", "到家", "小心", "顺利", "恭喜", "生日快乐",
    "节日快乐", "平安", "摸摸", "乖", "听话", "放心", "早点休息", "多喝",
    "保暖", "照顾好自己", "想你了", "喜欢你哦", "爱你哦",
]

COLD = [
    "呵呵", "随便", "算了", "不用", "没事", "忙着", "没空", "再说", "改天",
    "睡了", "困了", "拜拜", "再见", "无语", "烦", "行吧", "好吧", "那行",
    "知道了", "收到", "不用了", "不需要", "不必", "没时间", "下次吧",
    "再说吧", "不清楚", "不知道", "你自己", "别烦", "懒得", "哦", "嗯",
]

QUESTION_RE = re.compile(
    r"[?？]|吗|呢|什么|怎么|为什么|哪|谁|几点|多少|是不是|要不要|有没有|好不好|行不行"
)


def _clean(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


def _hits(text: str, words: list[str]) -> int:
    return sum(1 for w in words if w in text)


def _fmt_seconds(sec: float | None) -> str:
    if sec is None:
        return "—"
    if sec < 90:
        return f"{int(sec)} 秒"
    if sec < 5400:
        return f"{int(sec / 60)} 分钟"
    if sec < 172800:
        return f"{sec / 3600:.1f} 小时"
    return f"{sec / 86400:.1f} 天"


def _pct(values: list[float], v: float | None, higher_better: bool = True) -> float:
    """Percentile rank of v among values, 0..100. None means no signal."""
    if v is None or not values:
        return 0.0 if higher_better else 0.0
    less = sum(1 for x in values if x < v)
    equal = sum(1 for x in values if x == v)
    p = (less + 0.5 * equal) / len(values) * 100.0
    return p if higher_better else 100.0 - p


def chat_metrics(chat: dict, recs: list[dict]) -> dict | None:
    if not recs:
        return None

    now = time.time()
    total = len(recs)
    mine = theirs = 0
    their_texts: list[str] = []
    their_chars: list[int] = []
    warm = cold = questions = emoji = media = late = 0
    sessions = init_other = init_self = 0
    my_bursts = replies = 0
    latencies: list[float] = []
    double_texts = 0
    recent = prior = 0
    senders: collections.Counter = collections.Counter()
    evidence: list[tuple[int, int, str, str]] = []
    first_ts = last_ts = None
    last_text = ""

    prev_ts = None
    prev_self = None

    for r in recs:
        ts = r.get("ts") or 0
        is_self = bool(r.get("is_self"))
        kind = r.get("kind") or "other"
        text = r.get("text") or ""

        if is_self:
            mine += 1
        else:
            theirs += 1
            senders[r.get("sender") or "?"] += 1
            if kind == "emoji":
                emoji += 1
            if kind in ("image", "video", "voice", "link", "file", "chat_history",
                        "transfer", "redpacket", "location"):
                media += 1
            if ts:
                hour = dt.datetime.fromtimestamp(ts).hour
                if hour >= 23 or hour < 3:
                    late += 1
            if kind in ("text", "quote") and text:
                their_texts.append(text)
                their_chars.append(len(text))
                w, c = _hits(text, WARM), _hits(text, COLD)
                warm += w
                cold += c
                if QUESTION_RE.search(text):
                    questions += 1
                if w:
                    evidence.append((w, ts, _clean(text), r.get("sender") or ""))

            if now - ts <= RECENT_DAYS * 86400:
                recent += 1
            elif now - ts <= 2 * RECENT_DAYS * 86400:
                prior += 1

        if ts:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        if kind != "system" and text:
            last_text = _clean(text, 60)

        new_session = prev_ts is None or (ts - prev_ts) > SESSION_GAP
        if new_session:
            sessions += 1
            if is_self:
                init_self += 1
            else:
                init_other += 1
        elif not is_self and prev_self:
            if ts - prev_ts <= REPLY_WINDOW:
                replies += 1
                latencies.append(float(ts - prev_ts))

        if is_self:
            if prev_self is False or new_session:
                my_bursts += 1
        else:
            if prev_self is False and not new_session:
                double_texts += 1

        prev_ts, prev_self = ts, is_self

    text_n = max(len(their_texts), 1)
    my_share = mine / total if total else 0
    balance = min(mine, theirs) / max(mine, theirs) if max(mine, theirs) else 0
    latency_med = statistics.median(latencies) if latencies else None

    evidence.sort(key=lambda e: (e[0], e[1]), reverse=True)
    seen = set()
    picked = []
    for hits, ts, text, sender in evidence:
        if text in seen:
            continue
        seen.add(text)
        picked.append({
            "ts": ts,
            "date": dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "",
            "text": text,
            "sender": sender,
        })
        if len(picked) >= MAX_EVIDENCE:
            break

    return {
        "username": chat["username"],
        "name": chat["name"],
        "is_group": bool(chat.get("is_group")),
        "msg_count": total,
        "mine": mine,
        "theirs": theirs,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "sessions": sessions,
        "init_other": init_other,
        "init_self": init_self,
        "init_share": init_other / sessions if sessions else 0,
        "reply_rate": replies / my_bursts if my_bursts else 0,
        "reply_latency": latency_med,
        "my_bursts": my_bursts,
        "median_chars": statistics.median(their_chars) if their_chars else 0,
        "question_rate": questions / text_n,
        "warm_rate": warm / text_n * 1000,
        "cold_rate": cold / text_n * 1000,
        "emoji_rate": emoji / max(theirs, 1),
        "media_rate": media / max(theirs, 1),
        "late_rate": late / max(theirs, 1),
        "double_rate": double_texts / max(theirs, 1),
        "balance": balance,
        "my_share": my_share,
        "recent_30d": recent,
        "prior_30d": prior,
        "top_senders": [{"name": n, "count": c} for n, c in senders.most_common(5)],
        "last_text": last_text,
        "evidence": picked,
    }


def build(verbose: bool = True) -> dict:
    if not os.path.exists(DATASET):
        raise SystemExit(f"dataset missing: {DATASET} (run `python -m mychat.dataset` or click sync)")
    con = sqlite3.connect(f"file:{DATASET}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        chat_list = [dict(r) for r in con.execute(
            "SELECT username, name, is_group FROM chats ORDER BY last_ts DESC")]
        rows = []
        for i, chat in enumerate(chat_list, 1):
            recs = [dict(r) for r in con.execute(
                "SELECT ts, sender, is_self, kind, text FROM messages "
                "WHERE chat_username=? ORDER BY ts, id", (chat["username"],))]
            m = chat_metrics(chat, recs)
            if m:
                rows.append(m)
            if verbose and i % 25 == 0:
                print(f"  …{i}/{len(chat_list)} chats", flush=True)
    finally:
        con.close()

    cohort = [r for r in rows if not r["is_group"] and r["msg_count"] >= MIN_COHORT]

    def vals(key):
        out = []
        for r in cohort:
            v = r.get(key)
            if v is not None:
                out.append(float(v))
        return out

    def inv(key):
        out = []
        for r in cohort:
            v = r.get(key)
            if v is not None:
                out.append(-float(v))
        return out

    v_init_share = vals("init_share")
    v_double = vals("double_rate")
    v_question = vals("question_rate")
    v_reply = vals("reply_rate")
    v_latency = inv("reply_latency")
    v_recent = vals("recent_30d")
    v_theirs = vals("theirs")
    v_chars = vals("median_chars")
    v_media = vals("media_rate")
    v_warm = vals("warm_rate")
    v_cold = vals("cold_rate")
    v_emoji = vals("emoji_rate")
    v_late = vals("late_rate")
    v_balance = vals("balance")

    for r in rows:
        lat = r["reply_latency"]
        lat_score = _pct(v_latency, -lat if lat is not None else None)
        p = {
            "init_share": _pct(v_init_share, r["init_share"]),
            "double": _pct(v_double, r["double_rate"]),
            "question": _pct(v_question, r["question_rate"]),
            "recent": _pct(v_recent, r["recent_30d"]),
            "reply": _pct(v_reply, r["reply_rate"]),
            "latency": lat_score,
            "theirs": _pct(v_theirs, r["theirs"]),
            "chars": _pct(v_chars, r["median_chars"]),
            "media": _pct(v_media, r["media_rate"]),
            "warm": _pct(v_warm, r["warm_rate"]),
            "cold": _pct(v_cold, r["cold_rate"], higher_better=False),
            "emoji": _pct(v_emoji, r["emoji_rate"]),
            "late": _pct(v_late, r["late_rate"]),
            "balance": _pct(v_balance, r["balance"]),
        }
        dims = {
            "initiative": statistics.mean([p["init_share"], p["double"], p["question"], p["recent"]]),
            "responsive": statistics.mean([p["reply"], p["latency"]]),
            "investment": statistics.mean([p["theirs"], p["chars"], p["media"]]),
            "intimacy": statistics.mean([p["warm"], p["cold"], p["emoji"], p["late"]]),
            "balance": p["balance"],
        }
        raw = (dims["initiative"] * 0.22 + dims["responsive"] * 0.24 +
               dims["investment"] * 0.16 + dims["intimacy"] * 0.28 +
               dims["balance"] * 0.10)
        r["_raw"] = raw

        r["percentiles"] = {k: round(v, 1) for k, v in p.items()}
        r["dimensions"] = {
            "initiative": round(dims["initiative"], 1),
            "responsive": round(dims["responsive"], 1),
            "investment": round(dims["investment"], 1),
            "intimacy": round(dims["intimacy"], 1),
            "balance": round(dims["balance"], 1),
        }
        if r["msg_count"] >= 500:
            r["confidence"] = "high"
        elif r["msg_count"] >= 100:
            r["confidence"] = "medium"
        else:
            r["confidence"] = "low"

    # Composite averages cluster near 50. Rank the composite across 1-on-1
    # chats and map to 8..97 so the UI shows a usable spread instead of noise.
    cohort_raw = sorted(r["_raw"] for r in cohort)
    for r in rows:
        p = _pct(cohort_raw, r["_raw"])
        score = 8 + 89 * p / 100
        if not r["is_group"] and r["msg_count"] < 120:
            score = 50 + (score - 50) * (0.45 + 0.55 * r["msg_count"] / 120)
        r.pop("_raw", None)
        r["score"] = round(score, 1)
        r["trend"] = trend(r["recent_30d"], r["prior_30d"])

    singles = sorted([r for r in rows if not r["is_group"]], key=lambda r: -r["score"])
    for i, r in enumerate(singles, 1):
        r["rank"] = i
        r["ranked_total"] = len(singles)
        r["rank_pct"] = round(i / len(singles) * 100, 1) if singles else 0
        r["narrative"] = narrative(r, i, len(singles))

    groups = sorted([r for r in rows if r["is_group"]], key=lambda r: -r["msg_count"])
    for r in groups:
        r["rank"] = None
        r["ranked_total"] = 0
        r["rank_pct"] = None
        r["narrative"] = group_narrative(r)

    buckets = [
        {"label": "80–100", "min": 80, "count": 0},
        {"label": "65–79", "min": 65, "count": 0},
        {"label": "50–64", "min": 50, "count": 0},
        {"label": "35–49", "min": 35, "count": 0},
        {"label": "0–34", "min": 0, "count": 0},
    ]
    for r in singles:
        for b in buckets:
            if r["score"] >= b["min"]:
                b["count"] += 1
                break

    total_msgs = sum(r["msg_count"] for r in rows)
    avg = statistics.mean([r["score"] for r in singles]) if singles else 0
    data = {
        "generated_at": int(time.time()),
        "totals": {
            "messages": total_msgs,
            "chats": len(rows),
            "singles": len(singles),
            "groups": len(groups),
            "avg_score": round(avg, 1),
        },
        "buckets": buckets,
        "top": singles[0]["name"] if singles else None,
        "chats": singles + groups,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    if verbose:
        print(f"[+] {OUT}: {len(singles)} singles, {len(groups)} groups, "
              f"{total_msgs} messages", flush=True)
    return data


def trend(recent: int, prior: int) -> dict:
    if prior < 5:
        return {"label": "样本不足", "pct": None}
    pct = (recent - prior) / prior * 100
    if pct >= 30:
        label = "升温"
    elif pct <= -30:
        label = "降温"
    else:
        label = "平稳"
    return {"label": label, "pct": round(pct)}


def narrative(r: dict, rank: int, total: int) -> str:
    parts = []
    if r["confidence"] == "low":
        parts.append(f"只有 {r['msg_count']} 条消息，样本偏少，结论仅供参考。")
    parts.append(
        f"在 {total} 个单聊中行为指数排第 {rank}（前 {r['rank_pct']:.0f}%），"
        f"分数 {r['score']:.0f}/100。"
    )
    parts.append(
        f"Ta 主动开启对话 {r['init_other']} 次，你 {r['init_self']} 次；"
        f"你发起的话题有 {r['reply_rate'] * 100:.0f}% 在 24 小时内得到回应，"
        f"中位等待 {_fmt_seconds(r['reply_latency'])}。"
    )
    parts.append(
        f"每千条消息出现 {r['warm_rate']:.1f} 次温暖表达、{r['cold_rate']:.1f} 次冷淡表达；"
        f"表情率 {r['emoji_rate'] * 100:.0f}%，深夜消息占 {r['late_rate'] * 100:.0f}%。"
    )
    dims = r["dimensions"]
    top = max(dims, key=dims.get)
    low = min(dims, key=dims.get)
    label = {"initiative": "主动度", "responsive": "回应度", "investment": "投入度",
             "intimacy": "亲密度", "balance": "平衡度"}
    parts.append(f"最强项是{label[top]}（{dims[top]:.0f}），最弱项是{label[low]}（{dims[low]:.0f}）。")
    return "".join(parts)


def group_narrative(r: dict) -> str:
    return (
        f"群聊共 {r['msg_count']} 条消息，你发了 {r['mine']} 条；"
        f"群友主动开启对话 {r['init_other']} 次，温暖表达密度 {r['warm_rate']:.1f}/千条。"
        "群聊分数只反映整体氛围，不代表某个人的态度。"
    )


if __name__ == "__main__":
    build()

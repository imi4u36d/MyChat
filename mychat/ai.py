#!/usr/bin/env python3
"""Local LLM layer: perception + conversation coaching.

Talks to an OpenAI-compatible server on localhost (LM Studio / omlx / vLLM).
Nothing leaves the machine. Results are cached per chat under data/ai/.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from .settings import (
    AI_BASE_URL,
    AI_CACHE_DIR,
    AI_CALL_CHARS,
    AI_CHUNK_CHARS,
    AI_MODEL,
    AI_TIMEOUT,
)

CACHE_DIR = str(AI_CACHE_DIR)
BASE_URL = AI_BASE_URL
MODEL = AI_MODEL
TIMEOUT = AI_TIMEOUT
SAMPLE_MESSAGES = 200          # guidance / Q&A window
MAX_CHARS = 14000
SINGLE_CALL_CHARS = AI_CALL_CHARS
CHUNK_CHARS = AI_CHUNK_CHARS
CHUNK_MAX_MESSAGES = 1500

SCOPE_LABELS = {
    "all": "全量（全部历史）",
    "half": "最近 50%（按条数）",
    "month": "最近一个月",
    "week": "最近 7 天",
    "3d": "最近 3 天",
}
SCOPE_DAYS = {"month": 30, "week": 7, "3d": 3}
DEFAULT_SCOPE = "month"

_cache_lock = threading.Lock()
_write_lock = threading.Lock()
_model_state: dict = {"at": 0.0, "models": [], "error": None, "model": None}
_status_state: dict = {"at": 0.0, "value": None}
_sem = threading.Semaphore(2)


class AIError(RuntimeError):
    pass




def _http(path: str, payload: dict | None = None, timeout: float = 30) -> dict:
    url = BASE_URL + path
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:400]
        except Exception:
            pass
        raise AIError(f"模型服务返回 {e.code}{('：' + detail) if detail else ''}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise AIError(f"无法连接本地模型 {BASE_URL}（{reason}）") from e
    try:
        return json.loads(raw)
    except ValueError as e:
        raise AIError("模型服务返回了非 JSON 数据") from e


def _models(force: bool = False) -> list[str]:
    with _cache_lock:
        fresh = time.time() - _model_state["at"] < 20
        if fresh and not force:
            return _model_state["models"]
    try:
        data = _http("/models", timeout=6)
        ids = [m.get("id") for m in data.get("data") or [] if m.get("id")]
        with _cache_lock:
            _model_state.update(at=time.time(), models=ids, error=None)
        return ids
    except AIError as e:
        with _cache_lock:
            _model_state.update(at=time.time(), models=[], error=str(e))
        raise


def resolve_model() -> str:
    """Prefer the configured model, fall back to any Qwen3.8 / first model."""
    global MODEL
    if os.environ.get("AI_MODEL"):
        return MODEL
    try:
        ids = _models()
    except AIError:
        return MODEL
    if not ids:
        return MODEL
    if MODEL in ids:
        return MODEL
    for want in ("qwen3.8flash", "qwen3.8-flash", "qwen3.8", "qwen"):
        hit = next((i for i in ids if want in i.lower()), None)
        if hit:
            MODEL = hit
            return hit
    MODEL = ids[0]
    return MODEL


def status() -> dict:
    now = time.time()
    with _cache_lock:
        if _status_state["value"] is not None and now - _status_state["at"] < 10:
            return _status_state["value"]
    try:
        ids = _models(force=True)
        value = {
            "available": True,
            "configured_model": os.environ.get("AI_MODEL") or "qwen3.8flash",
            "model": resolve_model(),
            "models": ids[:20],
            "base_url": BASE_URL,
            "error": None,
        }
    except AIError as e:
        value = {
            "available": False,
            "configured_model": os.environ.get("AI_MODEL") or "qwen3.8flash",
            "model": MODEL,
            "models": [],
            "base_url": BASE_URL,
            "error": str(e),
        }
    with _cache_lock:
        _status_state.update(at=now, value=value)
    return value


def _strip_reasoning(text: str) -> str:
    text = re.sub(r"(?s)<!--.*?-->", "", text or "")
    text = re.sub(r"(?s) thinking.*?<｜end▁of▁thinking｜>", "", text)
    return text.strip()


def _json_from(text: str, prefer_keys: tuple[str, ...] = ()) -> dict:
    text = _strip_reasoning(text)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    candidates = []
    try:
        val = json.loads(text)
        if isinstance(val, dict):
            candidates.append(val)
    except ValueError:
        pass
    dec = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            val, _ = dec.raw_decode(text[start:])
            if isinstance(val, dict):
                candidates.append(val)
        except ValueError:
            pass
        start = text.find("{", start + 1)
    if not candidates:
        raise AIError("模型没有返回可解析的 JSON，请重试")
    def rank(obj):
        score = sum(1 for k in prefer_keys if k in obj)
        return (score, len(obj))
    candidates.sort(key=rank, reverse=True)
    return candidates[0]


def _chat(messages: list[dict], *, temperature: float = 0.25,
          max_tokens: int = 1600, json_mode: bool = False) -> tuple[str, str | None, list[dict]]:
    model = resolve_model()
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        data = _http("/chat/completions", payload, timeout=TIMEOUT)
    except AIError as e:
        if "404" in str(e):
            ids = _models(force=True)
            if ids and payload["model"] not in ids:
                globals()["MODEL"] = ids[0]
                payload["model"] = ids[0]
                data = _http("/chat/completions", payload, timeout=TIMEOUT)
            else:
                raise
        elif json_mode and "400" in str(e):
            payload.pop("response_format", None)
            data = _http("/chat/completions", payload, timeout=TIMEOUT)
        else:
            raise
    choices = data.get("choices") or []
    if not choices:
        raise AIError("模型没有返回结果")
    choice = choices[0]
    msg = choice.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    content = _strip_reasoning(content or "")
    if not content:
        raise AIError("模型返回了空结果（可能被思考模式占满，请重试）")
    return content, choice.get("finish_reason"), messages


def _call_json(messages: list[dict], max_tokens: int = 1600,
               prefer_keys: tuple[str, ...] = ("summary", "scores", "read", "replies"),
               required: tuple[str, ...] = ()) -> dict:
    last_error: AIError | None = None
    for attempt in range(3):
        try:
            content, finish, _ = _chat(messages, max_tokens=max_tokens + attempt * 900,
                                       json_mode=True,
                                       temperature=0.25 + attempt * 0.15)
        except AIError as e:
            last_error = e
            continue
        if finish == "length":
            last_error = AIError("模型输出被截断，正在重试…")
            continue
        try:
            result = _json_from(content, prefer_keys)
        except AIError as e:
            last_error = e
            continue
        missing = [k for k in required if k not in result]
        if missing or not any(k in result for k in prefer_keys):
            last_error = AIError("模型返回的 JSON 缺少字段，正在重试…")
            continue
        result["_model"] = resolve_model()
        return result
    raise last_error or AIError("模型没有返回有效结果")



# ---------------------------------------------------------------- input prep

def _name(chat: dict) -> str:
    return (chat.get("name") or chat.get("username") or "").strip()


def _clip(text: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _line(chat: dict, m: dict) -> str:
    kind = m.get("kind") or "text"
    text = m.get("text") or ""
    if kind not in ("text", "quote") and not text:
        return ""
    body = _clip(text, 180)
    if not body:
        return ""
    ts = m.get("ts") or 0
    stamp = dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
    who = "我" if m.get("is_self") else (m.get("sender") or _name(chat))
    return f"[{stamp}] {who}：{body}"


def _transcript(chat: dict, msgs: list[dict], limit_chars: int = MAX_CHARS) -> str:
    lines = []
    total = 0
    for m in msgs:
        line = _line(chat, m)
        if not line:
            continue
        if total + len(line) > limit_chars:
            break
        lines.append(line)
        total += len(line)
    if len(lines) < len(msgs):
        prefix = f"（记录较长，本段只保留前 {len(lines)} / {len(msgs)} 条）\n"
    else:
        prefix = ""
    return prefix + "\n".join(lines)


def _fmt_span(first, last) -> str:
    if not first or not last:
        return "时间未知"
    a = dt.datetime.fromtimestamp(first).strftime("%Y-%m-%d")
    b = dt.datetime.fromtimestamp(last).strftime("%Y-%m-%d")
    return a if a == b else f"{a} ~ {b}"


def _stats_block(chat: dict) -> str:
    def pct(v):
        try:
            return f"{float(v) * 100:.0f}%"
        except (TypeError, ValueError):
            return "—"

    lat = chat.get("reply_latency")
    lat_txt = "—"
    if lat is not None:
        lat_txt = f"{lat / 60:.0f} 分钟" if lat < 3600 else f"{lat / 3600:.1f} 小时"
    trend = chat.get("trend") or {}
    return "\n".join([
        f"关系称呼：{_name(chat)}",
        f"聊天类型：{'群聊' if chat.get('is_group') else '单聊'}",
        f"消息总量：{chat.get('msg_count', 0)} 条（我 {chat.get('mine', 0)} / 对方 {chat.get('theirs', 0)}）",
        f"对方主动开启对话：{chat.get('init_other', 0)} 次，我 {chat.get('init_self', 0)} 次",
        f"我发起后 24 小时内获回复率：{pct(chat.get('reply_rate'))}，中位回复等待：{lat_txt}",
        f"对方中位单条字数：{chat.get('median_chars', 0)} 字，提问率 {pct(chat.get('question_rate'))}",
        f"对方温暖表达 {float(chat.get('warm_rate') or 0):.1f}/千条，冷淡表达 {float(chat.get('cold_rate') or 0):.1f}/千条",
        f"表情率 {pct(chat.get('emoji_rate'))}，深夜消息 {pct(chat.get('late_rate'))}，双发率 {pct(chat.get('double_rate'))}",
        f"关系平衡度 {pct(chat.get('balance'))}，近 30 天 {chat.get('recent_30d', 0)} 条 / 前 30 天 {chat.get('prior_30d', 0)} 条",
        f"当前趋势：{trend.get('label') or '未知'}"
        + (f"（{trend.get('pct'):+d}%）" if isinstance(trend.get("pct"), (int, float)) else ""),
    ])


def _context(chat: dict, msgs: list[dict], coverage: dict | None = None) -> str:
    until = dt.datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M")
    if coverage:
        scope_line = (f"【分析范围】{coverage['label']}：{_fmt_span(coverage['first_ts'], coverage['last_ts'])}"
                      f"（截至 {until}），本次分析 {coverage['count']} 条，该聊天历史共 {coverage['total']} 条")
    else:
        scope_line = "【分析范围】最近聊天记录"
    return (scope_line + "\n\n【行为统计】（全历史，供参考）\n" + _stats_block(chat) +
            "\n\n【聊天记录】（按时间顺序）\n" + _transcript(chat, msgs, SINGLE_CALL_CHARS))


# ---------------------------------------------------------------- prompts

PERCEPTION_SYSTEM = """你是一位严谨、克制的聊天关系分析师。你只根据给定的事实和聊天记录判断，不美化、不臆测、不进行人身攻击。
分析对象是「对方」对「我」的态度与看法。必须区分证据强度：单条消息不能代表整体。

严格只输出一个 JSON 对象，不要 Markdown、不要解释、不要代码块。字符串内部一律用「」引述，禁止出现未转义的英文双引号。结构如下：
{
  "summary": "80-140字，整体判断，语气客观",
  "scores": {
    "friendliness": 0-100,
    "affection": 0-100,
    "aversion": 0-100,
    "trust": 0-100,
    "reliance": 0-100
  },
  "mood": "对方在这段对话里的主要情绪，12字以内",
  "tags": ["给这段关系/对方态度打的标签，2-6个字，最多5个，如 忽冷忽热、工作伙伴、真诚、敷衍"],
  "signals": [
    {"type": "positive|negative|neutral", "label": "信号短语，8字以内", "quote": "聊天记录中的原话，逐字引用"}
  ],
  "relations": [
    {"type": "同事|朋友|同学|家人|暧昧|情侣|客户|服务关系|陌生人|其他", "confidence": 0-100, "reason": "20字以内依据"}
  ],
  "red_flags": ["负面/回避信号，最多3条"],
  "green_flags": ["正面/靠近信号，最多3条"],
  "advice": ["给「我」的互动建议，2-3条，具体可执行"]
}

评分口径：
- friendliness 友善度：语气礼貌、愿意帮忙、积极回应的程度。
- affection 好感度：亲近、想念、在乎、撒娇、主动分享的程度。
- aversion 厌恶度：明显敷衍、不耐烦、回避、反感；与好感度独立，证据不足就给低值。
- trust 信任度：愿意说私事、求助、暴露脆弱。
- reliance 依赖度：主动找「我」、征求意见、情绪托付的程度。
数字必须是整数；没有证据的维度给 50 左右，不要瞎猜。signals 最多4条，必须逐字引用，找不到就空数组。
relations 按可能性从高到低，最多3个，confidence 合计不必为100。tags 必须来自聊天内容，不要套话。"""

GUIDANCE_SYSTEM = """你是擅长中文即时通讯的对话教练。你的任务是基于真实聊天记录，帮「我」想清楚怎么接话，给出可以直接发送的回复。
原则：真诚、不油腻、不PUA、不过度热情、不连续追问；贴合两人既有的称呼和语气；短句优先；尊重对方边界。
严格只输出一个 JSON 对象，不要 Markdown、不要解释、不要代码块。字符串内部一律用「」引述，禁止出现未转义的英文双引号。结构如下：
{
  "read": "60-110字，对方最近的态度和潜台词，客观",
  "strategy": "25字以内，这一轮的整体策略",
  "replies": [
    {"tone": "自然|温柔|轻松|幽默|克制|主动|暧昧", "text": "可直接发送的回复，60字以内", "why": "15字以内为什么这样说"}
  ],
  "topics": ["可以自然引出的新话题，3条以内，每条20字以内"],
  "cautions": ["这轮要避免的事，2条以内"],
  "next_step": "一条下一步建议，25字以内"
}
replies 给 3 条，风格要有明显差异。不要编造聊天记录里不存在的事实。"""


def _perception_messages(chat: dict, msgs: list[dict], coverage: dict | None = None,
                         digests: list[str] | None = None) -> list[dict]:
    if digests is not None:
        body = ("【行为统计】（全历史，供参考）\n" + _stats_block(chat) +
                "\n\n【分段摘要】（全量记录被切成 " + str(len(digests)) +
                " 段逐段阅读，下面是每段摘要；引号内为原话）\n" +
                "\n\n".join(digests))
        context = ("【分析范围】" +
                   f"{coverage['label']}：{_fmt_span(coverage['first_ts'], coverage['last_ts'])}"
                   f"（截至 {dt.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M')}），"
                   f"本次分析 {coverage['count']} 条，该聊天历史共 {coverage['total']} 条" +
                   "\n\n" + body)
    else:
        context = _context(chat, msgs, coverage)
    return [
        {"role": "system", "content": PERCEPTION_SYSTEM},
        {"role": "user", "content":
            "请分析下面这段聊天中，对方对我的看法与疑似关系。\n\n" +
            context +
            "\n\n只输出 JSON。"},
    ]


CHUNK_SYSTEM = """你是聊天记录压缩器。阅读一段聊天记录，只为后续关系分析保留证据。
严格输出 JSON，字符串内用「」引述，禁止未转义英文双引号：
{
  "summary": "这段记录里双方互动的客观概括，80字以内",
  "tone": "对方的主要语气，10字以内",
  "signals": [{"type":"positive|negative|neutral","label":"8字以内","quote":"逐字原话"}],
  "shifts": "这段记录里态度/频率的明显变化，40字以内；没有就写无",
  "topics": ["反复出现的主题，最多3个"]
}
signals 给 2-4 条，优先保留能代表态度的原话；不要评价我，不要编造。"""


def _chunk_digests(chat: dict, msgs: list[dict], coverage: dict) -> list[str]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for m in msgs:
        line = _line(chat, m)
        if not line:
            continue
        if current and (size + len(line) > CHUNK_CHARS or len(current) >= CHUNK_MAX_MESSAGES):
            chunks.append(current)
            current, size = [], 0
        current.append(m)
        size += len(line) + 1
    if current:
        chunks.append(current)

    digests = []
    failed = 0
    for i, chunk in enumerate(chunks, 1):
        span = _fmt_span(chunk[0].get("ts"), chunk[-1].get("ts"))
        try:
            raw = _call_json([
                {"role": "system", "content": CHUNK_SYSTEM},
                {"role": "user", "content":
                    f"这是第 {i}/{len(chunks)} 段，时间 {span}。\n\n" +
                    _transcript(chat, chunk, CHUNK_CHARS) + "\n\n只输出 JSON。"},
            ], 1200, ("summary", "signals", "tone"), ("summary",))
        except AIError:
            failed += 1
            digests.append(f"第 {i}/{len(chunks)} 段（{span}）：模型读取失败，这一段未纳入。")
            continue
        parts = [f"第 {i}/{len(chunks)} 段（{span}）：{_text(raw.get('summary'), 220)}"]
        if raw.get("tone"):
            parts.append(f"语气：{_text(raw.get('tone'), 20)}")
        if raw.get("shifts"):
            parts.append(f"变化：{_text(raw.get('shifts'), 80)}")
        sigs = []
        for sig in (raw.get("signals") or [])[:4]:
            if isinstance(sig, dict) and sig.get("quote"):
                sigs.append(f"{_text(sig.get('label'), 12)}「{_text(sig.get('quote'), 80)}」")
        if sigs:
            parts.append("信号：" + "；".join(sigs))
        digests.append("\n".join(parts))
    if failed == len(chunks):
        raise AIError("分段阅读全部失败，请重试")
    return digests


def _guidance_messages(chat: dict, msgs: list[dict], draft: str, goal: str) -> list[dict]:
    extra = []
    if draft:
        extra.append(f"我打算这样回：「{_clip(draft, 300)}」——请保留我的原意，给出更好的版本。")
    if goal:
        extra.append(f"我的目标：{_clip(goal, 60)}。")
    extra.append("如果没有给定草稿，就针对对方最后的话给出接下来可以发的消息。")
    return [
        {"role": "system", "content": GUIDANCE_SYSTEM},
        {"role": "user", "content":
            "根据下面的聊天记录，给我对话指导。\n\n" +
            _context(chat, msgs) + "\n\n" + "\n".join(extra) + "\n\n只输出 JSON。"},
    ]


# ---------------------------------------------------------------- normalizing

def _int(v, default: int = 50) -> int:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return default
    return max(0, min(100, n))


def _text(v, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def _strs(v, limit: int = 4) -> list[str]:
    if not isinstance(v, list):
        return []
    out = []
    for item in v:
        s = _text(item, 120)
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _norm_perception(raw: dict, chat: dict, msgs: int) -> dict:
    scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
    mood = _text(raw.get("mood"), 30) or "中性"
    signals = []
    for s in raw.get("signals") or []:
        if not isinstance(s, dict):
            continue
        quote = _text(s.get("quote"), 200)
        if not quote:
            continue
        signals.append({
            "type": s.get("type") if s.get("type") in ("positive", "negative", "neutral") else "neutral",
            "label": _text(s.get("label"), 16) or "信号",
            "quote": quote,
        })
        if len(signals) >= 5:
            break
    relations = []
    for r in raw.get("relations") or []:
        if isinstance(r, str):
            r = {"type": r}
        if not isinstance(r, dict):
            continue
        relations.append({
            "type": _text(r.get("type"), 20) or "其他",
            "confidence": _int(r.get("confidence"), 50),
            "reason": _text(r.get("reason"), 60),
        })
        if len(relations) >= 3:
            break
    return {
        "summary": _text(raw.get("summary"), 400) or "证据不足，暂时无法判断。",
        "scores": {
            "friendliness": _int(scores.get("friendliness")),
            "affection": _int(scores.get("affection")),
            "aversion": _int(scores.get("aversion")),
            "trust": _int(scores.get("trust")),
            "reliance": _int(scores.get("reliance")),
        },
        "mood": mood,
        "tags": _strs(raw.get("tags"), 5),
        "signals": signals,
        "relations": relations,
        "red_flags": _strs(raw.get("red_flags"), 3),
        "green_flags": _strs(raw.get("green_flags"), 3),
        "advice": _strs(raw.get("advice"), 3),
        "message_count": msgs,
        "name": _name(chat),
        "model": raw.get("_model"),
    }


def _norm_guidance(raw: dict, draft: str, goal: str) -> dict:
    replies = []
    for r in raw.get("replies") or []:
        if isinstance(r, str):
            r = {"text": r}
        if not isinstance(r, dict):
            continue
        text = _text(r.get("text"), 200)
        if not text:
            continue
        replies.append({
            "tone": _text(r.get("tone"), 10) or "自然",
            "text": text,
            "why": _text(r.get("why"), 60),
        })
        if len(replies) >= 4:
            break
    return {
        "read": _text(raw.get("read"), 400) or "—",
        "strategy": _text(raw.get("strategy"), 60) or "自然回应，保持节奏。",
        "replies": replies,
        "topics": _strs(raw.get("topics"), 3),
        "cautions": _strs(raw.get("cautions"), 3),
        "next_step": _text(raw.get("next_step"), 80),
        "draft": _text(draft, 300),
        "goal": _text(goal, 60),
        "model": raw.get("_model"),
    }


# ---------------------------------------------------------------- cache

def _cache_path(username: str) -> str:
    h = hashlib.sha1(username.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{h}.json")


def _read_cache(username: str) -> dict:
    try:
        with open(_cache_path(username), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(username: str, data: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        os.chmod(CACHE_DIR, 0o700)
    except OSError:
        pass
    path = _cache_path(username)
    tmp = path + ".tmp"
    with _write_lock:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)


def normalize_scope(scope: str | None) -> str:
    return scope if scope in SCOPE_LABELS else DEFAULT_SCOPE


def describe_scope(chat: dict, scope: str = DEFAULT_SCOPE) -> dict:
    """Cheap metadata for the scope selector (no message bodies loaded)."""
    scope = normalize_scope(scope)
    username = chat.get("username") or ""
    try:
        from . import dataset as _ds
    except Exception:
        return {"scope": scope, "label": SCOPE_LABELS[scope], "count": 0,
                "total": 0, "first_ts": None, "last_ts": None}
    since = None
    offset = 0
    if scope in SCOPE_DAYS:
        since = int(time.time()) - SCOPE_DAYS[scope] * 86400
    all_stats = _ds.scope_stats(username)
    if scope == "half":
        offset = all_stats["count"] // 2
    stats = _ds.scope_stats(username, since, offset) if (since or offset) else all_stats
    return {
        "scope": scope,
        "label": SCOPE_LABELS[scope],
        "count": stats["count"],
        "total": all_stats["count"],
        "first_ts": stats["first_ts"],
        "last_ts": stats["last_ts"],
    }


def load_scope(chat: dict, scope: str) -> tuple[list[dict], dict]:
    scope = normalize_scope(scope)
    username = chat.get("username") or ""
    from . import dataset as _ds
    since = None
    offset = 0
    total = _ds.scope_stats(username)["count"]
    if scope in SCOPE_DAYS:
        since = int(time.time()) - SCOPE_DAYS[scope] * 86400
    elif scope == "half":
        offset = total // 2
    msgs = _ds.messages_range(username, since, offset)
    coverage = {
        "scope": scope,
        "label": SCOPE_LABELS[scope],
        "count": len(msgs),
        "total": total,
        "first_ts": msgs[0]["ts"] if msgs else None,
        "last_ts": msgs[-1]["ts"] if msgs else None,
    }
    return msgs, coverage


def cached(username: str, scope: str | None = None) -> dict:
    data = _read_cache(username)
    perceptions = data.get("perceptions") or {}
    entry = ((perceptions.get(normalize_scope(scope)) or {}) if scope
             else (data.get("perception") or {}))
    return {
        "username": username,
        "last_ts": data.get("last_ts"),
        "msg_count": data.get("msg_count"),
        "scope": entry.get("scope"),
        "coverage": entry.get("coverage"),
        "perception": entry or None,
        "guidance": data.get("guidance"),
    }


def _merge_cache(username: str, **patch) -> None:
    data = _read_cache(username)
    data.setdefault("username", username)
    data["updated_at"] = int(time.time())
    data.update(patch)
    _write_cache(username, data)


def _is_fresh(entry: dict, chat: dict) -> bool:
    return bool(entry) and entry.get("last_ts") == chat.get("last_ts") and \
        entry.get("msg_count") == chat.get("msg_count")


# ---------------------------------------------------------------- public API

def perception_sync(chat: dict, refresh: bool = False, scope: str = DEFAULT_SCOPE,
                    msgs: list[dict] | None = None) -> dict:
    username = chat.get("username") or ""
    if chat.get("is_group"):
        raise ValueError("群聊暂不支持「对方怎么看」分析，请使用对话指导")
    scope = normalize_scope(scope)
    cache = _read_cache(username)
    entry = (cache.get("perceptions") or {}).get(scope) or {}
    if entry and not refresh and entry.get("scope") == scope and _is_fresh(entry, chat):
        return {"cached": True, "stale": False, "perception": entry["result"],
                "created_at": entry.get("created_at"), "scope": scope,
                "coverage": entry.get("coverage")}

    if msgs is None:
        msgs, coverage = load_scope(chat, scope)
    else:
        coverage = {"scope": scope, "label": SCOPE_LABELS[scope], "count": len(msgs),
                    "total": chat.get("msg_count") or len(msgs),
                    "first_ts": msgs[0]["ts"] if msgs else None,
                    "last_ts": msgs[-1]["ts"] if msgs else None}
    if not msgs:
        raise ValueError("这个时间范围内没有消息，换一个范围试试")

    started = time.time()
    chars = 0
    for m in msgs:
        chars += len(_line(chat, m)) + 1
    digests = None
    if chars > SINGLE_CALL_CHARS:
        digests = _chunk_digests(chat, msgs, coverage)
    with _sem:
        raw = _call_json(_perception_messages(chat, msgs, coverage, digests), 2400,
                         ("summary", "scores", "relations", "tags"),
                         ("summary", "scores"))
    result = _norm_perception(raw, chat, len(msgs))
    result["coverage"] = coverage
    created = int(time.time())
    entry = {"created_at": created, "result": result, "scope": scope,
             "coverage": coverage, "last_ts": chat.get("last_ts"),
             "msg_count": chat.get("msg_count")}
    old = _read_cache(username)
    perceptions = old.get("perceptions") or {}
    perceptions[scope] = entry
    _merge_cache(username, last_ts=chat.get("last_ts"), msg_count=chat.get("msg_count"),
                 name=_name(chat), model=result.get("model"),
                 perceptions=perceptions, perception=entry)
    return {"cached": False, "stale": False, "perception": result, "created_at": created,
            "scope": scope, "coverage": coverage,
            "elapsed": round(time.time() - started, 1)}


def guidance_sync(chat: dict, msgs: list[dict], draft: str = "", goal: str = "",
                  refresh: bool = False) -> dict:
    username = chat.get("username") or ""
    draft, goal = (draft or "").strip(), (goal or "").strip()
    cache = _read_cache(username)
    entry = cache.get("guidance") or {}
    same_request = (entry.get("draft") or "") == draft[:300] and (entry.get("goal") or "") == goal[:60]
    if entry and not refresh and same_request and _is_fresh(entry, chat):
        return {"cached": True, "stale": False, "guidance": entry["result"],
                "created_at": entry.get("created_at")}

    started = time.time()
    with _sem:
        raw = _call_json(_guidance_messages(chat, msgs, draft, goal), 1800,
                         ("read", "replies", "strategy"), ("read", "replies"))
    result = _norm_guidance(raw, draft, goal)
    created = int(time.time())
    _merge_cache(username, last_ts=chat.get("last_ts"), msg_count=chat.get("msg_count"),
                 name=_name(chat), model=result.get("model"),
                 guidance={"created_at": created, "result": result,
                           "last_ts": chat.get("last_ts"),
                           "msg_count": chat.get("msg_count"),
                           "draft": draft[:300], "goal": goal[:60]})
    return {"cached": False, "stale": False, "guidance": result, "created_at": created,
            "elapsed": round(time.time() - started, 1)}


async def perception(chat: dict, refresh: bool = False, scope: str = DEFAULT_SCOPE) -> dict:
    return await asyncio.to_thread(perception_sync, chat, refresh, scope)


async def guidance(chat: dict, msgs: list[dict], draft: str = "", goal: str = "",
                   refresh: bool = False) -> dict:
    return await asyncio.to_thread(guidance_sync, chat, msgs, draft, goal, refresh)


def list_cache(chats: list[dict] | None = None, scope: str | None = None) -> list[dict]:
    """Summaries of every cached AI judgement, for overview tagging/ranking."""
    by_user = {c.get("username"): c for c in (chats or [])}
    out = []
    try:
        names = os.listdir(CACHE_DIR)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(CACHE_DIR, name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if scope:
            entry = (data.get("perceptions") or {}).get(normalize_scope(scope)) or {}
        else:
            entry = data.get("perception") or {}
        if not entry or not entry.get("result"):
            continue
        chat = by_user.get(data.get("username") or "") or {}
        result = entry["result"]
        out.append({
            "username": data.get("username"),
            "name": data.get("name") or chat.get("name"),
            "is_group": bool(chat.get("is_group")),
            "scores": result.get("scores") or {},
            "tags": result.get("tags") or [],
            "mood": result.get("mood"),
            "relations": result.get("relations") or [],
            "summary": result.get("summary"),
            "created_at": entry.get("created_at"),
            "analyzed_last_ts": entry.get("last_ts"),
            "analyzed_msg_count": entry.get("msg_count"),
            "fresh": bool(chat) and entry.get("last_ts") == chat.get("last_ts")
                     and entry.get("msg_count") == chat.get("msg_count"),
            "model": result.get("model"),
            "scope": entry.get("scope"),
            "coverage": entry.get("coverage"),
        })
    return out


# ---------------------------------------------------------------- free-form Q&A

ASK_SYSTEM = """你是一台 Mac 上的私人微信数据分析助手，只根据提供的聊天数据回答「我」的问题。
规则：
1. 只能使用【数据摘要】【相关记录】【最近对话】里的事实。数据不足就直说“现有记录看不出”，不要编造。
2. 回答结论要带证据：尽量给出日期、原话或统计数字。
3. 明确区分“记录显示”和“推测”，推测必须标注“可能”。
4. 简洁：默认 180 字以内；内容多时用短列表；不要复述问题，不要客套。
5. 不做道德审判，不建议操控、试探或冷暴力；涉及情绪困扰时给出真诚、尊重的建议。
6. 引用原话时保持简短，不要泄露无关隐私。"""

_STOP_TERMS = {
    "什么", "怎么", "为什么", "哪个", "哪些", "多少", "多久", "是否", "是不是",
    "我们", "你们", "他们", "对方", "这个", "那个", "一个", "现在", "最近",
    "聊天", "记录", "消息", "告诉", "觉得", "认为", "看法", "态度", "关系",
    "可以", "能不能", "有没有", "是不是", "的话", "然后", "所以", "因为",
    "我的", "你的", "他的", "她的", "对我", "对你", "怎么想", "怎么样",
    "一次", "时候", "之前", "以后", "还是", "就是", "真的", "到底", "请问",
}


def _query_terms(question: str) -> list[str]:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", question or "")
    chunks = re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", cleaned)
    terms: list[str] = []
    for chunk in chunks:
        if chunk.isascii():
            terms.append(chunk)
            continue
        if chunk in _STOP_TERMS:
            continue
        # Long Chinese runs: keep the meaningful 2-4 char windows.
        found = False
        for size in (4, 3, 2):
            for i in range(0, len(chunk) - size + 1):
                piece = chunk[i:i + size]
                if piece in _STOP_TERMS:
                    continue
                terms.append(piece)
                found = True
            if found:
                break
        if not found and len(chunk) >= 2:
            terms.append(chunk[:4])
    return list(dict.fromkeys(t for t in terms if t))[:10]


def _fmt_day(ts) -> str:
    if not ts:
        return "未知"
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _facts_block(facts: dict) -> str:
    if not facts:
        return "（无聚合数据）"
    lines = [
        f"首次互动：{_fmt_day(facts.get('first_ts'))}，最近一条：{_fmt_day(facts.get('last_ts'))}，"
        f"总消息 {facts.get('total', 0)} 条",
    ]
    for s in facts.get("senders") or []:
        lines.append(f"对方主发言者 {s.get('sender') or '?'}：{s.get('n')} 条")
    months = [(m.get("m"), m.get("n")) for m in facts.get("months") or [] if m.get("m")]
    if months:
        lines.append("近 12 个月消息量：" + "，".join(f"{m}:{n}" for m, n in months))
    return "\n".join(lines)


def _ref_lines(rows: list[dict]) -> str:
    out = []
    for m in rows:
        stamp = _fmt_day(m.get("ts"))
        who = "我" if m.get("is_self") else (m.get("sender") or "对方")
        out.append(f"[{stamp}] {who}：{_clip(m.get('text') or '', 140)}")
    return "\n".join(out)


def _ask_messages(chat: dict, recent: list[dict], facts: dict, hits: list[dict],
                  question: str, history: list[dict]) -> list[dict]:
    context = "\n".join([
        "【数据摘要】",
        _facts_block(facts),
        "",
        "【行为统计】（AI 预测依据，非事实）",
        _stats_block(chat),
        "",
        f"【相关记录】（按问题关键词检索到 {len(hits)} 条）",
        _ref_lines(hits) or "（无匹配记录）",
        "",
        "【最近对话】",
        _transcript(chat, recent),
    ])
    messages = [{"role": "system", "content": ASK_SYSTEM + "\n\n# 背景数据\n" + context}]
    for turn in (history or [])[-8:]:
        role = turn.get("role")
        content = _clip(str(turn.get("content") or ""), 600)
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": _clip(question, 800)})
    return messages


def ask_sync(chat: dict, recent: list[dict], question: str, history: list[dict] | None = None) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")
    username = chat.get("username") or ""
    try:
        from . import dataset as _ds
        facts = _ds.chat_facts(username)
        hits = _ds.search(username, _query_terms(question), limit=24)
    except Exception:
        facts, hits = {}, []
    started = time.time()
    with _sem:
        text, _finish, _ = _chat(
            _ask_messages(chat, recent, facts, hits, question, history or []),
            temperature=0.35, max_tokens=1200)
    return {
        "answer": text[:4000],
        "model": resolve_model(),
        "references": [{
            "ts": m.get("ts"),
            "date": _fmt_day(m.get("ts")),
            "sender": "我" if m.get("is_self") else (m.get("sender") or "对方"),
            "text": _clip(m.get("text") or "", 140),
        } for m in hits[:24]],
        "facts": {
            "total": facts.get("total"),
            "first_ts": facts.get("first_ts"),
            "last_ts": facts.get("last_ts"),
        },
        "elapsed": round(time.time() - started, 1),
    }


async def ask(chat: dict, recent: list[dict], question: str,
              history: list[dict] | None = None) -> dict:
    return await asyncio.to_thread(ask_sync, chat, recent, question, history)

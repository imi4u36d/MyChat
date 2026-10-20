const $ = (id) => document.getElementById(id);

const DIM_LABELS = {
  initiative: "主动度",
  responsive: "回应度",
  investment: "投入度",
  intimacy: "亲密度",
  balance: "平衡度",
};

const KIND_LABELS = {
  image: "[图片]", voice: "[语音]", video: "[视频]", emoji: "[表情]",
  link: "[链接]", file: "[文件]", location: "[位置]", call: "[通话]",
  transfer: "[转账]", redpacket: "[红包]", system: "[系统消息]", other: "[消息]",
};

const SCOPE_LABELS = {
  all: "全量（全部历史）",
  half: "最近 50%（按条数）",
  month: "最近一个月",
  week: "最近 7 天",
  "3d": "最近 3 天",
};

const AI_SCORE_LABELS = {
  friendliness: ["友善度", false],
  affection: ["好感度", false],
  aversion: ["厌恶度", true],
  trust: ["信任度", false],
  reliance: ["依赖度", false],
};

const state = {
  data: null,
  status: null,
  aiStatus: null,
  aiTags: {},
  aiMode: "ai",
  aiTab: "perception",
  scope: localStorage.getItem("wechatInsightScope") || "month",
  scopeInfo: null,
  detailAI: { perception: null, guidance: null, stale: false },
  aiBusy: null,
  aiTimer: null,
  drawerView: "overview",
  loadingMore: false,
  batch: null,
  batchPoll: null,
  filter: "single",
  query: "",
  selected: null,
  messages: [],
  oldestTs: null,
  hasMore: true,
  polling: null,
  syncing: false,
  chatPending: {},
  chatSessions: {},
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtNum(n) {
  return new Intl.NumberFormat("zh-CN").format(n ?? 0);
}

function fmtDate(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function fmtListTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const same = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (same(d, now)) return hm;
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (same(d, y)) return "昨天";
  const week = ["日", "一", "二", "三", "四", "五", "六"][d.getDay()];
  if (now - d < 7 * 86400000) return `星期${week}`;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}/${d.getDate()}`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

function fmtChatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const same = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (same(d, now)) return hm;
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (same(d, y)) return `昨天 ${hm}`;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}

function fmtStamp(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${fmtTime(ts)}`;
}

function timeAgo(ts) {
  if (!ts) return "从未";
  const diff = Date.now() / 1000 - ts;
  if (diff < 0) return "刚刚";
  if (diff < 3600) return `${Math.max(1, Math.floor(diff / 60))} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`;
  return fmtDate(ts);
}

function hueOf(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
  return h;
}

function initial(name) {
  const cleaned = String(name || "").replace(/[\s【】\[\]（）()·.,，。:：_\-/\\|]+/g, "");
  return [...(cleaned || String(name || "?"))][0] || "?";
}

function avatar(name, cls = "", username = "") {
  const h = hueOf(name || "?");
  const src = username === "self" ? "/api/avatars/self" : `/api/avatars/${encodeURIComponent(username)}`;
  const img = username
    ? `<img loading="lazy" src="${src}" alt="" onerror="this.remove()">`
    : "";
  return `<div class="avatar ${cls}" style="background:linear-gradient(135deg,hsl(${h} 52% 47%),hsl(${(h + 46) % 360} 48% 38%))"><span>${esc(initial(name))}</span>${img}</div>`;
}

function scoreClass(v) {
  if (v == null) return "none";
  return v >= 80 ? "high" : v >= 50 ? "mid" : "low";
}

function toast(msg, isError = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = "toast"; }, 3200);
}

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  let body = null;
  try { body = await r.json(); } catch (_) { /* ignore */ }
  if (!r.ok) throw new Error((body && (body.error || body.detail)) || `HTTP ${r.status}`);
  return body;
}

function postJSON(url, payload) {
  return getJSON(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
}

function rich(text) {
  const lines = esc(text || "").split("\n");
  let html = "";
  let inList = false;
  for (const raw of lines) {
    let line = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    if (/^\s*[-•]\s+/.test(line)) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${line.replace(/^\s*[-•]\s+/, "")}</li>`;
      continue;
    }
    if (inList) { html += "</ul>"; inList = false; }
    if (line.trim()) html += `<div>${line}</div>`;
    else html += `<div style="height:4px"></div>`;
  }
  if (inList) html += "</ul>";
  return html;
}

/* ---------- AI index ---------- */

function scopeLabel(scope) {
  return SCOPE_LABELS[scope] || scope;
}

function fmtRange(firstTs, lastTs) {
  if (!firstTs || !lastTs) return "没有消息";
  const a = fmtDate(firstTs);
  const b = fmtDate(lastTs);
  return a === b ? a : `${a} ~ ${b}`;
}

function scopeSummary(info) {
  if (!info) return "";
  if (!info.count) return `${info.label} · 这个范围没有消息`;
  return `${info.label} · ${fmtRange(info.first_ts, info.last_ts)} · ${fmtNum(info.count)} 条`;
}

function aiFor(username) {
  return state.aiTags[username] || null;
}

function aiAffection(chat) {
  const a = aiFor(chat.username);
  const v = a && a.scores ? Number(a.scores.affection) : null;
  return Number.isFinite(v) ? v : null;
}

function detailAIFor(chat) {
  const entry = state.detailAI.perception;
  const r = entry && entry.result;
  if (!r) return null;
  const scope = (r.coverage && r.coverage.scope) || entry.scope;
  if (scope && scope !== state.scope) return null;
  return {
    scores: r.scores || {},
    tags: r.tags || [],
    mood: r.mood,
    relations: r.relations || [],
    created_at: entry.created_at,
    coverage: r.coverage || entry.coverage,
    fresh: !state.detailAI.stale,
  };
}

function aiRelation(chat) {
  const a = aiFor(chat.username);
  const rel = a && Array.isArray(a.relations) ? a.relations[0] : null;
  return rel ? rel.type : null;
}

function aiFreshness() {
  const all = Object.values(state.aiTags).filter((x) => x && !x.is_group);
  const fresh = all.filter((x) => x.fresh).length;
  return { total: all.length, fresh };
}

async function loadAITags(rerender = true) {
  try {
    const data = await getJSON("/api/ai/tags");
    const map = {};
    for (const item of data.items || []) map[item.username] = item;
    state.aiTags = map;
    if (rerender) renderAll();
  } catch (e) {
    /* AI index is optional */
  }
}

async function refreshAIStatus() {
  try {
    const s = await getJSON("/api/ai/status");
    state.aiStatus = s;
    const pill = $("aiPill");
    const text = $("aiPillText");
    if (s.available) {
      pill.className = "ai-pill ok";
      text.textContent = `AI 已连接 · ${s.model || "本地模型"}`;
      pill.title = `${s.base_url} · ${s.model}`;
    } else {
      pill.className = "ai-pill bad";
      text.textContent = "AI 未连接";
      pill.title = s.error || s.base_url;
    }
    $("aiModelBadge").textContent = s.available ? (s.model || "本地模型") : "模型离线";
  } catch (e) {
    $("aiPill").className = "ai-pill bad";
    $("aiPillText").textContent = "AI 状态未知";
  }
}

/* ---------- data load / sync ---------- */

async function loadAnalysis() {
  try {
    const data = await getJSON("/api/analysis");
    state.data = data;
    renderAll();
    refreshStatus();
    if (state.selected) {
      const chat = data.chats.find((c) => c.username === state.selected);
      if (chat) fillDetail(chat);
    }
  } catch (e) {
    if (String(e.message).includes("索引")) {
      $("syncMeta").textContent = "首次分析中…";
      startStatusPoll();
    } else {
      toast(e.message, true);
    }
  }
}

async function refreshStatus() {
  try {
    const s = await getJSON("/api/status");
    state.status = s;
    updateMeta();
    if (s.state === "running" || s.state === "indexing") {
      showSyncBar(true);
      $("syncLog").textContent = s.log.join("\n");
      $("syncLog").scrollTop = $("syncLog").scrollHeight;
      $("syncStateText").textContent = s.state === "indexing" ? "首次分析中…" : "同步中…";
      $("syncBtn").disabled = true;
      startStatusPoll();
    } else {
      $("syncBtn").disabled = false;
      if (!state.syncing) stopStatusPoll();
      if (state.syncing && s.state === "done") {
        state.syncing = false;
        hideSyncBar();
        toast("同步完成，已刷新最新数据");
        await loadAnalysis();
        await loadAITags();
        if (state.selected) loadDetailAI(state.selected);
      } else if (state.syncing && s.state === "error") {
        state.syncing = false;
        hideSyncBar();
        toast(s.error || "同步失败", true);
      }
    }
    if (s.has_data && !state.data) await loadAnalysis();
  } catch (e) {
    $("syncMeta").textContent = "服务未连接";
  }
}

function updateMeta() {
  const s = state.status;
  if (!s || !s.has_data) {
    $("syncMeta").textContent = "等待数据…";
    return;
  }
  const t = new Date(s.generated_at * 1000);
  const time = `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
  const n = state.data ? state.data.totals.messages : "";
  $("syncMeta").textContent = `更新于 ${time}${n ? " · " + fmtNum(n) + " 条" : ""}`;
  $("syncMeta").classList.toggle("stale", !!s.stale);
  if (s.stale) $("syncMeta").textContent += " · 有新数据待同步";
}

function startStatusPoll() {
  if (state.polling) return;
  state.polling = setInterval(refreshStatus, 1000);
}

function stopStatusPoll() {
  clearInterval(state.polling);
  state.polling = null;
}

function showSyncBar() { $("syncBar").hidden = false; }
function hideSyncBar() { $("syncBar").hidden = true; stopStatusPoll(); }

async function syncNow() {
  if (state.syncing) return;
  state.syncing = true;
  $("syncBtn").disabled = true;
  $("syncLog").textContent = "正在启动同步任务…";
  $("syncStateText").textContent = "同步中…";
  showSyncBar();
  try {
    await postJSON("/api/sync");
    startStatusPoll();
  } catch (e) {
    state.syncing = false;
    $("syncBtn").disabled = false;
    hideSyncBar();
    toast(e.message, true);
  }
}

/* ---------- rendering ---------- */

function renderAll() {
  if (!state.data) return;
  renderStats();
  renderDist();
  renderTagCloud();
  renderRanking();
  renderChatList();
  updateMeta();
  if (state.selected) {
    const chat = state.data.chats.find((c) => c.username === state.selected);
    if (chat) fillDetail(chat);
  }
}

function renderStats() {
  const t = state.data.totals;
  const ai = aiFreshness();
  let avg = null;
  const scored = [];
  for (const c of state.data.chats) {
    if (c.is_group) continue;
    const v = aiAffection(c);
    if (v != null) scored.push(v);
  }
  if (scored.length) avg = scored.reduce((a, b) => a + b, 0) / scored.length;
  $("stats").innerHTML = [
    ["消息总数", fmtNum(t.messages), `${fmtNum(t.chats)} 个聊天`],
    ["单聊人数", fmtNum(t.singles), `${fmtNum(t.groups)} 个群聊`],
    ["AI 平均好感度", avg == null ? "—" : avg.toFixed(1),
      avg == null ? "还没有 AI 判定" : `已分析 ${scored.length} 人`],
    ["AI 覆盖", `${ai.total}/${t.singles}`, ai.total ? `${ai.fresh} 人结果是最新` : "点击下方开始批量分析"],
  ].map(([label, value, hint]) => `
    <div class="stat">
      <div class="label">${label}</div>
      <div class="value">${esc(value)}</div>
      <div class="hint">${esc(hint)}</div>
    </div>`).join("");
}

function aiBuckets() {
  const buckets = [
    { label: "80–100", min: 80, count: 0 },
    { label: "65–79", min: 65, count: 0 },
    { label: "50–64", min: 50, count: 0 },
    { label: "35–49", min: 35, count: 0 },
    { label: "0–34", min: 0, count: 0 },
  ];
  for (const c of state.data.chats) {
    if (c.is_group) continue;
    const v = aiAffection(c);
    if (v == null) continue;
    for (const b of buckets) {
      if (v >= b.min) { b.count += 1; break; }
    }
  }
  return buckets;
}

function renderDist() {
  const useAI = state.aiMode === "ai";
  const buckets = useAI ? aiBuckets() : (state.data.buckets || []);
  $("distTitle").textContent = useAI ? "AI 好感度分布" : "行为指数分布";
  const total = buckets.reduce((a, b) => a + b.count, 0);
  $("distHint").textContent = useAI
    ? (total ? `${total} 人已判定` : "还没有 AI 判定")
    : "统计口径";
  if (!total) {
    $("dist").innerHTML = `<div class="empty" style="padding:26px 10px">${
      useAI ? "在下方启动 AI 批量分析，或打开某个聊天单独判定" : "暂无数据"}</div>`;
    return;
  }
  const max = Math.max(1, ...buckets.map((b) => b.count));
  $("dist").innerHTML = buckets.map((b) => `
    <div class="dist-row">
      <span>${esc(b.label)}</span>
      <span class="bar"><i style="width:${(b.count / max) * 100}%"></i></span>
      <span class="n">${b.count}</span>
    </div>`).join("");
}

function renderTagCloud() {
  const counts = new Map();
  for (const item of Object.values(state.aiTags)) {
    if (!item || item.is_group) continue;
    for (const tag of item.tags || []) {
      const key = String(tag).trim();
      if (!key) continue;
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  }
  const rows = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 22);
  if (!rows.length) {
    $("tagCloud").innerHTML = `<div class="empty" style="padding:26px 10px">AI 分析后，这里会汇总联系人身上反复出现的标签</div>`;
    return;
  }
  const max = rows[0][1];
  $("tagCloud").innerHTML = rows.map(([tag, n]) => `
    <span class="tag ${n >= Math.max(2, max * 0.6) ? "hot" : ""}">${esc(tag)}<b>${n}</b></span>`).join("");
}

function renderRanking() {
  const rows = state.data.chats.filter((c) => !c.is_group).map((c) => ({
    c,
    ai: aiFor(c.username),
    aiScore: aiAffection(c),
    behavior: c.score,
  }));
  const useAI = state.aiMode === "ai";
  rows.sort((a, b) => {
    if (useAI) {
      const av = a.aiScore == null ? -1 : a.aiScore;
      const bv = b.aiScore == null ? -1 : b.aiScore;
      if (av !== bv) return bv - av;
    }
    return b.behavior - a.behavior;
  });
  const ready = rows.filter((r) => r.aiScore != null).length;
  $("rankHint").textContent = useAI
    ? `按 AI 好感度排序 · 已判定 ${ready}/${rows.length} 人`
    : `按聊天行为统计排序 · AI 判定不作为依据`;
  $("ranking").innerHTML = rows.map((r, i) => {
    const value = useAI ? r.aiScore : r.behavior;
    const tag = useAI && r.ai && r.ai.tags && r.ai.tags.length ? r.ai.tags[0] : null;
    const rel = useAI && aiRelation(r.c);
    const sub = useAI
      ? `${r.ai ? timeAgo(r.ai.created_at) : "待 AI 判定"}${r.ai && !r.ai.fresh ? " · 有新消息" : ""}`
      : `${timeAgo(r.c.last_ts)} · ${fmtNum(r.c.msg_count)} 条消息`;
    return `
      <div class="rank-row" data-user="${esc(r.c.username)}">
        <div class="idx">${value == null && useAI ? "—" : i + 1}</div>
        ${avatar(r.c.name, "", r.c.username)}
        <div>
          <div class="name">${esc(r.c.name)}</div>
          <div class="sub"><span>${esc(sub)}</span>${tag ? `<span class="tag-mini">${esc(tag)}</span>` : ""}${rel ? `<span class="tag-mini">${esc(rel)}</span>` : ""}</div>
        </div>
        ${value == null ? '<div></div>' : `<div class="track"><i style="width:${value}%"></i></div>`}
        <div class="score ${scoreClass(value)}">${value == null ? "待分析" : value.toFixed(0)}<small class="${useAI && value != null ? "ai" : ""}">${useAI ? (value == null ? "AI" : "AI 好感度") : "行为指数"}</small></div>
      </div>`;
  }).join("");
}

function filteredChats() {
  let rows = state.data.chats;
  if (state.filter === "single") rows = rows.filter((c) => !c.is_group);
  if (state.filter === "group") rows = rows.filter((c) => c.is_group);
  if (state.query) {
    const q = state.query.toLowerCase();
    rows = rows.filter((c) => c.name.toLowerCase().includes(q) || c.username.toLowerCase().includes(q));
  }
  return rows;
}

function renderChatList() {
  const rows = filteredChats();
  $("chatCount").textContent = `${rows.length} 个`;
  if (!rows.length) {
    $("chatList").innerHTML = `<div class="empty">没有匹配的聊天</div>`;
    return;
  }
  $("chatList").innerHTML = rows.map((c) => {
    const ai = aiFor(c.username);
    const preview = c.last_text || (c.is_group ? "群聊" : "暂无消息");
    return `
      <div class="chat-item ${state.selected === c.username ? "active" : ""}" data-user="${esc(c.username)}">
        ${avatar(c.name, "", c.username)}
        <div class="chat-main">
          <div class="chat-row">
            <span class="name">${esc(c.name)}</span>
            <span class="time">${fmtListTime(c.last_ts)}</span>
          </div>
          <div class="preview">${esc(preview)}</div>
        </div>
        ${ai ? '<i class="ai-dot" title="已有 AI 判定"></i>' : ""}
      </div>`;
  }).join("");
}

/* ---------- detail ---------- */

function currentChat() {
  return state.data && state.selected
    ? state.data.chats.find((c) => c.username === state.selected)
    : null;
}

function setDrawerView(view) {
  state.drawerView = view;
  [...$("drawerTabs").children].forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $("overview").hidden = view !== "overview";
  $("chatAnalysis").hidden = view !== "chat";
}

function openDrawer(view = "overview") {
  if (view === "chat" && !state.selected) {
    toast("先从左侧选择一个聊天");
    view = "overview";
  }
  setDrawerView(view);
  $("insightDrawer").classList.add("open");
  $("insightDrawer").setAttribute("aria-hidden", "false");
  $("drawerBackdrop").hidden = false;
  requestAnimationFrame(() => $("drawerBackdrop").classList.add("show"));
}

function closeDrawer() {
  $("insightDrawer").classList.remove("open");
  $("insightDrawer").setAttribute("aria-hidden", "true");
  $("drawerBackdrop").classList.remove("show");
  setTimeout(() => { $("drawerBackdrop").hidden = true; }, 220);
}

function openDetail(username, opts = {}) {
  const chat = state.data.chats.find((c) => c.username === username);
  if (!chat) return;
  state.selected = username;
  state.messages = [];
  state.oldestTs = null;
  state.hasMore = true;
  state.loadingMore = false;
  state.detailAI = { perception: null, guidance: null, stale: false };
  state.aiTab = "perception";
  state.aiBusy = null;
  stopAILoader();
  $("emptyChat").hidden = true;
  $("detail").hidden = false;
  document.querySelector(".wechat").classList.add("chatting");
  fillDetail(chat);
  setAITab("perception", true);
  $("sendText").value = "";
  setSendHint("");
  if (!state.sendReady) refreshSendStatus();
  else $("sendBtn").disabled = false;
  renderChatList();
  loadMessages(true);
  loadDetailAI(username);
  if (opts.drawer) openDrawer(opts.drawer);
}

function fillDetail(c) {
  const ai = detailAIFor(c);
  const aff = ai ? Number(ai.scores.affection) : null;
  $("dName").textContent = c.name;
  if ($("drawerName")) $("drawerName").textContent = c.name;
  $("dMeta").textContent = `${c.is_group ? "群聊" : "单聊"} · ${fmtNum(c.msg_count)} 条消息 · 最后互动 ${timeAgo(c.last_ts)}`;
  const av = $("avatar");
  const h = hueOf(c.name);
  av.style.background = `linear-gradient(135deg,hsl(${h} 52% 47%),hsl(${(h + 46) % 360} 48% 38%))`;
  av.innerHTML = `<span>${esc(initial(c.name))}</span><img loading="lazy" src="/api/avatars/${encodeURIComponent(c.username)}" alt="" onerror="this.remove()">`;

  const ring = $("ring");
  const value = c.is_group ? null : (aff ?? c.score);
  $("dScore").textContent = value == null ? "—" : value.toFixed(0);
  $("dScoreLabel").textContent = c.is_group ? "群聊" : (aff != null ? "AI 好感度" : "行为指数");
  ring.classList.toggle("pending", value == null);
  ring.style.setProperty("--p", value == null ? 0 : value);
  let rankText;
  if (c.is_group) {
    rankText = "群聊 · 不做单人判定";
  } else if (aff != null) {
    const peers = state.data.chats
      .filter((x) => !x.is_group)
      .map((x) => ({ x, v: aiAffection(x) }))
      .filter((x) => x.v != null)
      .sort((a, b) => b.v - a.v);
    const pos = peers.findIndex((x) => x.x.username === c.username) + 1;
    rankText = `AI 第 ${pos} / ${peers.length} 名`;
  } else {
    rankText = `行为统计第 ${c.rank} / ${c.ranked_total} 名`;
  }
  $("dRank").textContent = rankText;
  const aiScope = ai && ai.coverage && ai.coverage.label ? ai.coverage.label : "AI";
  $("dScoreSource").textContent = c.is_group
    ? "可问 AI / 对话指导"
    : aff != null
      ? `${aiScope} · ${fmtStamp(ai.created_at)}${ai.fresh ? "" : " · 有新消息"}`
      : "按当前范围 · AI 待判定";

  const rel = ai && ai.relations && ai.relations.length ? ai.relations[0] : null;
  const chips = [];
  if (rel) chips.push(`<span class="band ai">${esc(rel.type)} · ${rel.confidence}%</span>`);
  if (ai && ai.mood) chips.push(`<span class="band">情绪 ${esc(ai.mood)}</span>`);
  chips.push(`<span class="band">${c.trend && c.trend.pct != null ? `${esc(c.trend.label)} ${c.trend.pct > 0 ? "+" : ""}${c.trend.pct}%` : "趋势样本不足"}</span>`);
  chips.push(`<span class="band">样本 ${c.confidence === "high" ? "充足" : c.confidence === "medium" ? "一般" : "偏少"}</span>`);
  $("dBand").innerHTML = chips.join("");

  $("dTags").innerHTML = ai && ai.tags
    ? ai.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")
    : "";

  $("dNarrative").innerHTML = esc(c.narrative || "");

  const dims = c.dimensions || {};
  $("dDims").innerHTML = Object.entries(DIM_LABELS).map(([k, label]) => `
    <div class="dim-row">
      <span>${label}</span>
      <span class="track"><i style="width:${dims[k] || 0}%"></i></span>
      <span class="n">${Math.round(dims[k] || 0)}</span>
    </div>`).join("");

  const metrics = [
    ["消息总数", fmtNum(c.msg_count)],
    ["对方发送", fmtNum(c.theirs)],
    ["我发送", fmtNum(c.mine)],
    ["主动开启", `${c.init_other} / ${c.init_other + c.init_self}`],
    ["回复率", `${Math.round(c.reply_rate * 100)}%`],
    ["中位回复", c.reply_latency == null ? "—" : fmtDuration(c.reply_latency)],
    ["对方消息长度", `${Math.round(c.median_chars)} 字`],
    ["提问率", `${Math.round(c.question_rate * 100)}%`],
    ["表情率", `${Math.round(c.emoji_rate * 100)}%`],
    ["深夜消息", `${Math.round(c.late_rate * 100)}%`],
    ["温暖密度", `${c.warm_rate.toFixed(1)}/千条`],
    ["冷淡密度", `${c.cold_rate.toFixed(1)}/千条`],
    ["近 30 天", `${fmtNum(c.recent_30d)} 条`],
    ["关系平衡", `${Math.round(c.balance * 100)}%`],
  ];
  $("dMetrics").innerHTML = metrics.map(([k, v]) => `<div class="metric"><span>${k}</span><b>${v}</b></div>`).join("");
  $("dConfidence").textContent = `样本 ${fmtNum(c.msg_count)} 条`;

  const ev = c.evidence || [];
  $("dEvidence").innerHTML = ev.length
    ? ev.map((q) => `
      <div class="quote">
        <p>“${esc(q.text)}”</p>
        <div class="qmeta"><span>${esc(q.sender || c.name)}</span><span>${esc(q.date)}</span></div>
      </div>`).join("")
    : `<div class="empty">没有发现明显的温暖表达，看看行为信号。</div>`;
}

function fmtDuration(sec) {
  if (sec < 90) return `${Math.round(sec)} 秒`;
  if (sec < 5400) return `${Math.round(sec / 60)} 分钟`;
  if (sec < 172800) return `${(sec / 3600).toFixed(1)} 小时`;
  return `${(sec / 86400).toFixed(1)} 天`;
}

/* ---------- AI: cached detail state ---------- */

async function loadDetailAI(username) {
  try {
    const d = await getJSON(`/api/chats/${encodeURIComponent(username)}/ai?scope=${encodeURIComponent(state.scope)}`);
    if (state.selected !== username) return;
    state.scopeInfo = d.scope_info || null;
    $("aiScope").value = d.scope || state.scope;
    $("scopeInfo").textContent = scopeSummary(state.scopeInfo);
    state.detailAI = {
      perception: d.perception || null,
      guidance: d.guidance || null,
      stale: !!d.stale,
    };
    const chat = currentChat();
    if (chat) fillDetail(chat);
    restoreGuidanceInputs();
    renderAIPane();
  } catch (e) {
    /* keep empty state */
  }
}

function restoreGuidanceInputs() {
  const entry = state.detailAI.guidance;
  if (!entry || !entry.result) return;
  const r = entry.result;
  const input = $("draftInput");
  if (!input.value && r.draft) input.value = r.draft;
  if (!input.dataset.touched) {
    [...$("goalChips").children].forEach((c) => {
      c.classList.toggle("active", !!r.goal && c.dataset.goal === r.goal);
    });
  }
}

function setAITab(tab, silent = false) {
  state.aiTab = tab;
  [...$("aiTabs").children].forEach((b) => b.classList.toggle("active", b.dataset.ai === tab));
  $("aiPerception").hidden = tab !== "perception";
  $("aiGuidance").hidden = tab !== "guidance";
  $("aiAsk").hidden = tab !== "ask";
  $("scopeRow").hidden = tab !== "perception";
  $("aiRefreshBtn").hidden = tab === "ask";
  $("aiRefreshBtn").textContent = tab === "guidance" ? "重新生成" : "重新分析";
  $("aiFreshness").textContent = "";
  if (!silent) renderAIPane();
  if (tab === "ask") {
    renderChatThread();
    $("chatInput").focus();
  }
}

function renderAIPane() {
  if (state.aiTab === "perception") renderPerception();
  else if (state.aiTab === "guidance") renderGuidance();
  else renderChatThread();
}

function startAILoader(container, note) {
  stopAILoader();
  const t0 = Date.now();
  container.innerHTML = `
    <div class="ai-loading">
      <div class="shimmer"></div><div class="shimmer"></div><div class="shimmer"></div>
      <div class="hint">${esc(note)} <span class="elapsed">0s</span></div>
    </div>`;
  state.aiTimer = setInterval(() => {
    const el = container.querySelector(".elapsed");
    if (el) el.textContent = `${Math.round((Date.now() - t0) / 1000)}s`;
  }, 500);
  const meta = state.aiStatus;
  $("aiFreshness").textContent = meta && meta.available
    ? `本地推理 · ${meta.model}`
    : "AI 未连接，请先在 127.0.0.1:8000 启动模型";
}

function stopAILoader() {
  clearInterval(state.aiTimer);
  state.aiTimer = null;
}

function aiOfflineHint() {
  const s = state.aiStatus;
  const base = s && s.base_url ? s.base_url : "http://127.0.0.1:8000/v1";
  return `连不上本地模型（${base}）。启动 LM Studio / omlx 的 OpenAI 兼容服务后重试。`;
}

function renderPerception() {
  const box = $("aiPerception");
  const chat = currentChat();
  if (state.aiBusy === "perception") return;
  stopAILoader();
  const entry = state.detailAI.perception;
  if (!entry || !entry.result) {
    $("aiFreshness").textContent = "";
    const info = state.scopeInfo;
    const count = info ? info.count : 0;
    box.innerHTML = `
      <div class="empty ai-cta">
        <p>${chat && chat.is_group
          ? "群聊不做单人对你的态度判定。可以直接「问 AI」或使用「对话指导」。"
          : `让本地模型阅读「${esc(scopeLabel(state.scope))}」内的消息，判定对方对你的友善度、好感度、厌恶度，以及疑似关系。`}</p>
        ${chat && !chat.is_group && info ? `<p class="faint-note">${esc(scopeSummary(info))}${info.count < info.total ? ` · 历史共 ${fmtNum(info.total)} 条` : ""}</p>` : ""}
        <button class="btn primary" data-action="analyze">${chat && chat.is_group ? "暂不可分析" : (count ? "开始判定" : "该范围没有消息")}</button>
      </div>`;
    const btn = box.querySelector('[data-action="analyze"]');
    if (btn) btn.disabled = !!(chat && chat.is_group) || (!chat ? false : !count);
    return;
  }
  const r = entry.result;
  const stale = state.detailAI.stale;
  const cov = r.coverage || (state.scopeInfo || null);
  const scores = r.scores || {};
  const metrics = Object.entries(AI_SCORE_LABELS).map(([key, [label, negative]]) => {
    const v = Number(scores[key] ?? 0);
    return `
      <div class="ai-metric ${negative ? "negative" : ""}">
        <div class="k"><span>${label}</span></div>
        <div class="v">${v}<small>/100</small></div>
        <div class="bar"><i style="width:${v}%"></i></div>
      </div>`;
  }).join("");

  const relations = (r.relations || []).map((rel) => `
    <div class="relation-row">
      <span class="type">${esc(rel.type)}</span>
      <span class="track"><i style="width:${rel.confidence}%"></i></span>
      <span class="n">${rel.confidence}%</span>
      ${rel.reason ? `<div class="reason">${esc(rel.reason)}</div>` : ""}
    </div>`).join("") || `<div class="faint-note">没有明显的关系指向</div>`;

  const signals = (r.signals || []).map((s) => `
    <div class="signal ${esc(s.type)}">
      <div class="sig-head"><span class="type"></span><span>${esc(s.label)}</span></div>
      <blockquote>“${esc(s.quote)}”</blockquote>
    </div>`).join("") || `<div class="faint-note">没有可引用的信号</div>`;

  const flags = (list, cls) => (list || []).map((x) => `<div class="flag ${cls}">${esc(x)}</div>`).join("");
  const flagsHtml = (flags(r.green_flags, "positive") || flags(r.red_flags, "negative"))
    ? `<div class="ai-columns">
        <div class="ai-block"><h4>靠近信号</h4><div class="flag-list">${flags(r.green_flags, "positive") || '<div class="faint-note">暂无</div>'}</div></div>
        <div class="ai-block"><h4>疏远信号</h4><div class="flag-list">${flags(r.red_flags, "negative") || '<div class="faint-note">暂无</div>'}</div></div>
      </div>`
    : "";

  const advice = (r.advice || []).length
    ? `<div class="ai-advice"><h4>互动建议</h4><ol>${r.advice.map((a) => `<li>${esc(a)}</li>`).join("")}</ol></div>`
    : "";

  box.innerHTML = `
    ${stale ? `<div class="tiny-badge warn" style="margin-bottom:10px">有新消息，结果可能过期</div>` : ""}
    <div class="ai-summary">
      ${rich(r.summary)}
      <div class="mood">情绪基调 · ${esc(r.mood || "中性")}
        ${(r.tags || []).map((t) => `<span class="tag-mini">${esc(t)}</span>`).join("")}
      </div>
    </div>
    <div class="ai-metrics">${metrics}</div>
    <div class="ai-columns">
      <div class="ai-block"><h4>疑似关系</h4>${relations}</div>
      <div class="ai-block"><h4>关键信号</h4>${signals}</div>
    </div>
    ${flagsHtml}
    ${advice}
    <div class="ai-foot">
      <span>AI 判定于 ${fmtStamp(entry.created_at)} · ${cov ? `${esc(cov.label)} · ${esc(fmtRange(cov.first_ts, cov.last_ts))} · 本次 ${fmtNum(cov.count)} 条` : `基于 ${fmtNum(r.message_count || 0)} 条消息`}</span>
      <span>${esc(r.model || "")}</span>
    </div>`;
}

async function runPerception(refresh = false) {
  const chat = currentChat();
  if (!chat || chat.is_group) return;
  const username = chat.username;
  const scope = state.scope;
  state.aiBusy = "perception";
  $("aiRefreshBtn").disabled = true;
  const box = $("aiPerception");
  const info = state.scopeInfo;
  const note = `本地模型正在阅读「${scopeLabel(scope)}」${info ? `（${fmtNum(info.count)} 条）` : ""}…`
    + (scope === "all" && info && info.count > 3000 ? " 全量会分段阅读，较大聊天可能需要 5–10 分钟。" : "");
  startAILoader(box, note);
  try {
    const res = await postJSON("/api/ai/perception", { username, scope, refresh });
    if (state.selected !== username || state.scope !== scope) return;
    state.detailAI.perception = { created_at: res.created_at, result: res.perception };
    state.detailAI.stale = !!res.stale;
    state.scopeInfo = res.coverage || state.scopeInfo;
    $("scopeInfo").textContent = scopeSummary(state.scopeInfo);
    const item = {
      username,
      name: chat.name,
      is_group: false,
      scores: res.perception.scores,
      tags: res.perception.tags,
      mood: res.perception.mood,
      relations: res.perception.relations,
      summary: res.perception.summary,
      created_at: res.created_at,
      scope: res.scope,
      coverage: res.coverage,
      fresh: true,
    };
    state.aiTags[chat.username] = item;
    $("aiFreshness").textContent = `刚判定 · ${res.elapsed ?? 0}s`;
    renderAll();
    renderAIPane();
    toast(res.cached ? "使用缓存的判定结果" : "AI 判定完成");
  } catch (e) {
    if (state.selected === username) {
      box.innerHTML = `<div class="empty"><p>${esc(e.message)}</p><p class="faint-note">${esc(aiOfflineHint())}</p></div>`;
    }
    toast(e.message, true);
  } finally {
    state.aiBusy = null;
    $("aiRefreshBtn").disabled = false;
    stopAILoader();
  }
}

function renderGuidance() {
  const box = $("guideResult");
  if (state.aiBusy === "guidance") return;
  const entry = state.detailAI.guidance;
  if (!entry || !entry.result) {
    box.innerHTML = `<div class="empty" style="padding:22px 10px">写一句你想回的话，或留空让 AI 直接给下一条消息的建议。</div>`;
    $("aiFreshness").textContent = "";
    return;
  }
  const r = entry.result;
  $("aiFreshness").textContent = `${fmtStamp(entry.created_at)} · ${r.model || ""}`;
  const replies = (r.replies || []).map((rep) => `
    <div class="reply-card">
      <div class="reply-head">
        <span class="tone">${esc(rep.tone)}</span>
        <button class="copy-btn" data-copy="${esc(rep.text)}">复制</button>
      </div>
      <div class="reply-text">${esc(rep.text)}</div>
      ${rep.why ? `<div class="reply-why">${esc(rep.why)}</div>` : ""}
    </div>`).join("");
  box.innerHTML = `
    <div class="guide-read">
      ${rich(r.read)}
      <span class="strategy">策略 · ${esc(r.strategy)}</span>
    </div>
    ${replies}
    <div class="guide-extra">
      <div class="block"><h4>可以聊</h4><ul>${(r.topics || []).map((t) => `<li>${esc(t)}</li>`).join("") || "<li>—</li>"}</ul></div>
      <div class="block"><h4>要避免</h4><ul>${(r.cautions || []).map((t) => `<li>${esc(t)}</li>`).join("") || "<li>—</li>"}</ul></div>
    </div>
    ${r.next_step ? `<div class="next-step">下一步 · ${esc(r.next_step)}</div>` : ""}`;
}

async function runGuidance(refresh = false) {
  const chat = currentChat();
  if (!chat) return;
  const username = chat.username;
  const draft = $("draftInput").value.trim();
  const active = $("goalChips").querySelector(".chip.active");
  const goal = active ? active.dataset.goal : "";
  state.aiBusy = "guidance";
  $("guideBtn").disabled = true;
  $("aiRefreshBtn").disabled = true;
  const box = $("guideResult");
  startAILoader(box, "本地模型正在结合最近对话想措辞…");
  try {
    const res = await postJSON("/api/ai/guidance", {
      username, draft, goal, refresh,
    });
    if (state.selected !== username) return;
    state.detailAI.guidance = { created_at: res.created_at, result: res.guidance };
    $("aiFreshness").textContent = `刚生成 · ${res.elapsed ?? 0}s`;
    renderGuidance();
    toast(res.cached ? "使用缓存的建议" : "建议已生成");
  } catch (e) {
    if (state.selected === username) {
      box.innerHTML = `<div class="empty"><p>${esc(e.message)}</p><p class="faint-note">${esc(aiOfflineHint())}</p></div>`;
    }
    toast(e.message, true);
  } finally {
    state.aiBusy = null;
    $("guideBtn").disabled = false;
    $("aiRefreshBtn").disabled = false;
    stopAILoader();
  }
}

/* ---------- AI chat ---------- */

function sessionFor(username) {
  if (!state.chatSessions[username]) state.chatSessions[username] = [];
  return state.chatSessions[username];
}

function renderChatThread() {
  const username = state.selected;
  if (!username) return;
  const box = $("chatThread");
  const session = sessionFor(username);
  const pending = !!state.chatPending[username];
  if (!session.length && !pending) {
    box.innerHTML = `<div class="empty" style="padding:24px 10px">问数据里的任何事：时间线、对方提过的话题、态度变化、谁更主动。AI 会检索聊天记录再回答。</div>`;
    $("chatPrompts").hidden = false;
    return;
  }
  $("chatPrompts").hidden = session.length > 0;
  const lines = session.map((m) => {
    if (m.role === "user") {
      return `<div class="chat-line user"><div class="chat-bubble">${rich(m.content)}</div></div>`;
    }
    if (m.error) {
      return `<div class="chat-line"><span class="ai-orb sm">AI</span><div class="chat-bubble err">${esc(m.content)}</div></div>`;
    }
    const refs = (m.refs || []).length
      ? `<details><summary>参考 ${m.refs.length} 条记录</summary>${m.refs.slice(0, 8).map((r) => `<div class="ref">${esc(r.date)} · ${esc(r.sender)}：${esc(r.text)}</div>`).join("")}</details>`
      : "";
    return `<div class="chat-line"><span class="ai-orb sm">AI</span><div class="chat-bubble">${rich(m.content)}${refs}</div></div>`;
  }).join("");
  const typing = pending
    ? `<div class="chat-line"><span class="ai-orb sm">AI</span><div class="chat-bubble"><span class="typing"><i></i><i></i><i></i></span></div></div>`
    : "";
  box.innerHTML = lines + typing;
  box.scrollTop = box.scrollHeight;
}

async function sendChat(question) {
  const username = state.selected;
  if (!username) return;
  const q = (question ?? $("chatInput").value).trim();
  if (!q || state.chatPending[username]) return;
  const session = sessionFor(username);
  const history = session
    .filter((m) => !m.error)
    .slice(-8)
    .map((m) => ({ role: m.role, content: m.content }));
  session.push({ role: "user", content: q });
  $("chatInput").value = "";
  $("chatInput").style.height = "";
  state.chatPending[username] = true;
  renderChatThread();
  try {
    const res = await postJSON("/api/ai/ask", { username, question: q, history });
    session.push({ role: "assistant", content: res.answer, refs: res.references || [] });
    $("chatFoot").textContent = `回答基于 ${fmtNum(res.facts?.total ?? 0)} 条消息 · 检索 ${res.references?.length ?? 0} 条 · ${res.model || ""} · ${res.elapsed ?? 0}s`;
  } catch (e) {
    session.push({ role: "assistant", content: e.message, error: true });
    toast(e.message, true);
  } finally {
    state.chatPending[username] = false;
    if (state.selected === username) renderChatThread();
  }
}

/* ---------- AI batch ---------- */

function updateBatchUI(s) {
  state.batch = s;
  const badge = $("aiConsoleBadge");
  const text = $("aiConsoleText");
  const progress = $("aiProgress");
  const bar = $("aiProgressBar");
  const startBtn = $("aiBatchBtn");
  const stopBtn = $("aiBatchStop");
  const running = s && s.state === "running";
  if (startBtn) startBtn.disabled = running;
  if (stopBtn) stopBtn.hidden = !running;
  progress.hidden = !s || s.state === "idle";
  if (!s || s.state === "idle") {
    badge.className = "tiny-badge";
    badge.textContent = "待开始";
    return;
  }
  const pct = s.total ? Math.round((s.done / s.total) * 100) : 0;
  bar.style.width = `${pct}%`;
  if (running) {
    badge.className = "tiny-badge running";
    badge.textContent = `分析中 ${s.done}/${s.total}`;
    text.textContent = `正在阅读「${s.current || "…"}」 · 成功 ${s.ok} · 失败 ${s.failed}`;
  } else if (s.state === "done") {
    badge.className = "tiny-badge done";
    badge.textContent = `已完成 ${s.done} 人`;
    text.textContent = `范围「${scopeLabel(s.analysis_scope || state.scope)}」已更新：成功 ${s.ok} 人${s.failed ? `，失败 ${s.failed} 人` : ""}。点击「完整排行」可查看 AI 好感度。`;
  } else if (s.state === "stopped") {
    badge.className = "tiny-badge warn";
    badge.textContent = `已停止 ${s.done}/${s.total}`;
    text.textContent = `批量分析已停止，已完成 ${s.ok} 人（范围：${scopeLabel(s.analysis_scope || state.scope)}），结果已缓存。`;
  } else {
    badge.className = "tiny-badge warn";
    badge.textContent = "上次失败";
    text.textContent = s.errors && s.errors.length ? s.errors[s.errors.length - 1].error : "批量分析失败";
  }
}

async function pollBatch() {
  try {
    const s = await getJSON("/api/ai/batch");
    updateBatchUI(s);
    if (s.state === "running") return;
    clearInterval(state.batchPoll);
    state.batchPoll = null;
    await loadAITags();
    if (state.selected) loadDetailAI(state.selected);
  } catch (e) {
    clearInterval(state.batchPoll);
    state.batchPoll = null;
  }
}

function startBatchPoll() {
  if (state.batchPoll) return;
  state.batchPoll = setInterval(pollBatch, 1500);
}

async function startBatch() {
  if (state.batch && state.batch.state === "running") return;
  if (state.scope === "all" && !window.confirm("全量范围下批量分析 90 个单聊可能需要数小时，确定继续？")) return;
  $("aiBatchBtn").disabled = true;
  try {
    const res = await postJSON("/api/ai/batch", { scope: "all", analysis_scope: state.scope });
    if (!res.started) {
      toast(res.reason || "没有需要分析的单聊");
      $("aiConsoleBadge").className = "tiny-badge done";
      $("aiConsoleBadge").textContent = "已是最新";
      $("aiConsoleText").textContent = "所有单聊都已有最新的 AI 判定。打开某个聊天可以单独重新分析。";
      return;
    }
    toast(`已开始按「${scopeLabel(state.scope)}」分析 ${res.total} 个单聊`);
    updateBatchUI({ state: "running", total: res.total, done: 0, ok: 0, failed: 0, current: "" });
    startBatchPoll();
  } catch (e) {
    toast(e.message, true);
  } finally {
    $("aiBatchBtn").disabled = false;
  }
}

/* ---------- messages ---------- */

async function loadMessages(initial = false) {
  if (!state.selected || (!initial && !state.hasMore)) return;
  if (!initial && state.loadingMore) return;
  state.loadingMore = true;
  const btn = $("loadMore");
  btn.disabled = true;
  try {
    const q = new URLSearchParams({ limit: "80" });
    if (!initial && state.oldestTs) q.set("before", state.oldestTs);
    const data = await getJSON(`/api/chats/${encodeURIComponent(state.selected)}/messages?${q}`);
    const msgs = data.messages || [];
    if (initial) state.messages = msgs;
    else state.messages = msgs.concat(state.messages);
    state.hasMore = msgs.length >= 80;
    state.oldestTs = state.messages.length ? state.messages[0].ts : null;
    renderTranscript(initial ? "bottom" : "preserve");
    $("transcriptHint").textContent = `已载入 ${state.messages.length} 条`;
  } catch (e) {
    toast(e.message, true);
  } finally {
    state.loadingMore = false;
    btn.disabled = false;
    $("loadMore").textContent = state.hasMore ? "加载更早" : "没有更早的消息";
  }
}

function renderTranscript(mode = "bottom") {
  const box = $("transcript");
  const chat = currentChat();
  const isGroup = !!(chat && chat.is_group);
  const prevHeight = $("chatBody").scrollHeight;
  const prevTop = $("chatBody").scrollTop;
  let prevTs = null;
  let prevKey = null;
  const html = state.messages.map((m) => {
    const gap = prevTs == null || (m.ts - prevTs) > 300;
    const sep = gap ? `<div class="time-sep">${fmtChatTime(m.ts)}</div>` : "";
    const key = m.is_self ? "self" : (isGroup ? `g:${m.sender}` : "other");
    const cont = !gap && prevKey === key;
    prevKey = key;
    prevTs = m.ts;
    const who = !m.is_self && isGroup && !cont ? `<div class="who">${esc(m.sender)}</div>` : "";
    let av;
    if (m.is_self) av = avatar("我", cont ? "cont" : "", "self");
    else if (isGroup) av = avatar(m.sender || "?", cont ? "cont" : "", "");
    else av = avatar(chat.name, cont ? "cont" : "", chat.username);
    const text = m.text || KIND_LABELS[m.kind] || "[消息]";
    const body = m.kind === "image" && m.md5
      ? `<img class="msg-img" loading="lazy" src="/api/images/${encodeURIComponent(m.md5)}" alt="图片" onerror="this.replaceWith(document.createTextNode('[图片已过期]'))">`
      : `<div class="text">${esc(text)}</div>`;
    return `${sep}<div class="msg${m.is_self ? " self" : ""}${cont ? " cont" : ""}">${av}<div class="bubble">${who}${body}</div></div>`;
  }).join("");
  box.innerHTML = html || `<div class="empty">没有可显示的消息</div>`;
  if (mode === "bottom") $("chatBody").scrollTop = $("chatBody").scrollHeight;
  else $("chatBody").scrollTop = $("chatBody").scrollHeight - prevHeight + prevTop;
}

/* ---------- events ---------- */

$("syncBtn").addEventListener("click", syncNow);

/* ---------- drawer ---------- */
$("insightBtn").addEventListener("click", () => openDrawer("overview"));
$("listAdd").addEventListener("click", () => openDrawer("overview"));
$("welcomeInsight").addEventListener("click", () => openDrawer("overview"));
$("openDrawerBtn").addEventListener("click", () => openDrawer("chat"));
$("drawerClose").addEventListener("click", closeDrawer);
$("drawerBackdrop").addEventListener("click", closeDrawer);
$("drawerTabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-view]");
  if (!btn) return;
  if (btn.dataset.view === "chat" && !state.selected) {
    toast("先从左侧选择一个聊天");
    return;
  }
  setDrawerView(btn.dataset.view);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("insightDrawer").classList.contains("open")) closeDrawer();
});
$("aiDraftBtn").addEventListener("click", () => {
  if (!state.selected) {
    toast("先选择一个聊天");
    return;
  }
  openDrawer("chat");
  setAITab("guidance");
  const draft = $("sendText").value.trim();
  if (draft) {
    $("draftInput").value = draft;
    $("draftInput").dataset.touched = "1";
  }
});
$("aiBatchBtn").addEventListener("click", startBatch);
$("aiBatchStop").addEventListener("click", async () => {
  try {
    const s = await postJSON("/api/ai/batch/stop");
    updateBatchUI(s);
    toast("已请求停止，当前这条完成后停下");
  } catch (e) {
    toast(e.message, true);
  }
});
$("search").addEventListener("input", (e) => {
  state.query = e.target.value.trim();
  renderChatList();
});
$("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-filter]");
  if (!btn) return;
  state.filter = btn.dataset.filter;
  [...$("tabs").children].forEach((b) => b.classList.toggle("active", b === btn));
  renderChatList();
});
$("chatList").addEventListener("click", (e) => {
  const row = e.target.closest("[data-user]");
  if (row) openDetail(row.dataset.user);
});
$("ranking").addEventListener("click", (e) => {
  const row = e.target.closest("[data-user]");
  if (row) openDetail(row.dataset.user, { drawer: "chat" });
});
$("rankMode").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-mode]");
  if (!btn) return;
  state.aiMode = btn.dataset.mode;
  [...$("rankMode").children].forEach((b) => b.classList.toggle("active", b === btn));
  renderDist();
  renderRanking();
  renderChatList();
});
$("loadMore").addEventListener("click", () => loadMessages(false));
$("chatBody").addEventListener("scroll", () => {
  if ($("chatBody").scrollTop < 70 && state.hasMore && !state.loadingMore) loadMessages(false);
});
$("mobileBack").addEventListener("click", () => {
  document.querySelector(".wechat").classList.remove("chatting");
  closeDrawer();
});
$("scrollLatestBtn").addEventListener("click", () => {
  $("chatBody").scrollTo({ top: $("chatBody").scrollHeight, behavior: "smooth" });
});

$("aiTabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-ai]");
  if (btn) setAITab(btn.dataset.ai);
});
$("aiRefreshBtn").addEventListener("click", () => {
  if (state.aiTab === "guidance") runGuidance(true);
  else runPerception(true);
});
$("aiScope").addEventListener("change", (e) => {
  state.scope = e.target.value;
  localStorage.setItem("wechatInsightScope", state.scope);
  state.detailAI = { perception: null, guidance: state.detailAI.guidance, stale: false };
  state.scopeInfo = null;
  state.aiBusy = null;
  stopAILoader();
  $("scopeInfo").textContent = "读取范围…";
  if (state.selected) loadDetailAI(state.selected);
  setAITab("perception");
  if (!state.batch || state.batch.state !== "running") {
    $("aiConsoleText").textContent = `按最近互动排序，在「${scopeLabel(state.scope)}」范围内为单聊生成态度评分、关系类型与印象标签。结果会缓存到本地。`;
  }
});

$("aiPerception").addEventListener("click", (e) => {
  const btn = e.target.closest('[data-action="analyze"]');
  if (btn && !btn.disabled) runPerception(false);
});
$("guideBtn").addEventListener("click", () => runGuidance(false));
$("goalChips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  const active = chip.classList.contains("active");
  [...$("goalChips").children].forEach((c) => c.classList.remove("active"));
  if (!active) chip.classList.add("active");
});
$("guideResult").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-copy]");
  if (!btn) return;
  try {
    await navigator.clipboard.writeText(btn.dataset.copy);
    btn.textContent = "已复制";
    setTimeout(() => { btn.textContent = "复制"; }, 1400);
  } catch (_) {
    toast("复制失败，请手动选择文本", true);
  }
});
$("chatPrompts").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-q]");
  if (chip) sendChat(chip.dataset.q);
});
$("chatSend").addEventListener("click", () => sendChat());
$("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    sendChat();
  }
});
$("draftInput").addEventListener("input", (e) => { e.target.dataset.touched = "1"; });
$("chatInput").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = `${Math.min(160, e.target.scrollHeight)}px`;
});

/* ---------- boot ---------- */

$("aiScope").value = state.scope;
$("aiScope").dispatchEvent(new Event("change"));
refreshAIStatus();
setInterval(refreshAIStatus, 30000);
loadAnalysis();
loadAITags(false).then(() => renderAll());
pollBatch().then(() => {
  if (state.batch && state.batch.state === "running") startBatchPoll();
});
refreshStatus();

/* image lightbox */
$("transcript").addEventListener("click", (e) => {
  const img = e.target.closest(".msg-img");
  if (!img) return;
  const box = $("lightbox");
  box.querySelector("img").src = img.src;
  box.hidden = false;
});
$("lightbox").addEventListener("click", () => { $("lightbox").hidden = true; });

/* ---------- live ingestion (SSE) ---------- */

function setLive(ok, text) {
  const badge = $("liveBadge");
  if (!badge) return;
  badge.className = `live-badge ${ok ? "on" : "off"}`;
  badge.querySelector("span").textContent = text || (ok ? "实时" : "重连中");
}

async function loadLatestMessages() {
  if (!state.selected) return;
  try {
    const data = await getJSON(`/api/chats/${encodeURIComponent(state.selected)}/messages?limit=50`);
    const keyOf = (m) => `${m.ts}:${m.local_id ?? `${m.sender}:${m.text}`}`;
    const seen = new Set(state.messages.map(keyOf));
    const added = (data.messages || []).filter((m) => !seen.has(keyOf(m)));
    if (!added.length) return;
    const box = $("chatBody");
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 160;
    state.messages = state.messages.concat(added).sort((a, b) => a.ts - b.ts);
    renderTranscript(nearBottom ? "bottom" : "preserve");
  } catch (_) { /* ignore */ }
}

function onLiveEvent(ev) {
  if (!ev || ev.type !== "messages" || !state.data) return;
  const chats = ev.chats || [];
  for (const u of chats) {
    const c = state.data.chats.find((x) => x.username === u.username);
    if (!c) continue;
    c.msg_count = u.total;
    c.last_ts = u.last_ts;
    c.mine = (c.mine || 0) + (u.mine || 0);
    c.theirs = (c.theirs || 0) + (u.theirs || 0);
  }
  state.data.totals.messages = (state.data.totals.messages || 0) + (ev.new || 0);
  renderStats();
  renderChatList();
  updateMeta();
  const current = chats.find((u) => u.username === state.selected);
  if (current && !$("detail").hidden) {
    loadLatestMessages();
    toast(`收到 ${current.new} 条新消息`);
  } else if (chats.length) {
    toast(`${chats[0].name} 收到 ${chats[0].new} 条新消息`);
  }
}

function connectLive() {
  if (!window.EventSource) {
    setLive(false, "不支持实时");
    return;
  }
  const es = new EventSource("/api/live");
  es.onopen = () => setLive(true, "实时");
  es.onerror = () => setLive(false, "重连中");
  es.addEventListener("messages", (e) => {
    try { onLiveEvent(JSON.parse(e.data)); } catch (_) { /* ignore */ }
  });
}

connectLive();

/* ---------- reply via WeChat UI automation ---------- */

const SEND_ERRORS = {
  helper_unavailable: "发送助手未安装或未运行。运行 ~/wechat-export-tool/helper/install.sh，并在 系统设置 → 隐私与安全性 → 辅助功能 里授权 WeChatSendHelper。",
  accessibility_denied: "发送助手没有辅助功能权限。到 系统设置 → 隐私与安全性 → 辅助功能 打开 WeChatSendHelper。",
  wechat_not_running: "微信没有运行，先打开并登录微信。",
  screen_locked: "屏幕已锁定，解锁后再发送。",
  user_busy: "检测到你正在使用电脑，本次没有发送。",
  user_activity: "发送过程中检测到键盘/鼠标操作，已在按回车前停止。",
  rate_limited: "发送太频繁，请稍后再试。",
  chat_not_found: "微信里找不到这个聊天。",
  ambiguous_chat: "匹配到多个同名聊天，未发送。",
  invalid_text: "消息内容不合法或过长。",
  ui_error: "微信界面状态异常，本次没有发送。",
  internal_error: "发送失败。",
};

function setSendHint(message, cls = "") {
  const el = $("sendHint");
  el.textContent = message || "";
  el.className = "composer-hint" + (cls ? " " + cls : "");
}

async function refreshSendStatus() {
  try {
    const s = await getJSON("/api/send/status");
    state.sendStatus = s;
    if (s.running && s.trusted) {
      state.sendReady = true;
      $("sendBtn").disabled = false;
      setSendHint("发送助手已连接", "ok");
    } else {
      state.sendReady = false;
      $("sendBtn").disabled = true;
      const code = s.error || (s.running && !s.trusted ? "accessibility_denied" : "helper_unavailable");
      setSendHint(SEND_ERRORS[code] || s.hint || SEND_ERRORS.helper_unavailable, "error");
    }
  } catch (e) {
    state.sendReady = false;
    $("sendBtn").disabled = true;
    setSendHint(e.message, "error");
  }
}

async function sendReply() {
  const text = $("sendText").value.trim();
  if (!text || !state.selected || state.sending) return;
  const btn = $("sendBtn");
  state.sending = true;
  btn.disabled = true;
  btn.textContent = "发送中…";
  setSendHint("正在操作微信，请不要使用键盘鼠标（约 10 秒）");
  try {
    const result = await getJSON(`/api/chats/${encodeURIComponent(state.selected)}/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (result.status === "sent" || result.status === "unverified" || result.status === "dry_run") {
      $("sendText").value = "";
      $("sendText").style.height = "";
      setSendHint(result.status === "unverified"
        ? "已按回车，数据库暂未确认；消息可能稍后出现"
        : "已发送", "ok");
      toast(result.status === "unverified" ? "已发送（等待数据库确认）" : "已发送");
      setTimeout(loadLatestMessages, 1200);
      setTimeout(loadLatestMessages, 4000);
    } else {
      setSendHint(SEND_ERRORS[result.error] || result.message || "发送失败", "error");
      toast("发送失败：" + (SEND_ERRORS[result.error] || result.error || "未知错误"), true);
    }
  } catch (e) {
    setSendHint(e.message, "error");
    toast(e.message, true);
  } finally {
    state.sending = false;
    btn.textContent = "发送";
    if (state.sendReady) btn.disabled = false;
  }
}

$("sendBtn").addEventListener("click", sendReply);
$("sendText").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendReply();
  }
});
$("sendText").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = `${Math.min(160, e.target.scrollHeight)}px`;
});

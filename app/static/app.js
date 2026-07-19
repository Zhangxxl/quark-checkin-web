const AVATAR_COLORS = [
  "#89b4fa", "#f38ba8", "#a6e3a1", "#f9e2af",
  "#cba6f7", "#94e2d5", "#fab387", "#74c7ec",
  "#eba0ac", "#b4befe", "#89dceb", "#f5c2e7",
];
function avatarColor(id) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), 2600);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

function fmtUrl(u) { return u.length > 44 ? u.slice(0, 44) + "…" : u; }

// ---- 渲染用户列表 ----
async function loadUsers() {
  const { data } = await api("/api/users");
  const list = document.getElementById("user-list");
  const count = document.getElementById("user-count");
  if (!data || data.length === 0) {
    list.innerHTML = '<div class="empty">还没有用户，点击「添加用户」开始。</div>';
    count.textContent = "共 0 个用户";
    return;
  }
  const signed = data.filter(u => u.signed_today).length;
  count.textContent = `共 ${data.length} 个用户，今日已签到 ${signed} 个`;
  list.innerHTML = "";
  data.forEach(u => {
    const card = document.createElement("div");
    card.className = "user-card";
    card.style.borderLeftColor = avatarColor(u.id);
    const initial = (u.nickname || "?").charAt(0);
    const statusCls = u.signed_today ? "status-signed" : "status-pending";
    const statusTxt = u.signed_today ? "✅ 已签到" : "⏳ 未签到";
    card.innerHTML = `
      <div class="avatar" style="background:${avatarColor(u.id)}">${escapeHtml(initial)}</div>
      <div class="card-info">
        <div class="card-name" title="${escapeHtml(u.nickname)}">${escapeHtml(u.nickname)}</div>
        <div class="card-url" title="${escapeHtml(u.url)}">${escapeHtml(fmtUrl(u.url))}</div>
      </div>
      <div class="card-status ${statusCls}">${statusTxt}</div>
      <button class="card-btn card-sign" ${u.signed_today ? "disabled" : ""} data-sign="${u.id}">签到</button>
      <button class="card-btn card-del" data-del="${u.id}">删除</button>
    `;
    list.appendChild(card);
  });
  list.querySelectorAll("[data-sign]").forEach(b =>
    b.addEventListener("click", () => signOne(b.getAttribute("data-sign"))));
  list.querySelectorAll("[data-del]").forEach(b =>
    b.addEventListener("click", () => deleteUser(b.getAttribute("data-del"))));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- 状态栏 ----
async function loadStatus() {
  const { data } = await api("/api/status");
  if (!data) return;
  document.getElementById("next-run").textContent = "下次: " + (data.next_run || "--");
  const signed = data.signed_today, total = data.total_users;
  document.getElementById("status-line").textContent =
    `定时 ${pad(data.schedule.hour)}:${pad(data.schedule.minute)} · 今日 ${signed}/${total} 已签到`;
}

// ---- 日志（实时） ----
async function loadLogs() {
  const { data } = await api("/api/logs?days=3");
  const area = document.getElementById("log-area");
  if (!data || data.length === 0) {
    if (area.textContent === "加载中…") area.textContent = "暂无日志";
    return;
  }
  const atBottom = area.scrollTop + area.clientHeight >= area.scrollHeight - 30;
  area.textContent = data.map(l => `[${l.ts}] ${l.level}  ${l.message}`).join("\n");
  if (atBottom) area.scrollTop = area.scrollHeight;
}

function pad(n) { return String(n).padStart(2, "0"); }

// ---- 操作 ----
async function signOne(id) {
  toast("⏳ 正在签到…");
  const { ok, data } = await api("/api/sign/one/" + id, { method: "POST" });
  if (ok) {
    const r = data.result;
    toast(r.message);
    showResult(r.success ? "签到结果" : "签到失败", (r.detail || r.message));
  } else {
    toast("签到请求失败");
  }
  await refreshAll();
}

async function signAll() {
  const { data } = await api("/api/users");
  if (!data || data.length === 0) { toast("请先添加用户"); return; }
  toast("⏳ 正在批量签到…");
  const { ok, data: res } = await api("/api/sign/all", { method: "POST" });
  if (ok) {
    const summary = res.results.map(r => r.message).join("\n");
    showResult("批量签到结果", summary);
  } else {
    toast("批量签到失败");
  }
  await refreshAll();
}

async function deleteUser(id) {
  if (!confirm("确定要删除该用户吗？")) return;
  await api("/api/users/" + id, { method: "DELETE" });
  toast("已删除用户");
  await refreshAll();
}

function refreshAll() {
  return Promise.all([loadUsers(), loadStatus(), loadLogs()]);
}

// ---- 弹窗 ----
function openModal(id) { document.getElementById(id).classList.add("open"); }
function closeModal(id) { document.getElementById(id).classList.remove("open"); }

function showResult(title, content) {
  document.getElementById("result-title").textContent = title;
  document.getElementById("result-area").textContent = content;
  openModal("modal-result");
}

// 添加用户
document.getElementById("btn-add").addEventListener("click", () => {
  document.getElementById("add-nickname").value = "";
  document.getElementById("add-url").value = "";
  openModal("modal-add");
});
document.getElementById("add-cancel").addEventListener("click", () => closeModal("modal-add"));
document.getElementById("add-confirm").addEventListener("click", async () => {
  const nickname = document.getElementById("add-nickname").value.trim();
  const url = document.getElementById("add-url").value.trim();
  if (!nickname) return toast("请输入用户昵称");
  if (!url) return toast("请输入签到URL");
  if (!/kps=/.test(url) || !/sign=/.test(url) || !/vcode=/.test(url))
    return toast("URL必须包含kps、sign、vcode参数");
  const { ok, data } = await api("/api/users", {
    method: "POST", body: JSON.stringify({ nickname, url }),
  });
  if (ok) { closeModal("modal-add"); toast("✅ 已添加用户"); await refreshAll(); }
  else toast(data.error || "添加失败");
});

// 定时设置
document.getElementById("btn-schedule").addEventListener("click", async () => {
  const { data: sch } = await api("/api/schedule");
  const { data: nm } = await api("/api/notify_mode");
  const { data: sk } = await api("/api/serverchan");
  document.getElementById("sch-hour").value = sch.hour;
  document.getElementById("sch-min").value = sch.minute;
  document.getElementById("notify-mode").value = nm.notify_mode;
  document.getElementById("sendkey").value = sk.sendkey || "";
  openModal("modal-schedule");
});
document.getElementById("sch-cancel").addEventListener("click", () => closeModal("modal-schedule"));
document.getElementById("sch-confirm").addEventListener("click", async () => {
  const hour = parseInt(document.getElementById("sch-hour").value, 10);
  const minute = parseInt(document.getElementById("sch-min").value, 10);
  if (isNaN(hour) || isNaN(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59)
    return toast("时间范围 0-23 / 0-59");
  const mode = document.getElementById("notify-mode").value;
  const sendkey = document.getElementById("sendkey").value.trim();
  await api("/api/schedule", { method: "POST", body: JSON.stringify({ hour, minute }) });
  await api("/api/notify_mode", { method: "POST", body: JSON.stringify({ notify_mode: mode }) });
  await api("/api/serverchan", { method: "POST", body: JSON.stringify({ sendkey }) });
  closeModal("modal-schedule");
  toast(`✅ 已设为 ${pad(hour)}:${pad(minute)}`);
  await refreshAll();
});
document.getElementById("sendkey-test").addEventListener("click", async () => {
  const sendkey = document.getElementById("sendkey").value.trim();
  toast("⏳ 正在测试推送…");
  const { ok, data } = await api("/api/serverchan/test", {
    method: "POST", body: JSON.stringify({ sendkey }),
  });
  toast(ok ? (data.message || "测试推送成功") : (data.error || "测试推送失败"));
});

// 批量选择
document.getElementById("btn-sign-selected").addEventListener("click", async () => {
  const { data } = await api("/api/users");
  if (!data || data.length === 0) return toast("请先添加用户");
  const box = document.getElementById("select-list");
  box.innerHTML = "";
  data.forEach(u => {
    const item = document.createElement("label");
    item.className = "select-item" + (u.signed_today ? " signed" : "");
    item.innerHTML = `<input type="checkbox" value="${u.id}" ${u.signed_today ? "disabled" : ""}/>
      <span>${escapeHtml(u.nickname)}</span>`;
    box.appendChild(item);
  });
  openModal("modal-select");
});
document.getElementById("sel-cancel").addEventListener("click", () => closeModal("modal-select"));
document.getElementById("sel-confirm").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll("#select-list input:checked")].map(i => i.value);
  if (ids.length === 0) return toast("没有选择任何用户");
  closeModal("modal-select");
  toast("⏳ 正在签到…");
  const { ok, data } = await api("/api/sign/selected", {
    method: "POST", body: JSON.stringify({ ids }),
  });
  if (ok) showResult("批量签到结果", data.results.map(r => r.message).join("\n"));
  await refreshAll();
});

// 帮助 / 结果 / 刷新
document.getElementById("btn-help").addEventListener("click", () => openModal("modal-help"));
document.getElementById("help-close").addEventListener("click", () => closeModal("modal-help"));
document.getElementById("result-close").addEventListener("click", () => closeModal("modal-result"));
document.getElementById("btn-refresh").addEventListener("click", () => refreshAll().then(() => toast("已刷新")));
document.getElementById("btn-sign-all").addEventListener("click", signAll);

// 点击遮罩关闭
document.querySelectorAll(".modal").forEach(m =>
  m.addEventListener("click", e => { if (e.target === m) m.classList.remove("open"); }));

// 实时日志轮询
setInterval(() => {
  if (document.getElementById("auto-log").checked) loadLogs();
}, 5000);

refreshAll();
setInterval(loadStatus, 30000);

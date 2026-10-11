
function esc(s){
  return String(s == null ? "" : s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function fmtElapsed(sec){
  if (sec == null || sec < 0) return "-";
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return String(h).padStart(2,"0") + ":" + String(m).padStart(2,"0") + ":" + String(s).padStart(2,"0");
}
function stateClass(state){
  if (state === "수집 중") return "ok";
  if (state === "중지" || state === "종료") return "bad";
  return "warn";
}
function collectState(state){
  const map = {
    "수집 중": "Collecting",
    "중지": "Stopped",
    "종료": "Finished",
    "대기": "Waiting",
    "재수집": "Recollecting",
    "캡차 대기": "Waiting for captcha",
    "계정 전환": "Switching account",
    "재기동 중": "Restarting"
  };
  return map[state] || state || "-";
}
function collectJob(job){
  if (job === "목록") return "List";
  return job || "";
}
function collectLast(last){
  return String(last || "-").replace(/^저장 /, "Saved ").replace(/^미저장 /, "Missed ");
}
function collectNote(note){
  const text = String(note || "");
  const exact = {
    "수집 진행 중": "Collecting",
    "중지": "Stopped",
    "캡차 대기": "Waiting for captcha",
    "캡차 통과 — 수집 재개": "Captcha cleared — collecting again",
    "미저장 — 다음 상품 계속 (재기동 없음)": "Missed — continuing without restart"
  };
  if (exact[text]) return exact[text];
  const rules = [
    [/끝날 때까지 대기/g, "Waiting until it finishes"],
    [/재수집 시작/g, "Recollect started"],
    [/재수집 생략/g, "Recollect skipped"],
    [/재수집 끝/g, "Recollect finished"],
    [/재수집/g, "Recollect"],
    [/완료 저장/g, "Saved"],
    [/세션 복원 시작/g, "Restoring session"],
    [/세션 복원/g, "Session restore"],
    [/세션 보관/g, "Saving session"],
    [/다음 세션으로 전환/g, "Switching to the next session"],
    [/다음 세션/g, "Next session"],
    [/계정 전환/g, "Switching account"],
    [/캡차 통과/g, "Captcha cleared"],
    [/캡차 대기/g, "Waiting for captcha"],
    [/수집 재개/g, "collection resumed"],
    [/수집 진행 중/g, "Collecting"],
    [/미저장/g, "Missed"],
    [/다음 상품 재기동/g, "restarting for the next item"],
    [/로그인/g, "Login"],
    [/중지/g, "Stopped"]
  ];
  let out = text;
  rules.forEach(function(pair){ out = out.replace(pair[0], pair[1]); });
  return out || "-";
}
function collectMethod(text){
  const raw = String(text || "");
  if (raw.indexOf("기본방식") >= 0) {
    return "Default method · fresh list · one extra pass for misses · no upstream proxy";
  }
  return raw || "-";
}
function phoneRow(p){
  const last = collectLast(p.last) + (p.last_sec == null ? "" : (" · " + p.last_sec + "s"));
  const rate = p.per_min == null ? "-" : p.per_min;
  const note = p.running ? collectNote(p.note) : "";
  return "<tr>"
    + "<td>" + esc(p.name) + "<br><span class='k'>" + esc(collectJob(p.job)) + " · " + esc(p.serial) + "</span></td>"
    + "<td class='" + stateClass(p.state) + "'>" + esc(collectState(p.state))
    + (note ? "<br><span class='k'>" + esc(note) + "</span>" : "") + "</td>"
    + "<td>" + esc(p.userid || "-") + "</td>"
    + "<td class='ok'>" + esc(p.saved) + "</td>"
    + "<td class='bad'>" + esc(p.miss) + "</td>"
    + "<td>" + esc(rate) + "</td>"
    + "<td class='warn'>" + esc(p.list_pending == null ? "-" : p.list_pending) + "</td>"
    + "<td>" + esc(last) + "</td>"
    + "</tr>";
}
function paintWorkload(wl, upload, phones){
  wl = wl || {};
  const total = wl.list_total == null ? "-" : wl.list_total;
  const done = wl.list_done == null ? 0 : wl.list_done;
  const left = wl.list_pending == null ? "-" : wl.list_pending;
  const missed = wl.list_missed == null ? 0 : wl.list_missed;
  const processed = wl.list_processed != null ? wl.list_processed : (done + missed);
  let pct = 0;
  if (total !== "-" && total > 0) {
    pct = Math.round(1000 * processed / total) / 10;
  } else if (wl.progress_pct != null) {
    pct = wl.progress_pct;
  }
  document.getElementById("wl-total").textContent = total;
  document.getElementById("wl-done").textContent = done;
  const missedEl = document.getElementById("wl-missed");
  if (missedEl) missedEl.textContent = missed;
  document.getElementById("wl-pending").textContent = left;
  document.getElementById("wl-pct").textContent = (total === "-" ? "-" : (pct + "%"));
  const bar = document.getElementById("wl-bar");
  if (bar) bar.style.width = Math.min(100, Math.max(0, pct)) + "%";
  const meta = [];
  if (wl.updated_at) meta.push("갱신 " + wl.updated_at);
  meta.push("서버 감시 중");
  document.getElementById("wl-meta").textContent = meta.length ? meta.join(" · ") : "서버 목록 없음";
  const bal = document.getElementById("wl-balance");
  if (bal) {
    const q = wl.queue_pending == null ? "-" : wl.queue_pending;
    const ok = wl.balance_ok !== false;
    bal.textContent = "큐 합계 " + q + " · 미할당 " + (wl.unassigned == null ? 0 : wl.unassigned)
      + (ok ? " · 잔량 일치" : " · 잔량 불일치");
    bal.className = "wl-balance " + (ok ? "ok" : "bad");
  }
  paintListPicks(wl, phones || []);
  const hint = document.getElementById("upload-hint");
  if (hint && upload) {
    const auto = upload.auto;
    hint.textContent = auto
      ? "서버 업로드: Auto — 상품 JSON 저장 직후 회원 허브로 전송(브리지 폴링·중복 제외)."
      : "서버 업로드: Manual — PC Upload 폴더에만 저장. Server 탭에서 Upload now 또는 Auto 전환.";
  }
}
function wlCountForShard(wl, shard){
  const rows = wl.phones || [];
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].shard === shard) return rows[i].pending == null ? 0 : rows[i].pending;
  }
  return 0;
}
function paintListPicks(wl, phones){
  const host = document.getElementById("wl-picks");
  if (!host) return;
  const pendingByShard = {};
  (wl.phones || []).forEach(function(row){
    pendingByShard[row.shard] = row.pending == null ? 0 : row.pending;
  });
  let html = "";
  (phones || []).forEach(function(p){
    const shard = p.shard;
    const cnt = pendingByShard[shard] == null ? (p.list_pending == null ? 0 : p.list_pending) : pendingByShard[shard];
    html += '<div class="wl-pick" data-shard="' + shard + '">'
      + '<div class="wl-pick-head"><div class="wl-name">' + esc(p.name) + '</div></div>'
      + '<div class="wl-metric"><span class="k">목록 잔량</span><div class="wl-count" id="wl-c-' + shard + '">' + esc(cnt) + '</div></div>'
      + '<div class="wl-actions">'
      + '<div class="wl-qty"><label for="wl-n-' + shard + '">수량</label>'
      + '<input type="number" min="1" value="10" id="wl-n-' + shard + '" inputmode="numeric"/>'
      + '</div>'
      + '<div class="wl-btns">'
      + '<button type="button" class="wl-btn" data-act="assign" data-shard="' + shard + '">할당</button>'
      + '<button type="button" class="wl-btn secondary" data-act="withdraw" data-shard="' + shard + '">퇴소</button>'
      + '</div></div></div>';
  });
  const un = wl.unassigned == null ? 0 : wl.unassigned;
  html += '<div class="wl-pick unassigned" data-shard="unassigned">'
    + '<div class="wl-pick-head"><div class="wl-name">미할당</div></div>'
    + '<div class="wl-metric"><span class="k">목록 잔량</span><div class="wl-count" id="wl-c-un">' + esc(un) + '</div></div>'
    + '<div class="wl-actions"><div class="wl-btns">'
    + '<button type="button" class="wl-btn accent" data-act="auto_distribute" data-shard="-1">자동배정</button>'
    + '</div></div></div>';
  host.innerHTML = html;
  host.querySelectorAll("button[data-act]").forEach(function(btn){
    btn.addEventListener("click", function(){
      listTransfer(btn.getAttribute("data-act"), parseInt(btn.getAttribute("data-shard") || "0", 10));
    });
  });
}
async function listTransfer(action, shard){
  let count = 0;
  if (action === "assign" || action === "withdraw") {
    const input = document.getElementById("wl-n-" + shard);
    count = input ? parseInt(input.value, 10) : 0;
    if (!count || count < 1) return;
  }
  const msg = document.getElementById("harvest-msg");
  try {
    const r = await fetch("/api/list/transfer", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: action, shard: shard, count: count})
    });
    const d = await r.json();
    if (d && d.error && msg) msg.textContent = d.error;
    else if (msg) msg.textContent = "";
    await tick();
  } catch (err) {
    if (msg) msg.textContent = "목록 이동 실패";
  }
}
const pickedPhones = new Set();
let phoneSnap = [];
function collectPhonePick(p){
  const picked = pickedPhones.has(p.name) ? " on" : "";
  const collecting = p.running ? " collecting" : "";
  const ledKind = p.running ? " green" : " blue";
  const led = '<span class="pick-led' + ledKind + '" aria-hidden="true"></span>';
  const run = p.running ? "동작 중" : "중지";
  const runCls = p.running ? "ok" : "warn";
  const last = p.last || "-";
  const lastSec = p.last_sec == null ? "" : (" · " + p.last_sec + "s");
  const rate = p.per_min == null ? "-" : p.per_min;
  const okRate = p.ok_rate == null ? "-" : (p.ok_rate + "%");
  const tail = (p.tail || []).map(esc).join("
");
  const body = '<span class="pick-name">' + esc(p.name) + '</span>'
    + '<p class="meta">' + esc(p.serial) + ' · port ' + esc(p.port)
    + '<br>계정 ' + esc(p.userid || "-") + '</p>'
    + '<div class="mini">'
    + '<div class="card"><div class="k">상태</div><div class="v ' + runCls + '">' + run + '</div></div>'
    + '<div class="card"><div class="k">저장</div><div class="v ok">' + esc(p.saved) + '</div></div>'
    + '<div class="card"><div class="k">미저장</div><div class="v bad">' + esc(p.miss) + '</div></div>'
    + '<div class="card"><div class="k">분당</div><div class="v">' + esc(rate) + '</div></div>'
    + '</div>'
    + '<p class="note">' + esc(p.running ? (p.note || "-") : "중지") + '</p>'
    + '<p class="meta">마지막 ' + esc(last) + esc(lastSec)
    + '<br>성공률 ' + esc(okRate)
    + ' · 경과 ' + fmtElapsed(p.elapsed_s) + '</p>'
    + '<pre>' + tail + '</pre>';
  return '<button type="button" class="pick collect-phone' + collecting + picked + '" data-phone="' + esc(p.name) + '" aria-pressed="' + (picked ? "true" : "false") + '">'
    + led
    + '<span class="pick-notch"></span>'
    + '<span class="pick-screen collect-body">'
    + body
    + '</span><span class="pick-home"></span></button>';
}
function syncCollectActions(){
  const selected = phoneSnap.filter(function(p){ return pickedPhones.has(p.name); });
  const start = document.getElementById("collect-start");
  const stop = document.getElementById("collect-stop");
  const all = document.getElementById("collect-all");
  const waiting = selected.some(function(p){ return !p.running; });
  const collecting = selected.some(function(p){ return p.running; });
  if (start) start.disabled = selected.length === 0 || !waiting;
  if (stop) stop.disabled = selected.length === 0 || waiting || !collecting;
  if (all) all.checked = phoneSnap.length > 0 && phoneSnap.every(function(p){ return pickedPhones.has(p.name); });
}
function toggleCollectAll(on){
  pickedPhones.clear();
  if (on) phoneSnap.forEach(function(p){ pickedPhones.add(p.name); });
  document.querySelectorAll("#collect-phones .pick").forEach(function(card){
    const name = card.getAttribute("data-phone");
    const onCard = pickedPhones.has(name);
    card.classList.toggle("on", onCard);
    card.setAttribute("aria-pressed", onCard ? "true" : "false");
  });
  syncCollectActions();
}
async function runCollectAction(action){
  const start = document.getElementById("collect-start");
  const stop = document.getElementById("collect-stop");
  if ((action === "start" && start.disabled) || (action === "stop" && stop.disabled)) return;
  const selected = phoneSnap.filter(function(p){ return pickedPhones.has(p.name); });
  const targets = selected.filter(function(p){ return action === "stop" ? p.running : !p.running; });
  if (!targets.length) return;
  start.disabled = true;
  stop.disabled = true;
  const msg = document.getElementById("harvest-msg");
  const errors = [];
  for (let i = 0; i < targets.length; i++) {
    try {
      const r = await fetch("/api/collect/action", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action: action, phone: targets[i].name})
      });
      const d = await r.json();
      if (d && d.error) errors.push(d.error);
    } catch (err) {
      errors.push("Could not reach the collector.");
    }
  }
  if (msg) msg.textContent = errors.join(" ");
  await tick();
}
async function freshCollect(){
  const btn = document.getElementById("collect-fresh");
  if (btn) btn.disabled = true;
  try {
    await fetch("/api/collect/fresh", {method: "POST"});
    await tick();
  } catch (err) {
    const msg = document.getElementById("harvest-msg");
    if (msg) msg.textContent = "Could not reset the counters.";
  } finally {
    if (btn) btn.disabled = false;
  }
}
async function tick(){
  const r = await fetch("/api/status?t=" + Date.now(), {cache: "no-store"});
  const d = await r.json();
  document.getElementById("method").textContent = collectMethod(d.method);
  document.getElementById("sub").textContent = "Watches the harvest on three phones. Refreshes every 2 seconds.";
  document.getElementById("collect-path").textContent = "Storage  " + (d.folder || "-");
  const run = document.getElementById("run");
  run.textContent = (d.running_n == null ? 0 : d.running_n) + " / " + (d.phones_n || 3);
  run.className = "v " + (d.running_n ? "ok" : "warn");
  document.getElementById("saved").textContent = d.saved;
  document.getElementById("miss").textContent = d.miss;
  document.getElementById("elapsed").textContent = fmtElapsed(d.elapsed_s);
  document.getElementById("rate").textContent = d.per_min == null ? "-" : d.per_min;
  document.getElementById("hourly").textContent = d.per_hour == null ? "-" : d.per_hour;
  document.getElementById("list-count").textContent = d.list_count == null ? "-" : d.list_count;
  const phones = d.phones || [];
  paintWorkload(d.list_workload, d.upload, phones);
  const names = {};
  phones.forEach(function(p){ names[p.name] = true; });
  Array.from(pickedPhones).forEach(function(name){ if (!names[name]) pickedPhones.delete(name); });
  phoneSnap = phones;
  document.getElementById("phone-rows").innerHTML = phones.map(phoneRow).join("");
  document.getElementById("collect-phones").innerHTML = phones.map(collectPhonePick).join("");
  syncCollectActions();
}
function fmtWhen(ts){
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "-";
  const p = n => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth()+1) + "-" + p(d.getDate())
    + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}
function stateMark(state){
  if (state === "신선") return "fresh";
  if (state === "이용중") return "busy";
  return "";
}
function stateLabel(state){
  if (state === "신선") return "Fresh";
  if (state === "이용중") return "In use";
  if (state === "이용 끝") return "Used";
  return state || "-";
}
function reasonLabel(reason){
  const text = String(reason || "");
  const known = {
    "시작": "start",
    "수집 시작": "Active",
    "차단": "Blocked",
    "페이지 사용 불가": "Page unavailable",
    "로그인 필요": "Login required",
    "화면에 캡차 없음": "No captcha on screen",
    "보관 세션 다시": "saved session again",
    "연속 미저장": "Consecutive misses"
  };
  if (known[text]) return known[text];
  const every = text.match(/^(\d+)건$/);
  if (every) return "Completed " + every[1] + " items and handed over";
  return text;
}
function accountStatus(phone, row){
  if (row.open && phone.collecting) return "Collecting";
  if (row.open) return "Active";
  if (!row.used) return "Fresh";
  return reasonLabel(row.reason) || "Used";
}
function showTab(name){
  document.getElementById("panel-collect").hidden = name !== "collect";
  document.getElementById("panel-sessions").hidden = name !== "sessions";
  document.getElementById("panel-sever").hidden = name !== "sever";
  const collect = document.getElementById("tab-collect");
  const sessions = document.getElementById("tab-sessions");
  const sever = document.getElementById("tab-sever");
  collect.className = name === "collect" ? "on" : "";
  sessions.className = name === "sessions" ? "on" : "";
  sever.className = name === "sever" ? "on" : "";
  collect.setAttribute("aria-selected", name === "collect" ? "true" : "false");
  sessions.setAttribute("aria-selected", name === "sessions" ? "true" : "false");
  sever.setAttribute("aria-selected", name === "sever" ? "true" : "false");
  if (name === "sessions") tickSessions();
  if (name === "sever") tickSever();
}
function useLine(u){
  const end = u.ended_at ? fmtWhen(u.ended_at) : "in use";
  const why = u.reason ? " · " + esc(reasonLabel(u.reason)) : "";
  return esc(u.phone || "-") + " · " + esc(u.saved) + " saved · "
    + fmtWhen(u.started_at) + " → " + end + why;
}
const selectedPhones = {};
function cardStatus(card){
  if (card.mode === "region") return card.label || "Preparing";
  if (card.mode === "session") return "Collecting";
  if (card.full) return "3 collected";
  return "Ready";
}
function renderCards(d){
  const cards = d.cards || [];
  cards.forEach(function(card){ if (card.locked) delete selectedPhones[card.name]; });
  document.getElementById("sess-cards").innerHTML = cards.map(function(card){
    const on = !card.locked && selectedPhones[card.name];
    const showCount = card.mode === "session" || card.full;
    const count = showCount ? "<div class='pick-n'>" + esc(card.count || 0) + "</div>" : "";
    const pct = Math.max(0, Math.min(100, Number(card.progress) || 0));
    const bar = card.mode === "region"
      ? "<div class='pick-bar'><span style='width:" + pct + "%'></span></div><div class='pick-pct'>" + pct + "%</div>"
      : "";
    const tone = card.mode === "session" ? " collecting" : (card.mode === "region" ? " region" : "");
    let ledKind = "";
    let showLed = false;
    if (card.mode === "region") {
      showLed = true;
      if (card.step === "preparing" || card.label === "Preparing") ledKind = " blue";
    } else if (card.full) {
      showLed = true;
      ledKind = " yellow";
    } else if (card.mode === "session") {
      showLed = true;
      ledKind = " green";
    } else {
      showLed = true;
      ledKind = " blue";
    }
    const led = showLed ? "<span class='pick-led" + ledKind + "' aria-hidden='true'></span>" : "";
    return "<button type='button' class='pick" + tone + (on ? " on" : "") + (card.locked ? " locked" : "") + "'"
      + " data-name='" + esc(card.name) + "' data-mode='" + esc(card.mode) + "'"
      + " data-full='" + (card.full ? "1" : "0") + "'"
      + (card.locked ? " disabled" : "") + " onclick='togglePhone(this)'>"
      + led
      + "<span class='pick-notch'></span>"
      + "<span class='pick-screen'>"
      + "<span class='pick-name'>" + esc(card.name) + "</span>"
      + "<span class='pick-st'>" + esc(cardStatus(card)) + "</span>" + bar + count
      + "</span><span class='pick-home'></span></button>";
  }).join("");
  syncPhoneControls();
}
function syncPhoneControls(){
  const picks = Array.prototype.slice.call(document.querySelectorAll("#sess-cards .pick"));
  const eligible = picks.filter(function(node){ return !node.disabled; });
  const selected = eligible.filter(function(node){ return !!selectedPhones[node.getAttribute("data-name")]; });
  const all = document.getElementById("sess-all");
  const region = document.getElementById("sess-region");
  const collect = document.getElementById("sess-collect");
  const busy = region.dataset.busy === "1";
  const canCollect = selected.some(function(node){
    return node.getAttribute("data-mode") !== "session" && node.getAttribute("data-full") !== "1";
  });
  all.disabled = eligible.length === 0;
  all.checked = eligible.length > 0 && selected.length === eligible.length;
  region.disabled = busy || selected.length === 0;
  collect.disabled = busy || !canCollect;
}
function togglePhone(el){
  if (el.disabled) return;
  const name = el.getAttribute("data-name");
  if (selectedPhones[name]) delete selectedPhones[name];
  else selectedPhones[name] = true;
  el.classList.toggle("on", !!selectedPhones[name]);
  syncPhoneControls();
}
function toggleAllPhones(checked){
  document.querySelectorAll("#sess-cards .pick").forEach(function(el){
    const name = el.getAttribute("data-name");
    if (el.disabled) {
      delete selectedPhones[name];
      return;
    }
    if (checked) selectedPhones[name] = true;
    else delete selectedPhones[name];
    el.classList.toggle("on", !!selectedPhones[name]);
  });
  syncPhoneControls();
}
function clearPhoneSelection(){
  Object.keys(selectedPhones).forEach(function(name){ delete selectedPhones[name]; });
  document.querySelectorAll("#sess-cards .pick.on").forEach(function(el){ el.classList.remove("on"); });
  syncPhoneControls();
}
async function runPhoneAction(action){
  const phones = Object.keys(selectedPhones);
  if (!phones.length) return;
  clearPhoneSelection();
  const region = document.getElementById("sess-region");
  const collect = document.getElementById("sess-collect");
  region.dataset.busy = "1";
  collect.dataset.busy = "1";
  syncPhoneControls();
  try {
    await fetch("/api/sessions/action", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: action, phones: phones})
    });
    await loadSessions();
  } finally {
    region.dataset.busy = "";
    collect.dataset.busy = "";
    syncPhoneControls();
  }
}
function regionColumn(phone){
  const items = (phone && phone.roster) || [];
  const unset = phone && phone.unset ? " unset" : "";
  const n = items.length;
  const head = "<h3><span class='phone-h'>" + esc(phone.name || "") + "</span><span class='phone-n'>" + n + "</span></h3>";
  if (!items.length) {
    return "<section class='manage-col" + unset + "'>" + head + "<div class='sess-empty'>None</div></section>";
  }
  const rows = items.map(function(s){
    const lamp = (s.lamp === "live" || s.lamp === "yellow") ? s.lamp : "blue";
    const when = s.saved_at ? fmtWhen(s.saved_at) : "-";
    const saved = s.saved == null ? "-" : s.saved;
    return "<div class='sess-item'><span class='sess-led " + lamp + "'></span>"
      + "<span class='sess-id'>" + esc(s.userid)
      + "<span class='sess-state'>" + esc(accountStatus(phone, s)) + "</span></span>"
      + "<span class='sess-when'>" + esc(when) + "</span>"
      + "<span class='sess-n'>" + esc(saved) + "</span></div>";
  }).join("");
  return "<section class='manage-col" + unset + "'>" + head
    + "<div class='sess-col'>"
    + "<div class='sess-item labels'><span></span><span>Session</span><span>Saved at</span><span>Saved</span></div>"
    + rows + "</div></section>";
}
function bindSessionDrag(root){
  root.querySelectorAll(".sess-col").forEach(function(col){
    col.addEventListener("pointerdown", function(ev){
      if (ev.button !== 0) return;
      const rect = col.getBoundingClientRect();
      if (ev.clientX > rect.right - 14) return;
      col._drag = { y: ev.clientY, top: col.scrollTop };
      col.classList.add("dragging");
      try { col.setPointerCapture(ev.pointerId); } catch (err) {}
    });
    col.addEventListener("pointermove", function(ev){
      if (!col._drag) return;
      col.scrollTop = col._drag.top - (ev.clientY - col._drag.y);
    });
    function endDrag(){
      if (!col._drag) return;
      col._drag = null;
      col.classList.remove("dragging");
    }
    col.addEventListener("pointerup", endDrag);
    col.addEventListener("pointercancel", endDrag);
  });
}
function renderSessions(d){
  document.getElementById("sess-path").textContent = "Storage  " + (d.path || "-");
  document.getElementById("sess-total").textContent = d.total || 0;
  document.getElementById("sess-free").textContent = d.available || 0;
  document.getElementById("sess-busy").textContent = d.in_use || 0;
  document.getElementById("sess-fresh").textContent = d.fresh || 0;
  renderCards(d);
  const box = document.getElementById("sess-manage");
  if (box.querySelector(".sess-col.dragging")) return;
  const tops = Array.from(box.querySelectorAll(".sess-col")).map(function(el){ return el.scrollTop; });
  const phones = d.phones || [];
  box.innerHTML = phones.map(regionColumn).join("");
  box.querySelectorAll(".sess-col").forEach(function(el, i){
    if (tops[i]) el.scrollTop = tops[i];
  });
  bindSessionDrag(box);
}
async function loadSessions(){
  const r = await fetch("/api/sessions?t=" + Date.now(), {cache: "no-store"});
  renderSessions(await r.json());
  const now = new Date();
  const p = n => String(n).padStart(2, "0");
  document.getElementById("sess-refreshed").textContent =
    "Updated " + p(now.getHours()) + ":" + p(now.getMinutes()) + ":" + p(now.getSeconds());
}
async function refreshSessions(){
  const btn = document.getElementById("sess-refresh");
  btn.disabled = true;
  try {
    await loadSessions();
  } finally {
    btn.disabled = false;
  }
}
async function tickSessions(){
  if (document.getElementById("panel-sessions").hidden) return;
  await loadSessions();
}
function paintLink(d){
  const el = document.getElementById("link-live");
  const on = !!(d && d.ok && d.bridge);
  el.className = "link-live" + (on ? " on" : " off");
  const label = on ? "Connected" : "Offline";
  el.title = label;
  el.setAttribute("aria-label", label);
}
function severConn(label, value){
  return "<tr><td>" + esc(label) + "</td><td>" + esc(value) + "</td></tr>";
}
function renderSever(d){
  const auto = d.mode !== "manual";
  document.getElementById("mode-auto").className = auto ? "on" : "";
  document.getElementById("mode-manual").className = auto ? "" : "on";
  document.getElementById("sever-upload-now").disabled = auto;
  document.getElementById("sever-path").textContent = "Upload  " + (d.folder || "-");
  document.getElementById("sever-down").textContent = "Download  " + (d.download || "-");
  document.getElementById("sever-note").textContent = d.note || (d.error || "");
  document.getElementById("sever-vault").textContent = d.packets == null ? "-" : d.packets;
  document.getElementById("sever-inbox").textContent = d.inbox == null ? "-" : d.inbox;
  document.getElementById("sever-tasks").textContent = d.tasks == null ? "-" : d.tasks;
  document.getElementById("sever-assigned").textContent = d.assigned == null ? "-" : d.assigned;
  document.getElementById("sever-sent").textContent = d.sent || 0;
  document.getElementById("sever-pending").textContent = d.pending || 0;
  const wl = d.list_workload || {};
  const wlDone = wl.list_done == null ? 0 : wl.list_done;
  const wlMiss = wl.list_missed == null ? 0 : wl.list_missed;
  const wlProcessed = wl.list_processed != null ? wl.list_processed : (wlDone + wlMiss);
  document.getElementById("sever-list-done").textContent = wlProcessed;
  document.getElementById("sever-list-pending").textContent = wl.list_pending == null ? 0 : wl.list_pending;
  const link = (d.host || "-") + ":" + (d.port || "-");
  document.getElementById("sever-conn").innerHTML = [
    severConn("Member", (d.member || "-") + (d.name ? " · " + d.name : "")),
    severConn("Host", link),
    severConn("Key", d.key_stored ? "stored locally" : "missing"),
    severConn("Hub", d.ok ? "connected" : (d.error || "offline")),
    severConn("Dispatch open", d.dispatch_open == null ? "-" : d.dispatch_open),
    severConn("Dispatch uploaded", d.dispatch_uploaded == null ? "-" : d.dispatch_uploaded),
    severConn("Local products", d.local_products || 0),
    severConn("Upload failures", d.upload_fail || 0),
    severConn("Harvest", d.harvest || "waiting"),
    severConn("Bridge", d.bridge ? ("live " + (d.bridge_at || "")) : "stopped"),
    severConn("List updated", d.tasks_at || "-"),
    severConn("Upload mode", d.upload_auto ? "Auto (on save)" : "Manual"),
    severConn("List progress", wl.list_total == null ? "-" : (wlProcessed + " / " + wl.list_total + " (완료+미저장)"))
  ].join("");
  const samples = d.samples || [];
  document.getElementById("sever-samples").innerHTML = samples.length
    ? samples.map(function(row){
        return "<tr><td>" + esc(row.shop_id) + "</td><td>" + esc(row.item_id)
          + "</td><td>" + esc(row.expire || "-") + "</td></tr>";
      }).join("")
    : "<tr><td class='pcell empty' colspan='3'>Waiting for the server list.</td></tr>";
  const recent = d.recent || [];
  document.getElementById("sever-recent").innerHTML = recent.length
    ? recent.map(function(row){
        return "<tr><td>" + esc(row.at || "-") + "</td><td>" + esc(row.file)
          + "</td><td>" + esc(row.result || "-") + "</td></tr>";
      }).join("")
    : "<tr><td class='pcell empty' colspan='3'>No upload yet. New product files are sent as they are saved.</td></tr>";
}
async function loadSever(){
  const r = await fetch("/api/sever?t=" + Date.now(), {cache: "no-store"});
  const d = await r.json();
  paintLink(d);
  renderSever(d);
  const now = new Date();
  const p = n => String(n).padStart(2, "0");
  document.getElementById("sever-refreshed").textContent =
    "Updated " + p(now.getHours()) + ":" + p(now.getMinutes()) + ":" + p(now.getSeconds());
}
async function tickLink(){
  if (!document.getElementById("panel-sever").hidden) {
    await loadSever();
    return;
  }
  try {
    const r = await fetch("/api/sever?t=" + Date.now(), {cache: "no-store"});
    paintLink(await r.json());
  } catch (e) {
    paintLink(null);
  }
}
async function refreshSever(){
  const btn = document.getElementById("sever-refresh");
  btn.disabled = true;
  try { await loadSever(); } finally { btn.disabled = false; }
}
async function tickSever(){
  if (document.getElementById("panel-sever").hidden) return;
  await loadSever();
}
async function setSeverMode(mode){
  await fetch("/api/sever/mode", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({mode: mode})
  });
  await loadSever();
}
async function uploadNow(){
  const btn = document.getElementById("sever-upload-now");
  btn.disabled = true;
  try {
    await fetch("/api/sever/upload", {method: "POST"});
    await loadSever();
  } finally {
    btn.disabled = document.getElementById("mode-manual").className !== "on";
  }
}
document.getElementById("collect-phones").addEventListener("click", function(ev){
  const card = ev.target.closest(".pick");
  if (!card) return;
  const name = card.getAttribute("data-phone");
  if (!name) return;
  if (pickedPhones.has(name)) pickedPhones.delete(name);
  else pickedPhones.add(name);
  const onCard = pickedPhones.has(name);
  card.classList.toggle("on", onCard);
  card.setAttribute("aria-pressed", onCard ? "true" : "false");
  syncCollectActions();
});
tick();
tickLink();
setInterval(tick, 2000);
setInterval(tickSessions, 2000);
setInterval(tickLink, 2000);

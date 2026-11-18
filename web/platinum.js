// OpenTax PLATINUM 期限一覧と進捗の画面。期限の計算はワーカーの中の Python（opentax.pro.kigen）だけが行う。
// 関与先の一覧と進捗はこの変数にだけ置く。localStorage・sessionStorage・IndexedDB・Cookie は使わない。
"use strict";

let clientsText = "";
let progress = null;
let last = null;

const worker = new Worker("worker.js", { type: "module" });
let nextId = 1;
const pending = new Map();
worker.onmessage = (event) => {
  const msg = event.data;
  if (msg.type === "progress") setStatus(msg.message);
  else if (msg.type === "ready") onReady();
  else if (msg.type === "fatal") setStatus(`読み込みに失敗しました: ${msg.message}`, true);
  else if (msg.type === "result") {
    const resolve = pending.get(msg.id);
    pending.delete(msg.id);
    resolve(JSON.parse(msg.result));
  }
};
function call(cmd, args) {
  const id = nextId++;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    worker.postMessage({ id, cmd, args });
  });
}

const $ = (id) => document.getElementById(id);
function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  for (const c of children) if (c !== null && c !== undefined) e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return e;
}
function setStatus(text, error) {
  const s = $("status");
  s.textContent = text;
  s.style.background = error ? "#fdecec" : "";
}
function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function download(name, text, type) {
  const a = el("a", { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  document.body.append(a);
  a.click();
  a.remove();
}
async function readText(file) {
  const buf = new Uint8Array(await file.arrayBuffer());
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buf);
  } catch {
    return new TextDecoder("shift_jis").decode(buf);
  }
}

// ライセンス（DESIGN §33）。キーはこの変数にだけ置き、確かめるのはワーカーの Python（通信しない）
let license = { ok: false, required: false };
let checkedKey = null;
let checking = Promise.resolve();
function checkLicense(key) {
  checkedKey = key;
  checking = checking.then(() => applyLicense(key));
  return checking;
}
async function applyLicense(key) {
  license = await call("ot_license", [key, "platinum", todayIso()]);
  const s = $("license-state");
  if (license.ok) {
    const l = license.license;
    s.textContent = `${l.plan_name}・${l.office} 様・${l.exp} まで（あと${l.days_left}日）`;
    s.style.color = l.days_left <= 30 ? "#b00" : "";
  } else {
    s.textContent = license.message || (license.required ? "キーを入れると使えます。" : "いまは試しにキーなしで使えます。");
    s.style.color = license.message ? "#b00" : "";
  }
}

function onReady() {
  setStatus("関与先の一覧を開くか、見本を入れてください。");
  for (const id of ["load-sample", "open-clients", "open-progress", "run", "license-key", "license-file"]) $(id).disabled = false;
  $("month").value = todayIso().slice(0, 7);
  checkLicense("");
}
$("license-key").addEventListener("change", () => checkLicense($("license-key").value));
$("license-file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  $("license-key").value = (await f.text()).trim();
  e.target.value = "";
  checkLicense($("license-key").value);
});

async function run(setId, setStatusValue, setMemo) {
  // 貼ってすぐ押したときも今のキーで確かめる。返事に required が無い（内部の失敗）ときは止める側に倒す
  if (checkedKey !== $("license-key").value) checkLicense($("license-key").value);
  await checking;
  if (license.required !== false && !license.ok) {
    setStatus("ライセンスキーを入れてください（料金・購入のページで買えます）。", true);
    return;
  }
  if (!clientsText) {
    setStatus("関与先の一覧を開いてください。", true);
    return;
  }
  const res = await call("ot_kigen", [clientsText, $("month").value, progress ? JSON.stringify(progress) : "", todayIso(),
    setId || "", setStatusValue || "", setMemo || ""]);
  $("messages").replaceChildren();
  if (!res.ok) {
    $("messages").append(el("p", { class: "error" }, res.message));
    setStatus("止まりました。", true);
    return;
  }
  progress = res.progress;
  last = res;
  render();
  setStatus(setId ? "進捗を書きました。保存はページ下の「進捗をファイルに保存」で。" : "一覧を作りました。");
}

function render() {
  const sched = last.schedule;
  const s = sched.summary;
  $("summary").textContent = `全 ${s.total} 件・済 ${s.done} 件・期限切れ ${s.overdue} 件（${sched.today} 時点）`;
  const staffSel = $("staff");
  const chosen = staffSel.value;
  const staffs = [...new Set(sched.items.map((r) => r.staff).filter(Boolean))].sort();
  staffSel.replaceChildren(el("option", { value: "" }, "全員"), ...staffs.map((x) => el("option", { value: x }, x)));
  staffSel.value = staffs.includes(chosen) ? chosen : "";
  const head = el("tr", {}, ...["期限", "関与先", "申告・納付", "状態", "メモ", "根拠"].map((h) => el("th", {}, h)));
  const rows = sched.items
    .filter((r) => (!staffSel.value || r.staff === staffSel.value) && !($("hide-done").checked && r.done))
    .map((r) => {
      const sel = el("select", { "aria-label": `${r.name} ${r.label} の状態` },
        ...last.statuses.map((x) => el("option", { value: x }, x)));
      sel.value = r.status;
      const memo = el("input", { type: "text", value: r.memo, "aria-label": `${r.name} ${r.label} のメモ` });
      const save = () => run(r.id, sel.value, memo.value);
      sel.addEventListener("change", save);
      memo.addEventListener("change", save);
      const left = r.done ? "済" : r.overdue ? `${-r.days_left}日過ぎ` : `あと${r.days_left}日`;
      const tr = el("tr", { class: r.done ? "done" : r.overdue ? "error" : "" },
        el("td", {}, r.due, el("div", { class: "note" }, left),
          r.due !== r.legal_due ? el("div", { class: "note" }, `法定 ${r.legal_due}（休日のため翌日へ）`) : null),
        el("td", {}, `${r.code} ${r.name}`, el("div", { class: "note" }, r.staff)),
        el("td", {}, r.label, el("div", { class: "note" }, r.period), r.note ? el("div", { class: "note" }, r.note) : null),
        el("td", {}, sel), el("td", {}, memo), el("td", { class: "note" }, r.source));
      if (r.overdue) tr.style.background = "#fde8e8";
      if (r.done) tr.style.color = "#999";
      return tr;
    });
  $("list").replaceChildren(el("thead", {}, head), el("tbody", {}, ...rows));
  renderGrid();
  $("results").hidden = false;
}

const TB_CYCLE = ["未着手", "資料待ち", "作成中", "確認待ち", "報告済"];
function renderGrid() {
  const g = last.grid;
  const staff = $("staff").value;
  const head = el("tr", {}, el("th", {}, "関与先"), ...g.months.map((m) => el("th", {}, `${Number(m.slice(5))}月`)));
  const rows = g.rows.filter((r) => !staff || r.staff === staff).map((r) =>
    el("tr", {}, el("th", {}, `${r.code} ${r.name}`, el("div", { class: "note" }, r.staff),
      r.late ? el("div", { class: "note error" }, `遅れ ${r.late}`) : null),
    ...r.cells.map((c) => {
      if (!c) return el("td", { class: "note" }, "－");
      const td = el("td", { title: `期限 ${c.due} ${c.memo}`.trim(), role: "button", tabindex: "0" }, c.done ? "済" : c.status);
      td.style.cursor = "pointer";
      if (c.done) td.style.background = "#e8f5e0";
      else if (c.overdue) { td.style.background = "#fde8e8"; td.style.color = "#c00"; }
      const next = () => {
        const i = TB_CYCLE.indexOf(c.status);
        run(c.id, TB_CYCLE[(i + 1) % TB_CYCLE.length], c.memo);
      };
      td.addEventListener("click", next);
      td.addEventListener("keydown", (e) => { if (e.key === "Enter") next(); });
      return td;
    })));
  $("grid").replaceChildren(el("thead", {}, head), el("tbody", {}, ...rows));
}

$("load-sample").addEventListener("click", async () => {
  const res = await call("ot_kigen_sample", []);
  clientsText = res.ok ? res.text : "";
  progress = null;
  setStatus("見本を入れました。");
  await run();
});
$("open-clients").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  clientsText = await readText(f);
  await run();
});
$("open-progress").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  try {
    progress = JSON.parse(await readText(f));
  } catch {
    setStatus("進捗のファイルが読めません（JSON ではありません）。", true);
    return;
  }
  if (clientsText) await run();
  else setStatus("進捗を読みました。関与先の一覧も開いてください。");
});
$("run").addEventListener("click", () => run());
$("month").addEventListener("change", () => clientsText && run());
$("staff").addEventListener("change", () => last && render());
$("hide-done").addEventListener("change", () => last && render());
$("save-progress").addEventListener("click", () =>
  download(`進捗_${todayIso()}.json`, JSON.stringify(progress || { kind: "opentax-progress", items: {} }, null, 1), "application/json"));
$("save-csv").addEventListener("click", () => last && download(`期限一覧_${last.schedule.month}.csv`, last.csv, "text/csv"));
$("save-html").addEventListener("click", () => last && download(`期限一覧_${last.schedule.month}.html`, last.html, "text/html"));
$("save-grid").addEventListener("click", () => last && download(`試算表の進捗表_${last.schedule.month}.html`, last.grid_html, "text/html"));

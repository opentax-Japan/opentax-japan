// かんたん入口（DESIGN §24）: ファイルを入れる → 読み取り（opentax.intake）→ 確かめる → 申告書（opentax.api）→ 印刷・保存・共有。
// ファイルと答えはこの変数にだけ置く。localStorage・sessionStorage・IndexedDB・Cookie は使わない。外へ送らない（AI で読むに同意したときだけ ai.js が送る）。
"use strict";

const worker = new Worker("worker.js", { type: "module" });
const pending = new Map();
let nextId = 1;
const st = { files: [], choices: {}, answers: {}, follow: {}, last: null, html: "", company: "", fiscalEnd: "", local: "", xtx: null, sample: false,
  target: null, xtxOk: true };

worker.onmessage = (event) => {
  const msg = event.data;
  if (msg.type === "progress") setStatus(msg.message);
  else if (msg.type === "ready") {
    setStatus("準備ができました。ファイルを入れてください。");
    document.getElementById("sample").disabled = false;
    call("ot_intake_checklist", []).then(renderNeeds);
  } else if (msg.type === "fatal") setStatus(`読み込みに失敗しました: ${msg.message}`, true);
  else if (msg.type === "result") {
    const resolve = pending.get(msg.id);
    pending.delete(msg.id);
    resolve(JSON.parse(msg.result));
  }
};

function call(cmd, args) {
  const id = nextId++;
  return new Promise((resolve) => { pending.set(id, resolve); worker.postMessage({ id, cmd, args }); });
}

function $(id) { return document.getElementById(id); }
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.append(k instanceof Node ? k : String(k));
  return e;
}
function setStatus(text, error) {
  const s = $("status");
  s.textContent = text;
  s.classList.toggle("msg", !!error);
  s.classList.toggle("error", !!error);
}
function yen(v) { return typeof v === "number" ? v.toLocaleString("ja-JP") + "円" : v; }

function b64(buf) {
  const u = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000));
  return btoa(s);
}

// ---- ファイルを入れる -------------------------------------------------------------------------
async function addFiles(list) {
  const added = [];
  for (const f of list) {
    const name = f.webkitRelativePath || f.name;
    if (st.files.some((x) => x.name === name)) continue;
    if (f.size > 20 * 1024 * 1024) continue;   // 大きすぎるもの（動画など）は読まない
    added.push({ name, b64: b64(await f.arrayBuffer()), type: f.type, size: f.size, mtime: (f.lastModified || 0) / 1000 });
  }
  st.files.push(...added);
  st.target = null;
  // フォルダごと入れたとき（またはたくさん入れたとき）は、中身で見分けて要るものだけを選ぶ（DESIGN §24.8）
  st.collect = added.some((x) => x.name.includes("/")) || st.files.length > 12;
  st.task = "";
  await readAll();
}

async function filesFromDrop(dt) {
  const out = [];
  const walk = async (entry, path) => {
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej));
      Object.defineProperty(file, "webkitRelativePath", { value: path + file.name });
      out.push(file);
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      let batch;
      do {
        batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        for (const e of batch) await walk(e, `${path}${entry.name}/`);
      } while (batch.length);
    }
  };
  const items = [...(dt.items || [])].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (items.length) for (const e of items) await walk(e, "");
  else out.push(...dt.files);
  return out;
}

async function readAll() {
  setStatus(`${st.files.length}個のファイルを読んでいます…`);
  st.choices = {}; st.answers = {}; st.follow = {};
  const res = st.collect
    ? await call("ot_intake_collect", [JSON.stringify(st.files.map(({ name, b64, mtime }) => ({ name, b64, mtime }))), st.task || "", ""])
    : await call("ot_intake_read", [JSON.stringify(st.files.map(({ name, b64 }) => ({ name, b64 })))]);
  if (!res.ok) { setStatus(`読めませんでした: ${res.message}`, true); return; }
  render(res);
  if (st.sample) {
    if (st.target === "corporate") autoAnswer(res);
    else { st.sample = false; await make(); }
    return;
  }
  setStatus("読みました。下で確かめてください。");
}

async function update() {
  const res = await call("ot_intake_update", [JSON.stringify({ choices: st.choices, answers: st.answers, follow: st.follow })]);
  if (!res.ok) { setStatus(`組み直せませんでした: ${res.message}`, true); return null; }
  render(res);
  return res;
}

// ---- 表示 ---------------------------------------------------------------------------------------
const LABELS = {
  "company.name": "会社の名前", "company.name_kana": "会社の名前（カナ）", "company.address": "本店の所在地",
  "company.tax_office": "税務署", "company.user_id": "利用者識別番号", "company.corporate_number": "法人番号",
  "company.representative": "代表者", "company.representative_kana": "代表者（カナ）", "company.business": "事業内容",
  "company.capital": "資本金", "company.capital_etc": "資本金等の額", "company.zip": "郵便番号", "company.phone": "電話番号",
  "company.blue_return": "青色申告", "company.employees": "従業者数", "fiscal_period.start": "事業年度の初日",
  "fiscal_period.end": "事業年度の末日", "issued_shares": "発行済株式の総数", "shareholders": "株主",
  "prior.schedule_05_01": "前期の別表五(一)の期末", "prior.schedule_05_02": "前期分の未納の税", "prior.losses": "繰越欠損金",
  "accounting.net_income": "当期純損益", "accounting.retained_earnings_end": "期末の繰越利益剰余金",
  "accounting.tax_provision_charged": "当期の納税充当金", "offices": "事務所のある市町村",
};

function show(v) {
  if (v === true) return "はい";
  if (v === false) return "いいえ";
  if (typeof v === "number") return yen(v);
  if (Array.isArray(v)) {
    return v.map((x) => (typeof x === "object" ? Object.values(x).map((y) => (typeof y === "number" ? y.toLocaleString("ja-JP") : y)).join(" ") : x)).join(" ／ ") || "なし";
  }
  if (v && typeof v === "object") return Object.values(v).join(" ");
  return v == null ? "" : String(v);
}

function render(res) {
  st.last = res;
  $("read").hidden = false;
  const ul = $("file-list");
  ul.replaceChildren(...res.files.map((f) => el("li", { class: f.ok ? "ok" : "ng" },
    el("span", { class: "mark" }, f.ok ? "読めた" : "読めない"), " ", el("b", {}, f.label), " ", el("span", { class: "fname" }, f.name),
    f.note ? el("div", { class: "note" }, f.note) : null)));
  $("notes").replaceChildren(...(res.notes || []).map((n) => el("div", { class: "msg warn" }, n)));
  const aiTargets = res.files.filter((f) => f.kind === "ai");
  $("ai").hidden = !aiTargets.length;
  window.OpenTaxAI && window.OpenTaxAI.setTargets(aiTargets.map((f) => st.files.find((x) => x.name === f.name)).filter(Boolean));

  renderNeeds(res.checklist || []);
  renderCollected(res.collected);
  const kinds = (res.taxes || []).map((t) => t.kind);
  if (!kinds.includes(st.target)) st.target = kinds[0] || null;
  renderTaxes(res.taxes || []);
  showTarget(res);
  // 食い違い: 候補を1タップで選ぶ
  $("conflicts").replaceChildren(...res.conflicts.map((c) => el("div", { class: "msg warn" },
    el("div", {}, `${LABELS[c.path] || c.label} が、ファイルによって違います。正しいものを押してください。`),
    el("div", { class: "row" }, ...c.candidates.map((cand, i) => el("button", { type: "button", onclick: async () => { st.choices[c.path] = i; await update(); } },
      `${show(cand.value)}（${cand.source}）`))))));
  // 読み取った値と出典
  const rows = Object.entries(res.marks).filter(([p]) => LABELS[p]).map(([p, m]) => el("tr", {},
    el("th", {}, LABELS[p]),
    el("td", {}, show(m.value), m.by === "ai" ? el("span", { class: "by by-ai" }, "AI") : null,
      m.weak ? el("span", { class: "by by-guess" }, "推定") : null),
    el("td", { class: "src" }, `${m.source}：${m.where}`),
    el("td", {}, isEditable(m.value) ? el("button", { type: "button", class: "small", onclick: () => editValue(p, m.value) }, "直す") : null)));
  $("readout").replaceChildren(...rows);
  // 足りない欄
  $("questions").replaceChildren(...res.questions.map((q) => questionBox(q)));
  // 申告調整・前期の未納の問い
  $("followups").replaceChildren(...(res.followups || []).map((q) => followBox(q)));
}

// ---- 色（申告の種類）と必要書類 ------------------------------------------------------------------
const COLOR_CLASS = { RED: "red", BLACK: "black", BLUE: "blue", WHITE: "white", GREEN: "green", GOLD: "gold", PINK: "pink", PLATINUM: "platinum" };
const STATE_MARK = (s) => (s.startsWith("使えます") ? "○" : s.startsWith("準備中") ? "×" : "△");

function renderNeeds(list) {
  if (!Array.isArray(list) || !list.length) return;
  $("needs").replaceChildren(...list.map((g) => el("div", { class: `edition ${COLOR_CLASS[g.color]}` },
    el("p", { class: "ed-name" }, g.color, el("span", { class: "ed-state" }, `${STATE_MARK(g.state)} ${g.state}`)),
    el("p", { class: "ed-who" }, g.who),
    el("ul", { class: "needs-list" }, ...g.items.map((it) => el("li", { class: it.done ? "done" : "" },
      el("span", { class: "chk", title: it.done ? "読めました" : it.auto ? "まだ入っていません" : "画面で聞くか、目で確かめます" },
        it.done ? "✓" : it.auto ? "□" : "—"),
      " ", el("b", {}, it.label), " ", el("span", { class: `need ${it.need.includes("必須") ? "must" : "may"}` }, it.need),
      el("div", { class: "where" }, it.where)))),
    g.color === "PINK" ? el("p", { class: "note" }, "給与計算（タイムカードから明細）と年末調整・源泉徴収票・法定調書合計表を作ります。先方の計算結果を記録するだけなら ", el("a", { href: "payroll.html" }, "給与の記録"), " へ") : null,
    g.color === "PLATINUM" ? el("p", { class: "note" }, "関与先ごとの今月の申告・納付の期限一覧と進捗は ", el("a", { href: "platinum.html" }, "期限一覧と進捗"), " へ") : null,
    g.sample ? el("div", { class: "row" }, el("button", { type: "button", class: "small", onclick: () => trySample(g.sample) }, "見本で試す")) : null)));
}

function renderTaxes(taxes) {
  $("taxes").replaceChildren(...(taxes.length ? taxes.map((t) => el("button", {
    type: "button", class: `tax-chip ${COLOR_CLASS[t.color]}${t.kind === st.target ? " on" : ""}`,
    onclick: () => { st.target = t.kind; renderTaxes(taxes); showTarget(st.last); } },
  el("b", {}, t.color), " ", t.label, t.ready ? "（作れます）" : "（問いがあります）"))
    : [el("p", { class: "msg warn" }, "作れる申告書が見つかりませんでした。下の「必要書類」を見て、ファイルを足してください。")]));
}

function showTarget(res) {
  const corp = st.target === "corporate";
  $("check").hidden = !corp;
  $("other").hidden = corp || !st.target;
  if (corp || !st.target || !res) return;
  const o = (res.others || []).find((x) => x.kind === st.target);
  if (!o) return;
  $("other-title").textContent = `${o.color}：${o.label}`;
  $("other-files").textContent = `使うファイル: ${o.files.join("・")}`;
  $("other-questions").replaceChildren(...o.questions.map((q) => (q.info
    ? el("div", { class: "msg warn" }, q.label)
    : questionBox(q))));
}

function renderCollected(c) {
  const box = $("collected");
  if (!c) { box.replaceChildren(); return; }
  const kids = [el("h3", {}, c.task ? `フォルダから「${c.label}」に要る書類を選びました` : "フォルダの中に、作れる申告の書類が見つかりませんでした")];
  if (c.used.length) kids.push(el("ul", { class: "needs-list" }, ...c.used.map((u) => el("li", { class: "done" },
    el("span", { class: "chk" }, "✓"), " ", el("b", {}, u.label), " ", el("span", { class: "fname" }, u.name), u.period ? `（${u.period}）` : ""))));
  if (c.unused.length) kids.push(el("details", {}, el("summary", {}, `使わなかったファイル（同じ種類のほかの期・古いもの） ${c.unused.length} 件`),
    el("ul", {}, ...c.unused.map((u) => el("li", {}, `${u.name}（${u.why}）`)))));
  if (c.other) kids.push(el("p", { class: "note" }, `ほかの申告の書類が ${c.other} 件あります。`));
  if ((c.tasks_found || []).length > 1) {
    kids.push(el("div", { class: "row" }, "ほかの申告で集め直す: ", ...c.tasks_found.filter((t) => t.task !== c.task).map((t) => el("button", {
      type: "button", class: "small", onclick: async () => { st.task = t.task; st.target = null; await readAll(); } }, t.label))));
  }
  if (c.ai_targets.length) kids.push(el("p", { class: "note" }, `PDF・写真・Excel ${c.ai_targets.length} 件はプログラムでは読めません（下の「AI で読む」か CSV に）。`));
  box.replaceChildren(el("div", { class: "msg ok" }, ...kids));
}

async function trySample(which) {
  st.files = (await call("ot_intake_samples", [which])).map((f) => ({ ...f }));
  st.sample = true;
  st.collect = false;
  st.target = { corporate: "corporate", platinum: "corporate", personal: "personal", personal_white: "personal",
    souzoku: "souzoku", green: "shukuhaku", pink: "kyuyo" }[which] || null;
  $("drop").scrollIntoView({ behavior: "smooth" });
  await readAll();
}

function isEditable(v) { return v == null || ["string", "number", "boolean"].includes(typeof v); }

function editValue(path, current) {
  const v = window.prompt(`${LABELS[path] || path} を入れてください`, current == null ? "" : String(current));
  if (v == null) return;
  st.answers[path] = typeof current === "number" ? Number(v.replace(/[,，円\s]/g, "")) : v;
  update();
}

function questionBox(q) {
  if (q.path === "offices" || q.path === "shareholders") {
    return el("div", { class: "msg warn" }, `${q.label}。`, el("a", { href: "detail.html" }, "詳しく入力する画面"), "で入れてください。");
  }
  const input = el("input", { type: "text", value: q.default == null ? "" : String(q.default), placeholder: q.label });
  const isNum = /（円|人|総数/.test(q.label);
  return el("div", { class: "question" }, el("label", {}, q.label, input),
    el("button", { type: "button", onclick: async () => {
      const v = input.value.trim();
      if (!v) return;
      st.answers[q.path] = isNum ? Number(v.replace(/[,，円人\s]/g, "")) : v;
      await update();
    } }, "決める"));
}

function followBox(q) {
  const box = el("div", { class: `question follow ${q.type}` }, el("div", { class: "q-label" }, q.label));
  const set = async (v) => { st.follow[q.key] = v; await update(); };
  if (q.type === "confirm" || q.type === "payment") {
    box.append(el("div", { class: "row" }, ...q.options.map((o) => el("button", { type: "button", onclick: () => set(o) }, o))));
  } else if (q.type === "dining") {
    const input = el("input", { type: "text", inputmode: "numeric", placeholder: "例 120000（なければ 0）" });
    box.append(el("div", { class: "row" }, input, el("button", { type: "button", onclick: () => {
      const n = Number(input.value.replace(/[,，円\s]/g, ""));
      if (Number.isFinite(n)) set({ account: q.account, amount: q.amount, dining: n });
    } }, "決める")));
  } else if (q.type === "dues") {
    box.append(el("div", { class: "row" }, el("button", { type: "button", onclick: () => set({}) }, "入っていない（0円）"),
      el("a", { href: "detail.html" }, "入っている（詳しく入力する画面で）")));
  } else if (q.type === "depreciation") {
    box.append(depTable(q, set));
  } else if (q.type === "dividends") {
    box.append(divTable(q, set));
  } else {
    box.classList.add("stop");
    box.append(el("div", { class: "note" }, "OpenTax ではまだ作れません。この科目があると申告書は作れません。"));
  }
  return box;
}

const DEP_COLS = [["kind", "種類（例 器具及び備品）"], ["method", "償却方法"], ["useful_life", "耐用年数"], ["cost", "取得価額"],
  ["book_end", "期末帳簿価額"], ["expensed", "当期償却額"], ["limit", "償却限度額"]];
function depTable(q, set) {
  const body = el("tbody");
  const addRow = (r) => body.append(el("tr", {}, ...DEP_COLS.map(([k, label]) => el("td", {},
    k === "method" ? el("select", { "data-k": k }, ...["定額法", "定率法", "旧定額法", "旧定率法"].map((m) => { const o = el("option", {}, m); if (r[k] === m) o.selected = true; return o; }))
      : el("input", { "data-k": k, type: "text", placeholder: label, value: r[k] == null ? "" : String(r[k]) })))));
  addRow({ method: "定額法", expensed: q.amount, limit: q.amount });
  const wrap = el("div", {}, el("table", { class: "dep" }, el("thead", {}, el("tr", {}, ...DEP_COLS.map(([, l]) => el("th", {}, l)))), body),
    el("div", { class: "row" }, el("button", { type: "button", onclick: () => addRow({ method: "定額法" }) }, "行を足す"),
      el("button", { type: "button", onclick: () => {
        const rows = [...body.children].map((tr) => {
          const r = {};
          tr.querySelectorAll("[data-k]").forEach((i) => { const k = i.dataset.k; const v = i.value.trim(); r[k] = ["kind", "method"].includes(k) ? v : Number(v.replace(/[,，円年\s]/g, "")); });
          r.detail = r.kind;
          return r;
        });
        set(rows);
      } }, "決める")),
    el("p", { class: "note" }, "固定資産台帳（または減価償却の明細）の数字です。償却限度額が分からなければ当期償却額と同じにしてください（償却超過なし）。"));
  return wrap;
}

// 受取配当金の内訳（別表八(一)・別表六(一)）。一部の期間だけ所有した銘柄の按分は詳しく入力する画面で
const DIV_COLS = [["name", "銘柄（支払った法人）"], ["amount", "配当の額（源泉徴収前）"], ["withholding", "源泉所得税（なければ 0）"],
  ["ratio", "保有割合（%）"], ["owned", "配当の計算期間を通じて所有"]];
function divTable(q, set) {
  const body = el("tbody");
  const addRow = (r) => body.append(el("tr", {}, ...DIV_COLS.map(([k, label]) => el("td", {},
    k === "owned" ? el("select", { "data-k": k }, ...["はい", "いいえ（一部の期間だけ）"].map((m) => el("option", {}, m)))
      : el("input", { "data-k": k, type: "text", placeholder: label, value: r[k] == null ? "" : String(r[k]) })))));
  (q.rows || [{}]).forEach(addRow);
  return el("div", {}, el("table", { class: "dep" }, el("thead", {}, el("tr", {}, ...DIV_COLS.map(([, l]) => el("th", {}, l)))), body),
    el("div", { class: "row" }, el("button", { type: "button", onclick: () => addRow({}) }, "行を足す"),
      el("button", { type: "button", onclick: () => {
        const rows = [...body.children].map((tr) => {
          const r = {};
          tr.querySelectorAll("[data-k]").forEach((i) => {
            const k = i.dataset.k; const v = i.value.trim();
            if (k === "name") r.name = v;
            else if (k === "owned") r.owned_whole_period = v === "はい";
            else r[k] = Number(v.replace(/[,，円%％\s]/g, "")) || 0;
          });
          return r;
        });
        set(rows);
      } }, "決める")),
    el("p", { class: "note" }, "支払通知書の数字です。保有割合が3分の1を超える銘柄・一部の期間だけ所有していた銘柄（所得税の控除を月数・株数で按分）は、"),
    el("a", { href: "detail.html" }, "詳しく入力する画面"), "で入れてください。");
}

// ---- 見本: 架空の会社で、問いにも見本の答えを入れて最後まで ----------------------------------------
const SAMPLE_DEP = [
  { method: "定額法", kind: "建物", structure: "鉄骨造", detail: "事務所", useful_life: 34, cost: 3000000, book_end: 2000000, expensed: 100000, limit: 100000 },
  { method: "定率法", kind: "機械及び装置", detail: "包装機", useful_life: 10, cost: 2000000, book_end: 941000, expensed: 300000, limit: 300000 },
  { method: "定率法", kind: "車両運搬具", detail: "営業車", useful_life: 6, cost: 2500000, book_end: 800000, expensed: 200000, limit: 200000 },
];
async function autoAnswer(res) {
  let r = res;
  for (let round = 0; round < 6 && r; round++) {
    const fs = r.followups || [];
    if (!fs.length) break;
    for (const q of fs) {
      if (q.type === "confirm" || q.type === "payment") st.follow[q.key] = q.options[0];
      else if (q.type === "dining") st.follow[q.key] = { account: q.account, amount: q.amount, dining: 120000 };
      else if (q.type === "depreciation") st.follow[q.key] = SAMPLE_DEP;
      else if (q.type === "dues") st.follow[q.key] = {};
      else if (q.type === "dividends") st.follow[q.key] = [{ name: "架空株式会社", amount: q.amount,
        withholding: Math.floor(q.amount * 0.15315), ratio: 1, owned_whole_period: true }];
    }
    r = await update();
  }
  st.sample = false;
  setStatus("見本の答えを入れました（実際は1つずつ押して答えます）。申告書を作っています…");
  await make();
}

// ---- 申告書を作る・渡す ------------------------------------------------------------------------
async function makeOther() {
  setStatus("申告書を作っています…");
  const res = await call("ot_intake_other_report", [JSON.stringify({ target: st.target, answers: st.answers })]);
  if (!res.ok) { setStatus(`作れませんでした: ${res.message}`, true); return; }
  st.html = res.html; st.company = res.name; st.fiscalEnd = res.when; st.local = ""; st.xtx = null; st.xtxOk = !!res.xtx; inlined = null;
  showReport(res.html);
  $("cab").closest("label").hidden = !res.xtx;
  const msgs = [...(res.warnings || []).map((w) => el("div", { class: "msg warn" }, `注意: ${w}`)),
    ...(res.checks || []).map((c) => el("div", { class: "msg" }, `確かめてください: ${c}`))];
  if (!res.xtx) msgs.unshift(el("div", { class: "msg" }, "この申告は e-Tax の申告データ（.xtx）を作りません。印刷するか、eLTAX・窓口で出してください。"));
  $("deliver-msg").replaceChildren(...msgs);
  setStatus("できました。印刷・保存・共有は下のボタンから。");
}

function showReport(html) {
  const frame = $("report");
  frame.onload = () => { fit(); setTimeout(fit, 300); };
  frame.srcdoc = html;
  $("out").hidden = false;
  $("share").hidden = !(navigator.canShare && navigator.share);
  $("out").scrollIntoView({ behavior: "smooth" });
}

async function make() {
  if (st.target && st.target !== "corporate") { await makeOther(); return; }
  $("cab").closest("label").hidden = false;
  $("deliver-msg").replaceChildren();
  const r = st.last;
  if (r && (r.questions.length || r.conflicts.length || (r.followups || []).length)) {
    setStatus("まだ答えていない問いがあります（上の黄色・白の枠）。", true);
    return;
  }
  setStatus("申告書を作っています…");
  const res = await call("ot_intake_report", [JSON.stringify({ choices: st.choices, answers: st.answers, follow: st.follow })]);
  if (!res.ok) { setStatus(`作れませんでした: ${res.message}`, true); return; }
  st.html = res.html; st.company = res.company; st.fiscalEnd = res.fiscal_end; st.local = res.local_sheet; st.xtx = null; inlined = null;
  showReport(res.html);
  setStatus("できました。印刷・保存・共有は下のボタンから。");
}

function fit() {
  const doc = $("report").contentDocument;
  const main = doc && doc.querySelector("main");
  if (main) $("report").style.height = `${Math.ceil(main.getBoundingClientRect().height) + 24}px`;
}

let inlined = null;
async function selfContained() {
  if (inlined) return inlined;
  let html = st.html;
  const paths = [...new Set([...html.matchAll(/href='(forms\/[^']+\.jpg)'/g)].map((m) => m[1]))];
  for (const p of paths) html = html.split(`href='${p}'`).join(`href='data:image/jpeg;base64,${b64(await (await fetch(p)).arrayBuffer())}'`);
  inlined = html;
  return html;
}

function baseName() { return `${st.company}_${(st.fiscalEnd || "").replace(/-/g, "")}`; }

async function bundle() {
  const enc = new TextEncoder();
  const files = [{ name: `申告書一式_${baseName()}.html`, data: enc.encode(await selfContained()) }];
  if (st.local) files.push({ name: `地方税の一覧_${baseName()}.html`, data: enc.encode(st.local) });
  if (st.xtx) files.push({ name: `申告データ_${baseName()}.xtx`, data: st.xtx });
  return new File([zip(files)], `申告書_${baseName()}.zip`, { type: "application/zip" });
}

function download(file) {
  const a = el("a", { href: URL.createObjectURL(file), download: file.name });
  document.body.append(a); a.click(); a.remove();
}

// 最小の zip（無圧縮）。外部の部品を使わないため
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
function crc32(d) { let c = 0xffffffff; for (let i = 0; i < d.length; i++) c = CRC[(c ^ d[i]) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }
function zip(files) {
  const enc = new TextEncoder();
  const parts = [], central = [];
  let offset = 0;
  for (const f of files) {
    const name = enc.encode(f.name), crc = crc32(f.data), size = f.data.length;
    const h = new DataView(new ArrayBuffer(30));
    h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true); h.setUint32(14, crc, true);
    h.setUint32(18, size, true); h.setUint32(22, size, true); h.setUint16(26, name.length, true);
    parts.push(new Uint8Array(h.buffer), name, f.data);
    const c = new DataView(new ArrayBuffer(46));
    c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true);
    c.setUint32(16, crc, true); c.setUint32(20, size, true); c.setUint32(24, size, true); c.setUint16(28, name.length, true); c.setUint32(42, offset, true);
    central.push(new Uint8Array(c.buffer), name);
    offset += 30 + name.length + size;
  }
  const csize = central.reduce((s, p) => s + p.length, 0);
  const e = new DataView(new ArrayBuffer(22));
  e.setUint32(0, 0x06054b50, true); e.setUint16(8, files.length, true); e.setUint16(10, files.length, true); e.setUint32(12, csize, true); e.setUint32(16, offset, true);
  return new Blob([...parts, ...central, new Uint8Array(e.buffer)], { type: "application/zip" });
}

// ---- つなぎ ------------------------------------------------------------------------------------
const drop = $("drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));
drop.addEventListener("drop", async (e) => { e.preventDefault(); await addFiles(await filesFromDrop(e.dataTransfer)); });
$("pick-files").addEventListener("change", async (e) => { await addFiles([...e.target.files]); e.target.value = ""; });
$("pick-folder").addEventListener("change", async (e) => { await addFiles([...e.target.files]); e.target.value = ""; });
if (!("webkitdirectory" in document.createElement("input"))) $("pick-folder-label").hidden = true;
$("sample").addEventListener("click", () => trySample("corporate"));
$("make").addEventListener("click", make);
$("make-other").addEventListener("click", make);
$("print").addEventListener("click", () => { const w = $("report").contentWindow; if (w) { w.focus(); w.print(); } });
$("save-all").addEventListener("click", async () => download(await bundle()));
$("share").addEventListener("click", async () => {
  const file = await bundle();
  const data = { files: [file], title: file.name };
  try {
    if (navigator.canShare(data)) await navigator.share(data);
    else { download(file); $("deliver-msg").replaceChildren(el("div", { class: "msg warn" }, "この端末ではファイルの共有ができないので、保存しました。")); }
  } catch (e) { if (e.name !== "AbortError") setStatus(`共有できませんでした: ${e.message}`, true); }
});
$("cab").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  setStatus("公式の XSD で確かめて .xtx を作っています…");
  const corp = !st.target || st.target === "corporate";
  const res = await call(corp ? "ot_intake_export" : "ot_intake_other_export",
    [JSON.stringify({ choices: st.choices, answers: st.answers, follow: st.follow, target: st.target }), new Uint8Array(await f.arrayBuffer())]);
  if (!res.ok) {
    $("deliver-msg").replaceChildren(el("div", { class: "msg error" }, `${res.message}\n${(res.errors || []).join("\n")}`));
    setStatus(".xtx は作れませんでした。", true);
    return;
  }
  st.xtx = Uint8Array.from(atob(res.xtx_base64), (c) => c.charCodeAt(0));
  download(new File([st.xtx], `申告データ_${baseName()}.xtx`, { type: "application/xml" }));
  $("deliver-msg").replaceChildren(el("div", { class: "msg ok" }, ".xtx を作って保存しました（「まとめて保存」にも入ります）。e-Taxソフトの「組み込み」で読み込めます。"));
  setStatus(".xtx を作りました。");
});
$("show-qr").addEventListener("click", () => {
  const url = location.href.split("#")[0];
  const qr = qrcode(0, "M");
  qr.addData(url);
  qr.make();
  $("qr-img").innerHTML = qr.createSvgTag({ cellSize: 5, margin: 2, scalable: true });
  $("qr").hidden = !$("qr").hidden;
});
window.addEventListener("resize", fit);
window.OpenTaxEasy = { addAiResult: async (name, json) => {
  st.files.push({ name, b64: b64(new TextEncoder().encode(json)) });
  await readAll();
} };

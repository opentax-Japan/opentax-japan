// OpenTax RED の画面。計算はワーカーの中の Python（opentax.api）だけが行う。
// 入力と結果は、この変数（state・lastResult）にだけ置く。localStorage・sessionStorage・IndexedDB・Cookie は使わない。
// 保存と再開は、利用者がボタンを押したときのファイルのダウンロードと、ファイルの選択だけ。
"use strict";

const RELATIONS = ["本人", "配偶者", "父", "母", "義父", "義母", "長男", "次男", "三男", "長女", "次女", "三女", "子", "孫", "祖父", "祖母", "兄弟", "姉妹", "その他"];
const LOCAL_TAXES = ["道府県民税", "市町村民税"];
const METHODS = ["充当金取崩し", "損金経理"];
const OUT_OF_SCOPE = ["黒字法人の税額計算", "税額控除（所得税額の控除・還付を含む）", "中間申告", "外形標準課税（資本金1億円超）",
  "欠損金の繰戻し還付", "グループ通算", "特別措置の適用（適用額明細書）", "剰余金の配当", "別表四のその他の加算・減算",
  "仮払経理による納付", "分割法人（2つ以上の都道府県・市町村に事務所）"];

// 入力項目。path は入力 JSON の場所（opentax の入力と同じ形）
const SECTIONS = [
  { title: "会社情報", fields: [
    { path: "company.name", label: "法人名", type: "text", required: true },
    { path: "company.name_kana", label: "法人名（カナ）", type: "text" },
    { path: "company.zip", label: "郵便番号", type: "text", hint: "例 700-0000" },
    { path: "company.address", label: "納税地", type: "text", required: true },
    { path: "company.phone", label: "電話番号", type: "text", hint: "例 086-000-0000" },
    { path: "company.tax_office", label: "所轄の税務署", type: "text", required: true, hint: "例 岡山東" },
    { path: "company.tax_office_code", label: "税務署コード（5桁）", type: "text", hint: "税務署名で決まらないときだけ" },
    { path: "company.user_id", label: "e-Tax 利用者識別番号（16桁）", type: "text", hint: ".xtx を作るときに必要" },
    { path: "company.representative", label: "代表者", type: "text" },
    { path: "company.representative_kana", label: "代表者（カナ）", type: "text" },
    { path: "company.business", label: "事業種目", type: "text" },
    { path: "company.capital", label: "資本金の額", type: "int", required: true },
    { path: "company.capital_etc", label: "資本金等の額", type: "int", required: true },
    { path: "company.capital_reserve", label: "資本準備金", type: "int" },
    { path: "company.capital_surplus", label: "資本剰余金", type: "int" },
    { path: "company.employees", label: "従業者数（期末）", type: "int", required: true },
    { path: "company.blue_return", label: "青色申告", type: "bool", required: true },
  ] },
  { title: "事業年度", fields: [
    { path: "fiscal_period.start", label: "事業年度の始め", type: "date", required: true },
    { path: "fiscal_period.end", label: "事業年度の終わり", type: "date", required: true },
  ] },
  { title: "事務所・事業所（1つの市町村だけ）", list: "offices", addLabel: "事務所を足す", fields: [
    { key: "prefecture", label: "都道府県", type: "prefecture", required: true },
    { key: "municipality", label: "市町村", type: "city", required: true },
    { key: "ward", label: "区（政令指定都市）", type: "text", hint: "岡山市は必須。例 北区" },
    { key: "pref_office", label: "県の提出先（PCdesk に登録した名称）", type: "text" },
    { key: "city_office", label: "市の提出先（PCdesk に登録した名称）", type: "text" },
    { key: "months", label: "事務所等を有していた月数", type: "int", hint: "空欄なら事業年度の月数" },
  ] },
  { title: "株主", fields: [
    { path: "issued_shares", label: "発行済株式の総数", type: "int", required: true },
    { path: "total_votes", label: "議決権の総数", type: "int", hint: "空欄なら株式の総数" },
  ], list: "shareholders", addLabel: "株主を足す", itemFields: [
    { key: "name", label: "氏名・法人名", type: "text", required: true },
    { key: "address", label: "住所", type: "text" },
    { key: "relation", label: "判定基準となる株主との続柄", type: "select", options: RELATIONS, required: true },
    { key: "group", label: "株主グループの番号", type: "int", required: true, hint: "同族関係者は同じ番号" },
    { key: "shares", label: "持株数", type: "int", required: true },
    { key: "votes", label: "議決権の数", type: "int", hint: "空欄なら持株数" },
  ] },
  { title: "決算の数字", fields: [
    { path: "accounting.net_income", label: "当期純損益（損失はマイナス）", type: "int", required: true },
    { path: "accounting.tax_provision_charged", label: "損金経理をした納税充当金（法人税、住民税及び事業税の計上額）", type: "int" },
    { path: "accounting.retained_earnings_end", label: "期末の繰越利益剰余金", type: "int", required: true },
  ] },
  { title: "当期に納付した前期分の住民税", list: "tax_payments", addLabel: "納付を足す", fields: [
    { key: "tax", label: "税目", type: "select", options: LOCAL_TAXES, required: true },
    { key: "period_end", label: "どの事業年度分か（終わりの日）", type: "date", required: true },
    { key: "method", label: "納付の仕方", type: "select", options: METHODS, required: true },
    { key: "amount", label: "金額", type: "int", required: true },
  ] },
  { title: "前期の別表五(一)（期末の残高）", list: "prior.schedule_05_01", addLabel: "行を足す", fields: [
    { key: "item", label: "区分", type: "text", required: true, hint: "繰越損益金・納税充当金・未納道府県民税・未納市町村民税・利益準備金 など" },
    { key: "amount", label: "金額（未納の税はプラスで）", type: "int", required: true },
  ] },
  { title: "前期の別表五(二)（期末の未納額）", list: "prior.schedule_05_02", addLabel: "行を足す", fields: [
    { key: "tax", label: "税目", type: "select", options: LOCAL_TAXES, required: true },
    { key: "period_start", label: "事業年度の始め", type: "date", required: true },
    { key: "period_end", label: "事業年度の終わり", type: "date", required: true },
    { key: "unpaid", label: "未納額", type: "int", required: true },
  ] },
  { title: "繰越欠損金（前期までの青色欠損金）", list: "prior.losses", addLabel: "行を足す", fields: [
    { key: "period_start", label: "事業年度の始め", type: "date", required: true },
    { key: "period_end", label: "事業年度の終わり", type: "date", required: true },
    { key: "amount", label: "控除未済欠損金額", type: "int", required: true },
  ] },
  { title: "申告書の記載", fields: [
    { path: "filing.settled_on", label: "決算確定の日", type: "date" },
    { path: "filing.submitted_on", label: "提出年月日", type: "date" },
    { path: "filing.preparer", label: "作成者（空欄なら法人名）", type: "text" },
    { path: "filing.attachments.balance_sheet", label: "添付: 貸借対照表", type: "bool", default: true },
    { path: "filing.attachments.profit_loss", label: "添付: 損益計算書", type: "bool", default: true },
    { path: "filing.attachments.equity_changes", label: "添付: 株主資本等変動計算書", type: "bool", default: true },
    { path: "filing.attachments.account_details", label: "添付: 勘定科目内訳明細書", type: "bool", default: true },
    { path: "filing.attachments.business_overview", label: "添付: 法人事業概況説明書", type: "bool", default: true },
    { path: "filing.tax_accountant_article30", label: "税理士法第30条の書面提出", type: "bool" },
    { path: "filing.tax_accountant_article33_2", label: "税理士法第33条の2の書面提出", type: "bool" },
  ] },
];

// ---- 状態（メモリだけ） ----
let state = {};
let info = null;
let dirty = false;
let lastInput = null;

// ---- ワーカー ----
const worker = new Worker("worker.js", { type: "module" });
let nextId = 1;
const pending = new Map();
worker.onmessage = (event) => {
  const msg = event.data;
  if (msg.type === "progress") setStatus(msg.message);
  else if (msg.type === "ready") onReady(msg.info);
  else if (msg.type === "fatal") setStatus(`読み込みに失敗しました: ${msg.message}`, true);
  else if (msg.type === "result") {
    const resolve = pending.get(msg.id);
    pending.delete(msg.id);
    resolve(JSON.parse(msg.result));
  }
};
function call(cmd, args, transfer) {
  const id = nextId++;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    worker.postMessage({ id, cmd, args }, transfer || []);
  });
}

// ---- 値の出し入れ ----
function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
function setPath(obj, path, value) {
  const keys = path.split(".");
  let o = obj;
  keys.slice(0, -1).forEach((k) => { if (o[k] == null || typeof o[k] !== "object") o[k] = {}; o = o[k]; });
  o[keys[keys.length - 1]] = value;
}
function allFields() {
  const out = [];
  for (const s of SECTIONS) {
    if (!s.list || s.itemFields) (s.fields || []).forEach((f) => out.push({ ...f, list: null }));
    const items = s.itemFields || (s.list ? s.fields : null);
    if (s.list) items.forEach((f) => out.push({ ...f, list: s.list }));
  }
  return out;
}
function parseInt10(text) {
  const t = String(text).replace(/[,，\s円]/g, "").replace(/^[△▲]/, "-");
  if (!/^-?\d+$/.test(t)) return null;
  return Number(t);
}
function formatInt(v) {
  const n = parseInt10(v);
  return n == null ? String(v) : n.toLocaleString("ja-JP");
}

// 画面の状態 → opentax の入力（空欄は入れない。数値は整数にする）
function buildInput() {
  const input = {};
  const errors = [];
  const put = (target, path, f, raw) => {
    if (f.type === "bool") { setPath(target, path, !!raw); return; }
    if (raw == null || String(raw).trim() === "") return;
    if (f.type === "int") {
      const n = parseInt10(raw);
      if (n == null) { errors.push(`${f.label}: 整数で入れてください`); return; }
      setPath(target, path, n);
    } else setPath(target, path, String(raw).trim());
  };
  for (const s of SECTIONS) {
    const plain = s.itemFields ? s.fields : (s.list ? [] : s.fields);
    plain.forEach((f) => {
      const raw = getPath(state, f.path);
      if (f.type === "bool" && raw === undefined && f.default === undefined && f.path !== "company.blue_return") return;
      put(input, f.path, f, raw === undefined ? f.default : raw);
    });
    if (s.list) {
      const items = s.itemFields || s.fields;
      const rows = (getPath(state, s.list) || []).map((row) => {
        const out = {};
        items.forEach((f) => put(out, f.key, f, row[f.key]));
        return out;
      }).filter((r) => Object.keys(r).length);
      if (rows.length) setPath(input, s.list, rows);
    }
  }
  return { input, errors };
}

// opentax の入力 → 画面の状態
function loadInput(obj) {
  state = JSON.parse(JSON.stringify(obj || {}));
  for (const f of allFields()) {
    if (f.type !== "int") continue;
    if (f.list) (getPath(state, f.list) || []).forEach((row) => { if (typeof row[f.key] === "number") row[f.key] = formatInt(row[f.key]); });
    else { const v = getPath(state, f.path); if (typeof v === "number") setPath(state, f.path, formatInt(v)); }
  }
  render();
}

// ---- 描画 ----
const form = document.getElementById("input-form");

function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  });
  children.flat().forEach((c) => e.append(c instanceof Node ? c : document.createTextNode(String(c))));
  return e;
}

function control(f, value, onChange, errorKey) {
  const id = `f-${errorKey.replace(/[^\w]/g, "_")}`;
  let input;
  if (f.type === "bool") {
    input = el("input", { type: "checkbox", id });
    input.checked = value === undefined ? !!f.default : !!value;
    input.addEventListener("change", () => onChange(input.checked));
  } else if (["select", "prefecture", "city"].includes(f.type)) {
    let options = f.options || [];
    if (f.type === "prefecture") options = info.prefectures;
    if (f.type === "city") options = info.cities.map((c) => c.name);
    input = el("select", { id }, el("option", { value: "" }, "選んでください"), options.map((o) => el("option", { value: o }, o)));
    if (value && !options.includes(value)) input.append(el("option", { value }, `${value}（未対応）`));
    input.value = value || "";
    input.addEventListener("change", () => onChange(input.value));
  } else {
    input = el("input", { type: f.type === "date" ? "date" : "text", id, class: f.type === "int" ? "num" : "",
      inputmode: f.type === "int" ? "numeric" : null, autocomplete: "off", spellcheck: "false" });
    input.value = value == null ? "" : value;
    input.addEventListener("input", () => onChange(input.value));
    if (f.type === "int") input.addEventListener("blur", () => { if (input.value) { input.value = formatInt(input.value); onChange(input.value); } });
  }
  return el("div", { class: `field${f.required ? " required" : ""}`, "data-key": errorKey },
    el("label", { for: id }, f.label), input, f.hint ? el("div", { class: "hint" }, f.hint) : "");
}

function changed() {
  dirty = true;
  // 入力が変わったら、前の計算結果からは .xtx を作らない（もう一度「計算する」を押す）
  if (lastInput) {
    lastInput = null;
    updateExportButton();
    const out = document.getElementById("export-result");
    if (out) out.textContent = "";
    showMessages([["warn", "入力が変わりました。もう一度「計算する」を押してください。"]]);
  }
}

function render() {
  form.textContent = "";
  for (const s of SECTIONS) {
    const body = [];
    const plain = s.itemFields ? s.fields : (s.list ? [] : s.fields);
    if (plain.length) {
      body.push(el("div", { class: "fields" }, plain.map((f) => control(f, getPath(state, f.path), (v) => { setPath(state, f.path, v); changed(); }, f.path))));
    }
    if (s.list) {
      const items = s.itemFields || s.fields;
      if (!Array.isArray(getPath(state, s.list))) setPath(state, s.list, []);
      const rows = getPath(state, s.list);
      const block = el("div", { class: "list-block" });
      rows.forEach((row, i) => {
        block.append(el("div", { class: "list-row" },
          el("div", { class: "fields" }, items.map((f) => control(f, row[f.key], (v) => { row[f.key] = v; changed(); }, `${s.list}[${i + 1}].${f.key}`))),
          el("button", { type: "button", class: "remove", onclick: () => { rows.splice(i, 1); changed(); render(); } }, "この行を消す")));
      });
      block.append(el("button", { type: "button", onclick: () => { rows.push({}); changed(); render(); } }, s.addLabel));
      body.push(block);
    }
    form.append(el("details", { class: "section", open: true }, el("summary", {}, s.title), body));
  }
  form.append(el("details", { class: "section" }, el("summary", {}, "OpenTax RED の対象外（入力できません）"),
    el("ul", { class: "out-of-scope" }, OUT_OF_SCOPE.map((t) => el("li", {}, t)))));
}

// ---- メッセージ ----
function setStatus(text, error) {
  const s = document.getElementById("status");
  s.textContent = text;
  s.classList.toggle("msg", !!error);
  s.classList.toggle("error", !!error);
}
function showMessages(list) {
  const box = document.getElementById("messages");
  box.textContent = "";
  list.forEach(([kind, text]) => box.append(el("div", { class: `msg ${kind}` }, text)));
}
function markErrors(message) {
  document.querySelectorAll(".field.error").forEach((e) => e.classList.remove("error"));
  (message || "").split("\n").forEach((line) => {
    const m = line.trim().match(/^([\w.\[\]]+):/);
    if (!m) return;
    const target = document.querySelector(`.field[data-key="${CSS.escape(m[1])}"]`);
    if (target) target.classList.add("error");
  });
}

// ---- 計算 ----
async function calculate() {
  const { input, errors } = buildInput();
  if (errors.length) { showMessages(errors.map((e) => ["error", e])); return; }
  setStatus("計算しています…");
  lastInput = input;
  const res = await call("ot_calculate", [JSON.stringify(input), trialMode()]);
  setStatus("計算しました。");
  markErrors(res.ok ? "" : res.message);
  if (!res.ok) {
    showMessages([["error", res.message]]);
    document.getElementById("results").hidden = true;
    document.getElementById("messages").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  const msgs = [];
  res.problems.forEach((p) => msgs.push(["error", `一致しない項目: ${p}`]));
  res.warnings.forEach((w) => msgs.push(["warn", w]));
  if (!res.problems.length) msgs.push(["ok", "帳票の式・帳票間のチェックは、すべて一致しました。"]);
  showMessages(msgs);
  showResults(res);
  document.getElementById("messages").scrollIntoView({ behavior: "smooth", block: "start" });
}

function trialMode() {
  return document.getElementById("trial").checked ? "truncate" : "";
}

function yen(v) {
  return v < 0 ? `△${(-v).toLocaleString("ja-JP")}` : v.toLocaleString("ja-JP");
}

function showResults(res) {
  document.getElementById("results").hidden = false;
  const s = res.summary;
  const cards = [
    ["所得金額（別表四 52）", yen(s.income)],
    ["翌期へ繰り越す欠損金", yen(s.loss_carry)],
    [`均等割 ${s.prefecture.jurisdiction}`, yen(s.prefecture.amount)],
    [`均等割 ${s.municipality.jurisdiction}${s.municipality.ward ? " " + s.municipality.ward : ""}`, yen(s.municipality.amount)],
  ];
  const summary = document.getElementById("summary");
  summary.textContent = "";
  cards.forEach(([label, value]) => summary.append(el("div", { class: "card" }, el("div", { class: "label" }, label), el("div", { class: "value" }, value))));

  lastViews = res.form_views;
  renderForms();
  renderPaper(res.paper_sheets || []);
  document.getElementById("local-sheet").srcdoc = res.local_sheet;
  lastLocalSheet = res.local_sheet;
  updateExportButton();
}

let lastLocalSheet = "";
let lastViews = [];

// 申告書（別表）の形のプレビュー
function cellContent(cell) {
  if (!cell) return "";
  const parts = [];
  if (cell.outer != null) parts.push(el("div", { class: "sub" }, `外 ${fmt(cell.outer)}`));
  if (cell.inner != null) parts.push(el("div", { class: "sub" }, `内 ${fmt(cell.inner)}`));
  (cell.parts || []).forEach((p) => parts.push(el("div", {}, el("span", { class: "sub" }, `${p.label} `), fmt(p.value))));
  if (cell.value != null) parts.push(el("div", {}, fmt(cell.value)));
  return parts;
}
function fmt(v) {
  return typeof v === "number" ? yen(v) : String(v);
}
// 紙の別表: 国税庁の様式の画像の上に、金額を SVG の文字で置く（元の画像の座標のまま）
const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs, text) {
  const e = document.createElementNS(SVG, tag);
  Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
  if (text != null) e.textContent = text;
  return e;
}
function textWidth(text) {
  let w = 0;
  for (const ch of text) w += ch.charCodeAt(0) < 0x2000 ? 0.6 : 1;
  return w;
}
function renderPaper(sheets) {
  const box = document.getElementById("paper");
  box.textContent = "";
  if (!sheets.length) {
    box.append(el("p", { class: "note" }, "この事業年度の紙の様式は、まだ用意していません。下の表で確かめてください。"));
    return;
  }
  sheets.forEach((sheet, i) => {
    const [w, h] = sheet.size;
    const s = svg("svg", { viewBox: `0 0 ${w} ${h}`, class: "paper-sheet", role: "img", "aria-label": sheet.title });
    s.append(svg("image", { href: sheet.image, x: 0, y: 0, width: w, height: h }));
    sheet.items.forEach((it) => {
      const [x0, y0, x1, y1] = it.box;
      const hgt = y1 - y0;
      if (it.kind === "circle") {
        s.append(svg("ellipse", { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, rx: (x1 - x0) / 2, ry: hgt / 2, class: "mark" }));
        return;
      }
      const lines = it.text.split("\n");
      const base = it.kind === "amount" ? Math.min(38, Math.max(24, hgt * 0.64)) : Math.min(it.kind === "center" ? 40 : 30, (hgt - 8) / lines.length);
      // 枠の幅に収まるように小さくする（半角は 0.6 文字分として見積もる）
      const widest = Math.max(...lines.map(textWidth));
      const size = Math.min(base, (x1 - x0 - 24) / Math.max(widest, 1));
      const anchor = { amount: "end", center: "middle" }[it.kind] || "start";
      const x = { amount: x1 - 14, center: (x0 + x1) / 2 }[it.kind] ?? x0 + 12;
      const top = (y0 + y1) / 2 - (size * lines.length) / 2;
      lines.forEach((line, j) => s.append(svg("text", { x, y: top + size * (j + 0.85), "font-size": size, "text-anchor": anchor,
        class: it.kind === "amount" ? "amt" : "txt" }, line)));
    });
    box.append(el("details", { class: "section", open: i === 0 }, el("summary", {}, sheet.title),
      el("div", { class: "paper-wrap" }, s), el("p", { class: "note src" }, sheet.source)));
  });
}

function renderForms() {
  const filledOnly = document.getElementById("filled-only").checked;
  const preview = document.getElementById("preview");
  preview.textContent = "";
  lastViews.forEach((form, i) => {
    const tables = form.blocks.map((block) => {
      const rows = block.rows.filter((r) => !filledOnly || Object.keys(r.cells).length);
      if (!rows.length) return null;
      return el("div", { class: "sheet-scroll" }, el("table", { class: "sheet" },
        el("thead", {}, el("tr", {}, el("th", { class: "line" }, "行"), el("th", { class: "label" }, "区分"),
          block.columns.map((c) => el("th", {}, c)))),
        el("tbody", {}, rows.map((r) => el("tr", { class: Object.keys(r.cells).length ? "filled" : "" },
          el("td", { class: "line" }, r.line), el("td", { class: "label" }, r.label),
          block.columns.map((c) => el("td", { class: "amt" }, cellContent(r.cells[c]))))))));
    }).filter(Boolean);
    preview.append(el("details", { class: "section sheet-form", open: i === 0 },
      el("summary", {}, form.title),
      el("div", { class: "sheet-head" }, el("span", {}, form.title), el("span", {}, `事業年度 ${form.period}`), el("span", {}, `法人名 ${form.company}`)),
      tables.length ? tables : el("p", { class: "note" }, "金額のある行はありません")));
  });
}
let cabFile = null;

function updateExportButton() {
  document.getElementById("export-etax").disabled = !(cabFile && lastInput);
}

async function exportEtax() {
  if (!cabFile || !lastInput) return;
  const out = document.getElementById("export-result");
  out.textContent = "";
  setStatus("公式XSD で検証して .xtx を作っています（1分ほどかかることがあります）…");
  const bytes = new Uint8Array(await cabFile.arrayBuffer());
  const res = await call("ot_export", [JSON.stringify(lastInput), trialMode(), bytes], [bytes.buffer]);
  setStatus(res.ok ? ".xtx を作りました。" : ".xtx を作れませんでした。", !res.ok);
  out.scrollIntoView({ behavior: "smooth", block: "center" });
  if (!res.ok) {
    out.append(el("div", { class: "msg error" }, res.message + (res.errors ? "\n" + res.errors.join("\n") : "")));
    return;
  }
  const bin = Uint8Array.from(atob(res.xtx_base64), (c) => c.charCodeAt(0));
  const end = (lastInput.fiscal_period && lastInput.fiscal_period.end || "").replace(/-/g, "");
  const name = `OpenTaxRED_${end}${res.trial ? "_trial" : ""}.xtx`;
  download(new Blob([bin], { type: "application/xml" }), name);
  out.append(el("div", { class: "msg ok" }, `公式XSD（手続 RHO0012）の検証: 誤りなし。${name}（${res.size.toLocaleString("ja-JP")} バイト）を保存しました。`
    + (res.trial ? "\n試し用です。本番の申告には使わないでください。" : "")));
}

// ---- ファイル ----
function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function onReady(i) {
  info = i;
  render();
  setStatus("準備ができました。入力して「計算する」を押してください。");
  ["load-sample", "open-input", "save-input", "clear-input", "calculate"].forEach((id) => { document.getElementById(id).disabled = false; });
}

document.getElementById("load-sample").addEventListener("click", () => {
  if (dirty && !confirm("入力中の内容を、見本で置き換えますか。")) return;
  loadInput(info.sample);
  dirty = false;
  showMessages([["ok", "見本（架空の法人: オープン商事株式会社）を入れました。「計算する」を押してください。"]]);
});
document.getElementById("open-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    loadInput(JSON.parse(await file.text()));
    dirty = false;
    showMessages([["ok", `${file.name} を開きました。`]]);
  } catch (err) {
    showMessages([["error", `ファイルを読めません: ${err.message}`]]);
  }
});
document.getElementById("save-input").addEventListener("click", () => {
  const { input, errors } = buildInput();
  if (errors.length) { showMessages(errors.map((e) => ["error", e])); return; }
  if (!confirm("入力した内容（利用者識別番号を含む）をそのままファイルに保存します。保存先に気をつけてください。")) return;
  download(new Blob([JSON.stringify(input, null, 2)], { type: "application/json" }), "opentax-input.json");
  dirty = false;
});
document.getElementById("clear-input").addEventListener("click", () => {
  if (!confirm("入力を消しますか（保存していない内容は失われます）。")) return;
  state = {};
  lastInput = null;
  dirty = false;
  render();
  document.getElementById("results").hidden = true;
  showMessages([]);
});
document.getElementById("calculate").addEventListener("click", calculate);
document.getElementById("filled-only").addEventListener("change", renderForms);
document.getElementById("trial").addEventListener("change", () => {
  if (lastInput) { dirty = true; changed(); }
});
document.getElementById("cab").addEventListener("change", (e) => {
  cabFile = e.target.files[0] || null;
  document.getElementById("cab-name").textContent = cabFile ? `${cabFile.name}（${cabFile.size.toLocaleString("ja-JP")} バイト）` : "未選択";
  updateExportButton();
});
document.getElementById("export-etax").addEventListener("click", exportEtax);
document.getElementById("download-local").addEventListener("click", () => {
  if (lastLocalSheet) download(new Blob([lastLocalSheet], { type: "text/html" }), "opentax-local-tax.html");
});
window.addEventListener("beforeunload", (e) => {
  if (dirty) { e.preventDefault(); e.returnValue = ""; }
});

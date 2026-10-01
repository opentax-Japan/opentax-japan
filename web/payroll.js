// OpenTax 給与の記録の画面。検算と集計はワーカーの中の Python（opentax.api）だけが行う。給与・税額は計算しない。
// 記録はこの変数（rec）にだけ置く。localStorage・sessionStorage・IndexedDB・Cookie は使わない。
// 保存と再開は、利用者がボタンを押したときのファイルのダウンロードと、ファイルの選択だけ。
"use strict";

const DED_KINDS = [["social", "社会保険料"], ["income_tax", "源泉所得税"], ["resident_tax", "住民税"], ["other", "その他"]];
const WORK = [["days", "労働日数"], ["hours", "労働時間数"], ["overtime_hours", "時間外"], ["holiday_hours", "休日"], ["night_hours", "深夜"]];

let rec = null;
let current = 0;
let lastLedger = "";

// ---- ワーカー（申告の画面と同じもの） ----
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

// ---- 小さな部品 ----
const $ = (id) => document.getElementById(id);
function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  for (const c of children) e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return e;
}
function setStatus(text, error) {
  const s = $("status");
  s.textContent = text;
  s.style.background = error ? "#fdecec" : "";
}
function toInt(text) {
  const t = String(text).replace(/[,，\s]/g, "");
  if (t === "") return null;
  return /^\d+$/.test(t) ? Number(t) : NaN;
}
function yen(v) {
  return v === null || v === undefined || v === "" ? "" : Number(v).toLocaleString("ja-JP");
}
function newKey(prefix, list) {
  let n = list.length + 1;
  while (list.some((i) => i.key === `${prefix}${n}`)) n++;
  return `${prefix}${n}`;
}
function emptyRecord() {
  return {
    format: "opentax-payroll/1", company: "", year: new Date().getFullYear(),
    items: {
      pay: [{ key: "basic", name: "基本給", taxable: true }, { key: "commute", name: "通勤手当（非課税）", taxable: false }],
      deduct: [{ key: "health", name: "健康保険料", kind: "social" }, { key: "pension", name: "厚生年金保険料", kind: "social" },
        { key: "employment", name: "雇用保険料", kind: "social" }, { key: "income_tax", name: "源泉所得税", kind: "income_tax" },
        { key: "resident_tax", name: "住民税", kind: "resident_tax" }],
    },
    people: [], payments: [],
  };
}

// ---- 表示 ----
function renderAll() {
  $("company").value = rec.company || "";
  $("year").value = rec.year || "";
  renderItems();
  renderPeople();
  renderPayments();
}

function renderItems() {
  const pay = $("pay-items");
  pay.replaceChildren(...rec.items.pay.map((item, i) => el("div", { class: "row" },
    el("input", { type: "text", value: item.name, "aria-label": "支給の項目名", oninput: (e) => { item.name = e.target.value; renderGrid(); } }),
    el("label", { class: "inline" }, el("input", { type: "checkbox", ...(item.taxable !== false ? { checked: "" } : {}),
      onchange: (e) => { item.taxable = e.target.checked; } }), "課税"),
    el("button", { type: "button", class: "remove", onclick: () => { removeItem("pay", i); } }, "消す"))));
  const ded = $("ded-items");
  ded.replaceChildren(...rec.items.deduct.map((item, i) => {
    const sel = el("select", { "aria-label": "控除の種類", onchange: (e) => { item.kind = e.target.value; } },
      ...DED_KINDS.map(([k, label]) => el("option", { value: k, ...(item.kind === k ? { selected: "" } : {}) }, label)));
    return el("div", { class: "row" },
      el("input", { type: "text", value: item.name, "aria-label": "控除の項目名", oninput: (e) => { item.name = e.target.value; renderGrid(); } }),
      sel,
      el("button", { type: "button", class: "remove", onclick: () => { removeItem("deduct", i); } }, "消す"));
  }));
}

function removeItem(group, i) {
  const key = rec.items[group][i].key;
  const used = rec.payments.some((p) => p.rows.some((r) => ((group === "pay" ? r.pay : r.deduct) || {})[key]));
  if (used && !confirm("この項目に金額が入っている支給日があります。項目と金額を消しますか？")) return;
  rec.items[group].splice(i, 1);
  for (const p of rec.payments) for (const r of p.rows) delete (group === "pay" ? r.pay : r.deduct)[key];
  renderItems();
  renderGrid();
}

function renderPeople() {
  $("people").replaceChildren(...rec.people.map((p, i) => el("div", { class: "row" },
    el("input", { type: "text", value: p.name || "", placeholder: "氏名", "aria-label": "氏名", oninput: (e) => { p.name = e.target.value; renderGrid(); } }),
    el("input", { type: "text", value: p.kana || "", placeholder: "フリガナ", "aria-label": "フリガナ", oninput: (e) => { p.kana = e.target.value; } }),
    el("select", { "aria-label": "性別", onchange: (e) => { p.sex = e.target.value; } },
      ...["", "男", "女"].map((s) => el("option", { value: s, ...(p.sex === s ? { selected: "" } : {}) }, s || "性別"))),
    el("select", { "aria-label": "役員・従業員", onchange: (e) => { p.role = e.target.value; } },
      ...["従業員", "役員"].map((s) => el("option", { value: s, ...(p.role === s ? { selected: "" } : {}) }, s))),
    el("button", { type: "button", class: "remove", onclick: () => removePerson(i) }, "消す"))));
}

function removePerson(i) {
  const id = rec.people[i].id;
  if (rec.payments.some((p) => p.rows.some((r) => r.person === id)) && !confirm("この人の記録がある支給日があります。人と記録を消しますか？")) return;
  rec.people.splice(i, 1);
  for (const p of rec.payments) p.rows = p.rows.filter((r) => r.person !== id);
  renderPeople();
  renderGrid();
}

function renderPayments() {
  rec.payments.sort((a, b) => String(a.date).localeCompare(String(b.date)));
  current = Math.min(current, Math.max(rec.payments.length - 1, 0));
  const sel = $("payment-select");
  sel.replaceChildren(...rec.payments.map((p, i) => el("option", { value: i, ...(i === current ? { selected: "" } : {}) },
    `${p.date || "（日付未入力）"} ${p.kind}`)));
  $("remove-payment").disabled = rec.payments.length === 0;
  const pm = rec.payments[current];
  const head = $("payment-head");
  if (!pm) {
    head.replaceChildren(el("p", { class: "note" }, "「支給日を足す」で始めてください。"));
    $("payment-grid").replaceChildren();
    return;
  }
  const field = (label, input) => el("div", { class: "field" }, el("label", {}, label), input);
  head.replaceChildren(
    field("支給日", el("input", { type: "date", value: pm.date || "", onchange: (e) => { pm.date = e.target.value; renderPayments(); } })),
    field("区分", el("select", { onchange: (e) => { pm.kind = e.target.value; renderPayments(); } },
      ...["給与", "賞与"].map((k) => el("option", { value: k, ...(pm.kind === k ? { selected: "" } : {}) }, k)))),
    field("賃金計算期間（始め）", el("input", { type: "date", value: pm.period_start || "", onchange: (e) => { pm.period_start = e.target.value || undefined; } })),
    field("賃金計算期間（終わり）", el("input", { type: "date", value: pm.period_end || "", onchange: (e) => { pm.period_end = e.target.value || undefined; } })));
  renderGrid();
}

function rowOf(pm, personId) {
  let r = pm.rows.find((x) => x.person === personId);
  if (!r) {
    r = { person: personId, pay: {}, deduct: {} };
    pm.rows.push(r);
  }
  return r;
}

function renderGrid() {
  const pm = rec.payments[current];
  const grid = $("payment-grid");
  if (!pm) return grid.replaceChildren();
  const head = el("tr", {}, el("th", {}, "氏名"),
    ...rec.items.pay.map((i) => el("th", {}, i.name)), el("th", {}, "支給計"),
    ...rec.items.deduct.map((i) => el("th", {}, i.name)), el("th", {}, "控除計"), el("th", {}, "差引支給額"),
    ...WORK.map(([, label]) => el("th", {}, label)));
  const body = rec.people.map((p) => {
    const r = rowOf(pm, p.id);
    const sumCell = el("td", { class: "amt" });
    const dedCell = el("td", { class: "amt" });
    const tr = el("tr", {});
    const refresh = () => {
      const pt = Object.values(r.pay).reduce((a, b) => a + (b || 0), 0);
      const dt = Object.values(r.deduct).reduce((a, b) => a + (b || 0), 0);
      sumCell.textContent = yen(pt);
      dedCell.textContent = yen(dt);
      const filled = pt || dt || r.net;
      tr.style.background = filled && r.net !== pt - dt ? "#fdecec" : "";
    };
    const amount = (obj, key) => el("td", {}, el("input", {
      type: "text", class: "num", inputmode: "numeric", size: 9, value: yen(obj[key]),
      oninput: (e) => {
        const v = toInt(e.target.value);
        e.target.style.background = Number.isNaN(v) ? "#fff6f6" : "";
        if (v === null) delete obj[key]; else if (!Number.isNaN(v)) obj[key] = v;
        refresh();
      },
    }));
    tr.append(el("td", {}, `${p.name || "（氏名未入力）"}（${p.role}）`),
      ...rec.items.pay.map((i) => amount(r.pay, i.key)), sumCell,
      ...rec.items.deduct.map((i) => amount(r.deduct, i.key)), dedCell, amount(r, "net"),
      ...WORK.map(([k]) => amount(r, k)));
    refresh();
    return tr;
  });
  grid.replaceChildren(el("thead", {}, head), el("tbody", {}, ...body));
}

// ---- 保存用の形（空の行を除く） ----
function cleaned() {
  const out = JSON.parse(JSON.stringify(rec));
  out.company = $("company").value.trim();
  out.year = Number($("year").value) || out.year;
  for (const p of out.payments) {
    p.rows = p.rows.filter((r) => Object.keys(r.pay).length || Object.keys(r.deduct).length || r.net !== undefined);
    for (const r of p.rows) if (r.net === undefined) r.net = 0;
  }
  return out;
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---- 検算と集計 ----
function table(headers, rows) {
  return el("div", { class: "sheet-scroll" }, el("table", { class: "preview" },
    el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", {}, h)))),
    el("tbody", {}, ...rows.map((r) => el("tr", {}, ...r.map((c) => el("td", typeof c === "number" ? { class: "amt" } : {}, typeof c === "number" ? yen(c) : c)))))));
}

let lastSummary = null;
function renderWithholding() {
  if (!lastSummary) return;
  const list = $("semiannual").checked ? lastSummary.withholding_semiannual : lastSummary.withholding_monthly;
  $("withholding").replaceChildren(table(["期間", "区分", "人員", "課税支給額", "源泉所得税", "支給日"],
    list.map((g) => [g.term, g.class, g.people, g.taxable, g.income_tax, g.dates.join("・")])));
}

async function run() {
  const msgs = $("messages");
  msgs.replaceChildren();
  const res = await call("ot_payroll", [JSON.stringify(cleaned())]);
  if (!res.ok) {
    msgs.append(el("div", { class: "msg error" }, res.message));
    $("results").hidden = true;
    return;
  }
  lastSummary = res.summary;
  lastLedger = res.ledger;
  msgs.append(el("div", { class: "msg ok" }, "検算: すべての行で 差引支給額 ＝ 支給計 − 控除計 です。"));
  renderWithholding();
  const names = Object.fromEntries(rec.items.pay.concat(rec.items.deduct).map((i) => [i.key, i.name]));
  $("annual").replaceChildren(...res.summary.annual.map((a) => el("div", {},
    el("h3", {}, `${a.person.name}（${a.person.role}）`),
    table(["支給日", "区分", "総支給額", "課税支給額", "社会保険料等", "源泉所得税", "住民税", "差引支給額"],
      a.lines.map((l) => [l.date, l.kind, l.pay_total, l.taxable, l.social, l.income_tax, l.resident_tax, l.net])
        .concat([["合計", "", a.total.pay_total, a.total.taxable, a.total.social, a.total.income_tax, a.total.resident_tax, a.total.net]])))));
  $("totals").replaceChildren(table(["支給日", "区分", "役員・従業員", "人員", "支給", "控除", "差引支給額"],
    res.summary.payment_totals.map((t) => [t.date, t.kind, t.role, t.people,
      Object.entries(t.pay).map(([k, v]) => `${names[k] || k} ${yen(v)}`).join("　"),
      Object.entries(t.deduct).map(([k, v]) => `${names[k] || k} ${yen(v)}`).join("　"), t.net])));
  $("ledger").srcdoc = res.ledger;
  $("results").hidden = false;
}

// ---- ボタン ----
function onReady() {
  setStatus("準備ができました。見本を入れるか、記録ファイルを開くか、新しく始めてください。");
  for (const id of ["load-sample", "open-record", "save-record", "new-record", "run"]) $(id).disabled = false;
  rec = emptyRecord();
  renderAll();
}

$("company").addEventListener("input", (e) => { rec.company = e.target.value; });
$("year").addEventListener("input", (e) => { rec.year = Number(e.target.value) || e.target.value; });
$("add-pay-item").addEventListener("click", () => { rec.items.pay.push({ key: newKey("pay", rec.items.pay), name: "", taxable: true }); renderItems(); renderGrid(); });
$("add-ded-item").addEventListener("click", () => { rec.items.deduct.push({ key: newKey("ded", rec.items.deduct), name: "", kind: "other" }); renderItems(); renderGrid(); });
$("add-person").addEventListener("click", () => {
  let n = rec.people.length + 1;
  while (rec.people.some((p) => p.id === `p${n}`)) n++;
  rec.people.push({ id: `p${n}`, name: "", kana: "", sex: "", role: "従業員" });
  renderPeople();
  renderGrid();
});
$("add-payment").addEventListener("click", () => {
  const prev = rec.payments[rec.payments.length - 1];
  rec.payments.push({ date: "", kind: "給与", rows: [] });
  if (prev) for (const r of prev.rows) rec.payments[rec.payments.length - 1].rows.push({ person: r.person, pay: { ...r.pay }, deduct: { ...r.deduct }, net: r.net });
  current = rec.payments.length - 1;
  renderPayments();
  setStatus("前の支給日の金額を写しました。変わったところだけ直してください。");
});
$("remove-payment").addEventListener("click", () => {
  if (!rec.payments[current] || !confirm("この支給日の記録を消しますか？")) return;
  rec.payments.splice(current, 1);
  current = Math.max(current - 1, 0);
  renderPayments();
});
$("payment-select").addEventListener("change", (e) => { current = Number(e.target.value); renderPayments(); });
$("load-sample").addEventListener("click", async () => {
  rec = await call("ot_payroll_sample", []);
  current = 0;
  renderAll();
  setStatus("見本（架空の法人）を入れました。");
});
$("open-record").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (data.format !== "opentax-payroll/1") throw new Error("OpenTax の給与の記録ファイルではありません");
    rec = data;
    current = 0;
    renderAll();
    setStatus(`開きました: ${file.name}`);
  } catch (err) {
    setStatus(`開けませんでした: ${err.message}`, true);
  }
});
$("save-record").addEventListener("click", () => {
  const out = cleaned();
  download(`給与記録_${out.company || "会社名未入力"}_${out.year}.json`, JSON.stringify(out, null, 1) + "\n", "application/json");
});
$("new-record").addEventListener("click", () => {
  if (!confirm("いまの記録を消して新しく始めますか？（保存していない内容は消えます）")) return;
  rec = emptyRecord();
  current = 0;
  renderAll();
  $("results").hidden = true;
});
$("run").addEventListener("click", run);
$("semiannual").addEventListener("change", renderWithholding);
$("download-ledger").addEventListener("click", () => {
  const out = cleaned();
  download(`賃金台帳_${out.company}_${out.year}.html`, lastLedger, "text/html");
});
window.addEventListener("beforeunload", (e) => {
  if (rec && rec.payments.length) e.preventDefault();
});

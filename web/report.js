// 申告書一式の画面。作るのはワーカーの中の Python（opentax.api.report）だけ。結果はこの変数にだけ置く。
"use strict";

const worker = new Worker("worker.js", { type: "module" });
const pending = new Map();
let nextId = 1;
let lastHtml = "";
let lastName = "";

worker.onmessage = (event) => {
  const msg = event.data;
  if (msg.type === "progress") setStatus(msg.message);
  else if (msg.type === "ready") {
    setStatus("準備ができました。ボタンを押してください。");
    document.getElementById("demo").disabled = false;
  } else if (msg.type === "fatal") setStatus(`読み込みに失敗しました: ${msg.message}`, true);
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

function setStatus(text, error) {
  const s = document.getElementById("status");
  s.textContent = text;
  s.classList.toggle("msg", !!error);
  s.classList.toggle("error", !!error);
}

function fitFrame() {
  const frame = document.getElementById("report");
  const doc = frame.contentDocument;
  if (doc && doc.documentElement) frame.style.height = `${doc.documentElement.scrollHeight + 20}px`;
}

function show(html, company) {
  lastHtml = html;
  inlined = null;
  lastName = `申告書一式_${company}.html`;
  const frame = document.getElementById("report");
  frame.onload = () => {
    fitFrame();
    setTimeout(fitFrame, 300);
    frame.contentDocument.addEventListener("toggle", fitFrame, true);   // 「確かめてほしいこと」を開いたとき
    // 目次（#章）を押したら、この画面ごとその章へ動かす（枠の中は高さいっぱいなので、枠の中では動かない）
    frame.contentDocument.addEventListener("click", (e) => {
      const a = e.target.closest("a[href^='#']");
      if (!a) return;
      const target = frame.contentDocument.getElementById(a.getAttribute("href").slice(1));
      if (!target) return;
      e.preventDefault();
      window.scrollTo({ top: frame.getBoundingClientRect().top + window.scrollY + target.offsetTop - 8, behavior: "smooth" });
    });
  };
  frame.srcdoc = html;
  document.getElementById("out").hidden = false;
}

// 様式の画像（このサイトの forms/…）を HTML の中に埋め込む。別のタブ・保存したファイルでも画像が出るように
let inlined = null;
async function selfContained() {
  if (inlined) return inlined;
  const paths = [...new Set([...lastHtml.matchAll(/href='(forms\/[^']+\.jpg)'/g)].map((m) => m[1]))];
  let html = lastHtml;
  for (const p of paths) {
    const buf = new Uint8Array(await (await fetch(p)).arrayBuffer());
    let s = "";
    for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    html = html.split(`href='${p}'`).join(`href='data:image/jpeg;base64,${btoa(s)}'`);
  }
  inlined = html;
  return html;
}

async function blobUrl() {
  return URL.createObjectURL(new Blob([await selfContained()], { type: "text/html" }));
}

document.getElementById("demo").addEventListener("click", async () => {
  setStatus("申告書一式を作っています…");
  const res = await call("ot_demo_report", []);
  if (!res.ok) { setStatus(`作れませんでした: ${res.message}`, true); return; }
  setStatus("作りました。下に順に並んでいます（目次から各章へ飛べます）。");
  show(res.html, res.company);
});
document.getElementById("open-tab").addEventListener("click", async () => {
  if (!lastHtml) return;
  const tab = window.open("", "_blank");   // 押したときに開いておく（後から開くとブラウザに止められる）
  const url = await blobUrl();
  if (tab) tab.location.href = url;
});
document.getElementById("save").addEventListener("click", async () => {
  if (!lastHtml) return;
  const a = document.createElement("a");
  a.href = await blobUrl();
  a.download = lastName;
  document.body.append(a);
  a.click();
  a.remove();
});
window.addEventListener("resize", fitFrame);

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

function blobUrl() {
  return URL.createObjectURL(new Blob([lastHtml], { type: "text/html" }));
}

document.getElementById("demo").addEventListener("click", async () => {
  setStatus("申告書一式を作っています…");
  const res = await call("ot_demo_report", []);
  if (!res.ok) { setStatus(`作れませんでした: ${res.message}`, true); return; }
  setStatus("作りました。下に順に並んでいます（目次から各章へ飛べます）。");
  show(res.html, res.company);
});
document.getElementById("open-tab").addEventListener("click", () => {
  if (lastHtml) window.open(blobUrl(), "_blank", "noopener");
});
document.getElementById("save").addEventListener("click", () => {
  if (!lastHtml) return;
  const a = document.createElement("a");
  a.href = blobUrl();
  a.download = lastName;
  document.body.append(a);
  a.click();
  a.remove();
});
window.addEventListener("resize", fitFrame);

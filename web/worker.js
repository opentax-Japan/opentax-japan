// ブラウザの中で Python（Pyodide）を動かすワーカー。画面の操作を止めないよう、計算は別のスレッドで行う。
// 読み込むのは、このサイトの中のファイルだけ（Pyodide・部品・OpenTax 本体）。外部には通信しない。
import { loadPyodide } from "./pyodide/pyodide.mjs";

let py = null;
const ready = (async () => {
  post({ type: "progress", message: "Python を読み込んでいます（初回は時間がかかります）…" });
  py = await loadPyodide({ indexURL: new URL("./pyodide/", import.meta.url).href });
  const manifest = await (await fetch("build.json")).json();
  for (const wheel of manifest.wheels) {
    post({ type: "progress", message: `部品を読み込んでいます: ${wheel}` });
    py.unpackArchive(await (await fetch(`wheels/${wheel}`)).arrayBuffer(), "wheel");
  }
  post({ type: "progress", message: "OpenTax を読み込んでいます…" });
  py.unpackArchive(await (await fetch("app.zip")).arrayBuffer(), "zip", { extractDir: "/home/pyodide/app" });
  py.runPython('import sys; sys.path.insert(0, "/home/pyodide/app/src")');
  py.runPython(await (await fetch("bridge.py")).text());
  post({ type: "ready", info: JSON.parse(py.globals.get("ot_info")()) });
})().catch((e) => post({ type: "fatal", message: String(e && e.message ? e.message : e) }));

function post(msg) {
  self.postMessage(msg);
}

self.onmessage = async (event) => {
  const { id, cmd, args } = event.data;
  try {
    await ready;
    const fn = py.globals.get(cmd);
    const result = fn(...args);
    post({ type: "result", id, result });
  } catch (e) {
    post({ type: "result", id, result: JSON.stringify({ ok: false, kind: "internal", message: String(e) }) });
  }
};

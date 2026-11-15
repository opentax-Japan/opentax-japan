// 「AI で読む」（DESIGN §24.3）: プログラムで読めない PDF・写真を、利用者自身の Claude API キーで読ませる。
// 既定では何もしない。送るのは、利用者が送る内容（ファイルと指示の全文）を見て同意したときだけ。キーも中身も保存しない。
// AI が返した値は決まった形（opentax-intake/1）の JSON にして読み取り層へ渡し、画面では「AI」の印を付けて出典と並べる。
// 計算は AI を使わない（opentax.api）。
"use strict";

(() => {
  const FIELDS = {
    "company.name": "会社の名前", "company.address": "本店の所在地", "company.representative": "代表者の氏名",
    "company.capital": "資本金（円・整数）", "company.tax_office": "所轄の税務署名（「署」は付けない）",
    "company.corporate_number": "法人番号（13桁）", "fiscal_period.start": "事業年度の初日（YYYY-MM-DD）",
    "fiscal_period.end": "事業年度の末日（YYYY-MM-DD）", "accounting.net_income": "当期純損益（円・整数。損失はマイナス）",
    "accounting.retained_earnings_end": "期末の繰越利益剰余金（円・整数）", "issued_shares": "発行済株式の総数（整数）",
  };
  const PROMPT = [
    "あなたは日本の法人の決算書・申告書の控えから、決まった項目を書き写す係です。計算・推測はしません。",
    "添付の書類に書いてある値だけを、次の項目について写してください。書いていない項目は出さないでください。",
    ...Object.entries(FIELDS).map(([k, v]) => `- ${k}: ${v}`),
    "答えは次の JSON だけを返してください（説明の文は書かない）。where には書類のどこに書いてあったか（ページ・表の名前・欄の名前）を書きます。",
    '{"values": {"company.name": {"value": "…", "where": "1ページ 表題"}}}',
  ].join("\n");
  let targets = [];

  function $(id) { return document.getElementById(id); }
  function mediaType(f) {
    const n = f.name.toLowerCase();
    if (n.endsWith(".pdf")) return "application/pdf";
    if (n.endsWith(".png")) return "image/png";
    if (n.endsWith(".webp")) return "image/webp";
    if (n.endsWith(".gif")) return "image/gif";
    return "image/jpeg";
  }

  function setTargets(files) {
    targets = files;
    $("ai-consent").hidden = true;
    $("ai-agree").checked = false;
    $("ai-send").disabled = true;
    $("ai-paste-file").replaceChildren(...targets.map((f, i) => {
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = f.name;
      return o;
    }));
    $("ai-paste").value = "";
    $("ai-paste-result").textContent = "";
  }

  // 返ってきた文から JSON を取り出し、決まった項目だけを残す（API の答えも、チャットに貼った答えも同じ）
  function pickValues(text) {
    const m = String(text || "").match(/\{[\s\S]*\}/);
    const got = m ? JSON.parse(m[0]) : {};
    const values = {};
    for (const [k, v] of Object.entries(got.values || {})) if (k in FIELDS && v && "value" in v) values[k] = { value: v.value, where: String(v.where || "") };
    return values;
  }

  // API キーがないとき: 利用者が自分の AI のチャット（Claude・ChatGPT など）でファイルと指示を送り、答えを貼る。この画面からは何も送らない
  $("ai-copy").addEventListener("click", async () => {
    const out = $("ai-paste-result");
    try {
      await navigator.clipboard.writeText(PROMPT);
      out.textContent = "指示をコピーしました。AI のチャットにファイルを添付して貼り付けてください。";
    } catch (e) {
      $("ai-prompt").textContent = PROMPT;
      $("ai-consent").hidden = false;
      out.textContent = "コピーできませんでした。上の「AI への指示（全文）」を開いて写してください。";
    }
  });
  $("ai-paste-apply").addEventListener("click", async () => {
    const out = $("ai-paste-result");
    const f = targets[Number($("ai-paste-file").value)];
    if (!f) { out.textContent = "どのファイルの答えかを選んでください。"; return; }
    let values;
    try {
      values = pickValues($("ai-paste").value);
    } catch (e) {
      out.textContent = `答えを読めませんでした（{ で始まる JSON を貼ってください）: ${e.message}`;
      return;
    }
    if (!Object.keys(values).length) { out.textContent = "答えに決まった項目がありませんでした。指示をそのまま貼ったか確かめてください。"; return; }
    const frag = { format: "opentax-intake/1", read_by: "ai", model: "利用者の AI チャット", source_file: f.name, values };
    await window.OpenTaxEasy.addAiResult(`${f.name}.AIが読んだ値.json`, JSON.stringify(frag));
    $("ai-paste").value = "";
    out.textContent = `${Object.keys(values).length} 項目を入れました。上の「読み取った内容」で、AI の印の付いた値を確かめてください。`;
  });

  $("ai-preview").addEventListener("click", () => {
    $("ai-files").replaceChildren(...targets.map((f) => {
      const li = document.createElement("li");
      li.textContent = `${f.name}（${Math.round((f.size || f.b64.length * 0.75) / 1024)} KB）`;
      return li;
    }));
    $("ai-prompt").textContent = PROMPT;
    $("ai-consent").hidden = false;
  });
  $("ai-agree").addEventListener("change", (e) => { $("ai-send").disabled = !e.target.checked; });

  $("ai-send").addEventListener("click", async () => {
    const key = $("ai-key").value.trim();
    const out = $("ai-result");
    if (!key) { out.textContent = "API キーを入れてください。"; return; }
    if (!$("ai-agree").checked) return;
    $("ai-send").disabled = true;
    const model = $("ai-model").value;
    for (const f of targets) {
      out.textContent = `${f.name} を読ませています…`;
      const mt = mediaType(f);
      const block = mt === "application/pdf"
        ? { type: "document", source: { type: "base64", media_type: mt, data: f.b64 } }
        : { type: "image", source: { type: "base64", media_type: mt, data: f.b64 } };
      try {
        const res = await fetch("https://api.anthropic.com/v1/messages", {
          method: "POST",
          headers: { "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01",
            "anthropic-dangerous-direct-browser-access": "true" },
          body: JSON.stringify({ model, max_tokens: 2000, messages: [{ role: "user", content: [block, { type: "text", text: PROMPT }] }] }),
          referrerPolicy: "no-referrer", credentials: "omit",
        });
        const body = await res.json();
        if (!res.ok) throw new Error(body.error && body.error.message ? body.error.message : `HTTP ${res.status}`);
        const text = (body.content || []).filter((c) => c.type === "text").map((c) => c.text).join("");
        const values = pickValues(text);
        const frag = { format: "opentax-intake/1", read_by: "ai", model, source_file: f.name, values };
        await window.OpenTaxEasy.addAiResult(`${f.name}.AIが読んだ値.json`, JSON.stringify(frag));
      } catch (e) {
        out.textContent = `${f.name} は読めませんでした: ${e.message}`;
        $("ai-send").disabled = false;
        return;
      }
    }
    out.textContent = "読みました。上の「読み取った内容」で、AI の印の付いた値を確かめてください。";
    $("ai-key").value = "";
  });

  window.OpenTaxAI = { setTargets, PROMPT, FIELDS };
})();

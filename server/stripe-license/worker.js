// OpenTax のライセンスキーを、Stripe のカード決済が済んだ人に渡す小さなサーバー（Cloudflare Workers。DESIGN §33.3）。
//
// 流れ: 料金のページ → Stripe の支払いリンク（カード） → 決済が済むと Stripe がここへ戻す
//       /license?session_id=cs_... → Stripe に「本当に払われたか」を問い合わせる → 署名したキーを画面に出す
//
// データベースは持たない。キーの中身は Stripe の決済（Checkout Session）だけから決まり、Ed25519 の署名は
// 同じ中身なら同じになるので、同じページを開き直しても同じキーが出る。カード番号はここを通らない（Stripe の画面で入れる）。
//
// 環境変数（wrangler secret / vars）:
//   STRIPE_SECRET_KEY       … Stripe の制限付きキー（Checkout Sessions の読み取りだけ許す rk_live_...）
//   LICENSE_SECRET_HEX      … ライセンスの秘密鍵（32バイトの16進。リポジトリに入れない）
//   LICENSE_KID             … 秘密鍵の番号（src/opentax/license の PUBLIC_KEYS と同じ。例 k1）
//   LICENSE_PUBLIC_HEX      … その番号の公開鍵（PUBLIC_KEYS と同じ値）。署名したらこれで確かめてから出す
//   ALLOW_TEST_MODE         … "1" のときだけテストモードの決済（cs_test_）でもキーを出す。テストは本番と別の鍵・kid で行う
//   PLAN_BY_PAYMENT_LINK    … 支払いリンクと製品の対応（JSON。例 {"plink_123":{"plan":"platinum","days":365}}）
//   SITE_URL                … 使い方のページ（例 https://opentax-japan.github.io/opentax-japan/）

const PKCS8_ED25519_PREFIX = "302e020100300506032b657004220420";
// 売っている製品（src/opentax/license の PLANS と同じ名前だけ）
const ALLOWED_PLANS = ["platinum"];
const CONTACT = '<p>キーが出ないとき・画面を閉じてしまったときは、お支払いの領収メールを添えて、<a href="https://opentax-japan.github.io/opentax-japan/pricing.html">料金のページ</a>の連絡先へお問い合わせください。決済番号からキーを出し直します。</p>';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/license") return licensePage(url, env, globalThis.fetch);
    return new Response("Not Found", { status: 404 });
  },
};

export async function licensePage(url, env, fetchFn) {
  const sessionId = url.searchParams.get("session_id") || "";
  if (!/^cs_(test|live)_[A-Za-z0-9]+$/.test(sessionId)) {
    return html(400, "決済の番号が読めません", "<p>支払いのあとに開いた画面のアドレスを、そのまま開き直してください。</p>", env);
  }
  let session;
  try {
    session = await stripeSession(sessionId, env, fetchFn);
  } catch (e) {
    return html(502, "決済の確認ができませんでした", `<p>時間をおいて、この画面を開き直してください。続くときはお問い合わせください（${esc(e.message)}）。</p>`, env);
  }
  if (session.livemode !== true && env.ALLOW_TEST_MODE !== "1") {
    return html(403, "テストの決済ではキーを出しません", "<p>本番の支払いページからお支払いください。</p>", env);
  }
  // 0円（100%引き・試用）や継続課金ではキーを出さない。無料で渡すときは opentax license issue で手で出す
  if (session.mode !== "payment" || session.payment_status === "no_payment_required") {
    return html(402, "この決済ではキーを出せません", "<p>0円の決済・継続課金の決済ではキーを出していません。</p>" + CONTACT, env);
  }
  if (session.status !== "complete" || session.payment_status !== "paid") {
    return html(402, "決済がまだ済んでいません", "<p>カードの決済が終わってから、この画面を開き直してください。</p>" + CONTACT, env);
  }
  let key, payload;
  try {
    ({ key, payload } = await licenseFromSession(session, env));
  } catch (e) {
    return html(500, "キーを作れませんでした", `<p>お手数ですがお問い合わせください（決済番号の末尾 ${esc(sessionId.slice(-8))}・${esc(e.message)}）。</p>` + CONTACT, env);
  }
  const body = `
<p><b>${esc(payload.office)}</b> 様、ありがとうございます。下のライセンスキーを OpenTax の画面の「ライセンスキー」の欄に貼り付けてください。</p>
<textarea id="k" readonly rows="5">${esc(key)}</textarea>
<p><button type="button" onclick="var b=this;navigator.clipboard.writeText(document.getElementById('k').value).then(function(){b.textContent='写しました'},function(){b.textContent='写せませんでした。上の文字を選んで写してください'})">キーを写す</button>
<a download="OpenTaxライセンスキー.txt" href="data:text/plain;charset=utf-8,${encodeURIComponent(key)}">キーをファイルで保存</a></p>
<table>
<tr><th>製品</th><td>${esc(payload.plan)}</td></tr>
<tr><th>使う人の数</th><td>${esc(payload.seats)}</td></tr>
<tr><th>有効期限</th><td>${esc(payload.exp)} まで</td></tr>
<tr><th>発行番号</th><td>${esc(payload.id)}</td></tr>
</table>
<p>このキーは控えておいてください（この画面はあとで開き直しても同じキーが出ます）。領収書は Stripe からメールで届きます。</p>
${CONTACT}
<p><a href="${esc(/^https:\/\//.test(env.SITE_URL || "") ? env.SITE_URL : "https://opentax-japan.github.io/opentax-japan/")}">OpenTax へ</a></p>`;
  return html(200, "ライセンスキー", body, env);
}

async function stripeSession(sessionId, env, fetchFn) {
  const r = await fetchFn(`https://api.stripe.com/v1/checkout/sessions/${sessionId}?expand[]=line_items`, {
    headers: { Authorization: `Bearer ${env.STRIPE_SECRET_KEY}` },
  });
  if (!r.ok) throw new Error(`Stripe ${r.status}`);
  return r.json();
}

export async function licenseFromSession(session, env) {
  const plans = JSON.parse(env.PLAN_BY_PAYMENT_LINK || "{}");
  const conf = plans[session.payment_link];
  if (!conf) throw new Error("支払いリンクが製品に結びついていません");
  if (!ALLOWED_PLANS.includes(conf.plan)) throw new Error(`知らない製品です: ${conf.plan}`);
  const days = conf.days === undefined ? 365 : conf.days;
  if (!Number.isInteger(days) || days < 1) throw new Error("有効日数の設定が違います");
  const perUnit = conf.seats_per_unit === undefined ? 1 : conf.seats_per_unit;
  if (!Number.isInteger(perUnit) || perUnit < 1) throw new Error("1つあたりの人数の設定が違います");
  const office = officeName(session);
  if (!office) throw new Error("事務所名がありません");
  const created = jstDate(session.created * 1000);
  const exp = jstDate(session.created * 1000 + days * 86400000);
  // 使う人の数＝ライセンスの明細（conf.price があればその価格の明細）の数量。準備費などほかの明細は数えない
  const items = (session.line_items && session.line_items.data) || [];
  const item = conf.price ? items.find((i) => i.price && i.price.id === conf.price) : items[0];
  if (!item) throw new Error("ライセンスの明細がありません");
  const quantity = Number.isInteger(item.quantity) && item.quantity >= 1 ? item.quantity : 1;
  const payload = {
    v: 1, kid: env.LICENSE_KID || "k1", id: (await sha256hex(session.id)).slice(0, 16), plan: conf.plan,
    office, seats: quantity * perUnit, iat: created, exp,
  };
  return { key: await sign(payload, env.LICENSE_SECRET_HEX, env.LICENSE_PUBLIC_HEX), payload };
}

function officeName(session) {
  // 支払いリンクに「事務所名」の入力欄（custom_fields の key: office）を付けておく。無ければ請求先の名前
  for (const f of session.custom_fields || []) {
    if (f.key === "office" && f.text && f.text.value) return f.text.value.trim();
  }
  const d = session.customer_details || {};
  return (d.business_name || d.name || "").trim();
}

function jstDate(ms) {
  return new Date(ms + 9 * 3600000).toISOString().slice(0, 10);
}

// Python 側（json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)）と同じ並びにする。
// 署名は送ったバイト列そのものを確かめるので一致は要らないが、同じ中身なら同じキーになるよう順を決めておく。
export function canonical(payload) {
  const o = {};
  for (const k of Object.keys(payload).sort()) o[k] = payload[k];
  return JSON.stringify(o);
}

export async function sign(payload, secretHex, publicHex) {
  const secret = String(secretHex || "").trim();
  const pub = String(publicHex || "").trim();
  if (!/^[0-9a-fA-F]{64}$/.test(secret)) throw new Error("秘密鍵の設定が16進64文字ではありません");
  if (!/^[0-9a-fA-F]{64}$/.test(pub)) throw new Error("公開鍵の設定が16進64文字ではありません");
  const bytes = new TextEncoder().encode(canonical(payload));
  const key = await crypto.subtle.importKey("pkcs8", hex(PKCS8_ED25519_PREFIX + secret), { name: "Ed25519" }, false, ["sign"]);
  const msg = new Uint8Array([...new TextEncoder().encode("OTX1."), ...bytes]);
  const sig = new Uint8Array(await crypto.subtle.sign({ name: "Ed25519" }, key, msg));
  // 秘密鍵と kid の取り違えで「払ったのに使えないキー」を出さないよう、公開鍵で確かめてから出す
  const pk = await crypto.subtle.importKey("raw", hex(pub), { name: "Ed25519" }, false, ["verify"]);
  if (!(await crypto.subtle.verify({ name: "Ed25519" }, pk, sig, msg))) throw new Error("秘密鍵と公開鍵が組になっていません");
  return `OTX1-${b64url(bytes)}.${b64url(sig)}`;
}

async function sha256hex(text) {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  return [...d].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function hex(s) {
  return new Uint8Array(s.match(/../g).map((h) => parseInt(h, 16)));
}

function b64url(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function esc(v) {
  return String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function html(status, title, body, env) {
  const page = `<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>OpenTax ${esc(title)}</title>
<style>body{font-family:sans-serif;max-width:40em;margin:2em auto;padding:0 1em;line-height:1.7}textarea{width:100%;font-family:monospace;word-break:break-all}th{text-align:left;padding-right:1em}</style>
</head><body><h1>OpenTax ${esc(title)}</h1>${body}</body></html>`;
  return new Response(page, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "referrer-policy": "no-referrer",
      "x-robots-tag": "noindex",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
      "x-content-type-options": "nosniff",
      "x-frame-options": "DENY",
    },
  });
}

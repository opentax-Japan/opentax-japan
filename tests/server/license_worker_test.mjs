// サーバー（server/stripe-license/worker.js）を Stripe の代わりの偽の応答で動かす。
// 使い方: node tests/server/license_worker_test.mjs <秘密鍵の16進> <公開鍵の16進>
//   → 標準出力にキーを1行出す（Python 側で確かめる）。どれかの確かめが外れたら例外で終わる
import { licensePage } from "../../server/stripe-license/worker.js";

const [secret, pub] = process.argv.slice(2);
const baseEnv = {
  STRIPE_SECRET_KEY: "rk_test_x", LICENSE_SECRET_HEX: secret, LICENSE_PUBLIC_HEX: pub, LICENSE_KID: "test",
  PLAN_BY_PAYMENT_LINK: JSON.stringify({ plink_1: { plan: "platinum", days: 365 }, plink_bad: { plan: "PLATINUM" }, plink_days: { plan: "platinum", days: "365" },
    plink_unit: { plan: "platinum", seats_per_unit: 0.5 }, plink_price: { plan: "platinum", price: "price_2" } }),
  SITE_URL: "https://example.invalid/",
};
const session = (over) => Object.assign({
  id: "cs_test_abc", livemode: true, mode: "payment", status: "complete", payment_status: "paid", payment_link: "plink_1",
  created: 1795132800, custom_fields: [{ key: "office", text: { value: "見本<税理士>事務所" } }],
  line_items: { data: [{ quantity: 2, price: { id: "price_1" } }] },
}, over);
const fake = (s, status = 200) => async (u, init) => {
  if (!u.startsWith("https://api.stripe.com/v1/checkout/sessions/cs_test_abc") || init.headers.Authorization !== "Bearer rk_test_x") throw new Error("bad call");
  return { ok: status === 200, status, json: async () => s };
};
const get = async (s, { id = "cs_test_abc", env = {}, status = 200 } = {}) =>
  licensePage(new URL(`https://w.invalid/license?session_id=${id}`), { ...baseEnv, ...env }, fake(s, status));
const expect = async (name, res, code) => {
  if (res.status !== code) throw new Error(`${name}: ${res.status} (期待 ${code}) ${await res.text()}`);
  return res;
};

const ok = await expect("ok", await get(session({})), 200);
const text = await ok.text();
if (!text.includes("見本&lt;税理士&gt;事務所")) throw new Error("事務所名のエスケープ");
if (ok.headers.get("x-content-type-options") !== "nosniff" || !ok.headers.get("content-security-policy").includes("frame-ancestors 'none'")) throw new Error("headers");
const key = text.match(/OTX1-[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+/)[0];
if (!(await (await get(session({}))).text()).includes(key)) throw new Error("同じ決済で違うキー");
await expect("unpaid", await get(session({ payment_status: "unpaid" })), 402);
await expect("0円", await get(session({ payment_status: "no_payment_required" })), 402);
await expect("open", await get(session({ status: "open" })), 402);
await expect("継続課金", await get(session({ mode: "subscription" })), 402);
await expect("テストモード", await get(session({ livemode: false })), 403);
await expect("テストモード許可", await get(session({ livemode: false }), { env: { ALLOW_TEST_MODE: "1" } }), 200);
await expect("知らないリンク", await get(session({ payment_link: "plink_other" })), 500);
await expect("知らない製品", await get(session({ payment_link: "plink_bad" })), 500);
await expect("事務所名なし", await get(session({ custom_fields: [], customer_details: {} })), 500);
await expect("鍵の取り違え", await get(session({}), { env: { LICENSE_PUBLIC_HEX: "11".repeat(32) } }), 500);
await expect("秘密鍵が16進でない", await get(session({}), { env: { LICENSE_SECRET_HEX: "zz" + secret.slice(2) } }), 500);
await expect("Stripe 404", await get(session({}), { status: 404 }), 502);
await expect("日数が文字列", await get(session({ payment_link: "plink_days" })), 500);
await expect("人数の設定が小数", await get(session({ payment_link: "plink_unit" })), 500);
await expect("価格の明細が無い", await get(session({ payment_link: "plink_price" })), 500);
const priced = await (await expect("価格で明細を選ぶ", await get(session({ payment_link: "plink_price",
  line_items: { data: [{ quantity: 9, price: { id: "price_1" } }, { quantity: 3, price: { id: "price_2" } }] } })), 200)).text();
if (!priced.includes("<tr><th>使う人の数</th><td>3</td></tr>")) throw new Error("price で明細を選ぶ");
await expect("番号が変", await get(session({}), { id: "x<script>" }), 400);
const byName = await (await expect("名前に落ちる", await get(session({ custom_fields: [], customer_details: { name: "山田事務所" } })), 200)).text();
if (!byName.includes("山田事務所")) throw new Error("customer_details.name");
console.log(key);

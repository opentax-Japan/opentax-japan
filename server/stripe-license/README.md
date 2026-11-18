# OpenTax ライセンス発行サーバー（Stripe のカード決済 → ライセンスキー）

設計は DESIGN.md §33。データベースを持たない Cloudflare Workers 1本。

```
料金のページ（web/pricing.html）
  → Stripe の支払いリンク（カード番号は Stripe の画面で入れる）
  → 決済が済むと https://<このサーバー>/license?session_id={CHECKOUT_SESSION_ID} へ戻る
  → Stripe に「払われたか」を問い合わせ、Ed25519 で署名したキーを画面に出す
  → 買った人は PLATINUM の画面にキーを貼る（画面は通信せず、公開鍵だけで確かめる）
```

## 始めるときに人がすること（1回だけ）

1. Stripe のアカウントを作り、本人確認・振込先口座を登録する（事業者の情報は人が入れる）
2. 商品「OpenTax PLATINUM（1年）」と価格を作り、**支払いリンク**を作る
   - 入力欄を足す: キー `office`・表示名「事務所名」・テキスト・必須
   - 数量を変えられるようにする（使う人の数）
   - 一回払い（継続課金にしない）。プロモーションコードは許可しない（0円の決済ではキーを出さない）
   - Stripe の設定で、支払いページに返品不可・利用規約・特定商取引法の表記（web/pricing.html）へのリンクが見えるようにする（最終確認画面の表示）
   - 支払い後の動作: 「自分のサイトへ移動」→ `https://<このサーバー>/license?session_id={CHECKOUT_SESSION_ID}`
3. Stripe で**制限付きキー**を作る（Checkout Sessions の読み取りだけ）
4. Cloudflare にこのサーバーを置く
   ```
   npx wrangler secret put STRIPE_SECRET_KEY      # 3 の rk_live_...
   npx wrangler secret put LICENSE_SECRET_HEX     # 秘密鍵（opentax license keygen で作ったファイルの中身）
   # wrangler.toml の LICENSE_PUBLIC_HEX はその秘密鍵の公開鍵（組でないとキーを出さずに止まる）
   # wrangler.toml の PLAN_BY_PAYMENT_LINK に {"plink_...":{"plan":"platinum","days":365}} を書く
   npx wrangler deploy
   ```
5. web/pricing.html に値段・特定商取引法に基づく表記・支払いリンクを書き、src/opentax/license の
   REQUIRED を True にする（キーがないと PLATINUM を止める）

テストモード（cs_test_ / rk_test_）で 2〜4 を先に通しておく。**テストは本番と別の鍵で**行う
（`opentax license keygen` で作った試し用の鍵・kid は test など PUBLIC_KEYS に無い番号、`ALLOW_TEST_MODE = "1"`）。
本番の鍵のままでは、テストモードの決済にはキーを出さない（livemode を見る）。

## キーが出なかった・画面を閉じた人へ

Stripe の管理画面で、その人の決済の Checkout Session（cs_live_…）を探し、
`https://<このサーバー>/license?session_id=cs_live_…` を開いて出たキーを渡す（同じ決済からは同じキーが出る）。

## 返金・チャージバックのとき

キーは返金しても期限まで使える（通信しないため）。止めるときは発行番号を src/opentax/license の REVOKED に足して版を出す。
このサーバーは返金を見ないので、同じページからキーは出続ける（返金した決済の番号は人に渡さない）。

## 手で発行する（振込で買った人・試用の人）

```
opentax license issue --secret-file <秘密鍵> --office "○○税理士事務所" --seats 3 --days 365
opentax license verify <キー> --product platinum
```

## 守ること

- 秘密鍵はリポジトリ・共有フォルダに置かない。**漏れたら**、漏れた鍵では誰でも好きな期限のキーを作れるので、
  `opentax license keygen` で新しい鍵（k2）を作る → PUBLIC_KEYS から k1 を消して k2 を足し、版を出す →
  サーバーの LICENSE_KID・LICENSE_SECRET_HEX・LICENSE_PUBLIC_HEX を k2 にする →
  買った人へ、Stripe の決済番号から /license を開いて k2 のキーを出し直して渡す
- このサーバーはカード番号を受け取らない。Stripe から読むのは決済の状態・事務所名・数量だけで、どこにも保存しない
- 確認: `node tests/server/license_worker_test.mjs <秘密鍵の16進> <公開鍵の16進>`（Stripe の代わりの偽の応答で動かす）

# OpenTax Japan

日本の法人税申告書（別表）の e-Tax 用データを作るオープンソースです。
特定の税務ソフトに頼らず、国税庁が公開している e-Tax 仕様書（XML構造設計書・帳票フィールド仕様書・XMLスキーマ・帳票間連動仕様書）を一次資料にします。

> **開発中です。** まだ申告には使えません。

## 免責

- **計算結果・出力データの正しさは保証しません。**
- **申告の内容と責任は利用者にあります。** 提出前に必ず利用者自身で確認してください。
- このソフトは国税庁と関係がありません。

## 対象（最初の版）

- 税目: 法人税（内国法人の確定申告・青色、手続 RHO0012）
- 帳票: 別表一・別表四・別表五(一)・別表五(二)・別表十五
- 出力: e-Tax ソフト（WEB版）で読み込める `.xtx`（電子署名・送信は e-Tax 側で行う）

対象外: 地方税（eLTAX）・消費税・所得税・電子署名・送信・GUI

## 国税庁の仕様書について

仕様書は国税庁の著作物のため、このリポジトリには含めていません。
取得スクリプトが国税庁のサイトからダウンロードし、`.cache/` に置きます。取得したファイルは SHA256 で照合します。

## 使い方（予定）

```
opentax fetch-spec --set ksk2-2026-08     # 仕様書の取得と照合
opentax calculate company.json            # 別表の計算
opentax export-etax company.json          # .xtx の出力
opentax validate company.xtx              # 公式XSDで検証
```

## 設計

- [DESIGN.md](DESIGN.md) — 全体設計
- [docs/etax/ETAX_SPEC_DESIGN.md](docs/etax/ETAX_SPEC_DESIGN.md) — e-Tax 仕様の取込み

## 謝辞

e-Tax 仕様の取込みの設計は [AI BOOKS](https://github.com/knishioka/ai-books)（MIT）を参考にしています。詳しくは [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## ライセンス

[MIT](LICENSE)

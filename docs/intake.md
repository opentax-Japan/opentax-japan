# 読み取り結果の JSON（opentax-intake/1）

OpenTax は「読み取り層」と「計算層」を分けています（DESIGN.md §24）。
PDF・写真・形のばらばらな表のように、プログラムで読めないものは AI（または人）が読み、次の形の JSON にして
かんたん入口（Web 画面）に落とすか `opentax intake` に渡します。計算は AI を使わずに opentax.api が行います。

```json
{
  "format": "opentax-intake/1",
  "read_by": "ai",
  "model": "読んだモデルの名前",
  "source_file": "前期の決算書.pdf",
  "values": {
    "company.name": {"value": "○○株式会社", "where": "1ページ 表題"},
    "fiscal_period.end": {"value": "2026-09-30", "where": "1ページ 事業年度"},
    "accounting.net_income": {"value": -1200000, "where": "損益計算書 当期純損失"}
  }
}
```

- `read_by` は `ai`（AI が読んだ）か `person`（人が読んで書いた）。画面では AI が読んだ値に「AI」の印を付け、`source_file` と `where` を出典として並べます。
- `values` の鍵は入力 JSON の場所（`company.name` など。src/opentax/red/model.py の SCHEMA）。金額は円の整数、日付は `YYYY-MM-DD`。
- 書類に書いていない値は書かない（推測で埋めない）。推測した値は `"weak": true` を付けます（ほかに読めた値があればそちらが使われます）。
- ほかのファイルと値が違えば、画面で候補として並び、人が選びます。

### 法人税以外の書類（個人・相続・事業所税・宿泊税）

`"kind"` に申告の種類を書くと、その申告の入力に入ります（`values` の鍵はその入力 JSON の場所）。`"document"` に書類の名前を書くと、
画面の必要書類に ✓ が付きます。

```json
{
  "format": "opentax-intake/1", "read_by": "ai", "kind": "personal", "document": "源泉徴収票",
  "source_file": "給与所得の源泉徴収票.pdf",
  "values": {"salary.income": {"value": 5000000, "where": "支払金額"}}
}
```

- `kind`: `personal`（個人の所得税。入力の形は tests/cases/kokuzei-taro-kojin/input.json）・`souzoku`（相続税。tests/cases/kokuzei-taro-souzoku/input.json）・
  `jigyosho`（事業所税）・`shukuhaku`（宿泊税。施設は `facility.name` など）
- `document` の例: 源泉徴収票・控除証明書・医療費・特定口座年間取引報告書・寄附金の受領証明書

## 事務所で Claude Code に読ませる手順

1. PDF・写真を1つのフォルダに入れる。
2. Claude Code に「docs/intake.md の形で、このフォルダの書類から読める値を JSON にして」と頼み、`<書類名>.AIが読んだ値.json` を作らせる。
3. その JSON と、会計ソフトの CSV・前期の .xtx を一緒にかんたん入口へ落とす（または `opentax intake <フォルダ> -o 下書き.json`）。
4. 画面の「読み取った内容」で、AI の印の付いた値を出典と見比べて確かめる。

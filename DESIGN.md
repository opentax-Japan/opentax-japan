# OpenTax Japan — DESIGN.md（v0.1 案・承認待ち）

作成 2026-09-30。承認前は本実装に入らない。
【確認】＝一次資料で確認済み／【未確認】＝裏取り未了（実装前に確認する）。

---

## 0. 承認していただきたい判断（先にここだけ）

| # | 論点 | 案 |
|---|---|---|
| A | 言語 | Python 3.12。金額はすべて `int`（円）。`float` 禁止 |
| B | ライセンス | MIT（決定） |
| C | 別表四の様式 | MVPは **簡易様式 HOA420** を出力。内部モデルは総括表 HOA410 と共通 |
| D | 「年度」の軸 | 税法ルールは **事業年度開始日** で選ぶ。e-Tax様式は **提出時点の仕様版** で選ぶ。2軸に分ける（§4.3） |
| E | 置き場所 | 開発はGitHubリポジトリ＋ローカルclone。Googleドライブ上で git 運用はしない（同期で .git が壊れる）。この DESIGN.md だけ仮置き |
| F | 参考OSS | **AI BOOKS＝knishioka/ai-books（MIT）** を主な参考にする。コード流用はライセンス確認後。aoiko（AGPL-3.0）は設計参考のみ（§2、docs/etax/ETAX_SPEC_DESIGN.md） |

---

## 1. 国税庁 法人税仕様の調査結果

### 1.1 公開場所【確認】
- 入口: https://www.e-tax.nta.go.jp/shiyo/index.htm
- 現行版一覧（R8.5.18更新）: https://www.e-tax.nta.go.jp/shiyo/shiyo3.htm
- **KSK2対応版**（R8.8.28更新）: https://www.e-tax.nta.go.jp/shiyo/ksk2/ksk2_shiyo3.htm
- 公開予定: https://www.e-tax.nta.go.jp/shiyo/keikaku.htm
  第3回＝事前情報 9/24、**仕様公開 10/30**、ソフト更新 11/25 → 10/30版で差分確認が必要
- 「2026年8月版」＝KSK2対応版の 8/12・8/28 公開分に当たる（「8月版」という名称の版はない）

### 1.2 使うファイル（すべてCAB。中身は xlsx / xsd / docx）【確認】
URL: `https://www.e-tax.nta.go.jp/shiyo/ksk2/download/<名前>.CAB`

| 名前 | 内容 | 版 | MVPでの用途 |
|---|---|---|---|
| e-tax10 | XML構造設計書・帳票フィールド仕様書【法人税】 | R8.8.12 / 35.1MB | 項目名・桁・計算式の対応表を作る |
| e-tax19 | XMLスキーマ | R8.8.28 / 9.4MB | XML生成と検証 |
| e-tax08 | 帳票間連動仕様書 | R8.8.12 | 別表間の転記ルールをテストに変換 |
| e-tax07 | 手続一覧・手続内帳票対応表 | R8.8.28 | 手続ID↔帳票IDの対応 |
| e-tax02-1 | API仕様書 | R8.8.12 | MVP対象外（送信はWEB版で手動） |
| e-tax03/04/05 | 電子署名・送受信モジュール | — | MVP対象外 |

### 1.3 帳票ID・手続ID【確認：KSK2版スキーマ】

| 帳票 | 帳票ID | スキーマ | VR |
|---|---|---|---|
| 別表一 | HOA112（次葉 HOA116） | HOA112-007.xsd | 7.0 |
| 別表四（総括表） | HOA410 | HOA410-025.xsd | 25.0 |
| 別表四（簡易様式） | HOA420 | HOA420-025.xsd | 25.0 |
| 別表五(一) | HOA511 | HOA511-016.xsd | 16.1 |
| 別表五(二) | HOA522 | HOA522-010.xsd | 10.0 |
| 別表十五 | HOE200 | HOE200-015.xsd | 15.0 |

- 手続: **RHO0012**＝内国法人の確定申告（青色）、RHO0012-260.xsd、VR="26.0.1"。白色 RHO0022、予定 RHO0061
- 名前空間: `http://xml.e-tax.nta.go.jp/XSD/hojin`（共通は `.../XSD/general`）

### 1.4 .xtx の構造【確認：RHO0012-260.xsd】
```
DATA
 ├─ RHO0012
 │   ├─ CATALOG            … 管理部
 │   └─ CONTENTS (id, VR 必須)
 │        ├─ IT            … 共通情報（法人番号・納税地・事業年度 等）
 │        ├─ HOA112 / HOA420 / HOA511 / HOA522 / HOE200 …
 │        └─ TENPU         … XBRL（財務諸表）・CSV
 └─ dsig:Signature (0..n)  … MVPでは付けない。WEB版で署名
```
- e-Tax WEB版は民間ソフト作成の .xtx を読込・署名・送信できる【確認】
  https://www.e-tax.nta.go.jp/toiawase/qa/e-taxweb/49.htm
- 財務諸表（TENPU/XBRL）はMVP対象外。WEB版で別途添付する運用とする【未確認：XBRLなしで読込エラーにならないか Phase 6 で試す】

### 1.5 利用条件【確認】
- 「各仕様書の内容は著作権の対象」。ただしソフト開発・市場供給は妨げない（shiyo/index.htm#anc04）
- 再配布の可否は明記なし → **リポジトリに同梱しない**。取得スクリプト＋SHA256台帳のみをコミット

### 1.6 税率等の一次情報（ルールファイルの出典欄に書く）

| 項目 | 値 | 状態 | 出典 |
|---|---|---|---|
| 中小法人 年800万円以下 | 15%（所得10億円超の事業年度は17%） | 【確認】R7.4.1以後開始事業年度 | nta.go.jp taxanswer 5759 / kaisei_gaiyo2025 F.pdf |
| 同 適用期限 | R9.3.31までに開始する事業年度 | 【未確認】民間解説のみ | — |
| 本則税率 | 23.2% | 【未確認】今回未取得 | — |
| 地方法人税 | 10.3% | 【確認】R1.10.1以後開始課税事業年度 | nta.go.jp chihou_hojin/01.htm |
| 交際費 定額控除限度額 | 800万円（月数按分） | 【確認】 | taxanswer 5265 |
| 接待飲食費の除外基準 | 1人当たり10,000円以下 | 【確認】R6.4.1以後支出 | kaisei_gaiyo2024 J.pdf |
| 令和8年度改正 | 交際費・軽減税率の改正なし | 【推測】大綱PDFに該当語なし | mof.go.jp fy2026 taikou.pdf |

**【未確認】の項目は、実装前に一次資料を取り直してからルールファイルに入れる。**

---

## 2. 既存OSS調査

| リポジトリ | ライセンス | 内容 | 扱い |
|---|---|---|---|
| **knishioka/ai-books**（AI BOOKS） | **MIT** | Python。所得税 青色申告決算書 KOA210/220/240 の manifest・field_catalog・layout・xtx・XSD検証。法人税なし | **主な参考**。流用はライセンス確認後（詳細は docs/etax/ETAX_SPEC_DESIGN.md） |
| **Lonshaus/aoiko** | **AGPL-3.0** | TS/Rust。所得税・消費税の xtx 生成＋XSD検証。法人税なし | **コード流用不可**。設計のみ参考 |
| hinokin/etax-xml-generator | MIT | Python。XSDは同梱せず各自取得。validator.py のみ実装 | 検証部の書き方を参考・流用可（表示義務あり） |
| louie47690/zeicheck | MIT | 作成済み xtx のルール点検CLI | 点検ルールの発想を参考 |
| kazukinagata/shinkoku | MIT | 所得税・消費税。作成コーナー操作。xtx生成なし | 対象外 |

- 「ai-books」は v0.1 の時点で見つけられなかったが、2026-09-30 に knishioka/ai-books と特定した（書籍リポジトリに埋もれていた）
- 法人税の別表計算・xtx生成のOSSは見つからなかった → OpenTax は先行例なし

### 2.1 aoiko から取り入れる設計（コードは写さない）
1. 公式XSDは SHA256 で固定し、起動時に照合
2. XSD → フィールド定義JSON を生成（手書きで二重管理しない）
3. 帳票ごとのマッピング層（内部モデル → XML要素）
4. `xmllint --schema` で検証。IT部・IDREF を含む全体検証が難しい箇所はラッパーXSDで部分検証
5. 既知の失敗: 手続コードの取り違え、送付書要素の混入、simpleContent 型の値の消失 → テストで先回り

### 2.2 Python で国税庁XSDを扱う注意【確認】
- `xmlschema` で厳密読込すると失敗する（共通語彙の restriction が XSD1.0 に厳密適合しない）→ スキーマ読込は `validation='lax'`、インスタンス検証は厳密
- XSD同士は `../general/General.xsd` 等の相対参照 → CAB展開後のフォルダ構成を崩さない
- 生成する xtx は UTF-8

---

## 3. ディレクトリ構成

指示書の構成を基本とし、Pythonのパッケージ名制約（数字始まり不可）と §4.3 の2軸に合わせて次のように変える。

```
opentax-japan/
├─ pyproject.toml
├─ LICENSE / THIRD_PARTY_NOTICES.md
├─ DESIGN.md
├─ src/opentax/
│  ├─ core/
│  │  ├─ money.py               … Yen型・端数処理（切捨単位を引数で受ける）
│  │  ├─ fiscal_year.py         … 事業年度・月数計算（按分用）
│  │  ├─ models/                … TaxAdjustmentModel, CompanyProfile 等（pydantic）
│  │  └─ validation/            … 入力検証（貸借・符号・必須）
│  ├─ corporate_tax/
│  │  ├─ rules/                 … ★年度別ルール（データ。コードに税率を書かない）
│  │  │  ├─ 2025-04-01.yaml     … 適用開始日で命名。出典URL必須
│  │  │  └─ schema.json         … ルールファイル自体の形式チェック
│  │  ├─ schedules/             … 別表ごとの計算（純関数）
│  │  │  ├─ s15_entertainment.py
│  │  │  ├─ s04_income.py
│  │  │  ├─ s01_tax.py
│  │  │  ├─ s05_02_taxes.py
│  │  │  └─ s05_01_retained.py
│  │  └─ engine.py              … 依存順に呼ぶだけ（§5）
│  ├─ etax/
│  │  ├─ spec_fetch/            … CAB取得・展開・SHA256台帳
│  │  ├─ field_catalog/         … XSD → フィールド定義JSON 生成
│  │  ├─ mapping/               … 版ごと: v2026_ksk2/HOA420.py など
│  │  ├─ xtx/                   … DATA/手続/CONTENTS/IT 組立
│  │  └─ validation/            … xmlschema / xmllint
│  └─ cli.py                    … opentax calculate / export-etax / fetch-spec
├─ spec-manifest/               … ★コミットするのはこれだけ
│  └─ ksk2-2026-08.lock.json    … ファイル名・URL・取得日・SHA256
├─ .cache/etax-spec/            … 取得物（.gitignore）
├─ tests/
│  ├─ unit/
│  ├─ integration/              … 別表15→4→1→5(2)→5(1)
│  ├─ golden/
│  │  ├─ cases/TEST001/input.json
│  │  └─ cases/TEST001/expected.json
│  └─ etax/                     … XSD検証（キャッシュがなければ skip）
├─ docs/
└─ tools/
   └─ anonymize/                … 既存ソフトの結果の匿名化（手元で実行し、架空データの出力だけをコミット）
```

---

## 4. Tax Adjustment Model 設計

### 4.1 入力（company.json）
```json
{
  "company": {
    "name": "サンプル株式会社", "corporate_number": null,
    "capital": 10000000, "is_sme": true, "blue_return": true,
    "large_corp_subsidiary": false
  },
  "fiscal_period": { "start": "2025-10-01", "end": "2026-09-30" },
  "accounting": {
    "net_income": 10000000,
    "corporate_taxes_expensed": 0,
    "tax_provision_charged": 0
  },
  "prior": {
    "schedule_05_01_closing": [ { "item": "減価償却超過額", "amount": 0 } ],
    "schedule_05_02_closing": [],
    "loss_carryforward": []
  },
  "interim_payments": { "corporate_tax": 0, "local_corporate_tax": 0 },
  "withholding_credit": 0,
  "entertainment": {
    "total": 9500000, "food_50pct_eligible": 0, "excluded_items": 500000
  },
  "adjustments": [
    { "code": "DEPRECIATION_EXCESS", "amount": 200000,
      "direction": "ADD", "classification": "TEMPORARY",
      "reserve_item": "減価償却超過額", "source": "manual", "memo": "" }
  ]
}
```

### 4.2 中核モデル
```
Adjustment
  code            … 調整コード（列挙。§4.4）
  amount          … int（円、正数）
  direction       … ADD | DEDUCT
  classification  … RETAINED（留保）| OUTFLOW（社外流出）
  outflow_type    … 配当 | その他 | ※（社外流出の区分。別表四の列に対応）
  reserve_item    … RETAINED のとき別表五(一)の行名
  origin          … schedule_15 | schedule_05_02 | manual | ai_candidate
  approved_by     … AI候補は人の承認なしに確定計算へ入れない（§7）
```
- 指示書の PERMANENT/TEMPORARY は、別表四の列構造に合わせ **留保／社外流出** に置き換える（交際費＝社外流出、償却超過＝留保）
- 別表四・五(一) はこのリストを **集計するだけ** で作る。別表ごとに別の入力を持たない

### 4.3 「年度」の2軸（重要）
| 軸 | キー | 例 |
|---|---|---|
| 税法ルール | 事業年度**開始日** | 2025-04-01.yaml（軽減税率17%区分の新設以降） |
| e-Tax様式 | 提出時点の仕様版 | v2026_ksk2（HOA420-025 等） |

`if year == 2026:` は書かない。エンジンは開始日からルールファイルを1つ選び、値だけを使う。
ルールファイルの各値には `source`（URL）と `verified`（日付・確認者）を必須とし、空ならテストを落とす。

### 4.4 MVPの調整コード
| code | 向き | 区分 | 発生元 |
|---|---|---|---|
| CORPORATE_TAX_EXPENSED（損金経理をした法人税・地方法人税） | ADD | 留保 | accounting |
| TAX_PROVISION（損金経理をした納税充当金） | ADD | 留保 | accounting |
| ENTERTAINMENT_NON_DEDUCTIBLE | ADD | 社外流出 | 別表十五 |
| DEPRECIATION_EXCESS / _REVERSAL | ADD / DEDUCT | 留保 | manual |
| TAX_REFUND_NONTAXABLE（還付等の益金不算入） | DEDUCT | 留保/流出 | manual |
| PROVISION_PAYMENT（納税充当金から支出した事業税等） | DEDUCT | 留保 | 別表五(二) |
| OTHER_ADD / OTHER_DEDUCT | 任意 | 任意 | manual（名称必須） |

欠損金・受取配当・所得税額控除（別表六）は Phase 3 以降に追加。MVPでは欠損金なし・所得税額控除は金額入力のみ。

---

## 5. 別表の依存関係

```
[入力] 会計利益・損金経理税額・前期5(1)5(2)残高・中間納付
   │
   ▼
別表十五 ──交際費損金不算入額──┐
   │                            ▼
   │                 Tax Adjustment Model（調整リスト）
   │                            │
   ▼                            ▼
                         別表四（所得金額）
                                │ 所得金額
                                ▼
                         別表一（法人税額・地方法人税額・差引納付額）
                                │ 当期確定税額
                                ▼
                         別表五(二)（租税公課の納付状況）
                                │ 未納法人税等・納税充当金
                                ▼
                         別表五(一)（利益積立金額）
```

計算順: **15 → 4 → 1 → 5(2) → 5(1)**（一方向。循環なし）
- 別表四に入る「損金経理をした法人税」「納税充当金」は**会計上の金額（入力）**で、別表一の結果ではない → 循環しない
- 別表五(二) の「当期確定額」は別表一の結果を受ける
- 別表五(一) の「未納法人税等」は別表五(二) の期末残高から受ける

### 5.1 検算（計算後に必ず通す。帳票間連動仕様書 e-tax08 から追加）
1. 別表四 留保合計（加算−減算） ＝ 別表五(一) 当期増減の差引合計（検算式）
2. 別表四 所得金額 ＝ 別表一 所得金額
3. 別表一 法人税額 ＝ 別表五(二) 当期確定額
4. 別表十五 損金不算入額 ＝ 別表四 交際費加算額
5. 別表五(一) 期首 ＝ 前期 別表五(一) 期末

### 5.2 端数処理（ルールファイルで持つ）
- 課税標準 1,000円未満切捨、税額 100円未満切捨（国税通則法118・119）【未確認：条文を14で取り直す】
- 地方法人税の課税標準法人税額 1,000円未満切捨
- 交際費の月数按分は「800万円 × 月数 ÷ 12」、1円未満の扱いは【未確認】

---

## 6. テスト戦略

| 層 | 対象 | 合格条件 |
|---|---|---|
| Unit | 交際費・税率（800万円境界・10億円超）・端数処理・月数按分 | 境界値の前後1円で期待どおり |
| Integration | 15→4→1→5(2)→5(1) | §5.1 の検算がすべて一致 |
| Golden | TPS1000 の匿名化結果 | **全対象項目が1円一致**。1項目でも違えば FAIL |
| e-Tax | 生成 xtx | 公式XSDで PASS（キャッシュがない環境では skip と明示） |
| ルール | rules/*.yaml | 全値に source と verified がある |

### 6.1 Golden Test の作り方
1. 手元の TPS1000 で申告書（別表一・四・五(一)(二)・十五）を出力
2. `tools/anonymize/` で匿名化
   - 社名・法人番号・住所・代表者 → 削除
   - 金額は **そのまま使わない**：入力（会計利益・交際費等）を別の値に置き換えて TPS1000 で再計算した結果を使う
   - 実在の会社と一致しない値にする（端数だけ変える等は不可）
3. `tests/golden/cases/TESTnnn/{input,expected}.json` と、TPS1000 の画面PDF（private/ に保管・コミットしない）を対応づける
4. 最初の10件（案）
   - TEST001 資本金1,000万・利益1,000万・交際費900万・償却超過20万・欠損金なし（指示書の例）
   - TEST002 所得800万円ちょうど（軽減税率の境界）
   - TEST003 所得800万円＋1,000円
   - TEST004 赤字（所得マイナス。税額0）
   - TEST005 交際費のうち接待飲食費50%基準が有利なケース
   - TEST006 事業年度6か月（按分）
   - TEST007 中間納付あり（還付）
   - TEST008 前期の償却超過の認容
   - TEST009 納税充当金の繰入・取崩あり
   - TEST010 資本金1億円超（中小法人でない：15%・定額控除なし）

---

## 7. AIと税務計算の分離

- `opentax` パッケージは AI・ネットワークに依存しない（spec_fetch を除く）。同じ入力 → 同じ出力
- AI（仕訳AI・監査AI）が出すのは `origin: "ai_candidate"` の調整候補だけ
- `approved_by` が空の候補が1件でもあれば、`calculate` はエラーで止まる
- 出力 JSON に入力のハッシュとルールファイル名を記録し、再現できるようにする

---

## 8. CLI（MVP）
```
opentax fetch-spec --set ksk2-2026-08     # CAB取得→展開→SHA256照合
opentax calculate company.json            # 別表15→4→1→5(2)→5(1)、結果JSON
opentax export-etax company.json          # company_2026.xtx（署名なし）
opentax validate company_2026.xtx         # 公式XSDで検証
```

---

## 9. フェーズと完了条件

| Phase | 内容 | 完了条件 |
|---|---|---|
| 1 | JSON入力 → 別表四 | Unit PASS、TEST001 の別表四が一致 |
| 2 | 別表四 → 一 → 五(二) → 五(一) | §5.1 検算 PASS、TEST001〜004 一致 |
| 3 | 別表十五ほか | TEST005〜010 一致 |
| 4 | 仕様取得・フィールド抽出 | SHA256台帳、HOA112/420/511/522/HOE200 の定義JSON |
| 5 | XML生成 | 公式XSD PASS |
| 6 | .xtx → e-Tax WEB版に手動読込 | 帳票表示がTPS1000のPDFと一致（**送信はしない**） |

MVP完成＝「TPS1000と一致し、e-Tax WEB版が読み込める」

## 10. MVPでやらないこと
地方税・eLTAX／消費税／所得税／相続税／電子署名の自動化／送信（API含む）／一括送信／顧問先管理／AI仕訳／GUI／財務諸表XBRL

## 11. 実装前に確認すること（未決）
1. AI BOOKS（MIT）のコードを流用するか（ライセンスを人が確認してから）
2. §1.6・§5.2 の【未確認】値 → 一次資料を取り直す
3. 10/30 公開の第3回仕様で HOA 各様式の版が変わるか
4. TPS1000 から別表を出力する方法（PDF／CSV）と、匿名化した再計算を誰が行うか
5. GitHub の公開範囲（最初は非公開で始め、Golden Test が安全と確認してから公開、を推奨）

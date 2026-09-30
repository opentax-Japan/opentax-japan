# e-Tax 仕様取込み設計（法人税）— v0.2

> OpenTax RED（赤字の単純な法人）で使う帳票は HOA112・HOA201・HOA420・HOA511・HOA522・HOB710。範囲は [DESIGN.md](../../DESIGN.md) を正とする。

作成 2026-09-30。DESIGN.md §1・§3 の `etax/` 部分の詳細設計。
【確認】＝実ファイル・一次資料で確認／【未確認】＝実装前に確認。

---

## 0. 参考元：AI BOOKS（knishioka/ai-books）

| 項目 | 内容 |
|---|---|
| URL | https://github.com/knishioka/ai-books |
| ライセンス | **MIT**（Copyright (c) 2026 Kenichiro Nishioka）【確認：LICENSE原文】 |
| 対象 | 所得税 青色申告決算書 KOA210 / KOA220 / KOA240（令和7年分）。法人税なし |
| 最終更新 | リポジトリ 2026-08-31、docs/etax 2026-06-10 |
| 関係ファイル | `docs/etax/{manifest.json, field_catalog.json, snapshot_mapping.json, handoff-runbook.md}`、`scripts/etax/{fetch_etax_spec.py, build_field_catalog.py, build_etax_layout.py, sync_web_layouts.py}`、`src/ai_books/etax/xsd.py`、`.github/workflows/etax-spec-watch.yml` |

### 0.1 コード流用の方針
- MIT なので、著作権表示と許諾文を残せば流用はできる
- ただし **ライセンスを人が確認するまでは流用しない**。確認するまでは、この文書の設計から **自分で書き直す**
- 流用する場合の手順
  1. 流用したファイルの先頭に、出典（元URL・コミットハッシュ）と著作権表示を残す
  2. `THIRD_PARTY_NOTICES.md` に AI BOOKS の MIT ライセンス全文を載せる
  3. 手を入れた箇所はファイルの冒頭に「ai-books の ○○ を改変」と書く
- 流用の候補になりそうなもの：CABリーダー（MSZIP展開）、xlsx を標準ライブラリで読む部分、SHA256見張りのCI

### 0.2 AI BOOKS から引き継ぐ設計
| AI BOOKS | OpenTax（法人税） | 変える理由 |
|---|---|---|
| manifest に CAB 単位と xsd 単位の SHA256 を記録 | 同じ。加えて **様式セット（手続XSD）単位** を追加 | 法人税は手続XSDが帳票XSDを include するため |
| field_catalog は **フィールド仕様書 xlsx** から作る | 同じ（e-tax10） | 項目名・書式・桁は xlsx にしかない |
| layout は **XSD** から作る | 同じ | 並び順・繰返しは XSD が正本 |
| xtx 出力は layout に沿って並べ、layout に無いコードは止める | 同じ | |
| 検証は帳票単位のラッパーXSD（`{form}SET`） | ラッパー ＋ **手続XSD（RHO0012）で文書全体を検証** | 法人税の手続XSDは `DATA` を最上位に持ち、全帳票を include している（§5） |
| IDREF 項目（氏名等）は空で出す | **IT部を生成して IDREF をつなぐ** | 全体検証が通せるため |
| 週次 cron で SHA256 を照合し、不一致なら issue | 同じ | |
| xsd が無ければテストは skip | 同じ。ただし **CI では skip を失敗にする** | skip のまま通ると、検証していないのに合格に見える |

---

## 1. 法人税の仕様書（取得対象）

【確認】KSK2対応版。URL: `https://www.e-tax.nta.go.jp/shiyo/ksk2/download/<name>.CAB`

| name | 中身 | 使い道 |
|---|---|---|
| e-tax19 | XMLスキーマ（全税目） | layout・検証 |
| e-tax10 | XML構造設計書・帳票フィールド仕様書【法人税】 | field_catalog |
| e-tax08 | 帳票間連動仕様書 | linkage_catalog（§3.3。AI BOOKS にはない） |
| e-tax07 | 手続一覧・手続内帳票対応表 | procedure_catalog |

【確認】XSDの実物（e-tax19 KSK2版を展開して確認）
- 置き場所: `19XMLスキーマ/hojin/`、共通は `general/`（General.xsd, ITdefinition.xsd, ITreference.xsd, CATALOG.xsd, XBRL*.xsd ほか）
- 帳票XSDの形（HOA420-025.xsd）
  ```xml
  <xsd:documentation>様式名：別表四(簡易様式)… version：25.0 Date：2026年04月11日</xsd:documentation>
  <xsd:import namespace=".../XSD/general" schemaLocation="../general/General.xsd"/>
  <xsd:simpleType name="HOA420-25-0VRtype">…"25.0"…</xsd:simpleType>
  <xsd:group name="HOA420-25-0group"> <xsd:element name="HOA420"> …
    <xsd:element name="ARB00000" type="ARB00000-25-0type" minOccurs="0">
      <xsd:annotation><xsd:appinfo>"当期利益又は当期欠損の額"</xsd:appinfo></xsd:annotation>
  ```
  - 項目名の日本語は `xsd:appinfo` に入っている → XSD だけでも仮の名前を付けられる
  - 金額は `gen:kingaku`（simpleContent。属性 `AutoCalc` あり）。HOA420 では119か所
  - 文字の項目は `…Rtype`（例：ARW00000 は gen:str、最大15桁）
  - IDREF の項目: `gen:NOZEISHA_NMref`, `gen:JIGYO_NENDO_FROMref`, `gen:JIGYO_NENDO_TOref`
    （ITreference.xsd で `IDREF` は必須、値は固定。例 `fixed="NOZEISHA_NM"`）
- 手続XSD（RHO0012-260.xsd）
  - `DATA` が最上位にある。`CONTENTS` の型は `RHO0012-260-1contentsType`
  - ITdefinition.xsd・CATALOG.xsd・XBRL060/190.xsd と、HOA112-007 / HOA410-025 / HOA420-025 / HOA511-016 / HOA522-010 / HOE200-015 ほか全別表を include している
  - 帳票は `<xsd:group ref="HOE200-15-0group" minOccurs="0" maxOccurs="unbounded"/>` の形で並ぶ
- 同じ帳票の過去版も同じフォルダにある（HOA112-001〜007）→ **手続XSDが include している版＝現行版** と判定する（「一番大きい番号」とは判定しない）

【未確認】e-tax10 の中にある法人税のフィールド仕様書 xlsx のファイル名・列の構成（未展開）→ Phase 4 の最初にやる

---

## 2. manifest.json（コミットする。仕様書そのものは含めない）

置き場所: `spec-manifest/ksk2-2026-08.manifest.json`（仕様のセットごとに1ファイル）

```json
{
  "manifest_version": 1,
  "spec_set": "ksk2-2026-08",
  "acquired_at": "2026-09-30",
  "acquired_by": "fetch-spec 0.1.0",
  "source_index": {
    "list": "https://www.e-tax.nta.go.jp/shiyo/ksk2/ksk2_shiyo3.htm",
    "plan": "https://www.e-tax.nta.go.jp/shiyo/keikaku.htm",
    "terms": "https://www.e-tax.nta.go.jp/shiyo/index.htm#anc04"
  },
  "redistribution": "国税庁の著作物のため原本は同梱しない。取得はスクリプトで行う",
  "packages": [
    {
      "name": "e-tax19",
      "url": "https://www.e-tax.nta.go.jp/shiyo/ksk2/download/e-tax19.CAB",
      "published": "2026-08-28",
      "size_bytes": null,
      "sha256": null,
      "role": "xsd"
    },
    { "name": "e-tax10", "role": "field_spec",   "…": "…" },
    { "name": "e-tax08", "role": "linkage_spec", "…": "…" },
    { "name": "e-tax07", "role": "procedure_list", "…": "…" }
  ],
  "procedures": [
    {
      "procedure_id": "RHO0012",
      "label": "内国法人の確定申告（青色）",
      "xsd": "19XMLスキーマ/hojin/RHO0012-260.xsd",
      "xsd_sha256": null,
      "vr": "26.0.1",
      "namespace": "http://xml.e-tax.nta.go.jp/XSD/hojin"
    }
  ],
  "forms": [
    {
      "form_id": "HOA420",
      "label": "別表四（簡易様式）",
      "version": "25.0",
      "xsd": "19XMLスキーマ/hojin/HOA420-025.xsd",
      "xsd_sha256": null,
      "xsd_documentation_date": "2026-04-11",
      "group": "HOA420-25-0group",
      "field_spec_workbook": null,
      "field_spec_sheet": null,
      "included_by": ["RHO0012"],
      "mvp": true
    }
  ],
  "support_files": [
    { "path": "19XMLスキーマ/general/General.xsd",      "sha256": null },
    { "path": "19XMLスキーマ/general/ITdefinition.xsd", "sha256": null },
    { "path": "19XMLスキーマ/general/ITreference.xsd",  "sha256": null },
    { "path": "19XMLスキーマ/general/CATALOG.xsd",      "sha256": null }
  ]
}
```
- `null` は初回取得時に `fetch-spec --init` で埋め、人が差分を見てコミットする
- forms の MVP 対象（OpenTax RED）: HOA112, HOA201, HOA420, HOA511, HOA522, HOB710。作成済みの HOA410・HOE200 は `mvp:false` にして残す
- **手続XSDが include している全ファイル** の SHA256 を記録する（帳票XSDだけ記録すると、General.xsd だけが変わった改訂を見逃す）
- manifest 自体の形式は `spec-manifest/manifest.schema.json`（JSON Schema）でチェックする

---

## 3. 生成するカタログ（すべて生成物。手で直さない）

### 3.1 field_catalog.json（元＝e-tax10 のフィールド仕様書 xlsx）
置き場所: `src/opentax/etax/layouts/<spec_set>/field_catalog.json`（layout と同じフォルダ）

実装（Phase 3）で決まったこと
- シートは manifest の版とシート2行目の版が一致するものを選び、manifest の `field_spec_workbook`・`field_spec_sheet` に記録する
- 列（`column`）は項目名から別表ごとの対応表（`field_catalog.COLUMN_RULES`）で決める。当たらないものは null にして `unmatched_columns` に並べる
- 本書き・外書き・内書きは `writing` に分ける。自動計算の式は `calc` に原文のまま入れる
- XMLタグで layout とつなぎ `layout_path` を持つ。日付・区分は部品（era/yy/mm/dd・kubun_CD）を `layout_part` に入れる
- `signed` は仕様書に手がかりがないため持たない

```json
{
  "spec_set": "ksk2-2026-08",
  "source": { "workbook": "…(法人税)….xlsx", "sha256": "…" },
  "generated_by": "tools/etax/build_field_catalog.py",
  "forms": [
    {
      "form_id": "HOA420", "version": "25.0", "field_count": 0,
      "fields": [
        {
          "seq": 2,
          "item_code": "ARB00000",
          "group": "当期利益又は当期欠損の額",
          "name": "",
          "line_no": "1",
          "column": "総額",
          "kind": "数値",
          "format": "ZZ,ZZZ,ZZZ,ZZZ,ZZ9",
          "int_digits": 13,
          "signed": true,
          "repeat": null,
          "input_required": false,
          "value_range": null,
          "xsd_type": "gen:kingaku",
          "xsd_appinfo": "当期利益又は当期欠損の額",
          "note": null
        }
      ]
    }
  ]
}
```
- AI BOOKS と同じ列（項番・入力型・グループ名・項目名・繰返し・書式・入力チェック・値の範囲）を取る
- 法人税で追加する列
  - `line_no`／`column`：別表の「行番号」と「①総額／②留保／③社外流出」。**計算エンジンとの対応に使う**
  - `signed`：マイナスがあり得るか（書式マスクの「－」で判定）
- `xsd_type`・`xsd_appinfo` は XSD から補って、xlsx と XSD の食い違いを見つけるのに使う
- `input_required` は xlsx の「入力チェック○」。XSD の minOccurs とは別物（AI BOOKS の注記を引き継ぐ）
- 【未確認】法人税の xlsx に行番号・列区分の列があるか。無ければ `xsd_appinfo` と帳票画像から手で対応表を作り、`line_map.yaml` として別に持つ

### 3.2 layout（元＝XSD）
置き場所: `src/opentax/etax/layout/<spec_set>/HOA420.layout.json`

```json
{
  "form_id": "HOA420", "version": "25.0",
  "namespace": "http://xml.e-tax.nta.go.jp/XSD/hojin",
  "group": "HOA420-25-0group",
  "xsd_sha256": "…",
  "root": {
    "tag": "HOA420",
    "attrs": { "VR": "25.0", "page": "gen:FormAttribute" },
    "children": [
      { "tag": "ARA00000", "label": "納税者等部", "min": 0, "max": 1, "children": [
          { "tag": "…", "kind": "idref", "idref": "NOZEISHA_NM" },
          { "tag": "…", "kind": "idref", "idref": "JIGYO_NENDO_FROM" }
      ]},
      { "tag": "ARB00000", "label": "当期利益又は当期欠損の額", "min": 0, "max": 1, "children": [
          { "tag": "…", "kind": "amount", "xsd_type": "gen:kingaku" }
      ]},
      { "tag": "ARW00000", "label": "…", "kind": "text", "max_length": 15 }
    ]
  }
}
```
- XSD の並び順（sequence）・min/maxOccurs・型をそのまま写す
- `kind` の種類: `amount`（gen:kingaku → 整数円）／`value`（文字・区分・日付の部品など。maxLength・enumeration 等の制約つき）／`idref`（IT部への参照）／`group`（入れ子）。繰り返しは `max`（maxOccurs、unbounded は null）で表す
- pattern（文字種）は layout に入れない。公式 XSD での検証で確認する
- 値の点検 `check_values`: 道筋（例 `HOA420/ARB00000/ARB00010`、繰り返しは `[2]`）で値を渡し、layout にない項目・上限超え・金額が整数でない・桁超え・区分外は全件まとめてエラーにする
- 帳票ID・版・group 名は `HOA420-25-0group` という名前から読み取る（AI BOOKS と同じ）
- `FormAttribute`（ページ属性）などの共通部品は General.xsd を辿って展開する
- CI で XSD から作り直し、コミット済みのものとずれていれば失敗にする

### 3.3 linkage_catalog.json（元＝e-tax08 帳票間連動仕様書）※法人税だけの追加
```json
{ "rules": [
  { "id": "L-0001",
    "from": { "form_id": "HOA420", "item_code": "…", "line_no": "…" },
    "to":   { "form_id": "HOA112", "item_code": "…" },
    "relation": "equal",
    "source": "e-tax08 …xlsx シート… 行…" }
]}
```
- DESIGN.md §5.1 の検算（別表四の所得＝別表一の所得 など）を、国税庁の連動仕様から **機械的に** 作る
- 計算エンジンの結合テストと、xtx 出力後の点検の両方で使う
- 【未確認】e-tax08 の書式（未展開）

### 3.4 procedure_catalog.json（元＝e-tax07 と 手続XSD）
- RHO0012 に入れてよい帳票・並び順・必須の帳票・繰返し可否
- 手続コードの取り違えを防ぐ（aoiko で実際に起きた不具合）

### 3.5 エンジンとの対応表（人が書く。唯一の手書きファイル）
`src/opentax/etax/mapping/<spec_set>/HOA420.map.yaml`
```yaml
form_id: HOA420
version: "25.0"
fields:
  ARB00000/…:   { from: schedule_04.accounting_profit.total }
  ARC…/…:       { from: schedule_04.additions[code=ENTERTAINMENT_NON_DEDUCTIBLE].outflow }
```
- 左は layout 上の項目の道筋、右は計算結果JSONの場所
- layout に無い道筋、catalog に無い項目コードはエラー（AI BOOKS と同じ）
- 版が上がったら、layout・catalog の差分レポートを見て map.yaml だけ直す

---

## 4. 仕様取得スクリプト（opentax fetch-spec）

Python 標準ライブラリだけ（urllib, hashlib, zipfile, zlib, xml.etree）。外部コマンドに頼らない。

```
opentax fetch-spec --set ksk2-2026-08           # 取得→照合→展開→配置
opentax fetch-spec --set ksk2-2026-08 --init    # 初回：SHA256等を埋めた manifest 案を出す（上書きしない）
opentax fetch-spec --set ksk2-2026-08 --check   # 見張り：原本を取り直し、SHA256の差分だけ報告
```
手順
1. manifest を読み、`manifest.schema.json` で形式チェック
2. `.cache/etax/cab/<name>.CAB` が無ければダウンロード（User-Agent 明記・再試行あり）
3. CAB の SHA256 を照合。不一致なら **止まる**（`--no-verify` は警告に落とすだけで、CIでは使えない）
4. CAB を展開（MSZIP／非圧縮に対応する自作リーダー。LZX が出たら止めて報告）
5. 展開した中から、manifest の `procedures`・`forms`・`support_files` と、手続XSDから include/import を辿って見つかるファイルを取り出し、
   `.cache/etax/schema/<spec_set>/19XMLスキーマ/{hojin,general,kyotsu}/` へ **元のフォルダ構成のまま** 置く（`../general/…` の相対参照を壊さない）
6. 各 xsd の SHA256 を照合。manifest に無いファイルが include されていたら「manifest 追加が必要」と報告
7. xlsx（e-tax10・08・07）を `.cache/etax/extracted/<spec_set>/` へ
8. `.cache/` は .gitignore。コミット禁止

見張り（GitHub Actions `spec-watch.yml`、毎月1日＋手動）
- `--check` を実行。**キャッシュを使わず毎回取り直す**
- 不一致・取得失敗・一覧ページに新しい公開日 → issue を自動で起票（同じ題名があれば起票しない）
- 2026年は 10/30（第3回仕様公開）の直後に手動でも回す

---

## 5. XSD検証（opentax validate）

### 5.1 2段階で検証する
| 段階 | 対象 | 使うXSD | 目的 |
|---|---|---|---|
| ① 帳票単位 | 1帳票の XML 断片 | ラッパー XSD（下） | 帳票ごとの単体テスト。速い |
| ② 手続単位 | `DATA/RHO0012/…` の文書全体 | **RHO0012-260.xsd そのもの** | IT部・IDREF・帳票の並び・VRまで含めて確認 |

① のラッパー（AI BOOKS と同じ考え方。自分で書く）
```xml
<xsd:schema targetNamespace="http://xml.e-tax.nta.go.jp/XSD/hojin" xmlns="…/hojin"
            xmlns:xsd="http://www.w3.org/2001/XMLSchema" elementFormDefault="qualified">
  <xsd:include schemaLocation="HOA420-025.xsd"/>
  <xsd:element name="HOA420SET"><xsd:complexType>
    <xsd:group ref="HOA420-25-0group"/></xsd:complexType></xsd:element>
</xsd:schema>
```
- IDREF 項目がある帳票は、①では IDREF が解決できない → ラッパーに IT部のダミー（ID だけ持つ要素）を並べるか、①では IDREF エラーだけ除外して ② で必ず確認する【どちらにするかは実物で試して決める】

② は法人税だけでできる（【確認】RHO0012-260.xsd が `DATA` を最上位に持ち、ITdefinition.xsd と全帳票を include している）
- IT部を生成し `<NOZEISHA_NM ID="NOZEISHA_NM">…` を置く。帳票側は `<… IDREF="NOZEISHA_NM"/>` で参照する
- AI BOOKS の「IDREF 項目は空で出す」はやらない（全体検証で確認できるため）

### 5.2 ライブラリ
- 本番の xtx 出力は標準ライブラリだけ（xml.etree）
- 検証は `xmlschema`（開発・CI用の依存）
  - スキーマの読込みは `validation="lax"`（国税庁XSDは restriction が XSD1.0 に厳密には合わず、strict だと読めない。hinokin/etax-xml-generator で確認済みの事象）
  - 文書の検証は厳密に行い、`iter_errors` で全件を出す
  - 読み込んだスキーマは pickle で `.cache/` に保存（RHO0012 は全別表を include していて重い）【未確認：読込時間】
- 補助として `xmllint --schema` でも同じ文書を検証し、両方が通ることを CI の条件にする（片方のライブラリの癖に頼らない）

### 5.3 XSD では見つからない誤り（出力後の点検 `opentax lint`）
- linkage_catalog の連動ルール（別表四の所得＝別表一の所得 など）
- field_catalog の桁数・符号・入力チェック○の項目
- `gen:kingaku` の値が整数か。`AutoCalc` 属性は出さない【未確認：e-Taxソフト（ダウンロード版）での扱い】
- simpleContent 型の値が消えていないか（aoiko で起きた不具合）
- 手続コードと CATALOG の整合

### 5.4 テストと CI
| テスト | キャッシュ無しのとき |
|---|---|
| unit（layout・catalog 生成のロジック。小さな自作XSDで） | 常に実行 |
| schema（① ②） | ローカルは skip と表示、**CI は失敗** |
| drift（XSDから作り直した layout・catalog とコミット済みの一致） | CI は失敗 |
| golden（TEST001 の xtx 断片と期待XMLの一致） | 常に実行 |

---

## 6. 版が上がったとき（例：10/30 の第3回仕様）
1. `--check` か issue で気づく
2. 新しい `spec_set`（例 `ksk2-2026-10`）の manifest を `--init` で作る
3. layout・catalog を作り、**前の spec_set との差分レポート**（増えた項目・消えた項目・桁の変更）を出す
4. map.yaml を直す。前の spec_set は消さない（過去の事業年度の再出力に使う）
5. どの spec_set を使うかは「提出日」で選ぶ（DESIGN.md §4.3）

---

## 7. 実装の順番（Phase 4〜5 の中身）
1. manifest.schema.json と KSK2版 manifest（`--init` で作ったものを人が確認）
2. fetch-spec（CAB読み・SHA256・配置）
3. layout 生成（HOA420 から。次に HOA112, HOA511, HOA522, HOE200）
4. 検証 ①（ラッパー）→ ②（RHO0012 全体、IT部つき）
5. e-tax10 を展開して field_catalog
6. e-tax08 を展開して linkage_catalog
7. map.yaml と xtx 出力 → `opentax lint`
8. 見張りの CI

## 8. 未決
1. AI BOOKS のコード流用可否（MIT。人がライセンスを確認したら §0.1 の手順で）
2. e-tax10・e-tax08 の xlsx の構成（未展開）
3. ①で IDREF をどう扱うか
4. 別表四の簡易様式（HOA420）と総括表（HOA410）のどちらで出すか（DESIGN.md §0-C）
5. 財務諸表（XBRL060/190）を含めない RHO0012 が e-Taxソフト（ダウンロード版）で読めるか

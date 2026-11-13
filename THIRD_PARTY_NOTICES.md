# Third Party Notices

## AI BOOKS

- URL: https://github.com/knishioka/ai-books
- 参照したコミット: d5ab92b2a040cdcf79f1ec51dd2655e507171d85
- 用途: e-Tax 仕様の取得・manifest・field_catalog・layout・XSD検証の設計を参考にしている
- コードの流用: 現時点ではなし。流用したファイルには、先頭に出典と著作権表示を残す

```
MIT License

Copyright (c) 2026 Kenichiro Nishioka

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 国税庁 e-Tax 仕様書

e-Tax の仕様書（CAB・XSD・xlsx）は国税庁の著作物です。このリポジトリには含めていません。
利用者が取得スクリプトで国税庁のサイトから直接ダウンロードします。
利用条件: https://www.e-tax.nta.go.jp/shiyo/index.htm

## 国税庁の様式（紙の別表の画像）

Web 画面の「申告書（紙の様式）」は、国税庁ホームページで提供されている法人税の別表の様式（PDF）の画像の上に、計算した金額を重ねて表示しています。

- 出典：国税庁ホームページ（https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/hojin/shinkoku/itiran2026/01.htm ほか各様式の URL）を加工して作成
- 利用条件：公共データ利用規約（第1.0版）（https://www.nta.go.jp/chuijiko/copy.htm）
- 国税庁が作成したものではありません。様式の PDF はリポジトリに含めず、サイトを組み立てるときに国税庁のサイトから取得し、SHA256 で照合しています

## 総務省 法人住民税・法人事業税 税率一覧表・全国地方公共団体コード

全都道府県・全市町村の均等割の税率（src/opentax/red/rules/local_rates/）は、総務省の公表資料を加工して作成しています。

- 出典：「令和７年度 法人住民税・法人事業税 税率一覧表」（総務省自治税務局、令和７年９月）（https://www.soumu.go.jp/main_content/001032861.pdf）を加工して作成
- 出典：「令和８年度 法人住民税・法人事業税 税率一覧表」（総務省自治税務局、令和８年９月）（https://www.soumu.go.jp/main_content/001092939.pdf）を加工して作成
- 出典：「都道府県コード及び市区町村コード」（令和6年1月1日更新）（総務省 全国地方公共団体コード https://www.soumu.go.jp/denshijiti/code.html）を加工して作成
- 利用条件：公共データ利用規約（第1.0版）（総務省ホームページ「当省ホームページについて」著作権について https://www.soumu.go.jp/menu_kyotsuu/policy/tyosaku.html）。2026年11月14日に利用
- 総務省が作成したものではありません。元の PDF・Excel はリポジトリに含めず、tools/build_local_rates.py が総務省のサイトから取得し、SHA256 で照合してから表を読み取ります
- 一覧の注記のとおり、税率は各年4月1日時点の採用税率です。申告の際は各都道府県・市町村の資料で確かめてください

## 内閣府 国民の祝日

地方税の納付書の納期限（休日なら翌日）の計算に使う祝日の一覧（src/opentax/red/rules/holidays.json）は、内閣府の公表データを加工して作成しています。

- 出典：内閣府「国民の祝日について」（https://www8.cao.go.jp/chosei/shukujitsu/gaiyou.html）の syukujitsu.csv を加工して作成
- 利用条件：公共データ利用規約（第1.0版）（内閣府ホームページ利用規約 https://www.cao.go.jp/notice/rule.html）。2026年11月14日に利用

## e-Gov法令検索 地方税法施行規則の別記様式

地方税の申告書・納付書（第六号様式・第二十号様式・第十二号の二様式・第二十二号の四様式など）は、e-Gov法令検索の地方税法施行規則の別記様式（PDF）の画像の上に値を重ねて表示しています。

- 出典：e-Gov法令検索（https://laws.e-gov.go.jp/law/329M50000002023 の各様式の添付ファイル）を加工して作成
- 利用条件：e-Gov 利用規約（https://www.e-gov.go.jp/terms）により公共データ利用規約（第1.0版）
- 様式の PDF はリポジトリに含めず、サイトを組み立てるときに取得し、SHA256 で照合しています

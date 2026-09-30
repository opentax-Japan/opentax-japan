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

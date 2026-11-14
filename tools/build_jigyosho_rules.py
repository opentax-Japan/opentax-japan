"""事業所税の設定（src/opentax/jigyosho/rules/jigyosho.json）を e-Gov 法令API（v2）の条文から作る。手では打たない。

    python tools/build_jigyosho_rules.py   → src/opentax/jigyosho/rules/jigyosho.json

取るもの（どれも現行の版。版の ID を出典に書く）:
- 地方税法 第701条の42（税率）・第701条の43（免税点）・第701条の41（課税標準の特例の表。号ごとの資産割・従業者割の割合）
- 地方税法施行令 第56条の15（法第701条の31第1項第1号ハの市）
- 地方自治法第252条の19第1項の指定都市の指定に関する政令（指定都市）
- 首都圏整備法施行令 第2条・近畿圏整備法施行令 第1条と別表（既成市街地・既成都市区域を有する市）
条文の言葉が見つからなければ止める（推測で書かない）。団体コードは src/opentax/red/rules/local_rates/r08.json（総務省の一覧から作ったもの）から名前で引く。
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "src" / "opentax" / "jigyosho" / "rules" / "jigyosho.json"
API = "https://laws.e-gov.go.jp/api/2/law_data/{}?response_format=xml"
UA = {"User-Agent": "opentax-japan rules builder"}

LAWS = {"chiho": "325AC0000000226", "rei": "325CO0000000245", "shitei": "331CO0000000254",
        "shutoken": "332CO0000000333", "kinki": "340CO0000000159"}
KANJI = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


class BuildError(Exception):
    pass


def _get(law_id: str) -> tuple[str, str]:
    req = urllib.request.Request(API.format(law_id), headers=UA)
    t = urllib.request.urlopen(req, timeout=180).read().decode("utf-8")
    rev = re.search(r"<law_revision_id>([^<]+)</law_revision_id>", t)
    if not rev:
        raise BuildError(f"{law_id}: 版の ID が取れない")
    return t, rev.group(1)


def _text(xml: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", xml))


def _article(xml: str, num: str) -> str:
    m = re.search(rf'<Article Num="{num}">(.*?)</Article>', xml, re.S)
    if not m:
        raise BuildError(f"第{num}条が見つからない")
    return m.group(1)


def _kanji_num(s: str) -> int:
    """「十九」「二十三」などを数にする（百未満）。"""
    if s.isdigit():
        return int(s)
    n = 0
    if "十" in s:
        a, _, b = s.partition("十")
        n = (KANJI[a] if a else 1) * 10 + (KANJI[b] if b else 0)
    else:
        n = KANJI[s]
    return n


def _fraction(s: str) -> str | None:
    """「四分の三」→ "3/4"。空欄は None。"""
    if not s:
        return None
    m = re.fullmatch(r"(.+)分の(.+)", s)
    if not m:
        raise BuildError(f"割合の言葉が読めない: {s}")
    return str(Fraction(_kanji_num(m.group(2)), _kanji_num(m.group(1))))


def build() -> dict:
    chiho, chiho_rev = _get(LAWS["chiho"])
    rei, rei_rev = _get(LAWS["rei"])
    shitei, shitei_rev = _get(LAWS["shitei"])
    shutoken, shutoken_rev = _get(LAWS["shutoken"])
    kinki, kinki_rev = _get(LAWS["kinki"])

    a42 = _text(_article(chiho, "701_42"))
    if "資産割にあつては一平方メートルにつき六百円" not in a42 or "従業者割にあつては百分の〇・二五" not in a42:
        raise BuildError(f"第701条の42 の文言が想定と違う: {a42}")
    a43 = _text(_article(chiho, "701_43"))
    if "合計面積が千平方メートル以下である場合には資産割を" not in a43 or "合計数が百人以下である場合には従業者割を" not in a43:
        raise BuildError("第701条の43 の文言が想定と違う")

    special = []
    for row in re.findall(r"<TableRow>(.*?)</TableRow>", _article(chiho, "701_41"), re.S):
        cols = [_text(c) for c in re.findall(r"<TableColumn[^>]*>(.*?)</TableColumn>", row, re.S)]
        if len(cols) != 3 or cols[0] == "施設":
            continue
        m = re.match(r"([一二三四五六七八九十]+)(.*)", cols[0])
        if not m:
            raise BuildError(f"特例の表の号が読めない: {cols[0][:30]}")
        special.append({"no": _kanji_num(m.group(1)), "facility": m.group(2),
                        "asset": _fraction(cols[1]), "payroll": _fraction(cols[2])})
    if [s["no"] for s in special] != list(range(1, len(special) + 1)) or len(special) < 15:
        raise BuildError("特例の表の号が順に並んでいない")

    a5615 = _text(_article(rei, "56_15"))
    m = re.search(r"政令で指定する市は、(.+)とする", a5615)
    if not m:
        raise BuildError("施行令第56条の15 の市の並びが読めない")
    ha = re.split(r"、|及び", m.group(1))

    body = _text(shitei[shitei.find("<MainProvision"):shitei.find("</MainProvision>")])
    m = re.search(r"指定する。(.+)$", body)
    designated = re.findall(r"(?:さいたま|[一-龥]{1,3})市", m.group(1)) if m else []
    if len(designated) < 20:
        raise BuildError(f"指定都市の並びが読めない: {designated}")

    s2 = _text(_article(shutoken, "2"))
    if "武蔵野市の区域並びに三鷹市、横浜市、川崎市及び川口市" not in s2:
        raise BuildError("首都圏整備法施行令 第2条 の文言が想定と違う")
    k_rows = re.findall(r"<TableRow>(.*?)</TableRow>", kinki[kinki.find("<AppdxTable"):], re.S)
    kinki_cities = [_text(re.findall(r"<TableColumn[^>]*>(.*?)</TableColumn>", r, re.S)[0]) for r in k_rows]
    kinki_cities = [c for c in kinki_cities if c.endswith("市") and c != "市名"]
    shutoken_cities = ["武蔵野市", "三鷹市", "横浜市", "川崎市", "川口市"]
    old_names = {"布施市": "東大阪市"}

    ro = [c for c in shutoken_cities + [old_names.get(c, c) for c in kinki_cities] if c not in designated]

    rates = json.loads((REPO / "src" / "opentax" / "red" / "rules" / "local_rates" / "r08.json").read_text(encoding="utf-8"))
    by_name: dict[str, list[dict]] = {}
    for mu in rates["municipalities"]:
        by_name.setdefault(mu["name"], []).append(mu)

    def city(name: str, basis: str) -> dict:
        hits = by_name.get(name, [])
        if len(hits) != 1:
            raise BuildError(f"{name}: 団体コードが1つに決まらない（{len(hits)} 件）")
        return {"code": hits[0]["code"], "prefecture": hits[0]["prefecture"], "name": name, "basis": basis}

    cities = [{"code": "131008", "prefecture": "東京都", "name": "特別区（東京都）", "basis": "法第735条第1項（都が特別区の存する区域で課する）"}]
    cities += [city(n, "法第701条の31第1項第1号イ（指定都市）") for n in designated]
    cities += [city(n, "法第701条の31第1項第1号ロ（既成市街地・既成都市区域を有する市）") for n in ro]
    cities += [city(n, "法第701条の31第1項第1号ハ（施行令第56条の15）") for n in ha]

    return {
        "note": "tools/build_jigyosho_rules.py が e-Gov 法令API の条文から作った。手で直さない",
        "sources": {
            "地方税法": chiho_rev, "地方税法施行令": rei_rev, "指定都市の指定に関する政令": shitei_rev,
            "首都圏整備法施行令": shutoken_rev, "近畿圏整備法施行令": kinki_rev,
            "api": "https://laws.e-gov.go.jp/api/2/law_data/<法令ID>",
            "布施市": "近畿圏整備法施行令の別表の「布施市」は現在の東大阪市。堺市「事業所税の手引」（令和8年4月）"
                     "https://www.city.sakai.lg.jp/kurashi/zei/shizei/sonota/jigyoshozei.files/tebiki0804.pdf の課税団体の一覧に東大阪市",
        },
        "rate": {"asset_yen_per_m2": 600, "payroll": "1/400", "basis": "法第701条の42"},
        "exemption": {"area_m2": 1000, "employees": 100, "basis": "法第701条の43（課税標準の算定期間の末日の現況で判定。同条第3項）"},
        "special": special,
        "special_basis": "法第701条の41第1項の表（号・資産割に係る割合・従業者割に係る割合。空欄は null）",
        "cities": cities,
        "removed": [
            {**city(n, "地方税法施行令の一部を改正する政令（令和8年6月25日公布）で指定を取消し。法人は非適用日の属する事業年度の直前の事業年度分まで、"
                       "個人は非適用日前に終了した課税期間分まで課税（施行令第56条の84第1項）。総務省「地方税法施行令の一部を改正する政令について」"
                       "https://www.soumu.go.jp/main_content/001079108.pdf"),
             "non_applicable_date": "2026-05-29"}
            for n in ("秋田市", "久留米市") if n not in ha
        ],
    }


def main() -> int:
    try:
        data = build()
    except BuildError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{OUT}: 課税団体 {len(data['cities'])}・特例 {len(data['special'])} 号")
    return 0


if __name__ == "__main__":
    sys.exit(main())

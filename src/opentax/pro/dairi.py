"""税務代理権限証書（税理士法第30条。e-Tax の様式 SOZ074「令和6年4月1日以降提出分」）の値を作る。

事務所の設定（office。税理士事務所ごとに1回書けばよい）と、会社の入力・申告の期間から作る。計算はしない。

office（JSON）:
  kind: 税理士 / 税理士法人
  name: 税理士の氏名（税理士法人なら法人の名称）・user_id: 税理士の利用者識別番号（16桁）
  office_name: 事務所の名称・address: 事務所の所在地・phone: 03-0000-0000
  association: 所属税理士会（例 中国税理士会）・branch: 支部（例 岡山東支部）・registration: 登録番号
  consents: {past_years, survey_notice, no_correction_notice, result_explanation, representative}（true/false。省略は false）
    past_years: 過年分に関する税務代理 / survey_notice: 調査の通知の同意 /
    no_correction_notice: 調査終了時に更正決定等をすべきと認められない場合の通知の同意 /
    result_explanation: 更正決定等をすべきと認められる場合の調査結果の内容の説明等の同意 /
    representative: 代理人が複数ある場合の代表する代理人の定め
engagement（会社ごと。省略可）: {date: 委任年月日（YYYY-MM-DD）, consents: office と同じ形（会社ごとに変えるとき）}
"""

from __future__ import annotations

import datetime
import re


class DairiInputError(Exception):
    """事務所の設定が足りない・形が違うとき。"""


_CONSENTS = {"past_years": "ATB00170", "survey_notice": "ATB00190", "no_correction_notice": "ATB00200",
             "result_explanation": "ATB00210", "representative": "ATB00220"}
_KIND = {"税理士": "1", "税理士法人": "2"}


def _date(v, where: str) -> datetime.date:
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v))
    except ValueError:
        raise DairiInputError(f"{where}: 日付は YYYY-MM-DD で書いてください: {v!r}") from None


def _phone(v: str | None, where: str) -> str | None:
    if not v:
        return None
    if not re.fullmatch(r"\d{1,6}-\d{1,4}-\d{1,4}", v):
        raise DairiInputError(f"{where}: 電話番号は 03-0000-0000 の形で書いてください: {v}")
    return v


def check_office(office: dict) -> list[str]:
    """事務所の設定で足りない欄（税務代理権限証書に要るもの）。"""
    need = {"name": "税理士の氏名（法人なら名称）", "office_name": "事務所の名称", "address": "事務所の所在地",
            "association": "所属税理士会", "registration": "登録番号"}
    out = [f"office.{k}（{label}）" for k, label in need.items() if not office.get(k)]
    if office.get("kind", "税理士") not in _KIND:
        out.append("office.kind（税理士 / 税理士法人）")
    uid = office.get("user_id")
    if uid and not re.fullmatch(r"\d{16}", str(uid)):
        out.append("office.user_id（税理士の利用者識別番号は16桁の数字）")
    return out


def values(office: dict, company: dict, corporate: tuple | None = None, consumption: tuple | None = None,
           engagement: dict | None = None, submit_date: datetime.date | None = None) -> dict:
    """SOZ074 の値（タグ → 値）。corporate・consumption は法人税の事業年度・消費税の課税期間（開始日, 終了日）。"""
    missing = check_office(office)
    if missing:
        raise DairiInputError("税務代理権限証書に要る事務所の設定がありません: " + "、".join(missing))
    eng = engagement or {}
    consents = {**(office.get("consents") or {}), **(eng.get("consents") or {})}
    unknown = set(consents) - set(_CONSENTS)
    if unknown:
        raise DairiInputError(f"consents に使えない名前があります: {sorted(unknown)}（{'・'.join(_CONSENTS)}）")
    v: dict = {
        "ATB00020": f"{company['tax_office']}税務署長" if company.get("tax_office") else None,
        "ATB00050": office["name"], "ATB00060": office.get("user_id") or None,
        "ATB00080": office["office_name"], "ATB00090": office["address"],
        "ATB00100": _phone(office.get("phone"), "office.phone"),
        "ATB00120": office["association"], "ATB00130": office.get("branch") or None, "ATB00140": office["registration"],
        "ATB00150": _KIND[office.get("kind", "税理士")],
        "ATB00250": company["name"], "ATB00260": company.get("user_id") or None,
        "ATB00280": company.get("address"), "ATB00290": _phone(company.get("phone"), "company.phone"),
    }
    if submit_date:
        v["ATB00010"] = submit_date
    if eng.get("date"):
        v["ATB00160"] = _date(eng["date"], "engagement.date")
    for name, tag in _CONSENTS.items():
        v[tag] = "1" if consents.get(name) else "2"
    if corporate:
        v.update({"ATC00050": "1", "ATC00070": corporate[0], "ATC00080": corporate[1]})
    if consumption:
        v.update({"ATC00100": "1", "ATC00120": consumption[0], "ATC00130": consumption[1]})
    return {k: x for k, x in v.items() if x not in (None, "")}

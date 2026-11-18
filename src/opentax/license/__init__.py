"""OpenTax のライセンスキー（DESIGN §33）。

有料の版（PLATINUM など）を買った人に渡す文字列。発行元の秘密鍵で署名してあり、
ここにある公開鍵だけで確かめられる。確かめるときにネットにはつながない（画面の約束「外部に通信しない」を守る）。

キーの形: ``OTX1-<中身(base64url)>.<署名(base64url)>``
中身（JSON）: v（版）・kid（鍵の番号）・id（発行番号）・plan（製品）・office（事務所名）・
seats（使う人の数）・iat（発行日）・exp（有効期限の日。この日まで使える）
署名: Ed25519。署名する文字列は ``b"OTX1." + 中身のバイト列``。

ソースは MIT なので、この確認は「正直な利用者に、買った範囲を分かりやすく示す」ためのもの。
外せないようにする仕組み（難読化・通信での認証）は入れない。
"""
from __future__ import annotations

import base64
import datetime
import json
import secrets
from dataclasses import dataclass, field

from . import ed25519

PREFIX = "OTX1-"
SIGNED_PREFIX = b"OTX1."

# 発行元の公開鍵（鍵を替えるときは kid を増やして足す。古い鍵で出したキーも期限までは使える）
PUBLIC_KEYS: dict[str, str] = {
    "k1": "593c656ddeddd6d3bb42172d06650343759eccdebdc6b76bfc5e7e12ea2bca14",
}

PLANS: dict[str, dict] = {
    # 製品名は DESIGN §33.2。includes は、そのキーで使える製品
    "platinum": {"name": "OpenTax PLATINUM", "includes": ["platinum", "red", "black", "blue", "white", "gold", "green", "pink"]},
}


# キーがないと止めるか（製品ごと）。値段と売り始めの日が決まるまでは False（キーがなくても全部使える。画面に案内だけ出す）
REQUIRED: dict[str, bool] = {"platinum": False}

# 使えなくしたキーの発行番号（返金・チャージバック・秘密鍵の漏れで出し直したもの）。版の更新で配る（DESIGN §33.4）
REVOKED: frozenset[str] = frozenset()

class LicenseError(ValueError):
    pass


@dataclass
class License:
    id: str
    plan: str
    office: str
    seats: int
    iat: datetime.date
    exp: datetime.date
    kid: str
    raw: dict = field(repr=False, default_factory=dict)

    def covers(self, product: str) -> bool:
        return product in PLANS.get(self.plan, {}).get("includes", [self.plan])

    def days_left(self, today: datetime.date) -> int:
        return (self.exp - today).days

    def to_dict(self, today: datetime.date | None = None) -> dict:
        d = {"id": self.id, "plan": self.plan, "plan_name": PLANS.get(self.plan, {}).get("name", self.plan),
             "office": self.office, "seats": self.seats, "iat": self.iat.isoformat(), "exp": self.exp.isoformat()}
        if today:
            d["days_left"] = self.days_left(today)
            d["valid"] = d["days_left"] >= 0
        return d


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


_B64URL = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def _clean(key: str) -> str:
    # メールや画面から写すときに入る空白・改行・BOM・ゼロ幅の文字を取る
    key = key.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    return "".join(key.split())


def parse(key: str, public_keys: dict[str, str] | None = None) -> License:
    """署名を確かめて中身を返す。期限切れでも返す（期限は呼んだ側で見る）。"""
    key = _clean(key)
    if not key.startswith(PREFIX) or key.count(".") != 1:
        raise LicenseError("ライセンスキーの形が違います（OTX1- で始まる文字列を、そのまま貼り付けてください）")
    body, sig = key[len(PREFIX):].split(".")
    if not body or not sig or not set(body + sig) <= _B64URL:
        raise LicenseError("ライセンスキーが途中で切れているか、文字が変わっています")
    try:
        payload = _b64d(body)
        signature = _b64d(sig)
    except ValueError as e:
        raise LicenseError("ライセンスキーが途中で切れているか、文字が変わっています") from e
    # 署名を確かめるまでは中身を読まない（壊れた中身で JSON の読み取りが落ちないように）。
    # どの鍵で確かめるかは中身の kid で決めるので、kid だけは文字列として探す
    keys = PUBLIC_KEYS if public_keys is None else public_keys
    pub = None
    for kid, pk in keys.items():
        if ed25519.verify(bytes.fromhex(pk), SIGNED_PREFIX + payload, signature):
            pub = kid
            break
    if pub is None:
        raise LicenseError("ライセンスキーの署名が合いません（文字が変わっているか、OpenTax が発行したものではないか、"
                           "古い鍵で作られています。OpenTax を新しくしてください）")
    try:
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict) or data.get("v") != 1 or data.get("kid") != pub:
            raise LicenseError("この版の OpenTax では読めないライセンスキーです。OpenTax を新しくしてください")
        lic = License(id=str(data["id"]), plan=str(data["plan"]), office=str(data.get("office", "")),
                      seats=int(data.get("seats", 1)), iat=datetime.date.fromisoformat(data["iat"]),
                      exp=datetime.date.fromisoformat(data["exp"]), kid=pub, raw=data)
    except LicenseError:
        raise
    except Exception as e:  # 署名は合っているのに中身が読めない（発行の誤り）
        raise LicenseError("ライセンスキーの中身が読めません。発行元へお知らせください") from e
    if lic.id in REVOKED:
        raise LicenseError("このライセンスキーは使えなくなっています（返金・支払いの取消など）。発行元へお問い合わせください")
    return lic


def check(key: str, product: str, today: datetime.date | None = None) -> License:
    """その製品を今日使えるか。使えなければ理由つきで LicenseError。"""
    today = today or datetime.date.today()
    lic = parse(key)
    if not lic.covers(product):
        raise LicenseError(f"このライセンスキー（{PLANS.get(lic.plan, {}).get('name', lic.plan)}）では使えない製品です")
    if lic.days_left(today) < 0:
        raise LicenseError(f"ライセンスの有効期限（{lic.exp.isoformat()}）を過ぎています。更新してください")
    return lic


def issue(secret_hex: str, plan: str, office: str, exp: datetime.date, *, seats: int = 1,
          iat: datetime.date | None = None, kid: str = "k1", license_id: str | None = None) -> str:
    """ライセンスキーを作る（発行する側だけが使う。秘密鍵はリポジトリに入れない）。"""
    if plan not in PLANS:
        raise LicenseError(f"知らない製品です: {plan}（{', '.join(PLANS)}）")
    office = office.strip()
    if not office:
        raise LicenseError("事務所名が空です")
    iat = iat or datetime.date.today()
    if int(seats) < 1:
        raise LicenseError("使う人の数は1以上です")
    if exp < iat:
        raise LicenseError("有効期限が発行日より前です")
    if kid not in PUBLIC_KEYS:
        raise LicenseError(f"鍵の番号 {kid} が PUBLIC_KEYS にありません（画面で確かめられないキーになります）")
    data = {"v": 1, "kid": kid, "id": license_id or secrets.token_hex(8), "plan": plan, "office": office,
            "seats": int(seats), "iat": iat.isoformat(), "exp": exp.isoformat()}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    try:
        secret = bytes.fromhex(secret_hex.strip().lstrip("\ufeff"))
    except ValueError as e:
        raise LicenseError("秘密鍵のファイルが16進64文字ではありません") from e
    if len(secret) != 32:
        raise LicenseError("秘密鍵のファイルが16進64文字ではありません")
    if ed25519.public_key(secret).hex() != PUBLIC_KEYS[kid]:
        raise LicenseError(f"秘密鍵が公開鍵 {kid} と組になっていません")
    return PREFIX + _b64e(payload) + "." + _b64e(ed25519.sign(secret, SIGNED_PREFIX + payload))

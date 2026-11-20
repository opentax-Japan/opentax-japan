"""ライセンスキー（DESIGN §33）: Ed25519 は RFC 8032 の試験ベクトル、キーは発行→確認・改ざん・期限・製品。
サーバー（server/stripe-license/worker.js）で作ったキーをこの検証で読めることも見る（node があるとき）。"""
from __future__ import annotations

import datetime
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from opentax import license as L
from opentax.license import ed25519

ROOT = Path(__file__).resolve().parents[2]

# RFC 8032 第7.1節 TEST 1・TEST 2・TEST 3
VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]

SECRET = "11" * 32
PUB = {"t1": ed25519.public_key(bytes.fromhex(SECRET)).hex()}
TODAY = datetime.date(2026, 11, 19)


def _key(**kw):
    args = dict(plan="platinum", office="見本税理士事務所", exp=datetime.date(2027, 11, 18), iat=TODAY, kid="t1")
    args.update(kw)
    return L.issue(SECRET, **args)


class Ed25519Test(unittest.TestCase):
    def test_rfc8032(self):
        for sk, pk, msg, sig in VECTORS:
            with self.subTest(pk=pk[:8]):
                self.assertEqual(ed25519.public_key(bytes.fromhex(sk)).hex(), pk)
                self.assertEqual(ed25519.sign(bytes.fromhex(sk), bytes.fromhex(msg)).hex(), sig)
                self.assertTrue(ed25519.verify(bytes.fromhex(pk), bytes.fromhex(msg), bytes.fromhex(sig)))
                self.assertFalse(ed25519.verify(bytes.fromhex(pk), bytes.fromhex(msg) + b"x", bytes.fromhex(sig)))

    def test_server_public_key_matches(self):
        """サーバーの設定（wrangler.toml）の公開鍵が PUBLIC_KEYS と同じ（鍵を替えたときの直し忘れ）。"""
        import re
        toml = (ROOT / "server" / "stripe-license" / "wrangler.toml").read_text(encoding="utf-8")
        kid = re.search(r'^LICENSE_KID = "(.+)"', toml, re.M).group(1)
        pub = re.search(r'^LICENSE_PUBLIC_HEX = "(.+)"', toml, re.M).group(1)
        self.assertEqual(L.PUBLIC_KEYS[kid], pub)

    def test_published_key_is_valid_point(self):
        for pk in L.PUBLIC_KEYS.values():
            self.assertIsNotNone(ed25519._decompress(bytes.fromhex(pk)))


@mock.patch.object(L, "PUBLIC_KEYS", PUB)
class LicenseTest(unittest.TestCase):
    def test_issue_and_check(self):
        key = _key(seats=3)
        lic = L.check(key, "platinum", TODAY)
        self.assertEqual((lic.office, lic.seats, lic.exp), ("見本税理士事務所", 3, datetime.date(2027, 11, 18)))
        self.assertEqual(L.check(key, "red", TODAY).plan, "platinum")  # PLATINUM は全部使える
        self.assertEqual(L.parse(key[:20] + "\n " + key[20:]).id, lic.id)  # メールから写したときの改行・空白
        self.assertEqual(L.check(key, "platinum", datetime.date(2027, 11, 18)).days_left(datetime.date(2027, 11, 18)), 0)

    def test_rejects(self):
        key = _key()
        body, sig = key[len(L.PREFIX):].split(".")
        forged = L._b64e(L._b64d(body).replace(b"2027-11-18", b"2099-11-18"))
        with self.assertRaisesRegex(L.LicenseError, "署名"):
            L.parse(L.PREFIX + forged + "." + sig)
        with self.assertRaisesRegex(L.LicenseError, "形"):
            L.parse("abc")
        with self.assertRaisesRegex(L.LicenseError, "期限"):
            L.check(key, "platinum", datetime.date(2027, 11, 19))
        with self.assertRaisesRegex(L.LicenseError, "鍵"):
            L.parse(key, {})
        with self.assertRaisesRegex(L.LicenseError, "知らない製品"):
            _key(plan="diamond")
        with self.assertRaisesRegex(L.LicenseError, "組"):
            L.issue("22" * 32, "platinum", "x", TODAY, kid="t1", iat=TODAY)

    def test_malformed_never_raises_other_errors(self):
        """どんな文字列でも LicenseError だけ（画面が「通す」側に倒れないように。検証報告 中1）。"""
        key = _key()
        deep = L.PREFIX + L._b64e(b"[" * 100000) + "." + L._b64e(b"\0" * 64)
        for bad in [deep, key + ".x", key.replace(".", ""), key + "=", key.replace("-", "+", 1) if "-" in key[5:] else key + "+",
                    L.PREFIX + ".", "", "OTX1-" + "あ" + "." + "b"]:
            with self.subTest(bad=bad[:30]):
                with self.assertRaises(L.LicenseError):
                    L.parse(bad)

    def test_signed_but_broken_payload(self):
        for payload in [b"[1]", b'{"v":1,"kid":"t1"}', b'{"v":2,"kid":"t1","id":"a","plan":"platinum","iat":"2026-01-01","exp":"2027-01-01"}',
                        b'{"v":1,"kid":"t1","id":"a","plan":"platinum","seats":1e999,"iat":"2026-01-01","exp":"2027-01-01"}']:
            k = L.PREFIX + L._b64e(payload) + "." + L._b64e(ed25519.sign(bytes.fromhex(SECRET), L.SIGNED_PREFIX + payload))
            with self.subTest(payload=payload[:20]):
                with self.assertRaises(L.LicenseError):
                    L.parse(k)

    def test_bom_and_zero_width(self):
        key = _key()
        self.assertEqual(L.parse("﻿" + key[:10] + "​" + key[10:]).office, "見本税理士事務所")

    def test_revoked(self):
        key = _key(license_id="dead")
        with mock.patch.object(L, "REVOKED", frozenset({"dead"})):
            with self.assertRaisesRegex(L.LicenseError, "使えなく"):
                L.parse(key)

    def test_issue_checks(self):
        with self.assertRaisesRegex(L.LicenseError, "1以上"):
            _key(seats=0)
        with self.assertRaisesRegex(L.LicenseError, "前"):
            _key(exp=datetime.date(2026, 1, 1))
        with self.assertRaisesRegex(L.LicenseError, "PUBLIC_KEYS"):
            _key(kid="zz")
        with self.assertRaisesRegex(L.LicenseError, "16進"):
            L.issue("xyz", "platinum", "x", datetime.date(2027, 1, 1), kid="t1", iat=TODAY)


class Ed25519EdgeTest(unittest.TestCase):
    def test_small_order_public_key_rejected(self):
        identity = (1).to_bytes(32, "little")  # y=1 は単位元
        self.assertFalse(ed25519.verify(identity, b"anything", identity + bytes(32)))

    def test_noncanonical_x_zero_with_sign(self):
        y_minus_one = ((2**255 - 19) - 1) | (1 << 255)  # (0,-1) に符号ビット1
        self.assertIsNone(ed25519._decompress(y_minus_one.to_bytes(32, "little")))

    def test_s_not_reduced(self):
        sk, pk, msg, sig = VECTORS[0]
        s = int.from_bytes(bytes.fromhex(sig)[32:], "little") + ed25519._L
        bad = bytes.fromhex(sig)[:32] + s.to_bytes(32, "little")
        self.assertFalse(ed25519.verify(bytes.fromhex(pk), b"", bad))


@unittest.skipUnless(shutil.which("node"), "node がない")
class ServerTest(unittest.TestCase):
    def test_server_key_verifies_in_python(self):
        """サーバー（Cloudflare Workers と同じ WebCrypto）で作ったキーを、この Python の検証で読めること。"""
        sk = os.urandom(32).hex()
        pk = ed25519.public_key(bytes.fromhex(sk)).hex()
        out = subprocess.run(["node", "tests/server/license_worker_test.mjs", sk, pk], cwd=ROOT, capture_output=True,
                             encoding="utf-8", check=True).stdout.strip()
        lic = L.parse(out, {"test": pk})
        self.assertEqual((lic.plan, lic.office, lic.seats), ("platinum", "見本<税理士>事務所", 2))
        self.assertEqual((lic.iat, lic.exp), (datetime.date(2026, 11, 20), datetime.date(2027, 11, 20)))


if __name__ == "__main__":
    unittest.main()

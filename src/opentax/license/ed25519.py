"""Ed25519 の署名と検証（RFC 8032 第5.1節・第6節の参照実装をもとに書いたもの。外部の部品を使わない）。

ブラウザ（Pyodide）でも CLI でも同じコードでライセンスを確かめるため、標準ライブラリだけで書く。
速さより読みやすさを優先している（ライセンスの確認は1回だけなので十分）。
署名の作成は発行する側（tools・サーバー）だけが使う。
"""
from __future__ import annotations

import hashlib

_p = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493


def _sha512(b: bytes) -> bytes:
    return hashlib.sha512(b).digest()


def _inv(x: int) -> int:
    return pow(x, _p - 2, _p)


_d = -121665 * _inv(121666) % _p
_sqrt_m1 = pow(2, (_p - 1) // 4, _p)


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _p
    C = 2 * P[3] * Q[3] * _d % _p
    D = 2 * P[2] * Q[2] % _p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _p, G * H % _p, F * G % _p, E * H % _p)


def _mul(s: int, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q) -> bool:
    if (P[0] * Q[2] - Q[0] * P[2]) % _p != 0:
        return False
    return (P[1] * Q[2] - Q[1] * P[2]) % _p == 0


def _recover_x(y: int, sign: int):
    if y >= _p:
        return None
    x2 = (y * y - 1) * _inv(_d * y * y + 1) % _p
    if x2 == 0:
        return None if sign else 0  # x=0 で符号ビット1は正準でない形なので断る
    x = pow(x2, (_p + 3) // 8, _p)
    if (x * x - x2) % _p != 0:
        x = x * _sqrt_m1 % _p
    if (x * x - x2) % _p != 0:
        return None
    if (x & 1) != sign:
        x = _p - x
    return x


_gy = 4 * _inv(5) % _p
_gx = _recover_x(_gy, 0)
_G = (_gx, _gy, 1, _gx * _gy % _p)
_IDENTITY = (0, 1, 1, 0)


def _compress(P) -> bytes:
    zinv = _inv(P[2])
    x = P[0] * zinv % _p
    y = P[1] * zinv % _p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _p)


def _expand(secret: bytes):
    if len(secret) != 32:
        raise ValueError("秘密鍵は32バイトです")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret: bytes) -> bytes:
    a, _ = _expand(secret)
    return _compress(_mul(a, _G))


def sign(secret: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(secret)
    A = _compress(_mul(a, _G))
    r = int.from_bytes(_sha512(prefix + msg), "little") % _L
    R = _compress(_mul(r, _G))
    h = int.from_bytes(_sha512(R + A + msg), "little") % _L
    s = (r + h * a) % _L
    return R + int.to_bytes(s, 32, "little")


def verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    if not A or _equal(_mul(8, A), _IDENTITY):
        return False  # 位数の小さい公開鍵（どんな文にも署名が作れる）は断る
    R = _decompress(signature[:32])
    if not R:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(_sha512(signature[:32] + public + msg), "little") % _L
    sB = _mul(s, _G)
    hA = _mul(h, A)
    return _equal(sB, _add(R, hA))

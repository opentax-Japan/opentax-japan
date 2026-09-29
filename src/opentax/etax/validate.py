"""申告データを公式の手続XSD で検証する。xmlschema（開発・検証用の依存）を使う。

国税庁の XSD は、共通語彙の restriction が XSD 1.0 に厳密には合わないため、スキーマの読込みは lax にする。
文書の検証は厳密に行い、誤りを全件返す。
"""

from __future__ import annotations

from pathlib import Path


class ValidationUnavailable(Exception):
    """xmlschema が入っていないとき。"""


_cache: dict[str, object] = {}


def validate_xtx(xml: bytes, schema_root: Path, procedure_xsd: str) -> list[str]:
    try:
        import xmlschema
    except ImportError as e:
        raise ValidationUnavailable("XSD の検証には xmlschema が必要です（pip install xmlschema）") from e
    path = str(schema_root / Path(*procedure_xsd.split("/")))
    schema = _cache.get(path)
    if schema is None:
        schema = xmlschema.XMLSchema(path, validation="lax")
        _cache[path] = schema
    return [f"{e.path}: {e.reason}" for e in schema.iter_errors(xml.decode("utf-8"))]

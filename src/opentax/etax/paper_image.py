"""紙の様式の画像を、公表元の PDF から作る（開発・サイトの組み立てのときだけ使う。pymupdf と Pillow が要る）。

- スキャン画像の PDF（国税庁の別表など）: ページの中の画像をそのまま取り出す
- 文字と線でできた PDF（render: true、または画像のないページ）: 位置のファイルの image_size の大きさに描く
座標は、どちらも image_size の画素（位置のファイルと同じ）。
"""

from __future__ import annotations

import io
from pathlib import Path


def page_image(pdf: Path, page: int, image_size: list[int] | None = None, render: bool = False, rotate: int = 0):
    import pymupdf
    from PIL import Image

    doc = pymupdf.open(pdf)
    pg = doc[page - 1]
    images = pg.get_images()
    if images and not render:
        img = Image.open(io.BytesIO(doc.extract_image(images[0][0])["image"])).convert("L")
    else:
        w, h = image_size or (2481, 3508)
        r = pg.rect
        if rotate in (90, 270):
            pg.set_rotation((pg.rotation + rotate) % 360)
            r = pg.rect
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(w / r.width, h / r.height), colorspace=pymupdf.csGRAY, alpha=False)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    if image_size and img.size != tuple(image_size):
        img = img.resize(tuple(image_size), Image.LANCZOS)
    return img


def words(pdf: Path, page: int, image_size: list[int], rotate: int = 0) -> list[tuple[list[int], str]]:
    """文字の入った PDF の、文字のかたまりと位置（image_size の画素）。欄の位置を決める手がかりにする。"""
    import pymupdf

    doc = pymupdf.open(pdf)
    pg = doc[page - 1]
    if rotate:
        pg.set_rotation((pg.rotation + rotate) % 360)
    sx, sy = image_size[0] / pg.rect.width, image_size[1] / pg.rect.height
    m = pg.rotation_matrix
    out = []
    for x0, y0, x1, y1, text, *_ in pg.get_text("words"):
        r = pymupdf.Rect(x0, y0, x1, y1) * m
        out.append(([round(r.x0 * sx), round(r.y0 * sy), round(r.x1 * sx), round(r.y1 * sy)], text))
    return out

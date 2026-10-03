"""Web 画面（静的サイト）を組み立てる。標準ライブラリだけ。

    python web/build.py            → web/_site/ にサイトを作る（GitHub Pages に置くもの）

- Pyodide と Python の部品（xmlschema・elementpath）は vendor.json の URL から取り、SHA256 を照合する。
  取得したものは .cache/web/ に置き、リポジトリには入れない。サイトはこれらを自分から配る（CDN を使わない）
- OpenTax 本体は app.zip にまとめる（src/opentax・spec-manifest・架空の法人の見本）
- 国税庁の仕様書は入れない（利用者が画面で e-tax19.CAB を選ぶ）
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

WEB = Path(__file__).resolve().parent
REPO = WEB.parent
SITE = WEB / "_site"
CACHE = REPO / ".cache" / "web"
STATIC = ["index.html", "style.css", "app.js", "worker.js", "bridge.py", "payroll.html", "payroll.js"]
PYODIDE_FILES = ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]
SAMPLES = ["tests/cases/open-shoji/input.json", "tests/cases/open-shoji-tokyo/input.json",
           "tests/cases/open-shoji-tokyo/payroll_2026.json", "tests/cases/open-shoji-tokyo/gaikyo.json",
           "tests/cases/open-shoji-tokyo/uchiwake_supplement.json",
           "tests/cases/open-shoji-tokyo/科目残高一覧表_架空_TKC形式.txt",
           "tests/cases/open-shoji-tokyo/科目残高推移表_架空_TKC形式.txt"]


def fetch(url: str, sha256: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / url.rsplit("/", 1)[-1]
    if not dest.exists():
        print(f"取得: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "opentax-japan web build"})
        with urllib.request.urlopen(req, timeout=300) as res, open(dest.with_suffix(".part"), "wb") as f:
            shutil.copyfileobj(res, f)
        dest.with_suffix(".part").replace(dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if digest != sha256:
        dest.unlink()
        sys.exit(f"SHA256 が vendor.json と違います: {dest.name}（{digest}）")
    return dest


def app_zip(dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted((REPO / "src" / "opentax").rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix in (".py", ".json"):
                z.write(path, path.relative_to(REPO).as_posix())
        for path in sorted((REPO / "spec-manifest").glob("*.json")):
            z.write(path, path.relative_to(REPO).as_posix())
        for sample in SAMPLES:
            z.write(REPO / sample, sample)


def paper_images(width: int = 1654) -> None:
    """紙の別表の画像（国税庁の様式 PDF の中のスキャン画像）を、幅を縮めた JPEG にする。PDF は SHA256 で照合する。"""
    import io
    try:
        import pymupdf
        from PIL import Image
    except ImportError:
        sys.exit("紙の別表の画像を作るには pymupdf と Pillow が要ります（pip install pymupdf pillow）")
    for manifest_path in sorted((REPO / "src" / "opentax" / "etax" / "paper").glob("*/manifest.json")):
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        out_dir = SITE / "forms" / m["edition"]
        out_dir.mkdir(parents=True, exist_ok=True)
        for form_id, entry in m["forms"].items():
            if not (manifest_path.parent / f"{form_id}.json").exists():
                continue
            f = m["files"][entry["file"]]
            doc = pymupdf.open(fetch(f["url"], f["sha256"]))
            ref = doc[entry["page"] - 1].get_images()[0][0]
            img = Image.open(io.BytesIO(doc.extract_image(ref)["image"])).convert("L")
            img = img.resize((width, round(width * img.height / img.width)), Image.LANCZOS)
            img.save(out_dir / f"{form_id}.jpg", quality=80, optimize=True, progressive=True)


def build() -> None:
    vendor = json.loads((WEB / "vendor.json").read_text(encoding="utf-8"))
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "pyodide").mkdir(parents=True)
    (SITE / "wheels").mkdir()

    tarball = fetch(vendor["pyodide"]["url"], vendor["pyodide"]["sha256"])
    with tarfile.open(tarball, "r:bz2") as tar:
        members = {Path(m.name).name: m for m in tar.getmembers() if m.isfile()}
        for name in PYODIDE_FILES:
            if name not in members:
                sys.exit(f"Pyodide の中に {name} がありません")
            with tar.extractfile(members[name]) as src, open(SITE / "pyodide" / name, "wb") as out:
                shutil.copyfileobj(src, out)
    wheels = []
    for w in vendor["wheels"]:
        path = fetch(w["url"], w["sha256"])
        shutil.copy(path, SITE / "wheels" / path.name)
        wheels.append(path.name)

    paper_images()
    for name in STATIC:
        shutil.copy(WEB / name, SITE / name)
    app_zip(SITE / "app.zip")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    files = {p.relative_to(SITE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(SITE.rglob("*")) if p.is_file()}
    (SITE / "build.json").write_text(json.dumps({"pyodide": vendor["pyodide"]["version"], "wheels": wheels, "files": files},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    size = sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file())
    print(f"できました: {SITE}（{len(files) + 1} ファイル、{size / 1e6:.1f} MB）")


if __name__ == "__main__":
    build()

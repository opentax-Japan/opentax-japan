"""e-Tax 仕様書の取得・展開・SHA256 照合。標準ライブラリだけ。

流れ: manifest の URL から CAB を取得 → SHA256 を照合 → CAB を展開 → CAB 内のフォルダ構成のままキャッシュに置く。
国税庁の仕様書は著作物のため、キャッシュ（.cache/）はリポジトリに入れない。コミットするのは manifest だけ。

manifest の packages[].extract
- "closure": 手続XSD（procedures[].xsd）から include / import をたどって届くファイルだけを取り出す（e-tax19 は全税目で 17,000 ファイル以上あるため）
- "all": CAB の中身をすべて取り出す
"""

from __future__ import annotations

import datetime
import hashlib
import json
import posixpath
import re
import shutil
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .cab import Cabinet, CabError

USER_AGENT = "opentax-japan (+https://github.com/opentax-Japan/opentax-japan)"
_XSD_REF_TAGS = ("include", "import", "redefine")


class SpecError(Exception):
    """仕様書の取得・照合を続けられないとき。"""


@dataclass
class Change:
    kind: str  # "CAB" | "ファイル"
    target: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.target}: {self.detail}"


@dataclass
class Result:
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, retries: int = 4) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as res, open(part, "wb") as f:
                shutil.copyfileobj(res, f, 1 << 20)
            part.replace(dest)
            return
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** (attempt + 1))


def xsd_references(data: bytes, path: str) -> list[str]:
    """XSD の include / import / redefine の参照先を、CAB 内のパスで返す。"""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise SpecError(f"XSD を読めません: {path}（{e}）") from e
    base = posixpath.dirname(path)
    refs = []
    for el in root:
        tag = el.tag.rsplit("}", 1)[-1]
        loc = el.get("schemaLocation")
        if tag in _XSD_REF_TAGS and loc:
            refs.append(posixpath.normpath(posixpath.join(base, loc)))
    return refs


def xsd_closure(read, roots: list[str]) -> dict[str, bytes]:
    """roots から include / import でたどれる XSD をすべて返す。read(path) は無ければ None を返す。"""
    found: dict[str, bytes] = {}
    stack = list(roots)
    missing = set()
    while stack:
        path = stack.pop()
        if path in found or path in missing:
            continue
        data = read(path)
        if data is None:
            missing.add(path)
            continue
        found[path] = data
        stack.extend(xsd_references(data, path))
    if missing:
        raise SpecError("参照先の XSD が CAB にありません: " + ", ".join(sorted(missing)))
    return found


_VERSION_RE = re.compile(r"version[：:]\s*([0-9.]+)")
_DATE_RE = re.compile(r"Date：\s*(\d{4})年(\d{2})月(\d{2})日")


def form_info(form_id: str, path: str, data: bytes) -> dict:
    """帳票XSD の注記から版・日付・group 名を読む。"""
    text = data.decode("utf-8")
    version = _VERSION_RE.search(text)
    date = _DATE_RE.search(text)
    group = re.search(r'<xsd:group name="(' + re.escape(form_id) + r'-[0-9-]+group)"', text)
    if not (version and group):
        raise SpecError(f"帳票XSD の版または group 名を読めません: {path}")
    return {
        "xsd": path,
        "version": version.group(1),
        "xsd_documentation_date": "-".join(date.groups()) if date else None,
        "group": group.group(1),
    }


class SpecSet:
    """1つの仕様セット（例: ksk2-2026-08）の manifest とキャッシュ。"""

    def __init__(self, manifest_path: Path, cache_root: Path):
        self.manifest_path = manifest_path
        if not manifest_path.exists():
            raise SpecError(f"manifest がありません: {manifest_path}")
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.cache = cache_root / self.manifest["spec_set"]

    # --- キャッシュの場所 ---
    def cab_path(self, pkg: dict) -> Path:
        return self.cache / "cab" / f"{pkg['name']}.CAB"

    def file_path(self, pkg: dict, path: str) -> Path:
        return self.cache / "files" / pkg["name"] / Path(*path.split("/"))

    def schema_root(self) -> Path:
        """XSD 一式の置き場所（相対参照がそのまま通る）。"""
        pkg = next(p for p in self.manifest["packages"] if p["extract"] == "closure")
        return self.cache / "files" / pkg["name"]

    # --- 展開 ---
    def _select(self, pkg: dict, cab: Cabinet) -> dict[str, bytes]:
        """CAB から取り出すファイルを選び、中身を返す。"""
        if pkg["extract"] == "all":
            return {e.path: cab.read(e) for e in _in_folder_order(cab.entries)}
        if pkg["extract"] != "closure":
            raise SpecError(f"extract の指定が不正です: {pkg['name']} {pkg['extract']}")
        entries = {e.path: e for e in cab.entries}
        roots = [p["xsd"] for p in self.manifest["procedures"]]
        return xsd_closure(lambda p: cab.read(entries[p]) if p in entries else None, roots)

    def _write(self, pkg: dict, files: dict[str, bytes]) -> None:
        for path, data in files.items():
            dest = self.file_path(pkg, path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

    def _open_cab(self, pkg: dict) -> Cabinet:
        try:
            return Cabinet(self.cab_path(pkg).read_bytes())
        except CabError as e:
            raise SpecError(f"{pkg['name']}.CAB を展開できません: {e}") from e

    # --- 初回：manifest に SHA256 等を書き込む ---
    def init(self, force: bool = False) -> Result:
        if not force and any(p.get("sha256") for p in self.manifest["packages"]):
            raise SpecError("manifest には既に SHA256 が入っています。作り直すときは --force を付けてください")
        result = Result()
        for pkg in self.manifest["packages"]:
            cab_file = self.cab_path(pkg)
            if force or not cab_file.exists():
                download(pkg["url"], cab_file)
            cab = self._open_cab(pkg)
            files = self._select(pkg, cab)
            self._write(pkg, files)
            pkg["sha256"] = sha256_file(cab_file)
            pkg["size_bytes"] = cab_file.stat().st_size
            pkg["files"] = [{"path": p, "sha256": sha256_bytes(d)} for p, d in sorted(files.items())]
            result.notes.append(f"{pkg['name']}: CAB {pkg['size_bytes']:,} バイト、{len(files)} ファイル")
            if pkg["extract"] == "closure":
                self._fill_forms(files)

        self.manifest["acquired_at"] = datetime.date.today().isoformat()
        self.save()
        return result

    def _fill_forms(self, files: dict[str, bytes]) -> None:
        # 手続XSD が直接 include している帳票XSD を現行版とする（フォルダ内の最大番号では判定しない）
        included = []
        for proc in self.manifest["procedures"]:
            data = files[proc["xsd"]]
            proc["xsd_sha256"] = sha256_bytes(data)
            version = _VERSION_RE.search(data.decode("utf-8"))
            if not version:
                raise SpecError(f"手続XSD の版を読めません: {proc['xsd']}")
            proc["version"] = version.group(1)
            included += xsd_references(data, proc["xsd"])
        for form in self.manifest["forms"]:
            hits = [p for p in included if posixpath.basename(p).startswith(form["form_id"] + "-")]
            if len(hits) != 1:
                raise SpecError(f"手続XSD から帳票XSD を1つに決められません: {form['form_id']} {hits}")
            form.update(form_info(form["form_id"], hits[0], files[hits[0]]))
            form["xsd_sha256"] = sha256_bytes(files[hits[0]])

    def save(self) -> None:
        text = json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n"
        with open(self.manifest_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    # --- 通常：キャッシュを manifest と照合する ---
    def verify(self) -> Result:
        """足りない CAB は取得し、足りないファイルは展開して補う。書き換わっていたら「変更あり」とする。"""
        self._require_initialized()
        result = Result()
        for pkg in self.manifest["packages"]:
            cab_file = self.cab_path(pkg)
            if not cab_file.exists():
                download(pkg["url"], cab_file)
                result.notes.append(f"{pkg['name']}.CAB を取得しました")
            if sha256_file(cab_file) != pkg["sha256"]:
                result.changes.append(Change("CAB", f"{pkg['name']}.CAB", "SHA256 が manifest と違います"))
                continue

            missing = [f for f in pkg["files"] if not self.file_path(pkg, f["path"]).exists()]
            if missing:
                cab = self._open_cab(pkg)
                wanted = {f["path"] for f in missing}
                self._write(pkg, {e.path: cab.read(e) for e in _in_folder_order(cab.entries) if e.path in wanted})
                result.notes.append(f"{pkg['name']}: {len(missing)} ファイルを CAB から展開しました")

            for f in pkg["files"]:
                path = self.file_path(pkg, f["path"])
                if not path.exists():
                    result.changes.append(Change("ファイル", f["path"], "CAB にありません"))
                elif sha256_file(path) != f["sha256"]:
                    result.changes.append(Change("ファイル", f["path"], "SHA256 が manifest と違います"))
        return result

    # --- 改訂の検知：国税庁から取り直して manifest と比べる ---
    def check_remote(self) -> Result:
        """キャッシュを使わずに CAB を取り直し、manifest との差分（ファイルの追加・削除・変更）を返す。キャッシュは書き換えない。"""
        self._require_initialized()
        result = Result()
        with tempfile.TemporaryDirectory() as tmp:
            for pkg in self.manifest["packages"]:
                dest = Path(tmp) / f"{pkg['name']}.CAB"
                download(pkg["url"], dest)
                if sha256_file(dest) == pkg["sha256"]:
                    continue
                result.changes.append(Change("CAB", f"{pkg['name']}.CAB", "国税庁の公開ファイルが manifest と違います"))
                try:
                    files = self._select(pkg, Cabinet(dest.read_bytes()))
                except (CabError, SpecError) as e:
                    result.notes.append(f"{pkg['name']}: 新しい CAB の中身を比べられません（{e}）")
                    continue
                old = {f["path"]: f["sha256"] for f in pkg["files"]}
                new = {p: sha256_bytes(d) for p, d in files.items()}
                for p in sorted(new.keys() - old.keys()):
                    result.changes.append(Change("ファイル", p, "追加"))
                for p in sorted(old.keys() - new.keys()):
                    result.changes.append(Change("ファイル", p, "削除"))
                for p in sorted(old.keys() & new.keys()):
                    if old[p] != new[p]:
                        result.changes.append(Change("ファイル", p, "変更"))
        return result

    def _require_initialized(self) -> None:
        if not all(p.get("sha256") for p in self.manifest["packages"]):
            raise SpecError("manifest に SHA256 がまだありません。先に --init を実行してください")


def _in_folder_order(entries):
    return sorted(entries, key=lambda e: (e.folder, e.offset))

"""フォルダから要る書類を集める（DESIGN §24.8）。

「フォルダを渡して『○○を頼む』と言えば、中から要る種類を見つけて一気に作る」ためのもの。
- ファイル名・フォルダ名では決めない。1つずつ中身を読み（intake.read_files）、種類を見分ける。
- 申告の種類（task）ごとに使う種類を決め、同じ種類が複数あれば「いちばん新しい期」（--period があればその期）を選ぶ。
  期で決まらなければ新しいファイル（更新日時）を選び、ほかを候補として一覧に出す。
- 選んだファイルで読み取り層→計算層（今までと同じ）を通す。使ったもの・使わなかったもの・足りないものを一覧にする。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import intake

READ_EXT = (".csv", ".txt", ".tsv", ".json", ".xtx", ".xml")
AI_EXT = intake.AI_TARGET_EXT + intake.SHEET_EXT
MAX_BYTES = 20 * 1024 * 1024

TASK_ALIASES = {"法人": "corporate", "法人税": "corporate", "corporate": "corporate",
                "個人": "personal", "所得税": "personal", "確定申告": "personal", "personal": "personal",
                "相続": "souzoku", "相続税": "souzoku", "souzoku": "souzoku",
                "宿泊税": "shukuhaku", "shukuhaku": "shukuhaku",
                "事業所税": "jigyosho", "jigyosho": "jigyosho"}
TASK_LABELS = {"corporate": "法人税・地方税", "personal": "個人の所得税", "souzoku": "相続税", "shukuhaku": "宿泊税", "jigyosho": "事業所税"}

# 申告の種類ごとに使うファイルの種類（intake の files[].kind）。1つだけ使う種類（single）と、全部使う種類
TASK_KINDS = {
    "corporate": {"single": ["balance", "xtx", "trend", "gaikyo", "supplement", "shohi", "office"], "all": ["payroll", "intake"]},
    "personal": {"single": ["kojin_input", "kojin_balance_business", "kojin_balance_real_estate", "kojin_xtx"], "all": ["ai_personal"]},
    "souzoku": {"single": ["souzoku_input"], "all": ["ai_souzoku"]},
    "shukuhaku": {"single": ["shukuhaku_facility", "shukuhaku_csv"], "all": ["ai_shukuhaku"]},
    "jigyosho": {"single": ["jigyosho_input"], "all": ["ai_jigyosho"]},
}
TASK_COLORS = {"personal": "BLUE", "souzoku": "GOLD", "shukuhaku": "GREEN", "jigyosho": "GREEN"}


@dataclass
class Found:
    name: str            # フォルダからの相対パス
    data: bytes
    mtime: float
    kind: str
    label: str
    period: str | None   # この書類の期（期末日・年・月。比べられる文字列）


@dataclass
class Collected:
    task: str
    used: list = field(default_factory=list)         # [Found]
    unused: list = field(default_factory=list)       # [(Found, 理由)]
    ai_targets: list = field(default_factory=list)   # PDF・写真・Excel（AI で読むか CSV にする）
    unknown: list = field(default_factory=list)      # 読めなかったファイル


def _period_of(d: intake.Draft, kind: str) -> str | None:
    """1つのファイルを読んだ下書きから、その書類の期を取り出す。"""
    if kind in ("balance", "trend", "xtx"):
        for c in d.candidates.get("fiscal_period.end", []):
            # 前期の申告データは「翌期の末日」を推定で入れている。比べやすいよう、そのまま翌期の末日で比べる
            return str(c["value"])
    o = d.others
    if kind.startswith("kojin"):
        y = o["personal"]["input"].get("year")
        return str(y) if y else None
    if kind == "shukuhaku_csv" and o["shukuhaku"]["month_counts"]:
        return o["shukuhaku"]["month_counts"].most_common(1)[0][0]
    if kind == "souzoku_input":
        return str(o["souzoku"]["input"].get("death_date") or "") or None
    if kind == "jigyosho_input":
        return str((o["jigyosho"]["input"].get("period") or {}).get("end") or "") or None
    return None


def scan(files: list[tuple[str, bytes, float]]) -> tuple[list[Found], list[str], list[str]]:
    """ファイルを1つずつ読んで種類を見分ける。(見分けたもの, AI で読むもの, 読めなかったもの)"""
    found, ai, unknown = [], [], []
    for name, data, mtime in files:
        low = name.lower()
        if low.endswith(AI_EXT):
            ai.append(name)
            continue
        if not low.endswith(READ_EXT):
            continue
        d = intake.read_files([(name, data)])
        for f in d.files:
            if f.get("ok"):
                found.append(Found(name, data, mtime, f["kind"], f["label"], _period_of(d, f["kind"])))
            elif f.get("kind") == "ai":
                ai.append(name)
            else:
                unknown.append(name)
    return found, ai, unknown


def guess_task(found: list[Found]) -> str | None:
    """申告の種類を指定しないとき、見つかった書類からいちばん当てはまるものを選ぶ。"""
    score = {t: sum(1 for f in found if f.kind in k["single"] + k["all"]) for t, k in TASK_KINDS.items()}
    best = max(score, key=lambda t: (score[t], t == "corporate"))
    return best if score[best] else None


def _match(period: str | None, want: str | None) -> bool:
    return bool(period and want and period.startswith(want))


def select(found: list[Found], task: str, period: str | None = None) -> Collected:
    k = TASK_KINDS[task]
    c = Collected(task)
    for kind in k["single"]:
        cands = [f for f in found if f.kind == kind]
        if not cands:
            continue
        if period:
            hit = [f for f in cands if _match(f.period, period)]
            cands_sorted = sorted(hit or cands, key=lambda f: (f.period or "", f.mtime), reverse=True)
        else:
            cands_sorted = sorted(cands, key=lambda f: (f.period or "", f.mtime), reverse=True)
        pick = cands_sorted[0]
        c.used.append(pick)
        for f in cands:
            if f is not pick:
                why = "期が違う" if (f.period and f.period != pick.period) else "同じ種類のより古いファイル"
                c.unused.append((f, why))
    # 前期の申告データは、選んだ試算表の期に合うもの（翌期の末日が試算表の期末と同じ）を優先する
    bal = next((f for f in c.used if f.kind == "balance"), None)
    xtx = next((f for f in c.used if f.kind == "xtx"), None)
    if bal and xtx and bal.period and xtx.period != bal.period:
        better = [f for f in found if f.kind == "xtx" and f.period == bal.period]
        if better:
            c.used.remove(xtx)
            c.unused = [(f, w) for f, w in c.unused if f is not better[0]] + [(xtx, "試算表の期に合わない")]
            c.used.append(better[0])
    for kind in k["all"]:
        c.used += [f for f in found if f.kind == kind]
    used_names = {f.name for f in c.used}
    for f in found:
        if f.name not in used_names and not any(f is u for u, _ in c.unused):
            c.unused.append((f, f"この申告では使わない（{f.label}）"))
    return c


def read_folder(folder: Path) -> list[tuple[str, bytes, float]]:
    out = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for n in names:
            if n.startswith(".") or n.lower() in ("thumbs.db", "desktop.ini"):
                continue
            p = Path(root) / n
            try:
                size = p.stat().st_size
                if size == 0 or size > MAX_BYTES:
                    continue
                rel = p.relative_to(folder).as_posix()
                out.append((rel, p.read_bytes(), p.stat().st_mtime))
            except OSError:
                continue
    return out


def run(files: list[tuple[str, bytes, float]], task: str | None = None, period: str | None = None,
        answers: dict | None = None, follow: dict | None = None) -> dict:
    """集めて、読み取り層→計算層まで一気に通す。戻り値: 一覧と申告書一式（作れたとき）。"""
    from . import api, intake_other
    found, ai, unknown = scan(files)
    task = TASK_ALIASES.get(task or "", task) if task else guess_task(found)
    if task not in TASK_KINDS:
        raise ValueError(f"申告の種類が分かりません: {task!r}（法人・個人・相続・宿泊税・事業所税）")
    c = select(found, task, period)
    c.ai_targets, c.unknown = ai, unknown
    d = intake.read_files([(f.name, f.data) for f in c.used])
    out = {"task": task, "label": TASK_LABELS[task], "used": [{"name": f.name, "label": f.label, "period": f.period} for f in c.used],
           "unused": [{"name": f.name, "label": f.label, "period": f.period, "why": w} for f, w in c.unused],
           "ai_targets": ai, "unknown": unknown, "questions": [], "html": None, "error": None}
    other_word = {"shukuhaku": "事業所税", "jigyosho": "宿泊"}.get(task)
    out["checklist"] = [{**g, "items": [i for i in g["items"] if not (other_word and other_word in i["label"])]}
                        for g in intake_other.checklist(d) if g["color"] in _colors(task, d)]
    out["tasks_found"] = sorted({t for t, k in TASK_KINDS.items() for f in found if f.kind in k["single"] + k["all"]})
    if task == "corporate":
        r = intake.resolve(d, None, answers)
        data = intake.apply_followups(r["input"], follow or {})
        fq = intake.followups(data, d.attachments.get("balance"))
        out["questions"] = [q["label"] for q in r["questions"]] + [c_["path"] + " が食い違い" for c_ in r["conflicts"]] + [q["label"] for q in fq]
        if out["questions"]:
            return out
        try:
            calc = api.calculate(data, None, d.attachments.get("balance"))
            a = d.attachments
            out["html"] = api.report(calc, a.get("balance"), a.get("trend"), a.get("payroll") or [], a.get("supplement"),
                                     a.get("gaikyo"), a.get("shohi"), office=a.get("office"))
            out["name"] = calc["input"]["company"]["name"]
        except Exception as e:  # noqa: BLE001 - 一覧は返す
            out["error"] = str(e)
        return out
    r = intake_other.resolve(d, answers).get(task)
    if not r:
        out["questions"] = [f"{TASK_LABELS[task]}の書類が見つかりませんでした"]
        return out
    out["questions"] = [q["label"] for q in r["questions"]]
    if out["questions"]:
        return out
    try:
        rep = intake_other.report(d, task, answers)
        out["html"], out["name"] = rep["html"], rep["name"]
        out["warnings"], out["checks"] = rep["warnings"], rep["checks"]
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
    return out


def _colors(task: str, d) -> set[str]:
    if task == "corporate":
        return {"RED", "BLACK", "PLATINUM"}
    if task == "personal":
        return {"BLUE", "WHITE"}
    return {TASK_COLORS[task]}


def _period_label(u: dict) -> str:
    if "前期の申告データ" in u["label"]:
        return f"{u['period']} の期の前期のデータ"
    return str(u["period"])


def summary_text(out: dict) -> str:
    """CLI に出す一覧（使ったもの・使わなかったもの・足りないもの）。"""
    lines = [f"【{out['label']}】"]
    lines.append("使ったファイル:")
    lines += [f"  ✓ {u['label']}  {u['name']}" + (f"（{_period_label(u)}）" if u.get("period") else "") for u in out["used"]] or ["  （なし）"]
    same = [u for u in out["unused"] if not u["why"].startswith("この申告では使わない")]
    other = len(out["unused"]) - len(same)
    if same:
        lines.append("使わなかったファイル（同じ種類のほかの期・古いもの）:")
        lines += [f"  - {u['name']}（{u['why']}）" for u in same]
    if other:
        lines.append(f"ほかの申告の書類: {other} 件（--task で切り替えると使います）")
    if out["ai_targets"]:
        lines.append("PDF・写真・Excel（プログラムでは読めません。AI で読むか CSV にしてください。docs/intake.md）:")
        lines += [f"  ? {n}" for n in out["ai_targets"]]
    missing = [(g["color"], i) for g in out.get("checklist", []) for i in g["items"] if i["done"] is False]
    if missing:
        lines.append("足りない書類:")
        lines += [f"  □ [{col}] {i['label']}（{i['need']}）… {i['where']}" for col, i in missing]
    if out["questions"]:
        lines.append("人が答える問い（画面で答えるか、入力ファイルを足してください）:")
        lines += [f"  ・{q}" for q in out["questions"]]
    if out.get("error"):
        lines.append(f"作れませんでした: {out['error']}")
    elif out.get("html"):
        lines.append(f"申告書一式を作りました（{out.get('name', '')}）")
    return "\n".join(lines)

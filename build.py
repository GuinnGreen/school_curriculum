#!/usr/bin/env python3
"""Parse 115班級課表.pdf + 115教師課表.pdf into a single static HTML site.

Both PDFs share the same page layout: a vertical title on the left, a 6×8 grid
(weekday × period). Each cell may contain 1 subject (e.g. 國語) or a compound
subject displayed in a 2×2 character grid (e.g. 健康 / 健體 → "健康健體").

Layout constants below were measured from the actual PDFs with PyMuPDF.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent
TEACHER_PDF = ROOT / "115教師課表.pdf"
CLASS_PDF = ROOT / "115班級課表.pdf"
TEMPLATE = ROOT / "template.html"
OUT_HTML = ROOT / "index.html"
OUT_JSON = ROOT / "data.json"

DAYS = ["一", "二", "三", "四", "五"]
DAY_X = {"一": (360, 410), "二": (311, 360), "三": (261, 311),
         "四": (211, 261), "五": (162, 212), "六": (112, 162)}
PERIOD_TOP = 110
PERIOD_HEIGHT = 66
PERIOD_COUNT = 7
SUBJECT_MAX_DY = 50  # subject chars sit in top ~50pt of a cell

# Known elementary-school subject + category names. Compound cells stack two or
# three of these in a 2D grid; we don't trust the visual order but reconstruct
# from a multiset of chars using longest-match-first tokenization.
SUBJECT_VOCAB = [
    "多采多億(閱讀)", "多采多億(作文)", "多采多億(作閱)",
    "億起創E(電腦)", "多采多億英文", "彈性課程社團",
    "多采多億", "自然科學", "表演藝術", "藝術音樂", "視覺藝術",
    "本土語文", "綜合活動",
    "校訂英語", "校訂議題",                      # 4-char first
    "本土語",                                    # 3-char
    "國語", "數學", "生活", "英語", "英文", "自然", "社會", "綜合",
    "體育", "健康", "健體", "美勞", "音樂", "表藝", "藝文",
    "閱讀", "作文", "社團", "電腦", "彈性",
]
CATEGORY_TOKENS = {"健體", "藝文", "生活", "彈性"}

# 115-1 uses longer curriculum names in the source PDFs.  The site deliberately
# shows the short names used by staff; standalone 多采多億 keeps its source name.
SUBJECT_DISPLAY_NAMES = {
    "多采多億英文": "英語",
    "英文": "英語",
    "多采多億(閱讀)": "閱讀",
    "多采多億(作文)": "作文",
    "多采多億(作閱)": "作閱",
    "自然科學": "自然",
    "表演藝術": "表藝",
    "億起創E(電腦)": "電腦",
    "本土語文": "本土語",
    "藝術音樂": "音樂",
    "視覺藝術": "美勞",
    "綜合活動": "綜合",
    "彈性課程社團": "社團",
}

GRADE_NAMES = {"一": "一年級", "二": "二年級", "三": "三年級",
               "四": "四年級", "五": "五年級", "六": "六年級"}

# Used to merge close-relative subjects when classifying 科任 teachers.
SUBJECT_NORMALIZE = {"校訂英語": "英語"}

# The PDFs' embedded text layer cannot encode these uncommon name glyphs.
# The 115 staff roster is the authoritative source for the corrections.
TEACHER_NAME_CORRECTIONS = {
    "辜?晶": "韋銹晶",
    "徐?慈": "徐彣慈",
    "葉?": "葉珉",
}

HOMEROOM_THRESHOLD = 0.7  # ≥70% slots in a single class → that class's 導師

PERIOD_TIMES = {
    1: "08:40–09:20", 2: "09:30–10:10", 3: "10:30–11:10", 4: "11:20–12:00",
    5: "13:30–14:10", 6: "14:20–15:00", 7: "15:20–16:00",
}

FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def cell_y_range(period: int) -> tuple[float, float]:
    top = PERIOD_TOP + (period - 1) * PERIOD_HEIGHT
    return top, top + PERIOD_HEIGHT


def words_in(words, x_lo, x_hi, y_lo, y_hi):
    return [w for w in words if x_lo <= w[0] < x_hi and y_lo <= w[1] < y_hi]


def extract_title(words) -> str:
    chars = [w for w in words if w[0] < 100 and w[1] < 700]
    chars.sort(key=lambda w: w[1])
    return "".join(w[4] for w in chars)


def tokenize_subject(chars: list[str]) -> tuple[list[str], list[str]]:
    """Multiset tokenization against SUBJECT_VOCAB (longest match first).

    Returns (tokens_in_match_order, leftover_chars).
    """
    pool: dict[str, int] = {}
    for c in chars:
        pool[c] = pool.get(c, 0) + 1
    vocab = sorted(SUBJECT_VOCAB, key=lambda s: (-len(s), s))
    tokens: list[str] = []
    progress = True
    while progress:
        progress = False
        for word in vocab:
            need: dict[str, int] = {}
            for c in word:
                need[c] = need.get(c, 0) + 1
            if all(pool.get(c, 0) >= n for c, n in need.items()):
                tokens.append(word)
                for c, n in need.items():
                    pool[c] -= n
                progress = True
                break
    leftover = [c for c, n in pool.items() if n > 0 for _ in range(n)]
    return tokens, leftover


def parse_subject(cell_words, cell_top: float, warn_ctx: str = "") -> tuple[str, list[str]]:
    """Return (display_string, tokens). Categories are pushed to the end."""
    sub = [w for w in cell_words if w[1] < cell_top + SUBJECT_MAX_DY]
    if not sub:
        return "", []
    chars = [w[4] for w in sub]
    tokens, leftover = tokenize_subject(chars)
    if leftover:
        # Fall back to raw concat sorted by (column, then row) for visibility.
        sub_sorted = sorted(sub, key=lambda w: (round(w[0]), w[1]))
        raw = "".join(w[4] for w in sub_sorted)
        print(f"  [warn] could not tokenize subject {raw!r} ({warn_ctx}); leftover={leftover}",
              file=sys.stderr)
        return raw, [raw]
    # Reorder: subjects first (in match order), categories last.
    subjects = [t for t in tokens if t not in CATEGORY_TOKENS]
    cats = [t for t in tokens if t in CATEGORY_TOKENS]
    # 校訂議題 is a curriculum sub-category, not the actual subject taught;
    # if a real subject (閱讀, 作文, …) is in the same cell, push 校訂議題
    # down so the real subject becomes the primary token.
    if "校訂議題" in subjects and len(subjects) > 1:
        subjects.remove("校訂議題")
        cats.insert(0, "校訂議題")
    ordered = [SUBJECT_DISPLAY_NAMES.get(t, t) for t in subjects + cats]
    return "／".join(ordered), ordered


def parse_class_designator(cell_words, cell_top: float) -> str:
    """Bottom of teacher-PDF cell: "一 １" → "一1"."""
    rel = [w for w in cell_words if w[1] >= cell_top + SUBJECT_MAX_DY]
    if not rel:
        return ""
    rel.sort(key=lambda w: w[0])
    text = "".join(w[4] for w in rel).translate(FW_DIGITS)
    text = re.sub(r"\s+", "", text)
    m = re.match(r"^([一二三四五六])(\d{1,2})$", text)
    return m.group(1) + str(int(m.group(2))) if m else text


def parse_teacher_name(cell_words, cell_top: float) -> str:
    """Bottom of class-PDF cell: a single word like "葉淑敏"."""
    rel = [w for w in cell_words if w[1] >= cell_top + SUBJECT_MAX_DY]
    if not rel:
        return ""
    rel.sort(key=lambda w: (w[1], w[0]))
    return normalize_teacher_name("".join(w[4] for w in rel).strip())


def normalize_teacher_name(name: str) -> str:
    return TEACHER_NAME_CORRECTIONS.get(name, name)


def parse_grid(words, kind: str):
    """Return {day: {period: {subject, partner}}} where partner is class or teacher."""
    grid: dict = {d: {} for d in DAYS}
    for day in DAYS:
        x_lo, x_hi = DAY_X[day]
        for p in range(1, PERIOD_COUNT + 1):
            y_lo, y_hi = cell_y_range(p)
            cell = words_in(words, x_lo, x_hi, y_lo, y_hi)
            if not cell:
                continue
            subject, tokens = parse_subject(cell, y_lo, warn_ctx=f"{day}{p}")
            if not subject:
                continue
            if kind == "teacher":
                partner = parse_class_designator(cell, y_lo)
                grid[day][p] = {"subject": subject, "tokens": tokens, "class": partner}
            else:
                partner = parse_teacher_name(cell, y_lo)
                grid[day][p] = {"subject": subject, "tokens": tokens, "teacher": partner}
    return grid


def parse_teacher_page(page) -> dict | None:
    words = page.get_text("words")
    title = extract_title(words)
    if not title:
        return None
    name = normalize_teacher_name(title.replace("老師", "").strip())
    # 編號: NNN at the bottom
    tid = ""
    hours = 0
    for w in words:
        if w[1] > 640:
            if w[4].startswith("編號") or w[4] == "編號:":
                # next word right of it on the same line
                neighbours = [x for x in words if abs(x[1] - w[1]) < 5 and x[0] > w[0]]
                neighbours.sort(key=lambda x: x[0])
                if neighbours:
                    tid = neighbours[0][4].strip().translate(FW_DIGITS)
            elif w[4].startswith("時數") or w[4] == "時數:":
                neighbours = [x for x in words if abs(x[1] - w[1]) < 5 and x[0] > w[0]]
                neighbours.sort(key=lambda x: x[0])
                if neighbours:
                    try:
                        hours = int(neighbours[0][4].strip().translate(FW_DIGITS))
                    except ValueError:
                        pass
    grid = parse_grid(words, "teacher")
    return {"id": tid, "name": name, "hours": hours, "schedule": grid}


def parse_class_page(page) -> dict | None:
    words = page.get_text("words")
    title = extract_title(words).translate(FW_DIGITS)
    if not title:
        return None
    m = re.match(r"^([一二三四五六])年(\d{1,2})班$", title)
    if m:
        class_number = str(int(m.group(2)))
        cid = m.group(1) + class_number
        name = f"{m.group(1)}年{class_number}班"
    else:
        cid = title
        name = title
    return {"id": cid, "name": name, "schedule": parse_grid(words, "class")}


def categorize_teacher(teacher: dict) -> tuple[str, str]:
    """Return (category, homeroom_class_id).

    `X年級導師` if ≥70% of slots fall in one class; homeroom is that class id.
    Otherwise `XX科任` and homeroom is empty.
    """
    class_counts: dict = {}
    subject_counts: dict = {}
    total = 0
    for slots in teacher["schedule"].values():
        for slot in slots.values():
            cid = slot.get("class") or ""
            if cid:
                class_counts[cid] = class_counts.get(cid, 0) + 1
            tokens = slot.get("tokens") or [slot["subject"]]
            primary = SUBJECT_NORMALIZE.get(tokens[0], tokens[0])
            subject_counts[primary] = subject_counts.get(primary, 0) + 1
            total += 1
    if total == 0:
        return "其他", ""
    if class_counts:
        top_class, top_count = max(class_counts.items(), key=lambda x: x[1])
        if top_count / total >= HOMEROOM_THRESHOLD:
            grade_char = top_class[0]
            return GRADE_NAMES.get(grade_char, grade_char) + "導師", top_class
    top_subject = max(subject_counts.items(), key=lambda x: x[1])[0]
    return f"{top_subject}科任", ""


def build():
    teachers = []
    classes = []

    print("Parsing teacher PDF...", file=sys.stderr)
    tdoc = fitz.open(TEACHER_PDF)
    for i, page in enumerate(tdoc):
        rec = parse_teacher_page(page)
        if rec:
            teachers.append(rec)
        else:
            print(f"  [warn] teacher page {i+1} produced no record", file=sys.stderr)

    print("Parsing class PDF...", file=sys.stderr)
    cdoc = fitz.open(CLASS_PDF)
    for i, page in enumerate(cdoc):
        rec = parse_class_page(page)
        if rec:
            classes.append(rec)
        else:
            print(f"  [warn] class page {i+1} produced no record", file=sys.stderr)

    # Cross-validate: teacher-side claim should match class-side claim.
    teacher_by_name = {t["name"]: t for t in teachers}
    class_by_id = {c["id"]: c for c in classes}
    warnings = 0
    for t in teachers:
        for day, slots in t["schedule"].items():
            for p, slot in slots.items():
                cid = slot["class"]
                cls = class_by_id.get(cid)
                if not cls:
                    continue
                cslot = cls["schedule"].get(day, {}).get(p)
                if not cslot:
                    print(
                        f"  [check] {t['name']} teaches {cid} {day}{p} but class side empty",
                        file=sys.stderr,
                    )
                    warnings += 1
                    continue
                if cslot["teacher"] != t["name"]:
                    print(
                        f"  [check] {t['name']}/{cslot['teacher']} mismatch at {cid} {day}{p}",
                        file=sys.stderr,
                    )
                    warnings += 1
                if cslot["subject"] != slot["subject"]:
                    print(
                        f"  [check] subject mismatch at {t['name']} {cid} {day}{p}: "
                        f"teacher={slot['subject']!r} class={cslot['subject']!r}",
                        file=sys.stderr,
                    )
                    warnings += 1

    raw_homeroom_claims: dict = {}
    for t in teachers:
        cat, homeroom = categorize_teacher(t)
        t["category"] = cat
        t["homeroom"] = homeroom
        if homeroom:
            raw_homeroom_claims.setdefault(homeroom, []).append(t)

    # If multiple teachers claim the same class, the real homeroom is whoever
    # spends the most hours there. Demote part-time claimants back to 科任.
    homeroom_by_class: dict = {}
    for cid, claimants in raw_homeroom_claims.items():
        winner = max(claimants, key=lambda t: t["hours"])
        homeroom_by_class[cid] = winner["name"]
        for t in claimants:
            if t is winner:
                continue
            t["homeroom"] = ""
            tokens_count: dict = {}
            for slots in t["schedule"].values():
                for slot in slots.values():
                    tokens = slot.get("tokens") or [slot["subject"]]
                    primary = SUBJECT_NORMALIZE.get(tokens[0], tokens[0])
                    tokens_count[primary] = tokens_count.get(primary, 0) + 1
            top_subj = max(tokens_count.items(), key=lambda x: x[1])[0] if tokens_count else "其他"
            t["category"] = f"{top_subj}科任"

    for c in classes:
        c["grade"] = GRADE_NAMES.get(c["id"][0], c["id"][0])
        c["homeroom_teacher"] = homeroom_by_class.get(c["id"], "")

    data = {
        "teachers": teachers,
        "classes": classes,
        "periods": [{"n": n, "time": PERIOD_TIMES[n]} for n in range(1, PERIOD_COUNT + 1)],
        "days": DAYS,
    }
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"teachers: {len(teachers)}, classes: {len(classes)}, warnings: {warnings}",
          file=sys.stderr)

    template = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False)
    OUT_HTML.write_text(template.replace("__DATA__", payload), encoding="utf-8")
    print(f"wrote {OUT_HTML}", file=sys.stderr)


if __name__ == "__main__":
    build()

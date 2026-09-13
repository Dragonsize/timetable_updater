"""
Pure HTML parser for TDTU student exam schedule pages.
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any
from bs4 import BeautifulSoup

from tdtu.exceptions import TDTUParsingError

logger = logging.getLogger(__name__)

EXPECTED_EXAM_TABLE_IDS: dict[str, set[str]] = {
    "0": {"giuaky", "midterm", "gk", "lichthi1_giuakytable"},
    "1": {"cuoiky", "final", "ck", "lichthi1_cuoikytable"},
    "2": {"cuoiky2", "final-2nd", "ck2", "lichthi1_cuoiky2table"},
}

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

VN_DAY_MAP = [
    ("thứ 2", "Monday"), ("thu 2", "Monday"), ("monday", "Monday"),
    ("thứ 3", "Tuesday"), ("thu 3", "Tuesday"), ("tuesday", "Tuesday"),
    ("thứ 4", "Wednesday"), ("thu 4", "Wednesday"), ("wednesday", "Wednesday"),
    ("thứ 5", "Thursday"), ("thu 5", "Thursday"), ("thursday", "Thursday"),
    ("thứ 6", "Friday"), ("thu 6", "Friday"), ("friday", "Friday"),
    ("thứ 7", "Saturday"), ("thu 7", "Saturday"), ("saturday", "Saturday"),
    ("chủ nhật", "Sunday"), ("chu nhat", "Sunday"), ("cn", "Sunday"), ("sunday", "Sunday"),
]


def _normalize_weekday(text: str) -> str:
    lower = (text or "").lower()
    for needle, en in VN_DAY_MAP:
        if needle in lower:
            return en
    return ""


def parse_date_iso(text: str, semester_hint: str = "") -> str:
    """Parse date from string (e.g. '15/12/2026') to 'YYYY-MM-DD'."""
    m = re.search(r"(\d{1,2})[/\.-](\d{1,2})(?:[/\.-](\d{2,4}))?", text or "")
    if not m:
        return ""
    d = int(m.group(1))
    mo = int(m.group(2))

    y = None
    if m.group(3):
        y = int(m.group(3))
        if y < 100:
            y += 2000

    if y is None and semester_hint:
        m_year = re.search(r"20\d{2}", semester_hint)
        if m_year:
            y = int(m_year.group(0))

    if y is None:
        y = datetime.date.today().year

    try:
        dt = datetime.date(y, mo, d)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return ""


def parse_time_str(text: str) -> str:
    """Parse time string like '07:30' or '7h30' into 'HH:MM'."""
    m = re.search(r"(\d{1,2})[:h](\d{2})", text or "", re.IGNORECASE)
    if not m:
        return ""
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_exam_html(
    html: str,
    default_exam_type: str = "exam",
    semester_hint: str = "",
    tab_arg: str | None = None,
    student_id: str = "",
) -> list[dict[str, Any]]:
    """Parse exam schedule HTML and extract exam records."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    target_table_ids = EXPECTED_EXAM_TABLE_IDS.get(str(tab_arg), set()) if tab_arg is not None else set()

    # 1. Parse standard tabular layouts
    tables = soup.find_all("table")

    # If target_table_ids specified, first check if any table matches the keyword
    matching_tables = [
        t for t in tables
        if any(kw in (t.get("id") or "").lower() or kw in (t.get("name") or "").lower() for kw in target_table_ids)
    ] if target_table_ids else []

    # If matching tables found, parse those; otherwise check all tables on the page
    tables_to_check = matching_tables if matching_tables else tables

    for table in tables_to_check:
        trs = table.find_all("tr")
        if not trs:
            continue

        head_cells = trs[0].find_all(["th", "td"])
        headers = [c.get_text().strip().lower() for c in head_cells[:15]]
        all_head = " ".join(headers)

        # Mode A: Weekday grid table (Thứ 2|Monday, Thứ 3|Tuesday...) — TDTU standard exam table
        is_weekday_grid = any(
            any(w in h for w in ["thứ", "thu", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "chủ nhật", "cn"])
            for h in headers
        )
        if is_weekday_grid:
            col_days = [_normalize_weekday(h) for h in headers]
            for tr in trs[1:]:
                if "Headerrow" in tr.get("class", []):
                    continue
                tds = tr.find_all("td")
                for col_idx, td in enumerate(tds):
                    if col_idx >= len(col_days):
                        break
                    dow = col_days[col_idx]
                    text = td.get_text("\n").strip()
                    if not text or text in ("-", "x", "trống", "rong"):
                        continue

                    # Could be multiple exam entries inside one cell
                    nodes = td.find_all(["div", "span", "p"]) or [td]
                    meaningful_nodes = [n for n in nodes if re.search(r"\d{1,2}[/\.-]\d{1,2}", n.get_text())] or [td]

                    for node in meaningful_nodes:
                        c_text = node.get_text("\n").strip()
                        if not c_text or c_text in ("-", "x"):
                            continue

                        lines = [l.strip() for l in c_text.split("\n") if l.strip()]
                        if not lines:
                            continue

                        first_line = lines[0]
                        parts = first_line.split("|")
                        subject_name = parts[0].split("(")[0].strip()
                        english_name = parts[1].split("(")[0].strip() if len(parts) > 1 else subject_name

                        code_match = re.search(r"\((\d{5,6})\b", c_text)
                        code = code_match.group(1) if code_match else ""

                        group_match = re.search(r"(?:Groups?|Nhóm)\s*:?\s*(\d+)", c_text, re.IGNORECASE)
                        group = group_match.group(1) if group_match else ""

                        subgroup_match = re.search(r"(?:Sub-group|Tổ)\s*:?\s*(\d+)", c_text, re.IGNORECASE)
                        subgroup = subgroup_match.group(1) if subgroup_match else ""

                        date_iso = parse_date_iso(c_text, semester_hint=semester_hint)
                        if not date_iso:
                            continue

                        start_t = parse_time_str(c_text)
                        end_t = ""
                        range_m = re.search(r"(\d{1,2}[:h]\d{2})\s*(?:-|–|—|to|đến|den|->|~)\s*(\d{1,2}[:h]\d{2})", c_text, re.IGNORECASE)
                        if range_m:
                            end_t = parse_time_str(range_m.group(2))

                        duration_min = 0
                        if start_t and end_t:
                            sh, sm = (int(x) for x in start_t.split(":"))
                            eh, em = (int(x) for x in end_t.split(":"))
                            duration_min = (eh * 60 + em) - (sh * 60 + sm)
                            if duration_min < 0:
                                duration_min += 24 * 60
                        else:
                            dur_m = re.search(r"(\d+)\s*(?:phút|min)", c_text, re.IGNORECASE)
                            if dur_m:
                                duration_min = int(dur_m.group(1))

                        room = ""
                        room_m = re.search(r"(?:phòng|phong|room)\s*[:\-]?\s*([A-Za-z0-9._-]+)", c_text, re.IGNORECASE)
                        if room_m:
                            room = room_m.group(1).strip()

                        type_m = re.search(r"(?:hình thức|hinh thuc|type|loại)\s*[:\-]?\s*([^\n]+)", c_text, re.IGNORECASE)
                        notes = type_m.group(1).strip() if type_m else default_exam_type

                        rows.append({
                            "type": "exam",
                            "student_id": student_id,
                            "subject_name": subject_name,
                            "english_name": english_name,
                            "code": code,
                            "group": group,
                            "subgroup": subgroup,
                            "day_of_week": dow,
                            "session_date": date_iso,
                            "start_time": start_t,
                            "end_time": end_t,
                            "duration_min": duration_min,
                            "exam_room": room,
                            "exam_type": default_exam_type,
                            "notes": notes,
                        })
            continue

        # Mode B: Columnar table (Môn học, Ngày thi, Giờ thi...)
        has_subject = bool(re.search(r"(môn|mon|subject|học phần|hoc phan)", all_head))
        has_date = bool(re.search(r"(ngày|ngay|date)", all_head))
        has_time = bool(re.search(r"(giờ|gio|time|ca\s*thi)", all_head))

        if not has_subject or not (has_date or has_time):
            continue

        idx_subject = next((i for i, h in enumerate(headers) if re.search(r"(môn|mon|subject)", h)), -1)
        idx_date = next((i for i, h in enumerate(headers) if re.search(r"(ngày|ngay|date)", h)), -1)
        idx_time = next((i for i, h in enumerate(headers) if re.search(r"(giờ|gio|time)", h)), -1)
        idx_room = next((i for i, h in enumerate(headers) if re.search(r"(phòng|phong|room)", h)), -1)
        idx_type = next((i for i, h in enumerate(headers) if re.search(r"(hình thức|hinh thuc|type|loại|loai)", h)), -1)

        for tr in trs[1:]:
            if "Headerrow" in tr.get("class", []):
                continue
            tds = [td.get_text().strip() for td in tr.find_all("td")]
            if len(tds) < 2:
                continue

            raw_subject = tds[idx_subject] if 0 <= idx_subject < len(tds) else ""
            if not raw_subject:
                continue

            # Extract subject name and english name if pipe-separated
            parts = raw_subject.split("|")
            subject_name = parts[0].split("(")[0].strip()
            english_name = parts[1].split("(")[0].strip() if len(parts) > 1 else subject_name

            code_match = re.search(r"\((\d{5,6})\b", raw_subject)
            code = code_match.group(1) if code_match else ""

            group_match = re.search(r"(?:Groups?|Nhóm)\s*:?\s*(\d+)", raw_subject, re.IGNORECASE)
            group = group_match.group(1) if group_match else ""

            subgroup_match = re.search(r"(?:Sub-group|Tổ)\s*:?\s*(\d+)", raw_subject, re.IGNORECASE)
            subgroup = subgroup_match.group(1) if subgroup_match else ""

            date_text = tds[idx_date] if 0 <= idx_date < len(tds) else " ".join(tds)
            date_iso = parse_date_iso(date_text, semester_hint=semester_hint)
            if not date_iso:
                continue

            dow = ""
            try:
                d_obj = datetime.date.fromisoformat(date_iso)
                dow = WEEKDAYS[d_obj.weekday()]
            except ValueError:
                pass

            time_text = tds[idx_time] if 0 <= idx_time < len(tds) else " ".join(tds)
            start_t = parse_time_str(time_text)
            end_t = ""
            range_m = re.search(r"(\d{1,2}[:h]\d{2})\s*(?:-|–|—|to|đến|den|->|~)\s*(\d{1,2}[:h]\d{2})", time_text, re.IGNORECASE)
            if range_m:
                end_t = parse_time_str(range_m.group(2))

            duration_min = 0
            if start_t and end_t:
                sh, sm = (int(x) for x in start_t.split(":"))
                eh, em = (int(x) for x in end_t.split(":"))
                duration_min = (eh * 60 + em) - (sh * 60 + sm)
                if duration_min < 0:
                    duration_min += 24 * 60
            else:
                dur_m = re.search(r"(\d+)\s*(?:phút|min)", time_text, re.IGNORECASE)
                if dur_m:
                    duration_min = int(dur_m.group(1))

            exam_room = tds[idx_room] if 0 <= idx_room < len(tds) else ""
            exam_type_label = tds[idx_type] if 0 <= idx_type < len(tds) else default_exam_type

            rows.append({
                "type": "exam",
                "student_id": student_id,
                "subject_name": subject_name,
                "english_name": english_name,
                "code": code,
                "group": group,
                "subgroup": subgroup,
                "day_of_week": dow,
                "session_date": date_iso,
                "start_time": start_t,
                "end_time": end_t,
                "duration_min": duration_min,
                "exam_room": exam_room,
                "exam_type": default_exam_type,
                "notes": exam_type_label,
            })

    # 2. Parse grid cell layout if table layout yielded nothing
    if not rows:
        cells = soup.find_all(["td", "div"])
        for cell in cells:
            text = cell.get_text("\n").strip()
            if not text:
                continue
            lowered = text.lower()
            if not ("ngày thi" in lowered or "ngay thi" in lowered or "date:" in lowered):
                continue
            if not ("giờ thi" in lowered or "gio thi" in lowered or "time:" in lowered):
                continue

            lines = [line.strip() for line in text.split("\n") if line.strip()]
            if not lines:
                continue

            first_line = lines[0]
            parts = first_line.split("|")
            subject_name = parts[0].split("(")[0].strip()
            english_name = parts[1].split("(")[0].strip() if len(parts) > 1 else subject_name

            code_m = re.search(r"\((\d{5,6})\b", text)
            code = code_m.group(1) if code_m else ""

            group_m = re.search(r"(?:Groups?|Nhóm)\s*:?\s*(\d+)", text, re.IGNORECASE)
            group = group_m.group(1) if group_m else ""

            subgroup_m = re.search(r"(?:Sub-group|Tổ)\s*:?\s*(\d+)", text, re.IGNORECASE)
            subgroup = subgroup_m.group(1) if subgroup_m else ""

            date_line = next((l for l in lines if re.search(r"(ngày|ngay|date)", l, re.IGNORECASE)), text)
            time_line = next((l for l in lines if re.search(r"(giờ|gio|time)", l, re.IGNORECASE)), text)
            room_line = next((l for l in lines if re.search(r"(phòng|phong|room)", l, re.IGNORECASE)), "")

            date_iso = parse_date_iso(date_line, semester_hint=semester_hint)
            if not date_iso:
                continue

            dow = ""
            try:
                d_obj = datetime.date.fromisoformat(date_iso)
                dow = WEEKDAYS[d_obj.weekday()]
            except ValueError:
                pass

            start_t = parse_time_str(time_line)
            end_t = ""
            range_m = re.search(r"(\d{1,2}[:h]\d{2})\s*(?:-|–|—|to|đến|den|->|~)\s*(\d{1,2}[:h]\d{2})", time_line, re.IGNORECASE)
            if range_m:
                end_t = parse_time_str(range_m.group(2))

            dur_m = re.search(r"(\d+)\s*(?:phút|min)", time_line, re.IGNORECASE)
            duration_min = int(dur_m.group(1)) if dur_m else 0

            room = ""
            room_m = re.search(r"(?:phòng|phong|room)\s*[:\-]?\s*(\S+)", room_line, re.IGNORECASE)
            if room_m:
                room = room_m.group(1).strip()

            rows.append({
                "type": "exam",
                "student_id": student_id,
                "subject_name": subject_name,
                "english_name": english_name,
                "code": code,
                "group": group,
                "subgroup": subgroup,
                "day_of_week": dow,
                "session_date": date_iso,
                "start_time": start_t,
                "end_time": end_t,
                "duration_min": duration_min,
                "exam_room": room,
                "exam_type": default_exam_type,
                "notes": "",
            })

    return deduplicate_exam_rows(rows)


def deduplicate_exam_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate exam entries."""
    seen: set[tuple] = set()
    deduped = []
    for r in rows:
        sig = (
            r.get("subject_name", "").lower(),
            r.get("session_date", ""),
            r.get("start_time", ""),
            r.get("exam_room", "").lower(),
            r.get("exam_type", ""),
        )
        if sig not in seen:
            seen.add(sig)
            deduped.append(r)
    return deduped

"""
Pure HTML parsers for TDTU student timetable / schedule pages.
"""

from __future__ import annotations

import datetime
import logging
import re
import unicodedata
from typing import Any
from bs4 import BeautifulSoup

from tdtu.exceptions import TDTUProtocolError

logger = logging.getLogger(__name__)

ENGLISH_DAYS = [
    ("monday", "Monday"),
    ("tuesday", "Tuesday"),
    ("wednesday", "Wednesday"),
    ("thursday", "Thursday"),
    ("friday", "Friday"),
    ("saturday", "Saturday"),
    ("sunday", "Sunday"),
]

VN_DAY_MAP = [
    ("thứ 2", "Monday"),
    ("thu 2", "Monday"),
    ("thứ hai", "Monday"),
    ("thứ 3", "Tuesday"),
    ("thu 3", "Tuesday"),
    ("thứ ba", "Tuesday"),
    ("thứ 4", "Wednesday"),
    ("thu 4", "Wednesday"),
    ("thứ tư", "Wednesday"),
    ("thứ 5", "Thursday"),
    ("thu 5", "Thursday"),
    ("thứ năm", "Thursday"),
    ("thứ 6", "Friday"),
    ("thu 6", "Friday"),
    ("thứ sáu", "Friday"),
    ("thứ 7", "Saturday"),
    ("thu 7", "Saturday"),
    ("thứ bảy", "Saturday"),
    ("chủ nhật", "Sunday"),
    ("chu nhat", "Sunday"),
    ("cn", "Sunday"),
]


def _normalize_text(text: str) -> str:
    """Normalize text by stripping diacritics and extra whitespace."""
    s = (text or "").strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).lower()


def detect_status(text: str) -> str:
    """Detect session status: absent, makeup, or normal."""
    norm = _normalize_text(text)
    if re.search(r"\b(bao\s*vang|gv\s*vang|nghi\s*hoc|nghi\s*tiet|vang\s*tiet|gv\s*bao\s*vang|lop\s*nghi)\b", norm):
        return "absent"
    if re.search(r"\b(hoc\s*bu|lich\s*bu|day\s*bu|bu\s*hoc|bu\s*tiet|lhb)\b", norm):
        return "makeup"
    return "normal"


def _normalize_day(raw: str) -> str:
    """Normalize day text to English weekday name."""
    lower = (raw or "").strip().lower()
    for needle, en in ENGLISH_DAYS:
        if needle in lower:
            return en
    for vn, en in VN_DAY_MAP:
        if vn in lower:
            return en
    return raw.strip()


def parse_semester_options(html: str) -> list[dict[str, Any]]:
    """Parse semester dropdown options."""
    soup = BeautifulSoup(html, "html.parser")
    select = (
        soup.find("select", id=re.compile(r".*cboHocKy.*", re.IGNORECASE))
        or soup.find("select", attrs={"name": re.compile(r".*cboHocKy.*", re.IGNORECASE)})
    )
    if not select:
        return []

    options = []
    for opt in select.find_all("option"):
        options.append({
            "value": opt.get("value", "").strip(),
            "text": opt.get_text().strip(),
            "selected": opt.has_attr("selected"),
        })
    return options


def parse_active_semester(html: str) -> str:
    """Extract currently selected semester text."""
    soup = BeautifulSoup(html, "html.parser")
    select = (
        soup.find("select", id=re.compile(r".*cboHocKy.*", re.IGNORECASE))
        or soup.find("select", attrs={"name": re.compile(r".*cboHocKy.*", re.IGNORECASE)})
    )
    if select:
        selected_opt = select.find("option", selected=True)
        if selected_opt:
            return selected_opt.get_text().strip()

    match = re.search(r"HK\s*\d*(?:\s*hè)?/\d{4}-\d{4}", html, re.IGNORECASE)
    if match:
        return match.group(0).strip()
    return ""


def parse_period_range(text: str) -> tuple[int, int]:
    """Parse start and end period (1..16)."""
    period_match = re.search(r"(?:Tiết|Period)[:\s]*([0-9\s\-to]+)", text, re.IGNORECASE)
    if not period_match:
        return 0, 0

    p_raw = period_match.group(1).strip()
    if not p_raw:
        return 0, 0

    # Explicit range with dash or 'to' (e.g. "1-3", "10-12")
    m_range = re.search(r"(\d{1,2})\s*(?:-|–|—|\bto\b)\s*(\d{1,2})", p_raw, re.IGNORECASE)
    if m_range:
        s, e = int(m_range.group(1)), int(m_range.group(2))
        if 1 <= s <= e <= 16:
            return s, e

    clean_p = re.sub(r"\s+", "", p_raw)
    if not clean_p.isdigit():
        return 0, 0

    if len(clean_p) <= 2:
        val = int(clean_p)
        if 1 <= val <= 16:
            return val, val
        return 0, 0

    if len(clean_p) == 6:
        p1, p2, p3 = int(clean_p[0:2]), int(clean_p[2:4]), int(clean_p[4:6])
        if 1 <= p1 <= p2 <= p3 <= 16:
            return p1, p3

    if len(clean_p) == 4:
        p1, p2, p3 = int(clean_p[0:1]), int(clean_p[1:2]), int(clean_p[2:4])
        if 1 <= p1 <= p2 <= p3 <= 16 and p2 == p1 + 1 and p3 == p2 + 1:
            return p1, p3

    if len(clean_p) == 3:
        p1, p2, p3 = int(clean_p[0]), int(clean_p[1]), int(clean_p[2])
        if 1 <= p1 <= p2 <= p3 <= 16:
            return p1, p3

    digits = [int(d) for d in clean_p if d.isdigit()]
    if digits:
        s, e = min(digits), max(digits)
        if 1 <= s <= e <= 16:
            return s, e

    return 0, 0


def _parse_schedule_cell_text(text: str, day_of_week: str, student_id: str) -> dict[str, Any] | None:
    """Parse text block inside a schedule table cell."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return None

    first_line = lines[0]
    if any(h in first_line for h in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]):
        return None

    # Subject name & English name extraction
    # Format: "Subject Name | English Name (123456) - Group: 01"
    # or "Subject Name (123456)"
    parts = first_line.split("|")
    subject_name = parts[0].split("(")[0].strip()
    if not subject_name:
        return None

    english_name = ""
    if len(parts) > 1:
        english_name = parts[1].split("(")[0].strip()
    if not english_name:
        english_name = subject_name

    full_text = " ".join(lines)

    # Subject code (5-6 digits in parenthesis)
    code_match = re.search(r"\((\d{5,6})\b", full_text)
    code = code_match.group(1) if code_match else ""

    # Group number
    group_match = re.search(r"(?:Groups?|Nhóm\|Groups?|Nhóm)\s*:?\s*(\d+)", full_text, re.IGNORECASE)
    group = group_match.group(1) if group_match else ""

    # Room
    room = ""
    room_match = re.search(
        r"(?:Phòng|Room)\b(?:[\s\n|]*(?:Room|Phòng)\b)*[\s\n|:]*([A-Z0-9._-]+(?:\s+[A-Z0-9._-]+)*)(?=\s*(?:\n|\(|Tuần|Week|Tiết|Period|GV|báo|vắng|nghỉ|học|bù|lhb|hủy|dời|$))",
        full_text,
        re.IGNORECASE,
    )
    if room_match:
        room = room_match.group(1).strip()
        room = re.sub(r"\s+(?:GV|vắng|nghỉ|báo|bù|lhb|hủy|dời|tuần|week).*$", "", room, flags=re.IGNORECASE).strip()

    status = detect_status(full_text)
    start_period, end_period = parse_period_range(full_text)

    return {
        "student_id": student_id,
        "type": "class",
        "subject_name": subject_name,
        "english_name": english_name,
        "code": code,
        "group": group,
        "room": room,
        "day_of_week": day_of_week,
        "session_date": "",
        "start_period": start_period,
        "end_period": end_period,
        "status": status,
    }


def parse_weekly_grid_table(html: str, student_id: str = "") -> list[dict[str, Any]] | None:
    """Parse weekly grid timetable HTML table."""
    soup = BeautifulSoup(html, "html.parser")

    week_btn = (
        soup.find("input", id=re.compile(r".*btnTuanHienTai.*", re.IGNORECASE))
        or soup.find("input", attrs={"name": re.compile(r".*btnTuanHienTai.*", re.IGNORECASE)})
    )
    table = soup.find("table", id=re.compile(r".*tbTKBTheoTuan.*|.*Table1.*|.*Grid.*", re.IGNORECASE))
    if not table:
        return None

    header_tr = table.find("tr", class_="Headerrow") or table.find("tr")
    if not header_tr:
        return None

    headers = [th.get_text().strip() for th in header_tr.find_all(["td", "th"])]
    if len(headers) < 8:
        return None

    col_days = [_normalize_day(h) for h in headers]

    start_dt = None
    end_dt = None
    if week_btn:
        btn_val = week_btn.get("value", "")
        matches = re.findall(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", btn_val)
        if len(matches) >= 2:
            try:
                d1, m1, y1 = int(matches[0][0]), int(matches[0][1]), int(matches[0][2])
                d2, m2, y2 = int(matches[1][0]), int(matches[1][1]), int(matches[1][2])
                if y1 < 100: y1 += 2000
                if y2 < 100: y2 += 2000
                start_dt = datetime.date(y1, m1, d1)
                end_dt = datetime.date(y2, m2, d2)
            except ValueError:
                pass

    if not start_dt or not end_dt:
        today = datetime.date.today()
        start_dt = today - datetime.timedelta(days=today.weekday())
        end_dt = start_dt + datetime.timedelta(days=6)

    day_indices = {
        "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
        "Friday": 4, "Saturday": 5, "Sunday": 6
    }
    dates_map: dict[str, str] = {}

    for idx, h_text in enumerate(headers):
        if idx >= len(col_days):
            break
        day_name = col_days[idx]
        if day_name not in day_indices:
            continue

        expected_weekday = day_indices[day_name]
        dt_obj = None

        dm = re.search(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", h_text)
        if dm:
            d_val, m_val = int(dm.group(1)), int(dm.group(2))
            y_val = int(dm.group(3)) if dm.group(3) else (start_dt.year if m_val == start_dt.month else end_dt.year)
            if y_val < 100:
                y_val += 2000
            try:
                candidate_dt = datetime.date(y_val, m_val, d_val)
                if candidate_dt.weekday() == expected_weekday:
                    dt_obj = candidate_dt
            except ValueError:
                pass

        if not dt_obj:
            dt_obj = start_dt + datetime.timedelta(days=expected_weekday)

        dates_map[day_name] = dt_obj.strftime("%Y-%m-%d")

    col_dates = [dates_map.get(d, "") for d in col_days]

    entries: list[dict[str, Any]] = []
    active_rowspans: dict[int, int] = {}

    for row in table.find_all("tr"):
        if "Headerrow" in row.get("class", []):
            continue

        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue

        p_match = re.search(r"\d+", cells[0].get_text().strip())
        row_period = int(p_match.group(0)) if p_match else 0

        logical_col = 1
        for cell in cells[1:]:
            # Skip columns currently occupied by rowspan from previous rows
            while logical_col < len(col_days) and active_rowspans.get(logical_col, 0) > 0:
                logical_col += 1

            if logical_col >= len(col_days):
                break

            try:
                rowspan = int(cell.get("rowspan", 1))
            except (ValueError, TypeError):
                rowspan = 1
            try:
                colspan = int(cell.get("colspan", 1))
            except (ValueError, TypeError):
                colspan = 1

            for c in range(logical_col, min(logical_col + colspan, len(col_days))):
                day_of_week = col_days[c]
                session_date = col_dates[c] if c < len(col_dates) else ""
                if not day_of_week:
                    continue

                text = cell.get_text("\n").strip()
                if not text or text in ("-", "x", "trống", "rong"):
                    continue

                # In case multiple classes or spans are present inside one cell
                inner_tables = cell.find_all("table")
                if inner_tables:
                    cell_nodes = cell.find_all("td")
                else:
                    cell_nodes = cell.find_all("span") or [cell]

                for node in cell_nodes:
                    c_text = node.get_text("\n").strip()
                    if not c_text or c_text in ("-", "x", "trống"):
                        continue

                    entry = _parse_schedule_cell_text(c_text, day_of_week, student_id)
                    if entry:
                        entry["session_date"] = session_date
                        if entry["start_period"] == 0 and 1 <= row_period <= 16:
                            entry["start_period"] = row_period
                            entry["end_period"] = min(row_period + rowspan - 1, 16) if rowspan > 1 else min(row_period + 2, 16)
                        entries.append(entry)

                if rowspan > 1:
                    active_rowspans[c] = rowspan

            logical_col += colspan

        # End of row: decrement active rowspans
        for c in list(active_rowspans.keys()):
            if active_rowspans[c] > 1:
                active_rowspans[c] -= 1
            else:
                del active_rowspans[c]

    return _deduplicate_schedule(entries)


def parse_schedule_html(html: str, student_id: str = "") -> list[dict[str, Any]]:
    """Parse schedule HTML (tries weekly view first, then general table)."""
    grid = parse_weekly_grid_table(html, student_id=student_id)
    if grid is not None:
        return grid
    return []


def _deduplicate_schedule(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate schedule records while keeping distinct statuses."""
    seen: set[tuple] = set()
    deduped = []
    for e in entries:
        sig = (
            e.get("subject_name", "").lower(),
            e.get("room", "").lower(),
            e.get("day_of_week", "").lower(),
            e.get("session_date", ""),
            e.get("start_period", 0),
            e.get("end_period", 0),
            e.get("status", ""),
        )
        if sig not in seen:
            seen.add(sig)
            deduped.append(e)
    return deduped

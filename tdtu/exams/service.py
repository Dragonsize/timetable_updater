"""
Exam service for orchestrating HTTP exam schedule retrieval across tab postbacks.
"""

from __future__ import annotations

import datetime
import logging
import os
import re
from typing import Any
from bs4 import BeautifulSoup

from tdtu.client import TDTUClient
from tdtu.exceptions import TDTUProtocolError
from tdtu.exams.parser import deduplicate_exam_rows, parse_exam_html

logger = logging.getLogger(__name__)

EXAM_TABS = [
    ("0", "midterm"),
    ("1", "final"),
    ("2", "final-2nd"),
]


def _resolve_default_semester() -> str:
    """Derive academic semester from current date (e.g. HK1/2026-2027 in Sept)."""
    today = datetime.date.today()
    if today.month >= 8:
        return f"HK1/{today.year}-{today.year+1}"
    elif today.month <= 5:
        return f"HK2/{today.year-1}-{today.year}"
    return f"Hè/{today.year-1}-{today.year}"


def fetch_exam_schedule_http(
    client: TDTUClient,
    selected_semester: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch exam schedule entries across all exam tabs via HTTP."""
    page = client.open_exam_page()
    sid = client.student_id

    # 1. Resolve target semester
    target_sem = (selected_semester or os.getenv("TIMETABLE_SEMESTER") or _resolve_default_semester()).strip()

    soup = BeautifulSoup(page.html, "html.parser")
    select = (
        soup.find("select", id=re.compile(r".*cboHocKy.*", re.IGNORECASE))
        or soup.find("select", attrs={"name": re.compile(r".*cboHocKy.*", re.IGNORECASE)})
    )
    if select:
        opts = select.find_all("option")
        target_opt = None
        target_norm = re.sub(r"[\s/\-]+", "", target_sem.lower())
        for opt in opts:
            txt = opt.get_text().strip()
            val = opt.get("value", "").strip()
            opt_norm = re.sub(r"[\s/\-]+", "", txt.lower())
            if val == target_sem or target_norm in opt_norm or target_sem.lower() in txt.lower():
                target_opt = {"value": val, "text": txt}
                break

        if target_opt:
            cur_sel = select.find("option", selected=True)
            cur_val = cur_sel.get("value", "").strip() if cur_sel else ""
            if cur_val != target_opt["value"]:
                logger.info("Switching exam semester to %s (%s)", target_opt["text"], target_opt["value"])
                page.postback(
                    event_target="LichThi1$cboHocKy",
                    extra={"LichThi1$cboHocKy": target_opt["value"]},
                )

    # 2. Re-evaluate active semester value for subsequent postbacks
    soup_after = BeautifulSoup(page.html, "html.parser")
    sel_after = soup_after.find("select", id=re.compile(r".*cboHocKy.*", re.IGNORECASE))
    sem_val = ""
    if sel_after:
        opt_sel = sel_after.find("option", selected=True)
        sem_val = opt_sel.get("value", "").strip() if opt_sel else ""

    postback_extra = {"LichThi1$cboHocKy": sem_val} if sem_val else {}

    # 3. Iterate through exam tabs
    all_exams: list[dict[str, Any]] = []

    # Check initial page before postback
    initial_exams = parse_exam_html(page.html, default_exam_type="exam", student_id=sid)
    if initial_exams:
        all_exams.extend(initial_exams)
        logger.info("Initial exam view: %d entries", len(initial_exams))

    for arg, tab_name in EXAM_TABS:
        if "LichThi1$Menu1" in page.html:
            try:
                page.postback(
                    event_target="LichThi1$Menu1",
                    event_argument=arg,
                    extra=postback_extra,
                )
            except Exception as exc:
                logger.warning("Exam tab %s postback failed: %s", tab_name, exc)
                continue

        tab_exams = parse_exam_html(
            page.html,
            default_exam_type=tab_name,
            tab_arg=arg,
            student_id=sid,
        )
        all_exams.extend(tab_exams)
        if tab_exams:
            logger.info("Exam %s: %d entries", tab_name, len(tab_exams))

    deduped = deduplicate_exam_rows(all_exams)
    logger.info("Total exams scraped via HTTP: %d", len(deduped))
    return deduped

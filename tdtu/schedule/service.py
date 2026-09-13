"""
Schedule service for orchestrating HTTP timetable retrieval and semester selection.
"""

from __future__ import annotations

import logging
from typing import Any

from tdtu.client import TDTUClient
from tdtu.exceptions import TDTUProtocolError
from tdtu.schedule.parser import (
    _deduplicate_schedule,
    parse_active_semester,
    parse_semester_options,
    parse_weekly_grid_table,
)

logger = logging.getLogger(__name__)


def get_current_semester_http(client: TDTUClient) -> str:
    """Retrieve current active semester string from portal."""
    page = client.open_schedule_page()
    semester = parse_active_semester(page.html)
    if not semester:
        raise TDTUProtocolError("Active semester string could not be resolved from portal page")
    logger.info("Active semester: %s", semester)
    return semester


def fetch_schedule_http(
    client: TDTUClient,
    selected_semester: str | None = None,
    max_weeks: int = 2,
) -> list[dict[str, Any]]:
    """Fetch class schedule entries via fast HTTP postbacks."""
    page = client.open_schedule_page()
    sid = client.student_id

    # 1. Select semester if specified
    if selected_semester:
        options = parse_semester_options(page.html)
        target_opt = None
        for opt in options:
            if opt["value"] == selected_semester or selected_semester.lower() in opt["text"].lower():
                target_opt = opt
                break

        if target_opt and not target_opt.get("selected"):
            logger.info("Switching semester to %s (%s)", target_opt["text"], target_opt["value"])
            page.postback(
                event_target="ThoiKhoaBieu1$cboHocKy",
                extra={"ThoiKhoaBieu1$cboHocKy": target_opt["value"]},
            )

    # 2. Switch to weekly view if not already
    if "radXemTKBTheoTuan" in page.html:
        logger.info("Switching to weekly view...")
        page.postback(
            event_target="ThoiKhoaBieu1$radXemTKBTheoTuan",
            extra={"ThoiKhoaBieu1$radChonLua": "radXemTKBTheoTuan"},
        )

    # 3. Parse week 1
    entries: list[dict[str, Any]] = []
    week_entries = parse_weekly_grid_table(page.html, student_id=sid)
    if week_entries:
        entries.extend(week_entries)
        logger.info("Week 1: %d classes", len(week_entries))
    else:
        logger.info("Week 1: empty")

    # 4. Navigate future weeks
    for week_idx in range(1, max_weeks):
        if "btnTuanSau" not in page.html:
            break
        logger.info("Navigating to week %d...", week_idx + 1)
        page.postback(
            event_target="ThoiKhoaBieu1$btnTuanSau",
            extra={"ThoiKhoaBieu1$btnTuanSau": ">>"},
        )
        w_entries = parse_weekly_grid_table(page.html, student_id=sid)
        if w_entries:
            entries.extend(w_entries)
            logger.info("Week %d: %d classes", week_idx + 1, len(w_entries))
        else:
            logger.info("Week %d: empty", week_idx + 1)

    deduped = _deduplicate_schedule(entries)
    logger.info("Total classes scraped via HTTP: %d", len(deduped))
    return deduped

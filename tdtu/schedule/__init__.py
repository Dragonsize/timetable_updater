"""
TDTU Schedule parsing and retrieval package.
"""

from tdtu.schedule.parser import (
    detect_status,
    parse_active_semester,
    parse_period_range,
    parse_schedule_html,
    parse_semester_options,
    parse_weekly_grid_table,
)
from tdtu.schedule.service import fetch_schedule_http, get_current_semester_http

__all__ = [
    "detect_status",
    "fetch_schedule_http",
    "get_current_semester_http",
    "parse_active_semester",
    "parse_period_range",
    "parse_schedule_html",
    "parse_semester_options",
    "parse_weekly_grid_table",
]

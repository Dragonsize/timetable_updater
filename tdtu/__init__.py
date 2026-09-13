"""
TDTU Student Portal Package.
Fast authenticated HTTP crawling, WebForms state management, and HTML parsing.
"""

from tdtu.client import TDTUClient, sanitize_url
from tdtu.exceptions import (
    TDTUAuthenticationError,
    TDTUError,
    TDTUParsingError,
    TDTUProtocolError,
)
from tdtu.exams.service import fetch_exam_schedule_http
from tdtu.schedule.service import fetch_schedule_http, get_current_semester_http

__all__ = [
    "TDTUAuthenticationError",
    "TDTUClient",
    "TDTUError",
    "TDTUParsingError",
    "TDTUProtocolError",
    "fetch_exam_schedule_http",
    "fetch_schedule_http",
    "get_current_semester_http",
    "sanitize_url",
]

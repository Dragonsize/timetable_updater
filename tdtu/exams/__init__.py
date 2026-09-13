"""
TDTU Exam parsing and retrieval package.
"""

from tdtu.exams.parser import deduplicate_exam_rows, parse_date_iso, parse_exam_html, parse_time_str
from tdtu.exams.service import fetch_exam_schedule_http

__all__ = [
    "deduplicate_exam_rows",
    "fetch_exam_schedule_http",
    "parse_date_iso",
    "parse_exam_html",
    "parse_time_str",
]

"""Sync TDTU timetable & exam data to Google Calendar via service account.

Uses the "replace bot-owned events" strategy:
  - Every event we create is tagged with private extended properties:
        source       = timetable-updater
        source_type  = class_session | exam
        source_key   = deterministic unique id (subject|date|periods|group...)
        source_hash  = md5 of the event payload
  - On each sync we reconcile: insert new, patch changed (hash differs),
    skip unchanged (same hash), delete stale (ours but no longer scraped).
  - Only events tagged with our `source` are ever modified/deleted, so
    manually-created calendar events are never touched.
"""
from __future__ import annotations

from datetime import datetime as dt, time as dtime, timedelta
import hashlib
import json
import logging
import os
import sqlite3
import time
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SOURCE_TAG = "timetable-updater"
TYPE_CLASS = "class_session"
TYPE_EXAM = "exam"

CALENDAR_SCOPE = ["https://www.googleapis.com/auth/calendar"]
TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Ho_Chi_Minh")
MAX_ATTEMPTS = 4
RETRY_STATUSES = {429, 500, 502, 503, 504}

# Configurable colors (Google Calendar color IDs: 1-11)
COLOR_EXAM = os.getenv("CALENDAR_COLOR_EXAM", "11")   # Tomato red
COLOR_CLASS = os.getenv("CALENDAR_COLOR_CLASS", "2")  # Light blue
COLOR_GRAPHITE = os.getenv("CALENDAR_COLOR_GRAPHITE", "8")  # Gray - teacher absent
COLOR_BASIL = os.getenv("CALENDAR_COLOR_BASIL", "10")  # Light green - makeup class

# TDTU official period → time mapping (must match main.py TIME_SLOTS)
TIME_SLOTS = {
    1: ('06:50', '07:40'), 2: ('07:40', '08:30'), 3: ('08:30', '09:20'),
    4: ('09:30', '10:20'), 5: ('10:20', '11:10'), 6: ('11:10', '12:00'),
    7: ('12:45', '13:35'), 8: ('13:35', '14:25'), 9: ('14:25', '15:15'),
    10: ('15:25', '16:15'), 11: ('16:15', '17:05'), 12: ('17:05', '17:55'),
    13: ('18:05', '18:55'), 14: ('18:55', '19:45'), 15: ('19:45', '20:35'),
}

# ---------------------------------------------------------------------------
# Event payload builders
# ---------------------------------------------------------------------------
def _class_payload(ev: dict) -> dict:
    date_str = ev.get("session_date", "")
    tz = ZoneInfo(TIMEZONE)
    # Class events carry periods, not raw times — map period → clock time
    ps = ev.get("start_period", 0)
    pe = ev.get("end_period", 0)
    st, _ = TIME_SLOTS.get(ps, ('08:00', '09:00'))
    _, et = TIME_SLOTS.get(pe, ('08:00', '09:00'))
    if not pe:
        _, et = TIME_SLOTS.get(ps, ('08:00', '09:00'))
    start_dt = _as_datetime(date_str, st, tz)
    end_dt = _as_datetime(date_str, et, tz)

    desc_parts = []
    if ev.get("code"):
        desc_parts.append(f"ID: {ev['code']}")
    if ev.get("group"):
        desc_parts.append(f"Group: {ev['group']}")
    desc_parts.append(f"Period {ev.get('start_period')}-{ev.get('end_period')}")

    return {
        "summary": ev.get("english_name") or ev.get("subject_name", ""),
        "description": "\n".join(desc_parts),
        "location": ev.get("room", ""),
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
        "colorId": COLOR_GRAPHITE if ev.get("status") == "absent" else COLOR_BASIL if ev.get("status") == "makeup" else COLOR_CLASS,
        "reminders": {"useDefault": True},
    }


def _exam_payload(ev: dict) -> dict:
    date_str = ev.get("session_date", "")
    tz = ZoneInfo(TIMEZONE)
    start_dt = _as_datetime(date_str, ev.get("start_time", "07:00"), tz)
    # exams have start time only; default 1h duration unless known
    dur = ev.get("duration_min") or 0
    if dur > 0:
        end_dt = start_dt + timedelta(minutes=dur)
    elif ev.get("end_time"):
        end_dt = _as_datetime(date_str, ev["end_time"], tz)
    else:
        end_dt = start_dt + timedelta(hours=1)

    desc_parts = [f"Type: {ev.get('exam_type', 'exam')}"]
    if ev.get("code"):
        desc_parts.append(f"ID: {ev['code']}")
    if ev.get("group"):
        desc_parts.append(f"Group: {ev['group']}")
    if dur:
        desc_parts.append(f"Duration: {dur}min")

    return {
        "summary": f"[EXAM] {ev.get('english_name') or ev.get('subject_name', '')}",
        "description": "\n".join(desc_parts),
        "location": ev.get("exam_room", ""),
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
        "colorId": COLOR_EXAM,
        "reminders": {"useDefault": True},
    }


def _as_datetime(date_iso: str, time_hm: str, tz: ZoneInfo):
    """Build tz-aware datetime from YYYY-MM-DD + HH:MM."""
    d = dt.fromisoformat(date_iso).date()
    h, m = (int(x) for x in time_hm.split(":"))
    return dt.combine(d, dtime(h, m), tzinfo=tz)


# ---------------------------------------------------------------------------
# source_key / hash helpers
# ---------------------------------------------------------------------------
def _source_key(ev: dict) -> str:
    if ev.get("type") == "exam":
        return (f"exam:{ev.get('session_date')}:{ev.get('start_time')}:"
                f"{ev.get('code')}:{ev.get('group')}")
    return (f"class:{ev.get('session_date')}:{ev.get('start_period')}-{ev.get('end_period')}:"
            f"{ev.get('code')}:{ev.get('group')}:{ev.get('status','normal')}")


def _source_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


def _event_source_key(event: dict) -> str:
    props = ((event.get("extendedProperties") or {}).get("private") or {})
    return str(props.get("source_key") or "").strip()


def _event_source_hash(event: dict) -> str:
    props = ((event.get("extendedProperties") or {}).get("private") or {})
    return str(props.get("source_hash") or "").strip()


def _event_source_type(event: dict) -> str:
    props = ((event.get("extendedProperties") or {}).get("private") or {})
    return str(props.get("source_type") or "").strip()


def _date_of(event: dict) -> str | None:
    """Return YYYY-MM-DD of an event's start (timed or all-day), else None.

    `_list_bot_events` requests singleEvents=True, so recurring events are
    expanded and carry `start.dateTime`.
    """
    start = event.get("start") or {}
    sd = start.get("dateTime") or start.get("date") or ""
    return sd[:10] if sd else None


# ---------------------------------------------------------------------------
# Service / API helpers
# ---------------------------------------------------------------------------
def _build_service():
    svc_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    svc_file = (os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or os.getenv("GOOGLE_CREDENTIALS") or "").strip()

    if svc_json:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(svc_json), scopes=CALENDAR_SCOPE)
    elif svc_file:
        if not os.path.isfile(svc_file):
            raise RuntimeError(f"Google service account file missing: {svc_file}")
        creds = service_account.Credentials.from_service_account_file(
            svc_file, scopes=CALENDAR_SCOPE)
    else:
        raise RuntimeError(
            "Set GOOGLE_SERVICE_ACCOUNT_JSON (GitHub secret), "
            "GOOGLE_SERVICE_ACCOUNT_FILE, or GOOGLE_CREDENTIALS.")

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _execute(operation: str, action):
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return action()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                raise
            last_exc = exc
        except (TimeoutError, OSError) as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            last_exc = exc
        delay = 2 ** (attempt - 1)
        logger.warning("%s failed (%s), retry %d/%d in %ss",
                       operation, last_exc, attempt, MAX_ATTEMPTS, delay)
        time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _list_bot_events(service, calendar_id: str) -> dict[str, dict]:
    """All events tagged with our source, keyed by source_key."""
    by_key: dict[str, dict] = {}
    page_token: str | None = None
    while True:
        resp = _execute(
            "calendar list",
            lambda: service.events().list(
                calendarId=calendar_id,
                singleEvents=True,
                privateExtendedProperty=f"source={SOURCE_TAG}",
                maxResults=2500,
                pageToken=page_token,
            ).execute(),
        )
        for ev in resp.get("items", []):
            key = _event_source_key(ev)
            if key:
                by_key[key] = ev
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return by_key


# ---------------------------------------------------------------------------
# SQLite Audit Logging
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = os.getenv("SYNC_DB_PATH", "sync_history.db")


def init_sync_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialize SQLite database for tracking sync history."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_key TEXT,
                event_id TEXT,
                summary TEXT,
                start_time TEXT,
                end_time TEXT,
                location TEXT,
                details TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_action ON sync_history(action)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_timestamp ON sync_history(timestamp)")
        conn.commit()


def log_sync_event(
    action: str,
    source_type: str,
    source_key: str,
    event_id: str,
    summary: str,
    start_time: str,
    end_time: str,
    location: str,
    details: str = "",
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Record an inserted, patched, or deleted event to SQLite."""
    init_sync_db(db_path)
    now_iso = dt.now().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sync_history (
                timestamp, action, source_type, source_key, event_id,
                summary, start_time, end_time, location, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso, action, source_type, source_key, event_id,
                summary, start_time, end_time, location, details
            ),
        )
        conn.commit()


def get_recent_sync_events(
    limit: int = 20,
    action: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict]:
    """Query recent sync events from SQLite."""
    init_sync_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if action:
            cur = conn.execute(
                """
                SELECT * FROM sync_history
                WHERE action = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (action.upper(), limit),
            )
        else:
            cur = conn.execute(
                """
                SELECT * FROM sync_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [dict(row) for row in cur.fetchall()]


def format_sync_history_table(events: list[dict]) -> str:
    """Format a list of sync events as a human-readable table."""
    if not events:
        return "No sync history found in database."

    header = f"{'ID':<5} {'ACTION':<8} {'TIMESTAMP':<20} {'DATE/TIME':<22} {'SUMMARY':<35} {'LOCATION'}"
    separator = "-" * 110
    lines = [header, separator]
    for e in events:
        row_id = str(e.get("id", ""))
        action = e.get("action", "")
        ts = str(e.get("timestamp", ""))[:19].replace("T", " ")
        start = str(e.get("start_time", ""))[:16].replace("T", " ")
        summary = str(e.get("summary", ""))[:33]
        location = str(e.get("location", ""))
        lines.append(f"{row_id:<5} {action:<8} {ts:<20} {start:<22} {summary:<35} {location}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------
def sync_to_google_calendar(events: list[dict]) -> tuple[int, int]:
    """Reconcile scraped `events` against the target calendar.

    Returns (upserted, deleted).
    """
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "").strip()
    if not calendar_id:
        raise RuntimeError("GOOGLE_CALENDAR_ID not set in .env")

    service = _build_service()

    # Build desired sync items
    desired: list[dict] = []
    for ev in events:
        payload = _exam_payload(ev) if ev.get("type") == "exam" else _class_payload(ev)
        key = _source_key(ev)
        desired.append({
            "key": key,
            "source_type": ev.get("type") == "exam" and TYPE_EXAM or TYPE_CLASS,
            "hash": _source_hash(payload),
            "payload": payload,
        })

    existing = _list_bot_events(service, calendar_id)
    desired_keys = {item["key"] for item in desired}

    # Determine the scraped class date window. _class_payload maps periods→times
    # so we read the ISO start date from each desired payload. Exams always cover
    # their full range already (all tabs are always scraped), so only bound classes.
    class_dates = [
        item["payload"]["start"]["dateTime"][:10]
        for item in desired
        if item["source_type"] == TYPE_CLASS
    ]
    window_lo = min(class_dates) if class_dates else None
    window_hi = max(class_dates) if class_dates else None

    upserted = 0
    for item in desired:
        cur = existing.get(item["key"])
        if cur and _event_source_hash(cur) == item["hash"]:
            continue  # unchanged
        event_id = str(cur.get("id") or "") if cur else ""
        body = dict(item["payload"])
        body["extendedProperties"] = {
            "private": {
                "source": SOURCE_TAG,
                "source_type": item["source_type"],
                "source_key": item["key"],
                "source_hash": item["hash"],
            }
        }
        start_str = body.get("start", {}).get("dateTime", "")
        end_str = body.get("end", {}).get("dateTime", "")
        summary = body.get("summary", "")
        loc = body.get("location", "")

        if event_id:
            _execute("calendar patch",
                     lambda: service.events().patch(
                         calendarId=calendar_id, eventId=event_id, body=body).execute())
            upserted += 1
            logger.info("~ [UPDATE] %s (%s, %s)", summary, start_str, loc)
            log_sync_event(
                action="UPDATE",
                source_type=item["source_type"],
                source_key=item["key"],
                event_id=event_id,
                summary=summary,
                start_time=start_str,
                end_time=end_str,
                location=loc,
                details=body.get("description", ""),
            )
        else:
            resp_ev = _execute("calendar insert",
                     lambda: service.events().insert(
                         calendarId=calendar_id, body=body).execute())
            upserted += 1
            new_id = str((resp_ev or {}).get("id") or "")
            logger.info("+ [INSERT] %s (%s, %s)", summary, start_str, loc)
            log_sync_event(
                action="INSERT",
                source_type=item["source_type"],
                source_key=item["key"],
                event_id=new_id,
                summary=summary,
                start_time=start_str,
                end_time=end_str,
                location=loc,
                details=body.get("description", ""),
            )

    deleted = 0
    for key, ev in existing.items():
        if key in desired_keys:
            continue
        stype = _event_source_type(ev)
        if stype not in (TYPE_CLASS, TYPE_EXAM):
            continue  # never touch other source types
        if stype == TYPE_CLASS:
            # Only prune stale classes inside the scraped date window, so a
            # short (e.g. 2-week) sync never deletes far-future weeks.
            if window_lo is None:
                continue  # no classes scraped -> preserve all
            ev_date = _date_of(ev)
            if ev_date is None or not (window_lo <= ev_date <= window_hi):
                continue
        eid = str(ev.get("id") or "").strip()
        if eid:
            _execute("calendar delete",
                     lambda: service.events().delete(
                         calendarId=calendar_id, eventId=eid).execute())
            deleted += 1
            summary = ev.get("summary", "")
            start_str = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date") or ""
            end_str = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date") or ""
            loc = ev.get("location", "")
            logger.info("- [DELETE] %s (%s, %s)", summary, start_str, loc)
            log_sync_event(
                action="DELETE",
                source_type=stype,
                source_key=key,
                event_id=eid,
                summary=summary,
                start_time=start_str,
                end_time=end_str,
                location=loc,
                details=ev.get("description", ""),
            )

    return upserted, deleted


def sync_csv_to_calendar(csv_path: str) -> tuple[int, int]:
    """Read Google-Calendar CSV produced by main.py and sync it.

    The CSV is a convenience fallback; prefer passing event dicts directly.
    """
    import csv as csvlib
    from datetime import datetime as dt

    events = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csvlib.DictReader(f):
            date_fmt = row.get("Start Date", "")
            try:
                d = dt.strptime(date_fmt, "%m/%d/%Y")
            except ValueError:
                continue
            ev = {
                "type": "exam" if row.get("Subject", "").startswith("[EXAM]") else "class",
                "session_date": d.strftime("%Y-%m-%d"),
                "english_name": row.get("Subject", "").replace("[EXAM] ", ""),
                "subject_name": row.get("Subject", "").replace("[EXAM] ", ""),
                "start_time": row.get("Start Time", ""),
                "end_time": row.get("End Time", ""),
                "code": "",
                "group": "",
                "exam_room": row.get("Location", ""),
                "room": row.get("Location", ""),
            }
            events.append(ev)
    return sync_to_google_calendar(events)

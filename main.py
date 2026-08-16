#!/usr/bin/env python3
"""
TDTU Timetable & Exam Scraper — Playwright-based.
Single login session. Default: current week + next week + exams.
Use --full to scan entire semester.
"""
import argparse
import csv
import logging
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STUDENT_ID = os.getenv("SCHOOL_USERNAME") or os.getenv("STUDENT_ID")
PASSWORD = os.getenv("SCHOOL_PASSWORD") or os.getenv("PASSWORD")
TARGET_SEMESTER = os.getenv("TIMETABLE_SEMESTER")
_raw_weeks = os.getenv("CRAWLER_WEEKS_AHEAD", "").strip()
CRAWLER_WEEKS_AHEAD = int(_raw_weeks) if _raw_weeks.isdigit() else 2

PORTAL_URL = "https://old-stdportal.tdtu.edu.vn/Login/"
SCHEDULE_URL = "https://lichhoc-lichthi.tdtu.edu.vn/tkb2.aspx"
EXAM_URL = "https://lichhoc-lichthi.tdtu.edu.vn/xemlichthi.aspx"

TIME_SLOTS = {
    1: ('06:50', '07:40'), 2: ('07:40', '08:30'), 3: ('08:30', '09:20'),
    4: ('09:30', '10:20'), 5: ('10:20', '11:10'), 6: ('11:10', '12:00'),
    7: ('12:45', '13:35'), 8: ('13:35', '14:25'), 9: ('14:25', '15:15'),
    10: ('15:25', '16:15'), 11: ('16:15', '17:05'), 12: ('17:05', '17:55'),
    13: ('18:05', '18:55'), 14: ('18:55', '19:45'), 15: ('19:45', '20:35'),
}

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Weekly grid parser
# ---------------------------------------------------------------------------
PARSE_WEEKLY_GRID_JS = r"""
() => {
    const englishDays = [
        ["monday", "Monday"], ["tuesday", "Tuesday"], ["wednesday", "Wednesday"],
        ["thursday", "Thursday"], ["friday", "Friday"], ["saturday", "Saturday"],
        ["sunday", "Sunday"]
    ];
    const vnMap = [
        ["thứ 2", "Monday"], ["thứ 3", "Tuesday"], ["thứ 4", "Wednesday"],
        ["thứ 5", "Thursday"], ["thứ 6", "Friday"], ["thứ 7", "Saturday"],
        ["chủ nhật", "Sunday"], ["cn", "Sunday"]
    ];
    const extractWeekday = (t) => {
        const l = (t||"").replace(/\s+/g," ").toLowerCase();
        for (const [n,d] of englishDays) { if (l.includes(n)) return d; }
        for (const [v,e] of vnMap) { if (l.includes(v)) return e; }
        return "";
    };
    const toIsoDate = (day, month, year) => {
        if (!day||!month||!year) return "";
        const d = new Date(Date.UTC(year, month-1, day));
        if (d.getUTCFullYear()!==year||d.getUTCMonth()!==month-1||d.getUTCDate()!==day) return "";
        return `${year.toString().padStart(4,"0")}-${month.toString().padStart(2,"0")}-${day.toString().padStart(2,"0")}`;
    };
    const extractWeekRange = () => {
        const v = document.querySelector("[id='ThoiKhoaBieu1_btnTuanHienTai']")?.value||"";
        const m = Array.from(v.matchAll(/(\d{1,2})\/(\d{1,2})\/(\d{2,4})/g));
        if (m.length<2) return null;
        const dates = m.slice(0,2).map(x => { let y=parseInt(x[3],10); if(y<100) y+=2000; return toIsoDate(parseInt(x[1],10),parseInt(x[2],10),y); });
        return dates[0]&&dates[1] ? {start:dates[0],end:dates[1]} : null;
    };
    const extractDate = (ht, wr) => {
        const m = (ht||"").replace(/\s+/g," ").match(/(\d{1,2})[\/.\-](\d{1,2})(?:[\/.\-](\d{2,4}))?/);
        if (!m) return "";
        const day=parseInt(m[1],10), mo=parseInt(m[2],10);
        if (m[3]) { let y=parseInt(m[3],10); if(y<100)y+=2000; return toIsoDate(day,mo,y); }
        if (!wr) return "";
        for (const y of new Set([parseInt(wr.start.slice(0,4),10),parseInt(wr.end.slice(0,4),10)])) {
            const c=toIsoDate(day,mo,y); if (c&&c>=wr.start&&c<=wr.end) return c;
        }
        return "";
    };
    const cleanSubject = (t) => (t||"").split("\n")[0].split("|")[0].trim();
    const extractEnglishName = (t) => {
        const firstLine = (t||"").split("(")[0].trim();
        const parts = firstLine.split("|");
        if (parts.length > 1) return parts[1].trim();
        return parts[0].trim();
    };
    const extractCode = (t) => {
        const m = (t||"").match(/\((\d{5,6})/);
        return m ? m[1] : "";
    };
    const extractGroup = (t) => {
        const m = (t||"").match(/(?:Groups?|Nhóm\|Groups?)\s*:?\s*(\d+)/i);
        return m ? m[1] : "";
    };
    const extractRoom = (t) => {
        const r=(t||"").match(/Room:\s*([^\n]+)/i)||(t||"").match(/Phòng:\s*([^\n]+)/i);
        return r?r[1].trim().replace(/\s*\)$/,"").trim():"";
    };
    const extractPeriodRange = (t,fs,fe) => {
        const fb={start:Number.isFinite(fs)?fs:0,end:Number.isFinite(fe)?fe:0};
        const mm=String(t||"").match(/(?:tiết|period)\s*:\s*([0-9,\-;\s]+)/i);
        if (!mm) return fb;
        const digits=(mm[1].match(/\d+/g)||[]).map(Number).filter(n=>Number.isFinite(n));
        if (!digits.length) return fb;
        const compact=mm[1].replace(/\s+/g,"");
        if (/^\d{2,}$/.test(compact)) {
            const ch=compact.split("").map(Number).filter(n=>Number.isFinite(n));
            return ch.length?{start:Math.min(...ch),end:Math.max(...ch)}:fb;
        }
        return {start:Math.min(...digits),end:Math.max(...digits)};
    };
    const splitCellEntries = (cell) => {
        const it=cell.querySelector("table");
        if (it) { const tds=Array.from(it.querySelectorAll("td")); if(tds.length>1) return tds.map(td=>(td.innerText||"").trim()).filter(t=>t.length>0); }
        return null;
    };

    const detectStatus = (t) => {
        const l = (t||"").toLowerCase();
        if (l.includes("báo vắng") || l.includes("bao vang")) return "absent";
        if (l.includes("dạy bù") || l.includes("day bu")) return "makeup";
        return "normal";
    };

    let target=null, targetRows=[];
    for (const tbl of Array.from(document.querySelectorAll("table"))) {
        const rows=Array.from(tbl.querySelectorAll(":scope > tbody > tr, :scope > tr"));
        if (rows.length<2) continue;
        const hc=Array.from(rows[0].querySelectorAll(":scope > th, :scope > td"));
        if (hc.length<3) continue;
        const ah=hc.map(c=>(c.innerText||"").toLowerCase()).join(" ");
        const fh=(hc[0]?.innerText||"").toLowerCase();
        const hp=/period|tiết|tiet|buổi|buoi|\bca\b|slot/.test(fh)||/period|tiết|tiet|buổi|buoi|\bca\b|slot/.test(ah);
        const hd=/day|thứ|thu|monday|tuesday|wednesday|thursday|friday|saturday|sunday|cn|chủ nhật|chu nhat/.test(ah);
        const hs=rows.slice(1).some(r=>{const fc=r.querySelector(":scope > td, :scope > th"); return /morning|afternoon|evening|sáng|chieu|chiều|tối|toi/.test((fc?.innerText||"").toLowerCase());});
        if (hd&&(hp||hs)) { target=tbl; targetRows=rows; break; }
    }
    if (!target) return null;

    const rows=targetRows;
    const hc=Array.from(rows[0].querySelectorAll(":scope > th, :scope > td"));
    if (hc.length<3) return null;
    const dbc={}, dxc={};
    const wr=extractWeekRange();
    for (let c=1;c<hc.length;c++) { const ht=hc[c]?.innerText||""; dbc[c]=extractWeekday(ht); dxc[c]=extractDate(ht,wr); }
    const carry={}, entries=[];
    for (let r=1;r<rows.length;r++) {
        const cells=Array.from(rows[r].querySelectorAll(":scope > td, :scope > th"));
        let lc=0, rp=0;
        for (const cell of cells) {
            while (carry[lc]>0) lc++;
            const rs=Math.max(parseInt(cell.getAttribute("rowspan")||"1",10)||1,1);
            const cs=Math.max(parseInt(cell.getAttribute("colspan")||"1",10)||1,1);
            const text=(cell.innerText||"").trim();
            if (lc===0) { const pm=text.match(/^(?:tiết|tiet|ca|period|slot)[.\s]*(\d+)$|^(\d+)$/i); if(pm) rp=parseInt(pm[1]||pm[2],10); }
            else {
                for (let c=lc;c<lc+cs;c++) {
                    const dow=dbc[c]||"", sd=dxc[c]||"";
                    if (!dow||!text||/^(-|x|trống)$/i.test(text)) continue;
                    const ets=splitCellEntries(cell)||[text];
                    for (const et of ets) {
                        const subject=cleanSubject(et); if (!subject) continue;
                        const pp=extractPeriodRange(et, rp, rp>0?rp+rs-1:0);
                        if (!(pp.start>0&&pp.end>=pp.start)) continue;
                        entries.push({subject_name:subject,english_name:extractEnglishName(et),code:extractCode(et),group:extractGroup(et),room:extractRoom(et),day_of_week:dow,session_date:sd,start_period:pp.start,end_period:pp.end,status: detectStatus(et)});
                    }
                }
            }
            if (rs>1) for (let c=lc;c<lc+cs;c++) carry[c]=Math.max(carry[c]||0,rs);
            lc+=cs;
        }
        for (const k of Object.keys(carry)) if (carry[k]>0) carry[k]--;
    }
    return entries;
}
"""

# ---------------------------------------------------------------------------
# Exam parser (grid layout: day headers, cells with embedded details)
# ---------------------------------------------------------------------------
EXAM_GRID_JS = r"""
(tableId) => {
    const englishDays = [
        ["monday", "Monday"], ["tuesday", "Tuesday"], ["wednesday", "Wednesday"],
        ["thursday", "Thursday"], ["friday", "Friday"], ["saturday", "Saturday"],
        ["sunday", "Sunday"]
    ];
    const vnMap = [
        ["thứ 2", "Monday"], ["thứ 3", "Tuesday"], ["thứ 4", "Wednesday"],
        ["thứ 5", "Thursday"], ["thứ 6", "Friday"], ["thứ 7", "Saturday"],
        ["chủ nhật", "Sunday"], ["cn", "Sunday"]
    ];
    const extractWeekday = (t) => {
        const l = (t||"").replace(/\s+/g," ").toLowerCase();
        for (const [n,d] of englishDays) { if (l.includes(n)) return d; }
        for (const [v,e] of vnMap) { if (l.includes(v)) return e; }
        return "";
    };
    const toIsoDate = (d, mo, y) => {
        if (!d||!mo||!y) return "";
        const dt = new Date(Date.UTC(y, mo-1, d));
        if (dt.getUTCFullYear()!==y||dt.getUTCMonth()!==mo-1||dt.getUTCDate()!==d) return "";
        return `${y.toString().padStart(4,"0")}-${mo.toString().padStart(2,"0")}-${d.toString().padStart(2,"0")}`;
    };

    const table = document.getElementById(tableId);
    if (!table) return [];
    const rows = Array.from(table.querySelectorAll("tr"));
    if (rows.length < 2) return [];
    const headerCells = Array.from(rows[0].querySelectorAll("td, th"));
    if (headerCells.length < 3) return [];

    const dayByCol = {};
    for (let c = 0; c < headerCells.length; c++)
        dayByCol[c] = extractWeekday(headerCells[c]?.innerText || "");

    const entries = [];
    for (let r = 1; r < rows.length; r++) {
        const cells = Array.from(rows[r].querySelectorAll("td"));
        for (let c = 0; c < cells.length; c++) {
            const dow = dayByCol[c];
            if (!dow) continue;
            const text = (cells[c].innerText || "").replace(/\s+/g, " ").trim();
            if (!text) continue;
            const subject = (text.split("(")[0].split("|")[0].trim()) || "";
            if (!subject) continue;
            const codeMatch = text.match(/\((\d{5,6})\)/);
            const dateMatch = text.match(/Date:\s*(\d{1,2})\/(\d{1,2})\/(\d{2,4})/);
            const timeMatch = text.match(/Time:\s*(\d{1,2}):(\d{2})/);
            const roomMatch = text.match(/Room:\s*(\S+)/);
            const groupMatch = text.match(/Group:\s*(\d+)/);
            const subgroupMatch = text.match(/Sub-group:\s*(\d+)/);
            const durationMatch = text.match(/Duration:\s*(\d+)/);
            let isoDate = "";
            if (dateMatch) {
                let y = parseInt(dateMatch[3], 10);
                if (y < 100) y += 2000;
                isoDate = toIsoDate(parseInt(dateMatch[1], 10), parseInt(dateMatch[2], 10), y);
            }
            const startTime = timeMatch ? `${timeMatch[1].padStart(2,"0")}:${timeMatch[2]}` : "";
            entries.push({
                subject_name: subject,
                code: codeMatch ? codeMatch[1] : "",
                day_of_week: dow,
                session_date: isoDate,
                start_time: startTime,
                duration_min: durationMatch ? parseInt(durationMatch[1], 10) : 0,
                exam_room: roomMatch ? roomMatch[1] : "",
                group: groupMatch ? groupMatch[1] : "",
                subgroup: subgroupMatch ? subgroupMatch[1] : "",
            });
        }
    }
    return entries;
}
"""


# ---------------------------------------------------------------------------
# Shared browser session
# ---------------------------------------------------------------------------
def _launch(pw):
    return pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-http2"],
    )


def _login(page):
    """Login to old portal, ready for navigation."""
    page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("#txtUser", state="visible", timeout=30000)
    page.fill("#txtUser", STUDENT_ID)
    page.fill("#txtPass", PASSWORD)
    page.locator("#btnLogIn").first.click(timeout=10000)
    page.wait_for_url(lambda url: "login" not in str(url).lower(), timeout=60000)
    page.wait_for_load_state("domcontentloaded", timeout=60000)
    logger.info("Login OK")


def _wait_postback(page, delay_ms=3000):
    """Wait after an ASP.NET __doPostBack."""
    page.wait_for_timeout(delay_ms)
    page.wait_for_load_state("domcontentloaded", timeout=30000)


# ---------------------------------------------------------------------------
# Scraping functions (share same page context)
# ---------------------------------------------------------------------------
def _scrape_classes(page, weeks: int, semester: str | None = None) -> list[dict]:
    """Scrape class timetable for `weeks` weeks from current."""
    page.goto(SCHEDULE_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1000)

    sem = semester or TARGET_SEMESTER
    if sem:
        cur = page.locator("[id='ThoiKhoaBieu1_cboHocKy'] option[selected]").get_attribute("value") or ""
        if cur != sem:
            page.locator("[id='ThoiKhoaBieu1_cboHocKy']").select_option(sem)
            _wait_postback(page)

    wr = page.locator("[id='ThoiKhoaBieu1_radXemTKBTheoTuan']")
    if wr.count() > 0:
        wr.click(timeout=15000)
        _wait_postback(page)

    entries = []
    for idx in range(weeks):
        raw = page.evaluate(PARSE_WEEKLY_GRID_JS) or []
        if raw:
            for e in raw:
                e["student_id"] = STUDENT_ID
                e["type"] = "class"
            entries.extend(raw)
            logger.info("Week %d: %d classes", idx + 1, len(raw))
        else:
            logger.info("Week %d: empty", idx + 1)
        if idx < weeks - 1:
            btn = page.locator("[id='ThoiKhoaBieu1_btnTuanSau']")
            if btn.count() == 0:
                break
            btn.click(timeout=15000)
            _wait_postback(page)

    return entries


def _scrape_exams(page, semester: str | None = None) -> list[dict]:
    """Scrape all exam tabs (midterm/final/final-2nd)."""
    page.goto(EXAM_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    # Exam page has its own semester dropdown
    sem = semester or TARGET_SEMESTER
    if sem:
        cur = page.locator("[id='LichThi1_cboHocKy'] option[selected]").get_attribute("value") or ""
        if cur != sem:
            page.locator("[id='LichThi1_cboHocKy']").select_option(sem)
            _wait_postback(page)

    tabs = [
        (0, "LichThi1_GiuaKyTable", "midterm"),
        (1, "LichThi1_CuoiKyTable", "final"),
        (2, "LichThi1_CuoiKy2Table", "final-2nd"),
    ]
    entries = []
    for tab_idx, table_id, tab_name in tabs:
        page.evaluate(f"__doPostBack('LichThi1$Menu1', '{tab_idx}')")
        _wait_postback(page)
        raw = page.evaluate(EXAM_GRID_JS, table_id) or []
        for e in raw:
            e["student_id"] = STUDENT_ID
            e["type"] = "exam"
            e["exam_type"] = tab_name
        entries.extend(raw)
        if raw:
            logger.info("Exam %s: %d entries", tab_name, len(raw))

    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_all(semester: str | None = None, weeks: int = 2,
              exams: bool = True) -> list[dict]:
    """Single-session scrape: classes for `weeks` weeks + exams."""
    if not STUDENT_ID or not PASSWORD:
        raise ValueError("Missing STUDENT_ID/SCHOOL_USERNAME or PASSWORD/SCHOOL_PASSWORD in .env")

    with sync_playwright() as pw:
        browser = _launch(pw)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        )
        page = ctx.new_page()
        try:
            _login(page)
            all_entries = _scrape_classes(page, weeks, semester)
            if exams:
                all_entries.extend(_scrape_exams(page, semester))
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"Browser timed out: {exc}") from exc
        finally:
            ctx.close()
            browser.close()

    # Deduplicate
    seen: set[tuple] = set()
    deduped = []
    for e in all_entries:
        sig = (e.get("subject_name"), e.get("room") or e.get("exam_room", ""),
               e.get("day_of_week"), e.get("session_date", ""),
               e.get("start_period", 0), e.get("end_period", 0),
               e.get("start_time", ""))
        if sig not in seen:
            seen.add(sig)
            deduped.append(e)
    return deduped


def export_csv(events: list[dict], filename: str) -> str:
    """Export to Google Calendar CSV."""
    headers = ["Subject", "Start Date", "Start Time", "End Date", "End Time",
               "All Day Event", "Description", "Location", "Private"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for ev in events:
            is_exam = ev.get("type") == "exam"
            date_str = ev.get("session_date", "")
            st = ev.get("start_time", "")
            et = ""
            code = ev.get("code", "")
            group = ev.get("group", "")
            if is_exam:
                subj = f"[EXAM] {ev.get('subject_name', '')}"
                desc = f"Type: {ev.get('exam_type', 'exam')}"
                room = ev.get("exam_room", "")
                if ev.get("duration_min"):
                    desc += f", Duration: {ev['duration_min']}min"
                if code:
                    desc += f"\nID: {code}"
                if group:
                    desc += f"\nGroup: {group}"
            else:
                subj = ev.get("english_name") or ev.get("subject_name", "")
                desc_parts = []
                if code:
                    desc_parts.append(f"ID: {code}")
                if group:
                    desc_parts.append(f"Group: {group}")
                desc_parts.append(f"Period {ev.get('start_period')}-{ev.get('end_period')}")
                desc = "\n".join(desc_parts)
                room = ev.get("room", "")
                st, _ = TIME_SLOTS.get(ev.get("start_period", 0), ("08:00", "09:00"))
                _, et = TIME_SLOTS.get(ev.get("end_period", 0), ("08:00", "09:00"))

            if not date_str:
                continue
            parts = date_str.split("-")
            date_fmt = (f"{int(parts[1]):02d}/{int(parts[2]):02d}/{parts[0]}"
                        if len(parts) == 3 else date_str)

            w.writerow({
                "Subject": subj, "Start Date": date_fmt, "Start Time": st,
                "End Date": date_fmt, "End Time": et, "All Day Event": "False",
                "Description": desc, "Location": room, "Private": "False",
            })
    logger.info("Exported %d events → %s", len(events), filename)
    return filename


def sync_to_calendar(events: list[dict]) -> tuple[int, int]:
    """Push events to Google Calendar using service account."""
    from calendar_sync import sync_to_google_calendar
    return sync_to_google_calendar(events)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="TDTU Timetable & Exam Scraper")
    parser.add_argument("--semester", "-s", help="Semester value (e.g. 136)")
    parser.add_argument("--output", "-o", default="timetable.csv")
    parser.add_argument("--weeks", "-w", type=int, default=CRAWLER_WEEKS_AHEAD,
                        help="Weeks to scrape (default: 2 = current + next)")
    parser.add_argument("--full", action="store_true",
                        help="Scan full semester (20 weeks, slow)")
    parser.add_argument("--no-exams", action="store_true",
                        help="Skip exam scraping")
    parser.add_argument("--sync", action="store_true",
                        help="Push events to Google Calendar after scraping")
    args = parser.parse_args()

    try:
        weeks = 16 if args.full else args.weeks
        logger.info("Fetching %d weeks + exams...", weeks)
        events = fetch_all(semester=args.semester, weeks=weeks,
                           exams=not args.no_exams)
        if not events:
            logger.warning("No events found.")
        else:
            export_csv(events, args.output)
            classes = sum(1 for e in events if e["type"] == "class")
            exams = sum(1 for e in events if e["type"] == "exam")
            print(f"\n✓ Done! {classes} classes + {exams} exams → {args.output}")

        if args.sync:
            logger.info("Syncing to Google Calendar...")
            upserted, deleted = sync_to_calendar(events)
            logger.info("Calendar sync: %d upserted, %d deleted", upserted, deleted)

    except Exception as exc:
        logger.error("Error: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

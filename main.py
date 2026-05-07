import os
import json
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

# Load credentials from environment variables or .env file
load_dotenv()
USERNAME = os.getenv('SCHOOL_USERNAME')
PASSWORD = os.getenv('SCHOOL_PASSWORD')
TIMETABLE_URL = "https://lichhoc-lichthi.tdtu.edu.vn/tkb2.aspx?Token=1ed4bcde&RequestId=9ae5ec31"


def login_and_get_timetable():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # Go to login page
        page.goto(TIMETABLE_URL)
        # Fill login form
        page.fill('input[name="email"], input#txtUser', USERNAME)
        page.fill('input[name="password"], input#txtPass', PASSWORD)
        page.click('button#btnLogIn, button.login100-form-btn')
        page.wait_for_load_state('networkidle')
        page.click('#ThoiKhoaBieu1_radXemTKBTheoTuan')
        page.wait_for_load_state('networkidle')

        week = os.getenv('TIMETABLE_WEEK')
        week_select = page.locator(
            'select[id*="Tuan"], select[name*="Tuan"], select[id*="Week"], select[name*="Week"]'
        ).first
        if week_select.count():
            if week:
                week_select.select_option(value=week)
            page.wait_for_load_state('networkidle')

        page.wait_for_selector('#ThoiKhoaBieu1_showTKB table, table[id*="ThoiKhoaBieu"]')
        timetable = page.locator('#ThoiKhoaBieu1_showTKB').evaluate("""
            root => {
                const days = Array.from(root.querySelectorAll('tr.Headerrow td'))
                    .slice(1)
                    .map(td => td.innerText.trim());
                const sessions = Array.from(root.querySelectorAll('tr.rowContent'));

                return sessions.flatMap(row => {
                    const cells = Array.from(row.querySelectorAll('td'));
                    const session = cells[0]?.innerText.trim() || '';

                    return cells.slice(1).flatMap((cell, index) => {
                        const day = days[index] || '';
                        return Array.from(cell.querySelectorAll('span')).map(item => ({
                            day,
                            session,
                            text: item.innerText.replace(/\\s+/g, ' ').trim()
                        }));
                    });
                });
            }
        """)
        html = json.dumps(timetable, ensure_ascii=False)
        browser.close()
        return html

if __name__ == "__main__":
    html = login_and_get_timetable()
    def save_timetable(json_data, filename="timetable.json"):
        with open(filename, "w", encoding="utf-8") as f:
            f.write(json_data)

    def sync_to_google_calendar(timetable_json):
        # TODO: Implement Google Calendar sync logic
        print("[MVP] Would sync to Google Calendar here.")

    save_timetable(html)
    sync_to_google_calendar(html)
    print("[MVP] Timetable saved and ready for Google Calendar sync.")

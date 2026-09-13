# Timetable & Exam Updater (TDTU)

Automates student login to Ton Duc Thang University (TDTU) portal, scrapes weekly class timetable and exam schedule via fast direct HTTP requests, and exports to CSV or syncs directly to Google Calendar.

## Features
- **Fast HTTP Crawler**: Uses direct authenticated HTTP requests and WebForms state management (`requests` + `BeautifulSoup4`). Crawls schedules in seconds without browser overhead.
- **Playwright Fallback**: Optional Chromium browser scraper fallback (`--playwright`) if needed.
- **Accurate Timetable Extraction**: Supports multi-week crawling, teacher absence ("GV báo vắng"), and make-up classes ("GV dạy bù").
- **Exam Schedule**: Scrapes midterm, final, and second-chance final exams across all tabs.
- **Google Calendar Sync & SQLite Audit Log**: Reconciles bot-owned events (`extendedProperties.private`) without overwriting manual events. Automatically records all inserted, updated, and deleted events in a local SQLite database (`sync_history.db`) so you always know what changed.
- **Custom Event Colors**: Tomato red for exams, graphite gray for teacher absences, light green for makeup classes, light blue for regular classes.
- **Flexible Export**: CSV ready for Google Calendar import, or JSON for debugging.

## Tech Stack
- Python 3.10+
- `requests` & `beautifulsoup4` (Fast HTTP crawling & HTML parsing)
- `playwright` (Optional browser fallback)
- `google-api-python-client` (Google Calendar API v3)
- `python-dotenv` (Environment variables)

## Setup

1. **Clone repository and install dependencies:**
   ```sh
   pip install -r requirements.txt
   ```
   *(Optional for Playwright fallback)*:
   ```sh
   python -m playwright install --with-deps chromium
   ```

2. **Configure environment:**
   Copy `.env.sample` to `.env` and configure credentials:
   ```sh
   cp .env.sample .env
   ```
   Edit `.env`:
   ```dotenv
   SCHOOL_USERNAME=your_student_id
   SCHOOL_PASSWORD=your_portal_password
   GOOGLE_CALENDAR_ID=primary
   GOOGLE_SERVICE_ACCOUNT_FILE=credentials.json
   ```

## Usage

### Basic Usage (Scrape 2 weeks + exams to CSV)
```sh
python main.py
```

### Direct Google Calendar Sync
```sh
python main.py --sync
```

### Full Semester Scrape
```sh
python main.py --full --sync
```

### Specify Number of Weeks
```sh
python main.py --weeks 4
```

### Specific Semester
```sh
python main.py --semester 136
```

### JSON Export
```sh
python main.py --json
```

### Force Playwright Browser Scraper
```sh
python main.py --playwright
```

### View SQLite Sync History (Audit Log)
Show recent inserted, updated, and deleted calendar events from SQLite:
```sh
# Show last 15 sync operations
python main.py --history

# Show last 30 sync operations
python main.py --history 30
```


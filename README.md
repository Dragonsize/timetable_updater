# Timetable Updater with requests and Google Calendar

## Overview
Automates login to your university timetable, scrapes weekly schedule, and outputs CSV for Google Calendar import. Uses direct HTTP requests instead of browser automation.

## Tech Stack
- Python 3
- requests (HTTP client)
- lxml (HTML parsing)
- Google Calendar API (optional sync)
- python-dotenv (environment variables)

## Setup
1. **Install dependencies:**
   ```sh
   pip install -r requirements.txt
   ```

2. **Google API Credentials (optional):**
   - Create a Google Cloud project and enable Calendar API.
   - Download `credentials.json` and add to repo (or use GitHub Secrets).

3. **Environment Variables:**
   Create `.env` file with:
   ```
   SCHOOL_USERNAME=your_student_id
   SCHOOL_PASSWORD=your_password
   TIMETABLE_SEMESTER=136  # Optional: e.g., 136=HK1/2026-2027
   GOOGLE_CALENDAR_ID=primary  # Optional: your calendar ID
   ```

## Usage

### Basic Usage (CSV output)
```sh
python main.py
```

### With Specific Semester
```sh
python main.py --semester 136
```

### With JSON Output (for debugging)
```sh
python main.py --json
```

### Disable CSV Output
```sh
python main.py --no-csv --json
```

## Output Formats

### CSV Format (for Google Calendar import)
The CSV file is ready to import directly into Google Calendar:
- Subject
- Start Date (MM/DD/YYYY)
- Start Time (HH:MM)
- End Date (MM/DD/YYYY)
- End Time (HH:MM)
- All Day Event (False)
- Description
- Location
- Private (False)

### JSON Format (optional)
Raw scraped data for debugging.

## Files
- `main.py` — Main script
- `requirements.txt` — Dependencies
- `.env` — Credentials (not committed)
- `.env.sample` — Template for `.env`

## How It Works

1. **Login Flow:**
   - GET login page at `stdportal.tdtu.edu.vn`
   - POST credentials to `/Login/SignIn`
   - Extract authentication token from response
   - Follow redirects to establish session

2. **Timetable Scraping:**
   - GET timetable page with authentication token
   - Extract ASP.NET ViewState from HTML
   - POST to select semester (if specified)
   - POST to select weekly view
   - Parse HTML table with lxml

3. **Data Processing:**
   - Extract course names, periods, days
   - Map periods to actual times
   - Format dates for Google Calendar

## Example Output
```
[1/4] Logging in to SSO...
  ✓ Logged in successfully
[2/4] Loading timetable...
  → Selecting weekly view...
[3/4] Parsing timetable...
  ✓ Found 25 events
[4/4] Exporting to CSV: timetable.csv
  ✓ Exported 25 events to timetable.csv

✓ Done!
  CSV: timetable.csv
```

## Importing to Google Calendar

1. Go to Google Calendar
2. Click Settings ⚙️ → Import & Export
3. Select the generated `timetable.csv` file
4. Choose which calendar to add events to
5. Click Import

## Customization

### Adjusting Time Slots
Edit the `TIME_SLOTS` dictionary in `main.py` to match your university's schedule.

### Different Semester
Use `--semester` flag or set `TIMETABLE_SEMESTER` in `.env`.

### Google Calendar API Sync
For automatic sync, set up OAuth2 credentials and use `google-api-python-client`. This feature can be added later.

## Error Handling
- Invalid credentials → Check username/password
- "Could not find timetable table" → Page structure may have changed
- Network errors → Check internet connection
- ViewState errors → Try clearing cookies or waiting

## Security
- Never commit `.env` file
- Use GitHub Secrets for CI/CD
- Rotate credentials periodically

## Next Steps
- [ ] Add Google Calendar API integration
- [ ] Add support for multiple semesters
- [ ] Add date range selection
- [ ] Add notification settings
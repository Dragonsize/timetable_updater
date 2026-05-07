# Timetable Updater with Playwright and Google Calendar

## Overview
Automates login to your university timetable, scrapes weekly schedule, and updates your Google Calendar. Runs daily via GitHub Actions.

## Tech Stack
- Python 3
- Playwright (browser automation)
- Google Calendar API
- GitHub Actions (automation)

## Setup
1. **Install dependencies:**
   ```sh
   pip install -r requirements.txt
   playwright install
   ```
2. **Google API Credentials:**
   - Create a Google Cloud project and enable Calendar API.
   - Download `credentials.json` and add to repo (or use GitHub Secrets).

3. **GitHub Actions:**
   - Store your school login and Google credentials as secrets.
   - Workflow runs daily.

## Files
- `main.py` — Main script
- `requirements.txt` — Dependencies
- `.github/workflows/update.yml` — GitHub Actions workflow

## Customization
- Edit `main.py` to adjust semester/week selection logic if needed.

---

**Next steps:**
- Implement Playwright login and scraping in `main.py`
- Add Google Calendar sync logic
- Add GitHub Actions workflow

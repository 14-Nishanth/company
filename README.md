# Company Data Tracker

A small password-protected web app for recording material movement and employee work in one SQLite database. It is built with the Python standard library, so no package installation is required.

## Features

- Login page for an admin user.
- Material records for `cooldust`, `cement`, `flyash`, and `dust`.
- Movement choices for `inward`, `outward-loading`, and `only loading`.
- Quantity, material rate, and automatically calculated amount.
- Employee records with date, employee/team name, number of workers, salary/worker rate, and automatically calculated total salary.
- One dashboard page that shows forms, all records, summaries, and an Excel-ready CSV download.
- SQLite database storage in `company.db` by default.

## Run locally

```bash
python3 app.py
```

Open <http://127.0.0.1:8000> and log in with the default local credentials:

- Username: `admin`
- Password: `admin123`

For a real deployment, set your own credentials and secret before starting the app:

```bash
APP_USERNAME=myuser APP_PASSWORD='change-me' SECRET_KEY='long-random-secret' python3 app.py
```

You can also choose a custom database file:

```bash
COMPANY_DB=/path/to/company.db python3 app.py
```

## Export for Excel

Use the **Download Excel CSV** button on the dashboard or open `/export.csv` after logging in. The downloaded `company-data.csv` file can be opened in Microsoft Excel, Google Sheets, or LibreOffice.

## Tests

```bash
python3 -m unittest
```

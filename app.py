#!/usr/bin/env python3
"""Small business tracking web app.

The app intentionally uses only the Python standard library so it can run in a
fresh checkout without installing packages. It provides:
- password-protected login
- SQLite persistence for material movement and employee records
- a single dashboard page that shows all records
- CSV export that opens in Excel
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import html
import os
import secrets
import sqlite3
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, date, datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from typing import Any

APP_TITLE = "Company Data Tracker"
DB_PATH = Path(os.environ.get("COMPANY_DB", "company.db"))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
SESSION_COOKIE = "company_session"

# Default local credentials. Override in production with APP_USERNAME and
# APP_PASSWORD environment variables before starting the server.
APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "admin123")
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))

MATERIAL_TYPES = ("cooldust", "cement", "flyash", "dust")
MOVEMENT_TYPES = ("inward", "outward-loading", "only loading")


@dataclass(frozen=True)
class Flash:
    kind: str
    message: str


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS material_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_date TEXT NOT NULL,
                material TEXT NOT NULL CHECK (material IN ('cooldust', 'cement', 'flyash', 'dust')),
                movement TEXT NOT NULL CHECK (movement IN ('inward', 'outward-loading', 'only loading')),
                quantity REAL NOT NULL CHECK (quantity >= 0),
                rate REAL NOT NULL CHECK (rate >= 0),
                amount REAL NOT NULL CHECK (amount >= 0),
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS employee_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_date TEXT NOT NULL,
                employee_name TEXT NOT NULL,
                workers INTEGER NOT NULL CHECK (workers >= 0),
                salary REAL NOT NULL CHECK (salary >= 0),
                total_salary REAL NOT NULL CHECK (total_salary >= 0),
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )


def parse_date(value: str, field: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid date") from exc


def parse_float(value: str, field: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number") from exc
    if number < 0:
        raise ValueError(f"{field} cannot be negative")
    return number


def parse_int(value: str, field: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a whole number") from exc
    if number < 0:
        raise ValueError(f"{field} cannot be negative")
    return number


def add_material_entry(form: dict[str, str], db_path: Path | str = DB_PATH) -> None:
    entry_date = parse_date(form.get("entry_date", ""), "Material date")
    material = form.get("material", "")
    movement = form.get("movement", "")
    if material not in MATERIAL_TYPES:
        raise ValueError("Select a valid material")
    if movement not in MOVEMENT_TYPES:
        raise ValueError("Select a valid movement")
    quantity = parse_float(form.get("quantity", ""), "Quantity")
    rate = parse_float(form.get("rate", ""), "Material rate")
    amount = round(quantity * rate, 2)
    note = form.get("note", "").strip()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO material_entries
                (entry_date, material, movement, quantity, rate, amount, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entry_date, material, movement, quantity, rate, amount, note, datetime.now(UTC).isoformat()),
        )


def add_employee_entry(form: dict[str, str], db_path: Path | str = DB_PATH) -> None:
    work_date = parse_date(form.get("work_date", ""), "Employee date")
    employee_name = form.get("employee_name", "").strip()
    if not employee_name:
        raise ValueError("Employee name is required")
    workers = parse_int(form.get("workers", ""), "Number of workers")
    salary = parse_float(form.get("salary", ""), "Employee salary/rate")
    total_salary = round(workers * salary, 2)
    note = form.get("employee_note", "").strip()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO employee_entries
                (work_date, employee_name, workers, salary, total_salary, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (work_date, employee_name, workers, salary, total_salary, note, datetime.now(UTC).isoformat()),
        )


def load_dashboard_data(db_path: Path | str = DB_PATH) -> dict[str, Any]:
    with get_connection(db_path) as conn:
        materials = conn.execute(
            "SELECT * FROM material_entries ORDER BY entry_date DESC, id DESC"
        ).fetchall()
        employees = conn.execute(
            "SELECT * FROM employee_entries ORDER BY work_date DESC, id DESC"
        ).fetchall()
        material_summary = conn.execute(
            """
            SELECT material, movement, SUM(quantity) AS total_quantity, SUM(amount) AS total_amount
            FROM material_entries
            GROUP BY material, movement
            ORDER BY material, movement
            """
        ).fetchall()
        employee_summary = conn.execute(
            """
            SELECT work_date, SUM(workers) AS total_workers, SUM(total_salary) AS total_salary
            FROM employee_entries
            GROUP BY work_date
            ORDER BY work_date DESC
            """
        ).fetchall()
    return {
        "materials": materials,
        "employees": employees,
        "material_summary": material_summary,
        "employee_summary": employee_summary,
    }


def money(value: Any) -> str:
    return f"{float(value):,.2f}"


def number(value: Any) -> str:
    return f"{float(value):,.2f}".rstrip("0").rstrip(".")


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def signed_value(value: str) -> str:
    return hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_session(username: str) -> str:
    token = f"{username}:{signed_value(username)}"
    return token


def is_valid_session(token: str | None) -> bool:
    if not token or ":" not in token:
        return False
    username, signature = token.split(":", 1)
    return username == APP_USERNAME and hmac.compare_digest(signature, signed_value(username))


def parse_cookies(header: str | None) -> SimpleCookie[str]:
    cookie = SimpleCookie()
    if header:
        cookie.load(header)
    return cookie


def render_page(content: str, authenticated: bool, flash: Flash | None = None) -> bytes:
    nav = ""
    if authenticated:
        nav = '<a href="/">Dashboard</a><a href="/export.csv">Export Excel CSV</a><a href="/logout">Logout</a>'
    flash_html = f'<div class="flash {esc(flash.kind)}">{esc(flash.message)}</div>' if flash else ""
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_TITLE}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #eef2f7; color: #172033; }}
    header {{ background: linear-gradient(135deg, #1f4e79, #28a4a8); color: white; padding: 24px clamp(18px, 4vw, 56px); }}
    header h1 {{ margin: 0 0 8px; font-size: clamp(28px, 4vw, 42px); }}
    header p {{ margin: 0; opacity: .9; }}
    nav {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 18px; }}
    nav a, .button {{ background: #ffffff; border: 0; border-radius: 10px; color: #1f4e79; cursor: pointer; display: inline-block; font-weight: 700; padding: 10px 14px; text-decoration: none; }}
    main {{ padding: 24px clamp(18px, 4vw, 56px) 48px; }}
    .grid {{ display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }}
    .card {{ background: white; border-radius: 18px; box-shadow: 0 10px 30px rgba(31, 78, 121, .10); padding: 22px; }}
    .card h2 {{ margin-top: 0; color: #1f4e79; }}
    label {{ display: block; font-weight: 700; margin: 12px 0 6px; }}
    input, select, textarea {{ border: 1px solid #cbd5e1; border-radius: 10px; box-sizing: border-box; font: inherit; padding: 10px 12px; width: 100%; }}
    textarea {{ min-height: 76px; resize: vertical; }}
    button.primary {{ background: #1f4e79; border: 0; border-radius: 10px; color: white; cursor: pointer; font-weight: 800; margin-top: 16px; padding: 12px 18px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #e2e8f0; padding: 10px; text-align: left; vertical-align: top; }}
    th {{ background: #f8fafc; color: #334155; font-size: 13px; text-transform: uppercase; }}
    .table-wrap {{ overflow-x: auto; }}
    .flash {{ border-radius: 12px; font-weight: 700; margin-bottom: 18px; padding: 14px 16px; }}
    .success {{ background: #dcfce7; color: #166534; }}
    .error {{ background: #fee2e2; color: #991b1b; }}
    .muted {{ color: #64748b; }}
  </style>
</head>
<body>
  <header>
    <h1>{APP_TITLE}</h1>
    <p>Track material inward/outward loading, employee workers, rates, salary, and Excel-ready reports.</p>
    <nav>{nav}</nav>
  </header>
  <main>{flash_html}{content}</main>
</body>
</html>"""
    return page.encode("utf-8")


def login_page(flash: Flash | None = None) -> bytes:
    content = """
    <section class="card" style="max-width: 460px; margin: 24px auto;">
      <h2>Login</h2>
      <p class="muted">Use the configured admin username and password to open the data dashboard.</p>
      <form method="post" action="/login">
        <label for="username">Username</label>
        <input id="username" name="username" autocomplete="username" required>
        <label for="password">Password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
        <button class="primary" type="submit">Login</button>
      </form>
    </section>
    """
    return render_page(content, authenticated=False, flash=flash)


def options(values: tuple[str, ...]) -> str:
    return "".join(f'<option value="{esc(value)}">{esc(value.title())}</option>' for value in values)


def dashboard_page(flash: Flash | None = None) -> bytes:
    data = load_dashboard_data()
    today = date.today().isoformat()
    material_rows = "".join(
        f"""
        <tr><td>{esc(row['entry_date'])}</td><td>{esc(row['material'])}</td><td>{esc(row['movement'])}</td>
        <td>{number(row['quantity'])}</td><td>{money(row['rate'])}</td><td>{money(row['amount'])}</td><td>{esc(row['note'])}</td></tr>
        """
        for row in data["materials"]
    ) or '<tr><td colspan="7" class="muted">No material entries yet.</td></tr>'
    employee_rows = "".join(
        f"""
        <tr><td>{esc(row['work_date'])}</td><td>{esc(row['employee_name'])}</td><td>{row['workers']}</td>
        <td>{money(row['salary'])}</td><td>{money(row['total_salary'])}</td><td>{esc(row['note'])}</td></tr>
        """
        for row in data["employees"]
    ) or '<tr><td colspan="6" class="muted">No employee entries yet.</td></tr>'
    material_summary_rows = "".join(
        f"<tr><td>{esc(row['material'])}</td><td>{esc(row['movement'])}</td><td>{number(row['total_quantity'])}</td><td>{money(row['total_amount'])}</td></tr>"
        for row in data["material_summary"]
    ) or '<tr><td colspan="4" class="muted">No summary yet.</td></tr>'
    employee_summary_rows = "".join(
        f"<tr><td>{esc(row['work_date'])}</td><td>{row['total_workers']}</td><td>{money(row['total_salary'])}</td></tr>"
        for row in data["employee_summary"]
    ) or '<tr><td colspan="3" class="muted">No summary yet.</td></tr>'

    content = f"""
    <div class="grid">
      <section class="card">
        <h2>Add Material</h2>
        <form method="post" action="/materials">
          <label for="entry_date">Date</label>
          <input id="entry_date" name="entry_date" type="date" value="{today}" required>
          <label for="material">Material</label>
          <select id="material" name="material" required>{options(MATERIAL_TYPES)}</select>
          <label for="movement">Movement</label>
          <select id="movement" name="movement" required>{options(MOVEMENT_TYPES)}</select>
          <label for="quantity">Quantity / Loading</label>
          <input id="quantity" name="quantity" type="number" min="0" step="0.01" required>
          <label for="rate">Rate for Material</label>
          <input id="rate" name="rate" type="number" min="0" step="0.01" required>
          <label for="note">Note</label>
          <textarea id="note" name="note" placeholder="Vehicle no., party name, or remarks"></textarea>
          <button class="primary" type="submit">Save Material</button>
        </form>
      </section>

      <section class="card">
        <h2>Add Employee Work</h2>
        <form method="post" action="/employees">
          <label for="work_date">Date</label>
          <input id="work_date" name="work_date" type="date" value="{today}" required>
          <label for="employee_name">Employee / Team Name</label>
          <input id="employee_name" name="employee_name" required>
          <label for="workers">No. of Workers</label>
          <input id="workers" name="workers" type="number" min="0" step="1" required>
          <label for="salary">Salary / Worker Rate</label>
          <input id="salary" name="salary" type="number" min="0" step="0.01" required>
          <label for="employee_note">Note</label>
          <textarea id="employee_note" name="employee_note" placeholder="Shift, job details, or remarks"></textarea>
          <button class="primary" type="submit">Save Employee</button>
        </form>
      </section>
    </div>

    <section class="card" style="margin-top: 20px;">
      <h2>Excel Data - All Records on One Page</h2>
      <p><a class="button" href="/export.csv">Download Excel CSV</a></p>
      <div class="grid">
        <div>
          <h3>Material Summary</h3>
          <div class="table-wrap"><table><thead><tr><th>Material</th><th>Movement</th><th>Total Qty</th><th>Total Amount</th></tr></thead><tbody>{material_summary_rows}</tbody></table></div>
        </div>
        <div>
          <h3>Employee Summary by Date</h3>
          <div class="table-wrap"><table><thead><tr><th>Date</th><th>Total Workers</th><th>Total Salary</th></tr></thead><tbody>{employee_summary_rows}</tbody></table></div>
        </div>
      </div>
      <h3>Material Entries</h3>
      <div class="table-wrap"><table><thead><tr><th>Date</th><th>Material</th><th>Movement</th><th>Quantity</th><th>Rate</th><th>Amount</th><th>Note</th></tr></thead><tbody>{material_rows}</tbody></table></div>
      <h3>Employee Entries</h3>
      <div class="table-wrap"><table><thead><tr><th>Date</th><th>Employee</th><th>Workers</th><th>Salary/Rate</th><th>Total Salary</th><th>Note</th></tr></thead><tbody>{employee_rows}</tbody></table></div>
    </section>
    """
    return render_page(content, authenticated=True, flash=flash)


def export_csv(db_path: Path | str = DB_PATH) -> bytes:
    data = load_dashboard_data(db_path)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Material Entries"])
    writer.writerow(["Date", "Material", "Movement", "Quantity", "Rate", "Amount", "Note"])
    for row in data["materials"]:
        writer.writerow([row["entry_date"], row["material"], row["movement"], row["quantity"], row["rate"], row["amount"], row["note"]])
    writer.writerow([])
    writer.writerow(["Employee Entries"])
    writer.writerow(["Date", "Employee/Team", "No. of Workers", "Salary/Worker Rate", "Total Salary", "Note"])
    for row in data["employees"]:
        writer.writerow([row["work_date"], row["employee_name"], row["workers"], row["salary"], row["total_salary"], row["note"]])
    writer.writerow([])
    writer.writerow(["Material Summary"])
    writer.writerow(["Material", "Movement", "Total Quantity", "Total Amount"])
    for row in data["material_summary"]:
        writer.writerow([row["material"], row["movement"], row["total_quantity"], row["total_amount"]])
    writer.writerow([])
    writer.writerow(["Employee Summary By Date"])
    writer.writerow(["Date", "Total Workers", "Total Salary"])
    for row in data["employee_summary"]:
        writer.writerow([row["work_date"], row["total_workers"], row["total_salary"]])
    return output.getvalue().encode("utf-8-sig")


class CompanyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        authenticated = self.authenticated
        path = urllib.parse.urlparse(self.path).path
        if path == "/login":
            self.respond(login_page())
        elif path == "/logout":
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; Max-Age=0; SameSite=Lax")
            self.end_headers()
        elif not authenticated:
            self.redirect("/login")
        elif path == "/":
            self.respond(dashboard_page())
        elif path == "/export.csv":
            payload = export_csv()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="company-data.csv"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        path = urllib.parse.urlparse(self.path).path
        if path == "/login":
            form = self.read_form()
            if form.get("username") == APP_USERNAME and form.get("password") == APP_PASSWORD:
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"{SESSION_COOKIE}={make_session(APP_USERNAME)}; Path=/; HttpOnly; SameSite=Lax")
                self.end_headers()
            else:
                self.respond(login_page(Flash("error", "Invalid username or password")), status=HTTPStatus.UNAUTHORIZED)
            return

        if not self.authenticated:
            self.redirect("/login")
            return

        try:
            form = self.read_form()
            if path == "/materials":
                add_material_entry(form)
                self.respond(dashboard_page(Flash("success", "Material entry saved")))
            elif path == "/employees":
                add_employee_entry(form)
                self.respond(dashboard_page(Flash("success", "Employee entry saved")))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.respond(dashboard_page(Flash("error", str(exc))), status=HTTPStatus.BAD_REQUEST)

    @property
    def authenticated(self) -> bool:
        cookies = parse_cookies(self.headers.get("Cookie"))
        session = cookies.get(SESSION_COOKIE)
        return is_valid_session(session.value if session else None)

    def read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        parsed = urllib.parse.parse_qs(body, keep_blank_values=True)
        return {key: values[0] for key, values in parsed.items()}

    def respond(self, payload: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server API
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), format % args))


def main() -> None:
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), CompanyHandler)
    print(f"Serving {APP_TITLE} at http://{HOST}:{PORT}")
    print(f"Default login: {APP_USERNAME} / {'*' * len(APP_PASSWORD)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

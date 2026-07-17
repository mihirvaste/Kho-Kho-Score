import hashlib
import html
import json
import secrets
import sqlite3
import re
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = "0.0.0.0"
PORT = 8000
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "login.db"
TEMPLATE_DIR = BASE_DIR / "templates"
SESSIONS = set()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)


def setup_database():
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
            "password_hash BLOB NOT NULL, salt BLOB NOT NULL)"
        )
        # tournaments table (already seeded elsewhere in code path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS tournaments ("
            "tournament_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "start_date TEXT NOT NULL, end_date TEXT NOT NULL, "
            "status TEXT NOT NULL, tournament_for TEXT NOT NULL)"
        )

        # tech teams table
        db.execute(
            "CREATE TABLE IF NOT EXISTS techteams ("
            "tt_id TEXT PRIMARY KEY, name TEXT NOT NULL, password_hash TEXT NOT NULL, "
            "age INTEGER, gender TEXT, kkfi_number TEXT UNIQUE, phone TEXT, email TEXT UNIQUE, blocked INTEGER DEFAULT 0)"
        )

        db.execute(
            "CREATE TABLE IF NOT EXISTS teams ("
            "team_id TEXT PRIMARY KEY, tournament_id TEXT, team_name TEXT NOT NULL, address TEXT, manager_id TEXT, blocked INTEGER DEFAULT 0)"
        )

        db.execute(
            "CREATE TABLE IF NOT EXISTS umpires ("
            "umpire_id TEXT PRIMARY KEY, name TEXT NOT NULL, kkfi_number TEXT UNIQUE, aadhar_photo_url TEXT, "
            "gender TEXT, age INTEGER, phone TEXT, email TEXT UNIQUE, password_hash TEXT NOT NULL, blocked INTEGER DEFAULT 0)"
        )

        # invites table for registration links and reset tokens
        db.execute(
            "CREATE TABLE IF NOT EXISTS invites (token TEXT PRIMARY KEY, expires_at TEXT NOT NULL, used INTEGER DEFAULT 0, role TEXT NOT NULL)"
        )

        if not db.execute("SELECT 1 FROM users WHERE username = ?", ("admin",)).fetchone():
            salt = secrets.token_bytes(16)
            db.execute(
                "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
                ("admin", password_hash("admin", salt), salt),
            )

        # seed some techteam dummy rows when empty
        if not db.execute("SELECT 1 FROM techteams LIMIT 1").fetchone():
            tech_dummy = [
                ("TT0001", "Ravi Kumar", hashlib.sha256(b"password1").hexdigest(), 32, "M", "KKFI001", "9876543210", "ravi@example.com", 0),
                ("TT0002", "Sneha Rao", hashlib.sha256(b"password2").hexdigest(), 28, "F", "KKFI002", "9876501234", "sneha@example.com", 0),
                ("TT0003", "Amit Shah", hashlib.sha256(b"password3").hexdigest(), 35, "M", "KKFI003", "9876512345", "amit@example.com", 1),
                ("TT0004", "Meera Joshi", hashlib.sha256(b"password4").hexdigest(), 30, "F", "KKFI004", "9876523456", "meera@example.com", 0),
                ("TT0005", "Sajid Khan", hashlib.sha256(b"password5").hexdigest(), 29, "M", "KKFI005", "9876534567", "sajid@example.com", 0),
                ("TT0006", "Pooja Nair", hashlib.sha256(b"password6").hexdigest(), 27, "F", "KKFI006", "9876545678", "pooja@example.com", 0),
            ]
            db.executemany(
                "INSERT INTO techteams (tt_id, name, password_hash, age, gender, kkfi_number, phone, email, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tech_dummy,
            )

        if not db.execute("SELECT 1 FROM umpires LIMIT 1").fetchone():
            umpire_dummy = [
                ("UM0001", "Anil Sharma", "UKFI001", "https://example.com/aadhar1.jpg", "M", 34, "9876500001", "anil@example.com", hashlib.sha256(b"umpirepass1").hexdigest(), 0),
                ("UM0002", "Geeta Singh", "UKFI002", "https://example.com/aadhar2.jpg", "F", 29, "9876500002", "geeta@example.com", hashlib.sha256(b"umpirepass2").hexdigest(), 0),
                ("UM0003", "Rajesh Kumar", "UKFI003", "https://example.com/aadhar3.jpg", "M", 42, "9876500003", "rajesh@example.com", hashlib.sha256(b"umpirepass3").hexdigest(), 1),
            ]
            db.executemany(
                "INSERT INTO umpires (umpire_id, name, kkfi_number, aadhar_photo_url, gender, age, phone, email, password_hash, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                umpire_dummy,
            )


def valid_login(username, password):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT password_hash, salt FROM users WHERE username = ?", (username,)
        ).fetchone()
    return bool(row and secrets.compare_digest(row[0], password_hash(password, row[1])))


def load_template(name, **context):
    template_path = TEMPLATE_DIR / name
    content = template_path.read_text(encoding="utf-8")
    # process include directives recursively: <!-- include: path/to/file -->
    pattern = re.compile(r"<!--\s*include:\s*([^\s]+)\s*-->")

    def expand_includes(text):
        def _inc(match):
            inc = match.group(1).strip()
            inc_path = TEMPLATE_DIR / inc
            try:
                inc_text = inc_path.read_text(encoding="utf-8")
                return expand_includes(inc_text)
            except Exception:
                return ""

        return pattern.sub(_inc, text)

    content = expand_includes(content)

    for key, value in context.items():
        content = content.replace(f"{{{{ {key} }}}}", html.escape(str(value)))
        content = content.replace(f"{{{{{key}}}}}", html.escape(str(value)))
    return content.encode("utf-8")


def send_json(handler, data, status=200):
    body = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def parse_json_body(handler, length):
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw) if raw else {}


def build_filter(where_clause, params, status=None, q=None):
    if status:
        if status == "completed":
            where_clause.append("status IN ('cancel', 'reschedule')")
        else:
            where_clause.append("status = ?")
            params.append(status)
    if q:
        where_clause.append("(LOWER(tournament_id) LIKE ? OR LOWER(name) LIKE ?)")
        query_value = f"%{q.lower()}%"
        params.extend([query_value, query_value])


def get_tournaments(status=None, q=None, limit=None, offset=None):
    where_clause = []
    params = []
    build_filter(where_clause, params, status, q)
    query = "SELECT tournament_id, name, start_date, end_date, status, tournament_for FROM tournaments"
    if where_clause:
        query += " WHERE " + " AND ".join(where_clause)
    query += " ORDER BY start_date DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
        if offset is not None:
            query += " OFFSET ?"
            params.append(offset)
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(query, params).fetchall()
    return [
        {
            "tournament_id": row[0],
            "name": row[1],
            "start_date": row[2],
            "end_date": row[3],
            "status": row[4],
            "tournament_for": row[5],
        }
        for row in rows
    ]


def count_tournaments(status=None, q=None):
    where_clause = []
    params = []
    build_filter(where_clause, params, status, q)
    query = "SELECT COUNT(*) FROM tournaments"
    if where_clause:
        query += " WHERE " + " AND ".join(where_clause)
    with sqlite3.connect(DB_PATH) as db:
        return db.execute(query, params).fetchone()[0]


def get_tournament_stats():
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(
            "SELECT status FROM tournaments"
        ).fetchall()
    total = len(rows)
    active = sum(1 for row in rows if row[0] == "active")
    upcoming = sum(1 for row in rows if row[0] == "UPCOMING")
    completed = sum(1 for row in rows if row[0] in {"cancel", "reschedule"})
    return {"total": total, "active": active, "upcoming": upcoming, "completed": completed}


def get_tournament_by_id(tournament_id):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT tournament_id, name, start_date, end_date, status, tournament_for FROM tournaments WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "tournament_id": row[0],
        "name": row[1],
        "start_date": row[2],
        "end_date": row[3],
        "status": row[4],
        "tournament_for": row[5],
    }


### Tech Team helpers and API

def get_techteam_stats():
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute("SELECT blocked FROM techteams").fetchall()
    total = len(rows)
    blocked = sum(1 for r in rows if r[0])
    return {"total": total, "blocked": blocked}


def build_team_filter(where_clause, params, blocked=None, q=None):
    if blocked is not None:
        where_clause.append("blocked = ?")
        params.append(1 if blocked else 0)
    if q:
        where_clause.append("(LOWER(team_id) LIKE ? OR LOWER(team_name) LIKE ? OR LOWER(address) LIKE ?)")
        v = f"%{q.lower()}%"
        params.extend([v, v, v])


def get_teams(blocked=None, q=None, limit=None, offset=None):
    where_clause = []
    params = []
    build_team_filter(where_clause, params, blocked, q)
    query = "SELECT t.team_id, t.team_name, t.tournament_id, tr.name, t.address, t.manager_id, t.blocked FROM teams t LEFT JOIN tournaments tr ON t.tournament_id = tr.tournament_id"
    if where_clause:
        query += " WHERE " + " AND ".join(where_clause)
    query += " ORDER BY t.team_name ASC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
        if offset is not None:
            query += " OFFSET ?"
            params.append(offset)
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(query, params).fetchall()
    return [
        {
            "team_id": r[0],
            "team_name": r[1],
            "tournament_id": r[2],
            "tournament_name": r[3],
            "address": r[4],
            "manager_id": r[5],
            "blocked": bool(r[6]),
        }
        for r in rows
    ]


def count_teams(blocked=None, q=None):
    where_clause = []
    params = []
    build_team_filter(where_clause, params, blocked, q)
    query = "SELECT COUNT(*) FROM teams"
    if where_clause:
        query += " WHERE " + " AND ".join(where_clause)
    with sqlite3.connect(DB_PATH) as db:
        return db.execute(query, params).fetchone()[0]


def get_team_by_id(team_id):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT team_id, team_name, tournament_id, address, manager_id, blocked FROM teams WHERE team_id = ?",
            (team_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "team_id": row[0],
        "team_name": row[1],
        "tournament_id": row[2],
        "address": row[3],
        "manager_id": row[4],
        "blocked": bool(row[5]),
    }


def create_team(data):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("SELECT MAX(CAST(SUBSTR(team_id, 3) AS INTEGER)) FROM teams").fetchone()
        max_index = row[0] if row and row[0] is not None else 0
        team_id = f"TT{max_index + 1:04d}"
        db.execute(
            "INSERT INTO teams (team_id, team_name, tournament_id, address, manager_id, blocked) VALUES (?, ?, ?, ?, ?, 0)",
            (team_id, data["team_name"], data.get("tournament_id"), data.get("address"), data.get("manager_id")),
        )
    return get_team_by_id(team_id)


def update_team(team_id, data):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "UPDATE teams SET team_name = ?, tournament_id = ?, address = ?, manager_id = ? WHERE team_id = ?",
            (data["team_name"], data.get("tournament_id"), data.get("address"), data.get("manager_id"), team_id),
        )
    return get_team_by_id(team_id)


def delete_team(team_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("DELETE FROM teams WHERE team_id = ?", (team_id,))


def set_team_block_status(team_id, blocked=True):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("UPDATE teams SET blocked = ? WHERE team_id = ?", (1 if blocked else 0, team_id))


def get_team_stats():
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute("SELECT blocked FROM teams").fetchall()
    total = len(rows)
    blocked = sum(1 for r in rows if r[0])
    return {"total": total, "blocked": blocked}


def build_tt_filter(where_clause, params, blocked=None, q=None):
    if blocked is not None:
        where_clause.append("blocked = ?")
        params.append(1 if blocked else 0)
    if q:
        where_clause.append("(LOWER(tt_id) LIKE ? OR LOWER(name) LIKE ? OR LOWER(kkfi_number) LIKE ?)")
        v = f"%{q.lower()}%"
        params.extend([v, v, v])


def get_techteams(blocked=None, q=None, limit=None, offset=None):
    where_clause = []
    params = []
    build_tt_filter(where_clause, params, blocked, q)
    query = "SELECT tt_id, name, age, gender, kkfi_number, phone, email, blocked FROM techteams"
    if where_clause:
        query += " WHERE " + " AND ".join(where_clause)
    query += " ORDER BY name ASC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
        if offset is not None:
            query += " OFFSET ?"
            params.append(offset)
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(query, params).fetchall()
    return [
        {
            "tt_id": r[0],
            "name": r[1],
            "age": r[2],
            "gender": r[3],
            "kkfi_number": r[4],
            "phone": r[5],
            "email": r[6],
            "blocked": bool(r[7]),
        }
        for r in rows
    ]


def count_techteams(blocked=None, q=None):
    where_clause = []
    params = []
    build_tt_filter(where_clause, params, blocked, q)
    query = "SELECT COUNT(*) FROM techteams"
    if where_clause:
        query += " WHERE " + " AND ".join(where_clause)
    with sqlite3.connect(DB_PATH) as db:
        return db.execute(query, params).fetchone()[0]


def get_techteam_by_id(tt_id):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT tt_id, name, age, gender, kkfi_number, phone, email, blocked FROM techteams WHERE tt_id = ?",
            (tt_id,),
        ).fetchone()
    if not row:
        return None
    return {"tt_id": row[0], "name": row[1], "age": row[2], "gender": row[3], "kkfi_number": row[4], "phone": row[5], "email": row[6], "blocked": bool(row[7])}


def get_umpire_stats():
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute("SELECT blocked FROM umpires").fetchall()
    total = len(rows)
    blocked = sum(1 for r in rows if r[0])
    return {"total": total, "blocked": blocked}


def build_umpire_filter(where_clause, params, blocked=None, q=None):
    if blocked is not None:
        where_clause.append("blocked = ?")
        params.append(1 if blocked else 0)
    if q:
        where_clause.append("(LOWER(umpire_id) LIKE ? OR LOWER(name) LIKE ? OR LOWER(kkfi_number) LIKE ?)")
        v = f"%{q.lower()}%"
        params.extend([v, v, v])


def get_umpires(blocked=None, q=None, limit=None, offset=None):
    where_clause = []
    params = []
    build_umpire_filter(where_clause, params, blocked, q)
    query = "SELECT umpire_id, name, gender, age, kkfi_number, phone, email, aadhar_photo_url, blocked FROM umpires"
    if where_clause:
        query += " WHERE " + " AND ".join(where_clause)
    query += " ORDER BY name ASC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
        if offset is not None:
            query += " OFFSET ?"
            params.append(offset)
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(query, params).fetchall()
    return [
        {
            "umpire_id": r[0],
            "name": r[1],
            "gender": r[2],
            "age": r[3],
            "kkfi_number": r[4],
            "phone": r[5],
            "email": r[6],
            "aadhar_photo_url": r[7],
            "blocked": bool(r[8]),
        }
        for r in rows
    ]


def count_umpires(blocked=None, q=None):
    where_clause = []
    params = []
    build_umpire_filter(where_clause, params, blocked, q)
    query = "SELECT COUNT(*) FROM umpires"
    if where_clause:
        query += " WHERE " + " AND ".join(where_clause)
    with sqlite3.connect(DB_PATH) as db:
        return db.execute(query, params).fetchone()[0]


def get_umpire_by_id(umpire_id):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT umpire_id, name, gender, age, kkfi_number, phone, email, aadhar_photo_url, blocked FROM umpires WHERE umpire_id = ?",
            (umpire_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "umpire_id": row[0],
        "name": row[1],
        "gender": row[2],
        "age": row[3],
        "kkfi_number": row[4],
        "phone": row[5],
        "email": row[6],
        "aadhar_photo_url": row[7],
        "blocked": bool(row[8]),
    }


def create_umpire(data):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("SELECT MAX(CAST(SUBSTR(umpire_id, 3) AS INTEGER)) FROM umpires").fetchone()
        max_index = row[0] if row and row[0] is not None else 0
        umpire_id = f"UM{max_index + 1:04d}"
        pwd_hash = hashlib.sha256(data["password"].encode()).hexdigest()
        db.execute(
            "INSERT INTO umpires (umpire_id, name, kkfi_number, aadhar_photo_url, gender, age, phone, email, password_hash, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (umpire_id, data["name"], data["kkfi_number"], data.get("aadhar_photo_url"), data.get("gender"), data.get("age"), data.get("phone"), data.get("email"), pwd_hash),
        )
    return get_umpire_by_id(umpire_id)


def update_umpire(umpire_id, data):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "UPDATE umpires SET name = ?, kkfi_number = ?, aadhar_photo_url = ?, gender = ?, age = ?, phone = ?, email = ? WHERE umpire_id = ?",
            (data["name"], data.get("kkfi_number"), data.get("aadhar_photo_url"), data.get("gender"), data.get("age"), data.get("phone"), data.get("email"), umpire_id),
        )
    return get_umpire_by_id(umpire_id)


def delete_umpire(umpire_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("DELETE FROM umpires WHERE umpire_id = ?", (umpire_id,))


def set_umpire_block_status(umpire_id, blocked=True):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("UPDATE umpires SET blocked = ? WHERE umpire_id = ?", (1 if blocked else 0, umpire_id))


def update_techteam(tt_id, data):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "UPDATE techteams SET name = ?, age = ?, gender = ?, phone = ?, email = ? WHERE tt_id = ?",
            (data["name"], data.get("age"), data.get("gender"), data.get("phone"), data.get("email"), tt_id),
        )
    return get_techteam_by_id(tt_id)


def delete_techteam(tt_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("DELETE FROM techteams WHERE tt_id = ?", (tt_id,))


def set_block_status(tt_id, blocked=True):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("UPDATE techteams SET blocked = ? WHERE tt_id = ?", (1 if blocked else 0, tt_id))


def create_invite(expires_seconds, role="techteam"):
    import datetime

    token = secrets.token_urlsafe(24)
    expires_at = (datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_seconds)).isoformat()
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT INTO invites (token, expires_at, used, role) VALUES (?, ?, 0, ?)", (token, expires_at, role))
    return token


def get_invite(token):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("SELECT token, expires_at, used, role FROM invites WHERE token = ?", (token,)).fetchone()
    return row



def create_tournament(data):
    with sqlite3.connect(DB_PATH) as db:
        count = db.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0]
        tournament_id = f"KT{count + 1:04d}"
        db.execute(
            "INSERT INTO tournaments (tournament_id, name, start_date, end_date, status, tournament_for) VALUES (?, ?, ?, ?, ?, ?)",
            (
                tournament_id,
                data["name"],
                data["start_date"],
                data["end_date"],
                data["status"],
                data["tournament_for"],
            ),
        )
    return get_tournament_by_id(tournament_id)


def update_tournament(tournament_id, data):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "UPDATE tournaments SET name = ?, start_date = ?, end_date = ?, status = ?, tournament_for = ? WHERE tournament_id = ?",
            (
                data["name"],
                data["start_date"],
                data["end_date"],
                data["status"],
                data["tournament_for"],
                tournament_id,
            ),
        )
    return get_tournament_by_id(tournament_id)


def delete_tournament(tournament_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("DELETE FROM tournaments WHERE tournament_id = ?", (tournament_id,))


class Handler(BaseHTTPRequestHandler):
    def session_token(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie"))
        morsel = jar.get("session")
        return morsel.value if morsel else None

    def send_html(self, content, status=200, cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(content)

    def redirect(self, location, cookie=None):
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            if self.session_token() in SESSIONS:
                self.redirect("/home")
                return
            self.send_html(load_template("admin_login.html", title="Admin Loginpage", message=""))
            return

        if path == "/home" and self.session_token() in SESSIONS:
            self.send_html(load_template("admin_home.html", title="Admin Homepage"))
            return

        if path == "/techteam" and self.session_token() in SESSIONS:
            self.send_html(load_template("admin_techteam.html", title="Tech Team"))
            return
        if path == "/umpires" and self.session_token() in SESSIONS:
            self.send_html(load_template("admin_umpires.html", title="Umpires"))
            return
        if path == "/teams" and self.session_token() in SESSIONS:
            self.send_html(load_template("admin_teams.html", title="Teams"))
            return
        if path == "/managers" and self.session_token() in SESSIONS:
            self.send_html(load_template("admin_managers.html", title="Managers"))
            return
        if path == "/players" and self.session_token() in SESSIONS:
            self.send_html(load_template("admin_players.html", title="Players"))
            return

        if path == "/api/tournaments" and self.session_token() in SESSIONS:
            params = parse_qs(parsed.query)
            status = params.get("status", [None])[0]
            q = params.get("q", [None])[0]
            page = int(params.get("page", [1])[0])
            page_size = int(params.get("page_size", [5])[0])
            tournaments = get_tournaments(status=status, q=q, limit=page_size, offset=(page - 1) * page_size)
            total = count_tournaments(status=status, q=q)
            send_json(self, {"tournaments": tournaments, "total": total})
            return

        if path == "/api/techteams" and self.session_token() in SESSIONS:
            params = parse_qs(parsed.query)
            blocked_raw = params.get("blocked", [None])[0]
            blocked = None
            if blocked_raw is not None and blocked_raw != "":
                # interpret truthy values
                blocked = True if blocked_raw in ("1", "true", "True", "yes") else False
            q = params.get("q", [None])[0]
            page = int(params.get("page", [1])[0])
            page_size = int(params.get("page_size", [6])[0])
            teams = get_techteams(blocked=blocked, q=q, limit=page_size, offset=(page - 1) * page_size)
            total = count_techteams(blocked=blocked, q=q)
            send_json(self, {"techteams": teams, "total": total})
            return

        if path == "/api/teams" and self.session_token() in SESSIONS:
            params = parse_qs(parsed.query)
            blocked_raw = params.get("blocked", [None])[0]
            blocked = None
            if blocked_raw is not None and blocked_raw != "":
                blocked = True if blocked_raw in ("1", "true", "True", "yes") else False
            q = params.get("q", [None])[0]
            page = int(params.get("page", [1])[0])
            page_size = int(params.get("page_size", [6])[0])
            teams = get_teams(blocked=blocked, q=q, limit=page_size, offset=(page - 1) * page_size)
            total = count_teams(blocked=blocked, q=q)
            send_json(self, {"teams": teams, "total": total})
            return

        if path == "/api/umpires" and self.session_token() in SESSIONS:
            params = parse_qs(parsed.query)
            blocked_raw = params.get("blocked", [None])[0]
            blocked = None
            if blocked_raw is not None and blocked_raw != "":
                blocked = True if blocked_raw in ("1", "true", "True", "yes") else False
            q = params.get("q", [None])[0]
            page = int(params.get("page", [1])[0])
            page_size = int(params.get("page_size", [6])[0])
            umpires = get_umpires(blocked=blocked, q=q, limit=page_size, offset=(page - 1) * page_size)
            total = count_umpires(blocked=blocked, q=q)
            send_json(self, {"umpires": umpires, "total": total})
            return

        if path == "/api/tournaments/stats" and self.session_token() in SESSIONS:
            send_json(self, get_tournament_stats())
            return

        if path == "/api/techteams/stats" and self.session_token() in SESSIONS:
            send_json(self, get_techteam_stats())
            return

        if path == "/api/teams/stats" and self.session_token() in SESSIONS:
            send_json(self, get_team_stats())
            return

        if path == "/api/umpires/stats" and self.session_token() in SESSIONS:
            send_json(self, get_umpire_stats())
            return

        if path.startswith("/api/tournaments/") and self.session_token() in SESSIONS:
            tournament_id = path.rsplit("/", 1)[-1]
            tournament = get_tournament_by_id(tournament_id)
            if tournament:
                send_json(self, tournament)
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return

        if path.startswith("/api/techteams/") and self.session_token() in SESSIONS:
            tt_id = path.rsplit("/", 1)[-1]
            team = get_techteam_by_id(tt_id)
            if team:
                send_json(self, team)
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return
        
        if path.startswith("/api/teams/") and self.session_token() in SESSIONS:
            team_id = path.rsplit("/", 1)[-1]
            team = get_team_by_id(team_id)
            if team:
                send_json(self, team)
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return
        if path.startswith("/api/umpires/") and self.session_token() in SESSIONS:
            umpire_id = path.rsplit("/", 1)[-1]
            umpire = get_umpire_by_id(umpire_id)
            if umpire:
                send_json(self, umpire)
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return

        # serve register page POST is handled below
        if path == "/register/techteam" and self.command == 'GET':
            params = parse_qs(parsed.query)
            token = params.get("token", [None])[0]
            if not token:
                self.send_html(b"Invalid token", 400)
                return
            self.send_html(load_template("register_techteam.html", title="Tech Team Registration", token=token))
            return

        if path == "/register/techteam":
            params = parse_qs(parsed.query)
            token = params.get("token", [None])[0]
            if not token:
                self.send_html(b"Invalid token", 400)
                return
            self.send_html(load_template("register_techteam.html", title="Tech Team Registration", token=token))
            return

        if path == "/register/umpire":
            params = parse_qs(parsed.query)
            token = params.get("token", [None])[0]
            if not token:
                self.send_html(b"Invalid token", 400)
                return
            self.send_html(load_template("register_umpire.html", title="Umpire Registration", token=token))
            return

        if path == "/register/team":
            params = parse_qs(parsed.query)
            token = params.get("token", [None])[0]
            if not token:
                self.send_html(b"Invalid token", 400)
                return
            self.send_html(load_template("register_team.html", title="Team Registration", token=token))
            return

        self.redirect("/")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", "0"))

        if path == "/login":
            fields = parse_qs(self.rfile.read(length).decode())
            username = fields.get("username", [""])[0]
            password = fields.get("password", [""])[0]
            if valid_login(username, password):
                token = secrets.token_urlsafe(32)
                SESSIONS.add(token)
                self.redirect(
                    "/home",
                    f"session={token}; HttpOnly; SameSite=Lax; Path=/"
                )
            else:
                self.send_html(
                    load_template(
                        "admin_login.html",
                        title="Admin Loginpage",
                        message="Invalid username or password.",
                    ),
                    401,
                )
            return

        if path == "/logout":
            token = self.session_token()
            SESSIONS.discard(token)
            self.redirect("/", "session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")
            return

        if path == "/api/tournaments" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            tournament = create_tournament(data)
            send_json(self, tournament, status=201)
            return

        # Techteam create
        if path == "/api/techteams" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            try:
                team = create_techteam(data)
            except sqlite3.IntegrityError as e:
                send_json(self, {"error": "Integrity error", "detail": str(e)}, status=400)
                return
            except KeyError as e:
                send_json(self, {"error": "Missing field", "detail": str(e)}, status=400)
                return
            except Exception as e:
                send_json(self, {"error": "Server error", "detail": str(e)}, status=500)
                return
            send_json(self, team, status=201)
            return

        # Teams create
        if path == "/api/teams" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            try:
                team = create_team(data)
            except sqlite3.IntegrityError as e:
                send_json(self, {"error": "Integrity error", "detail": str(e)}, status=400)
                return
            except KeyError as e:
                send_json(self, {"error": "Missing field", "detail": str(e)}, status=400)
                return
            except Exception as e:
                send_json(self, {"error": "Server error", "detail": str(e)}, status=500)
                return
            send_json(self, team, status=201)
            return

        # Umpire create
        if path == "/api/umpires" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            try:
                umpire = create_umpire(data)
            except sqlite3.IntegrityError as e:
                send_json(self, {"error": "Integrity error", "detail": str(e)}, status=400)
                return
            except KeyError as e:
                send_json(self, {"error": "Missing field", "detail": str(e)}, status=400)
                return
            except Exception as e:
                send_json(self, {"error": "Server error", "detail": str(e)}, status=500)
                return
            send_json(self, umpire, status=201)
            return

        # Create invite / registration link
        if path == "/api/techteams/links" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            expires = int(data.get("expires_seconds", 3600))
            token = create_invite(expires, role="techteam")
            link = f"http://{HOST}:{PORT}/register/techteam?token={token}"
            send_json(self, {"link": link})
            return

        if path == "/api/umpires/links" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            expires = int(data.get("expires_seconds", 3600))
            token = create_invite(expires, role="umpire")
            link = f"http://{HOST}:{PORT}/register/umpire?token={token}"
            send_json(self, {"link": link})
            return

        if path == "/api/teams/links" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            expires = int(data.get("expires_seconds", 3600))
            token = create_invite(expires, role="team")
            link = f"http://{HOST}:{PORT}/register/team?token={token}"
            send_json(self, {"link": link})
            return

        # password reset request (simulate send)
        if path.endswith("/reset_password") and self.session_token() in SESSIONS:
            entity_id = path.rsplit("/", 2)[-2]
            token = create_invite(3600, role="reset")
            send_json(self, {"token": token})
            return

        # block/unblock endpoint (expects JSON {blocked: true/false})
        if path.endswith("/block") and self.session_token() in SESSIONS:
            entity_id = path.rsplit("/", 2)[-2]
            body = parse_json_body(self, length)
            blocked = bool(body.get("blocked", True))
            if path.startswith("/api/umpires/"):
                set_umpire_block_status(entity_id, blocked)
            elif path.startswith("/api/teams/"):
                set_team_block_status(entity_id, blocked)
            else:
                set_block_status(entity_id, blocked)
            send_json(self, {"success": True})
            return
        
        # handle registration POST (from public form)
        if path == "/register/techteam" and self.command == 'POST':
            # body should be json with token present in query
            params = parse_qs(parsed.query)
            token = params.get("token", [None])[0]
            if not token:
                send_json(self, {"error": "Missing token"}, status=400)
                return
            invite = get_invite(token)
            if not invite:
                send_json(self, {"error": "Invalid token"}, status=400)
                return
            import datetime
            expires_at = invite[1]
            used = invite[2]
            if used:
                send_json(self, {"error": "Token already used"}, status=400)
                return
            if datetime.datetime.fromisoformat(expires_at) < datetime.datetime.utcnow():
                send_json(self, {"error": "Token expired"}, status=400)
                return
            data = parse_json_body(self, length)
            required = ["name", "password", "age", "gender", "kkfi_number", "phone", "email"]
            if not all(k in data and data[k] for k in required):
                send_json(self, {"error": "Missing fields"}, status=400)
                return
            team = create_techteam(data)
            with sqlite3.connect(DB_PATH) as db:
                db.execute("UPDATE invites SET used = 1 WHERE token = ?", (token,))
            send_json(self, team, status=201)
            return

        if path == "/register/umpire" and self.command == 'POST':
            params = parse_qs(parsed.query)
            token = params.get("token", [None])[0]
            if not token:
                send_json(self, {"error": "Missing token"}, status=400)
                return
            invite = get_invite(token)
            if not invite:
                send_json(self, {"error": "Invalid token"}, status=400)
                return
            import datetime
            expires_at = invite[1]
            used = invite[2]
            if used:
                send_json(self, {"error": "Token already used"}, status=400)
                return
            if datetime.datetime.fromisoformat(expires_at) < datetime.datetime.utcnow():
                send_json(self, {"error": "Token expired"}, status=400)
                return
            data = parse_json_body(self, length)
            required = ["name", "password", "age", "gender", "kkfi_number", "phone", "email", "aadhar_photo_url"]
            if not all(k in data and data[k] for k in required):
                send_json(self, {"error": "Missing fields"}, status=400)
                return
            umpire = create_umpire(data)
            with sqlite3.connect(DB_PATH) as db:
                db.execute("UPDATE invites SET used = 1 WHERE token = ?", (token,))
            send_json(self, umpire, status=201)
            return

        if path == "/register/team" and self.command == 'POST':
            params = parse_qs(parsed.query)
            token = params.get("token", [None])[0]
            if not token:
                send_json(self, {"error": "Missing token"}, status=400)
                return
            invite = get_invite(token)
            if not invite:
                send_json(self, {"error": "Invalid token"}, status=400)
                return
            import datetime
            expires_at = invite[1]
            used = invite[2]
            if used:
                send_json(self, {"error": "Token already used"}, status=400)
                return
            if datetime.datetime.fromisoformat(expires_at) < datetime.datetime.utcnow():
                send_json(self, {"error": "Token expired"}, status=400)
                return
            data = parse_json_body(self, length)
            required = ["team_name", "tournament_id", "address"]
            if not all(k in data and data[k] for k in required):
                send_json(self, {"error": "Missing fields"}, status=400)
                return
            team = create_team(data)
            with sqlite3.connect(DB_PATH) as db:
                db.execute("UPDATE invites SET used = 1 WHERE token = ?", (token,))
            send_json(self, team, status=201)
            return

        self.redirect("/")

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", "0"))

        if path.startswith("/api/tournaments/") and self.session_token() in SESSIONS:
            tournament_id = path.rsplit("/", 1)[-1]
            data = parse_json_body(self, length)
            tournament = update_tournament(tournament_id, data)
            if tournament:
                send_json(self, tournament)
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return

        if path.startswith("/api/techteams/") and self.session_token() in SESSIONS:
            tt_id = path.rsplit("/", 1)[-1]
            data = parse_json_body(self, length)
            team = update_techteam(tt_id, data)
            if team:
                send_json(self, team)
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return

        if path.startswith("/api/umpires/") and self.session_token() in SESSIONS:
            umpire_id = path.rsplit("/", 1)[-1]
            data = parse_json_body(self, length)
            umpire = update_umpire(umpire_id, data)
            if umpire:
                send_json(self, umpire)
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return

        if path.startswith("/api/teams/") and self.session_token() in SESSIONS:
            team_id = path.rsplit("/", 1)[-1]
            data = parse_json_body(self, length)
            team = update_team(team_id, data)
            if team:
                send_json(self, team)
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return

        self.redirect("/")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/tournaments/") and self.session_token() in SESSIONS:
            tournament_id = path.rsplit("/", 1)[-1]
            delete_tournament(tournament_id)
            send_json(self, {"success": True})
            return

        if path.startswith("/api/techteams/") and self.session_token() in SESSIONS:
            tt_id = path.rsplit("/", 1)[-1]
            delete_techteam(tt_id)
            send_json(self, {"success": True})
            return

        if path.startswith("/api/umpires/") and self.session_token() in SESSIONS:
            umpire_id = path.rsplit("/", 1)[-1]
            delete_umpire(umpire_id)
            send_json(self, {"success": True})
            return

        if path.startswith("/api/teams/") and self.session_token() in SESSIONS:
            team_id = path.rsplit("/", 1)[-1]
            delete_team(team_id)
            send_json(self, {"success": True})
            return

        self.redirect("/")

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


def run():
    setup_database()
    print(f"Login app running at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    run()

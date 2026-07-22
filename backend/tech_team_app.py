import datetime
import hashlib
import json
import re
import sqlite3
import secrets
from contextlib import closing
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http import cookies
from urllib.parse import parse_qs, urlparse
# Draws system removed: no draw_services import

HOST = "127.0.0.1"
PORT = 8001
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "login.db"
TEMPLATE_DIR = BASE_DIR / "templates_techteam"
SESSIONS = set()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)


def setup_database():
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS techteams ("
            "tt_id TEXT PRIMARY KEY, name TEXT NOT NULL, password_hash TEXT NOT NULL, age INTEGER, gender TEXT, kkfi_number TEXT UNIQUE, phone TEXT, email TEXT UNIQUE, blocked INTEGER DEFAULT 0)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS tournaments ("
            "tournament_id TEXT PRIMARY KEY, name TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, status TEXT NOT NULL, tournament_for TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS team_managers ("
            "manager_id TEXT PRIMARY KEY, name TEXT NOT NULL, age INTEGER, gender TEXT, kkfi_number TEXT UNIQUE, phone TEXT, email TEXT UNIQUE, password_hash TEXT NOT NULL, address TEXT, blocked INTEGER DEFAULT 0)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS teams ("
            "team_id TEXT PRIMARY KEY, team_name TEXT NOT NULL, tournament_id TEXT, address TEXT, manager_id TEXT, blocked INTEGER DEFAULT 0)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS players ("
            "player_id TEXT PRIMARY KEY, team_id TEXT, player_name TEXT NOT NULL, kkfi_number TEXT UNIQUE, chest_number INTEGER, document_url TEXT, manager_id TEXT, blocked INTEGER DEFAULT 0)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS umpires ("
            "umpire_id TEXT PRIMARY KEY, name TEXT NOT NULL, kkfi_number TEXT UNIQUE, aadhar_photo_url TEXT, gender TEXT, age INTEGER, phone TEXT, email TEXT UNIQUE, password_hash TEXT NOT NULL, blocked INTEGER DEFAULT 0)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS invites ("
            "token TEXT PRIMARY KEY, expires_at TEXT NOT NULL, used INTEGER DEFAULT 0, role TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS matches (match_id TEXT PRIMARY KEY, draw_id TEXT, match_number INTEGER NOT NULL, tournament_id TEXT, group_name TEXT, stage_name TEXT, team_a_id TEXT, team_b_id TEXT, match_status TEXT, is_follow_on_enforced INTEGER DEFAULT 0, final_winner_id TEXT, win_type TEXT, win_margin TEXT, umpire_id TEXT)"
        )
        cursor = db.execute("PRAGMA table_info(matches)").fetchall()
        columns = [row[1] for row in cursor]
        if 'umpire_id' not in columns:
            try:
                db.execute("ALTER TABLE matches ADD COLUMN umpire_id TEXT")
            except sqlite3.OperationalError:
                pass
        if 'draw_id' not in columns:
            try:
                db.execute("ALTER TABLE matches ADD COLUMN draw_id TEXT")
            except sqlite3.OperationalError:
                pass
        if 'stage_name' not in columns:
            try:
                db.execute("ALTER TABLE matches ADD COLUMN stage_name TEXT")
            except sqlite3.OperationalError:
                pass
        # tournament_draws table removed along with draws feature
        if not db.execute("SELECT 1 FROM techteams WHERE tt_id = ?", ("TT0001",)).fetchone():
            db.execute(
                "INSERT INTO techteams (tt_id, name, password_hash, age, gender, kkfi_number, phone, email, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                ("TT0001", "Tech Admin", hashlib.sha256(b"techpass").hexdigest(), 30, "M", "KKFI9001", "9999999999", "tech@example.com",),
            )


def valid_login(tt_id_or_email, password):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT tt_id, password_hash FROM techteams WHERE tt_id = ? OR email = ?",
            (tt_id_or_email, tt_id_or_email),
        ).fetchone()
    if not row:
        return False
    return secrets.compare_digest(row[1], hashlib.sha256(password.encode()).hexdigest())


def create_invite(expires_seconds, role):
    token = secrets.token_urlsafe(24)
    expires_at = (datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_seconds)).isoformat()
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT INTO invites (token, expires_at, used, role) VALUES (?, ?, 0, ?)", (token, expires_at, role))
    return token


def get_invite(token):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("SELECT token, expires_at, used, role FROM invites WHERE token = ?", (token,)).fetchone()
    return row if row else None


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


def create_tournament(data):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("SELECT MAX(CAST(SUBSTR(tournament_id, 3) AS INTEGER)) FROM tournaments").fetchone()
        max_index = row[0] if row and row[0] is not None else 0
        tournament_id = f"KT{max_index + 1:04d}"
        db.execute(
            "INSERT INTO tournaments (tournament_id, name, start_date, end_date, status, tournament_for) VALUES (?, ?, ?, ?, ?, ?)",
            (tournament_id, data.get("name"), data.get("start_date"), data.get("end_date"), data.get("status"), data.get("tournament_for")),
        )
    return {"tournament_id": tournament_id, "name": data.get("name"), "start_date": data.get("start_date"), "end_date": data.get("end_date"), "status": data.get("status"), "tournament_for": data.get("tournament_for")}


def create_player(data):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("SELECT MAX(CAST(SUBSTR(player_id, 3) AS INTEGER)) FROM players").fetchone()
        max_index = row[0] if row and row[0] is not None else 0
        player_id = f"PL{max_index + 1:04d}"
        chest_number = data.get("chest_number")
        if chest_number in (None, ""):
            chest_number = None
        db.execute(
            "INSERT INTO players (player_id, team_id, player_name, kkfi_number, chest_number, document_url, manager_id, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (player_id, data.get("team_id"), data.get("player_name"), data.get("kkfi_number"), chest_number, data.get("document_url"), data.get("manager_id")),
        )
    return {"player_id": player_id, "player_name": data.get("player_name"), "team_id": data.get("team_id")}


# Draws persistence and APIs removed


def get_dashboard_stats():
    with sqlite3.connect(DB_PATH) as db:
        umpires = db.execute("SELECT COUNT(*) FROM umpires").fetchone()[0]
        managers = db.execute("SELECT COUNT(*) FROM team_managers").fetchone()[0]
        players = db.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        tournaments = db.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0]
    return {"umpires": umpires, "managers": managers, "players": players, "tournaments": tournaments}


def save_draws(tournament_id, payload):
    draws = payload.get('draws', []) or []
    scope = payload.get('scope')
    if scope not in ('group', 'manual', 'knockout'):
        return {'success': False, 'error': 'Invalid save scope'}

    # basic validation: no duplicate matches, teams not playing against themselves
    seen = set()
    for d in draws:
        a = d.get('team_a_id')
        b = d.get('team_b_id')
        if not a or not b:
            return {'success': False, 'error': 'Each fixture must have two teams'}
        if a == b:
            return {'success': False, 'error': 'A team cannot play itself'}
        # key = tuple(sorted([a, b])) + ((d.get('group_name') or '') if scope == 'group' else ('Manual',))
        group_key = (
            d.get("group_name") or ""
            if scope == "group"
            else d.get("stage_name") or d.get("group_name") or "Manual"
        )
        key = tuple(sorted((str(a), str(b)))) + (group_key,)

        if key in seen:
            return {'success': False, 'error': 'Duplicate fixture detected'}
        seen.add(key)

    try:
        with closing(sqlite3.connect(DB_PATH)) as db:
            cur = db.cursor()
            row = cur.execute("SELECT MAX(CAST(SUBSTR(match_id, 3) AS INTEGER)) FROM matches").fetchone()
            max_index = row[0] if row and row[0] is not None else 0
            draw_id = f"DW{tournament_id[2:] if tournament_id.startswith('KT') else tournament_id}"
            for i, d in enumerate(draws, start=1):
                match_id = f"TM{max_index + i:04d}"
                stage_name = d.get('stage_name') or d.get('group_name') or ('Manual' if scope == 'manual' else None)
                cur.execute(
                    "INSERT INTO matches (match_id, draw_id, match_number, tournament_id, group_name, stage_name, team_a_id, team_b_id, match_status, is_follow_on_enforced, final_winner_id, win_type, win_margin, umpire_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        match_id,
                        draw_id,
                        d.get('match_number') or i,
                        tournament_id,
                        d.get('group_name') or stage_name,
                        stage_name,
                        d.get('team_a_id'),
                        d.get('team_b_id'),
                        d.get('match_status') or None,
                        1 if d.get('is_follow_on_enforced') else 0,
                        d.get('final_winner_id') or None,
                        d.get('win_type') or None,
                        d.get('win_margin') or None,
                        d.get('umpire_id') or None,
                    )
                )
            db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return {'success': False, 'error': str(e)}
    return {'success': True, 'tournament_id': tournament_id}


def get_manager_rows():
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(
            "SELECT m.manager_id, m.name, m.age, m.gender, m.kkfi_number, m.phone, m.email, m.address, m.blocked, "
            "t.team_name, t.team_id, t.tournament_id, tr.name AS tournament_name "
            "FROM team_managers m LEFT JOIN teams t ON t.manager_id = m.manager_id "
            "LEFT JOIN tournaments tr ON tr.tournament_id = t.tournament_id ORDER BY m.name ASC"
        ).fetchall()
    return [{
        "manager_id": r[0],
        "name": r[1],
        "age": r[2],
        "gender": r[3],
        "kkfi_number": r[4],
        "phone": r[5],
        "email": r[6],
        "address": r[7],
        "blocked": bool(r[8]),
        "team_name": r[9],
        "team_id": r[10],
        "tournament_id": r[11],
        "tournament_name": r[12],
    } for r in rows]


def get_team_rows():
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(
            "SELECT t.team_id, t.team_name, t.tournament_id, tr.name AS tournament_name, t.address, t.manager_id, t.blocked, "
            "m.name AS manager_name, m.phone AS manager_phone, m.kkfi_number "
            "FROM teams t LEFT JOIN tournaments tr ON tr.tournament_id = t.tournament_id "
            "LEFT JOIN team_managers m ON m.manager_id = t.manager_id ORDER BY t.team_name ASC"
        ).fetchall()
    return [{
        "team_id": r[0],
        "team_name": r[1],
        "tournament_id": r[2],
        "tournament_name": r[3],
        "address": r[4],
        "manager_id": r[5],
        "blocked": bool(r[6]),
        "manager_name": r[7],
        "manager_phone": r[8],
        "kkfi_number": r[9],
    } for r in rows]


def get_player_rows():
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(
            "SELECT p.player_id, p.team_id, p.player_name, p.kkfi_number, p.chest_number, p.document_url, p.manager_id, p.blocked, "
            "t.team_name, t.tournament_id, tr.name AS tournament_name, tm.name AS manager_name, tm.phone AS manager_phone "
            "FROM players p LEFT JOIN teams t ON t.team_id = p.team_id "
            "LEFT JOIN tournaments tr ON tr.tournament_id = t.tournament_id "
            "LEFT JOIN team_managers tm ON tm.manager_id = p.manager_id ORDER BY p.player_name ASC"
        ).fetchall()
    return [{
        "player_id": r[0],
        "team_id": r[1],
        "player_name": r[2],
        "kkfi_number": r[3],
        "chest_number": r[4],
        "document_url": r[5],
        "manager_id": r[6],
        "blocked": bool(r[7]),
        "team_name": r[8],
        "tournament_id": r[9],
        "tournament_name": r[10],
        "manager_name": r[11],
        "manager_phone": r[12],
    } for r in rows]


def get_umpire_rows():
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(
            "SELECT umpire_id, name, gender, age, kkfi_number, phone, email, aadhar_photo_url, blocked FROM umpires ORDER BY name ASC"
        ).fetchall()
    return [{
        "umpire_id": r[0],
        "name": r[1],
        "gender": r[2],
        "age": r[3],
        "kkfi_number": r[4],
        "phone": r[5],
        "email": r[6],
        "aadhar_photo_url": r[7],
        "blocked": bool(r[8]),
    } for r in rows]


def load_template(name, **context):
    path = TEMPLATE_DIR / name
    if not path.exists():
        alt_path = BASE_DIR / "templates" / name
        if alt_path.exists():
            path = alt_path
    content = path.read_text(encoding="utf-8")
    include_pattern = re.compile(r"<!--\s*include:\s*([^\s]+)\s*-->")
    var_pattern = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

    def expand_includes(text):
        def _inc(match):
            include_path = match.group(1).strip()
            include_file = TEMPLATE_DIR / include_path
            try:
                include_text = include_file.read_text(encoding="utf-8")
                return expand_includes(include_text)
            except Exception:
                return ""

        return include_pattern.sub(_inc, text)

    def replace_var(match):
        var_name = match.group(1)
        return str(context.get(var_name, match.group(0)))

    content = expand_includes(content)
    content = var_pattern.sub(replace_var, content)
    return content.encode("utf-8")


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
            self.send_html(load_template("login.html", title="Tech Team Login"))
            return

        if path == "/home" and self.session_token() in SESSIONS:
            self.send_html(load_template("home.html", title="Tech Team Home"))
            return

        if path == "/umpires" and self.session_token() in SESSIONS:
            self.send_html(load_template("umpires.html", title="Umpires"))
            return

        if path == "/managers" and self.session_token() in SESSIONS:
            self.send_html(load_template("managers.html", title="Managers"))
            return

        if path == "/players" and self.session_token() in SESSIONS:
            self.send_html(load_template("players.html", title="Players"))
            return

        if path == "/teams" and self.session_token() in SESSIONS:
            self.send_html(load_template("teams.html", title="Teams"))
            return

        if path == "/tournaments" and self.session_token() in SESSIONS:
            self.send_html(load_template("tournaments.html", title="Tournament Matches"))
            return

        if path.startswith("/tournaments/") and path.endswith("/draws-view") and self.session_token() in SESSIONS:
            segments = [segment for segment in path.split("/") if segment]
            if len(segments) == 3 and segments[0] == "tournaments" and segments[2] == "draws-view":
                tournament_id = segments[1]
                with sqlite3.connect(DB_PATH) as db:
                    tournament_name = db.execute("SELECT name FROM tournaments WHERE tournament_id = ?", (tournament_id,)).fetchone()
                tournament_name = tournament_name[0] if tournament_name else tournament_id
                self.send_html(load_template("draws_view.html", title=f"Draws - {tournament_name}", tournament_name=tournament_name, tournament_id=tournament_id))
                return

        if path.startswith("/tournaments/") and self.session_token() in SESSIONS:
            tournament_id = path.strip("/").split("/")[-1]
            with sqlite3.connect(DB_PATH) as db:
                tournament_name = db.execute("SELECT name FROM tournaments WHERE tournament_id = ?", (tournament_id,)).fetchone()
            tournament_name = tournament_name[0] if tournament_name else tournament_id
            # Serve the draws UI (Group Stage focused)
            self.send_html(load_template("draws.html", title=f"Create Draws - {tournament_name}", tournament_name=tournament_name, tournament_id=tournament_id))
            return

        if path == "/logout":
            SESSIONS.discard(self.session_token())
            self.redirect("/", "session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")
            return

        if path == "/register/manager":
            token = parse_qs(parsed.query).get("token", [None])[0]
            if not token:
                self.send_html(b"Invalid token", 400)
                return
            self.send_html(load_template("register_manager.html", title="Manager Registration", token=token))
            return

        if path == "/register/umpire":
            token = parse_qs(parsed.query).get("token", [None])[0]
            if not token:
                self.send_html(b"Invalid token", 400)
                return
            self.send_html(load_template("register_umpire.html", title="Umpire Registration", token=token))
            return

        if path == "/register/team":
            token = parse_qs(parsed.query).get("token", [None])[0]
            if not token:
                self.send_html(b"Invalid token", 400)
                return
            self.send_html(load_template("register_team.html", title="Team Registration", token=token))
            return

        if path == "/register/player":
            token = parse_qs(parsed.query).get("token", [None])[0]
            if not token:
                self.send_html(b"Invalid token", 400)
                return
            self.send_html(load_template("register_player.html", title="Player Registration", token=token))
            return

        if path == "/register/tournament":
            token = parse_qs(parsed.query).get("token", [None])[0]
            if not token:
                self.send_html(b"Invalid token", 400)
                return
            self.send_html(load_template("register_tournament.html", title="Tournament Registration", token=token))
            return

        if path == "/api/dashboard/stats":
            send_json(self, get_dashboard_stats())
            return

        if path == "/api/tournaments":
            with sqlite3.connect(DB_PATH) as db:
                rows = db.execute("SELECT tournament_id, name, start_date, end_date, status, tournament_for FROM tournaments ORDER BY start_date DESC").fetchall()
            send_json(self, [{"tournament_id": r[0], "name": r[1], "start_date": r[2], "end_date": r[3], "status": r[4], "tournament_for": r[5]} for r in rows])
            return

        if path == "/api/teams":
            rows = get_team_rows()
            send_json(self, {"teams": rows})
            return

        if path.startswith("/api/teams/"):
            team_id = path.rsplit("/", 1)[-1]
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT team_id, team_name, tournament_id, address, manager_id, blocked FROM teams WHERE team_id = ?", (team_id,)).fetchone()
            if row:
                send_json(self, {"team_id": row[0], "team_name": row[1], "tournament_id": row[2], "address": row[3], "manager_id": row[4], "blocked": bool(row[5])})
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return

        if path.startswith("/api/managers/"):
            manager_id = path.rsplit("/", 1)[-1]
            if "/block" in path or "/reset_password" in path:
                return
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT manager_id, name, age, gender, kkfi_number, phone, email, address, blocked FROM team_managers WHERE manager_id = ?", (manager_id,)).fetchone()
            if row:
                send_json(self, {"manager_id": row[0], "name": row[1], "age": row[2], "gender": row[3], "kkfi_number": row[4], "phone": row[5], "email": row[6], "address": row[7], "blocked": bool(row[8])})
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return

        if path.startswith("/api/players/"):
            player_id = path.rsplit("/", 1)[-1]
            if "/block" in path or "/reset_password" in path:
                return
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT player_id, team_id, player_name, kkfi_number, chest_number, document_url, manager_id, blocked FROM players WHERE player_id = ?", (player_id,)).fetchone()
            if row:
                send_json(self, {"player_id": row[0], "team_id": row[1], "player_name": row[2], "kkfi_number": row[3], "chest_number": row[4], "document_url": row[5], "manager_id": row[6], "blocked": bool(row[7])})
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return

        if path == "/api/managers":
            rows = get_manager_rows()
            send_json(self, {"managers": rows, "total": len(rows)})
            return

        if path == "/api/players":
            rows = get_player_rows()
            send_json(self, {"players": rows, "total": len(rows)})
            return

        if path.startswith("/api/umpires/"):
            umpire_id = path.rsplit("/", 1)[-1]
            if "/block" in path or "/reset_password" in path:
                return
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT umpire_id, name, kkfi_number, aadhar_photo_url, gender, age, phone, email, blocked FROM umpires WHERE umpire_id = ?", (umpire_id,)).fetchone()
            if row:
                send_json(self, {"umpire_id": row[0], "name": row[1], "kkfi_number": row[2], "aadhar_photo_url": row[3], "gender": row[4], "age": row[5], "phone": row[6], "email": row[7], "blocked": bool(row[8])})
            else:
                send_json(self, {"error": "Not found"}, status=404)
            return

        if path == "/api/umpires":
            rows = get_umpire_rows()
            send_json(self, {"umpires": rows, "total": len(rows)})
            return

        if path == "/api/matches":
            query = parse_qs(parsed.query)
            tournament_id = query.get("tournament_id", [None])[0]
            if tournament_id:
                with sqlite3.connect(DB_PATH) as db:
                    rows = db.execute(
                        "SELECT m.match_id, m.draw_id, m.match_number, m.tournament_id, COALESCE(m.stage_name, m.group_name) AS stage_name, m.team_a_id, m.team_b_id, m.match_status, m.is_follow_on_enforced, m.final_winner_id, m.win_type, m.win_margin, t_a.team_name, t_b.team_name, m.umpire_id "
                        "FROM matches m "
                        "LEFT JOIN teams t_a ON t_a.team_id = m.team_a_id "
                        "LEFT JOIN teams t_b ON t_b.team_id = m.team_b_id "
                        "WHERE m.tournament_id = ? ORDER BY m.match_number ASC",
                        (tournament_id,)
                    ).fetchall()
                send_json(self, [{
                    "match_id": r[0],
                    "draw_id": r[1] or "",
                    "match_number": r[2],
                    "tournament_id": r[3],
                    "stage_name": r[4],
                    "team_a_id": r[5],
                    "team_b_id": r[6],
                    "match_status": r[7],
                    "is_follow_on_enforced": bool(r[8]),
                    "final_winner_id": r[9],
                    "win_type": r[10],
                    "win_margin": r[11],
                    "team_a_name": r[12] or "",
                    "team_b_name": r[13] or "",
                    "umpire_id": r[14] or ""
                } for r in rows])
                return
            with sqlite3.connect(DB_PATH) as db:
                rows = db.execute("SELECT match_id, draw_id, match_number, tournament_id, COALESCE(stage_name, group_name) AS stage_name, team_a_id, team_b_id, match_status, is_follow_on_enforced, final_winner_id, win_type, win_margin, umpire_id FROM matches ORDER BY match_number ASC").fetchall()
            send_json(self, [{"match_id": r[0], "draw_id": r[1] or "", "match_number": r[2], "tournament_id": r[3], "stage_name": r[4], "team_a_id": r[5], "team_b_id": r[6], "match_status": r[7], "is_follow_on_enforced": bool(r[8]), "final_winner_id": r[9], "win_type": r[10], "win_margin": r[11], "umpire_id": r[12] or ""} for r in rows])
            return

        # Draws API removed

        if path.startswith("/api/matches/"):
            match_id = path.rsplit("/", 1)[-1]
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT match_id, draw_id, match_number, tournament_id, COALESCE(stage_name, group_name) AS stage_name, team_a_id, team_b_id, match_status, is_follow_on_enforced, final_winner_id, win_type, win_margin, umpire_id FROM matches WHERE match_id = ?", (match_id,)).fetchone()
            if row:
                send_json(self, {"match_id": row[0], "draw_id": row[1] or "", "match_number": row[2], "tournament_id": row[3], "stage_name": row[4], "team_a_id": row[5], "team_b_id": row[6], "match_status": row[7], "is_follow_on_enforced": bool(row[8]), "final_winner_id": row[9], "win_type": row[10], "win_margin": row[11], "umpire_id": row[12] or ""})
            else:
                send_json(self, {"error": "Not found"}, status=404)
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
                self.redirect("/home", f"session={token}; HttpOnly; SameSite=Lax; Path=/")
            else:
                self.send_html(load_template("login.html", title="Tech Team Login", message="Invalid credentials"), 401)
            return

        if path == "/api/managers" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT MAX(CAST(SUBSTR(manager_id, 3) AS INTEGER)) FROM team_managers").fetchone()
                max_index = row[0] if row and row[0] is not None else 0
                manager_id = f"TM{max_index + 1:04d}"
                db.execute(
                    "INSERT INTO team_managers (manager_id, name, age, gender, kkfi_number, phone, email, password_hash, address, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (manager_id, data.get("name"), data.get("age"), data.get("gender"), data.get("kkfi_number"), data.get("phone"), data.get("email"), hashlib.sha256(str(data.get("password", "")).encode()).hexdigest(), data.get("address")),
                )
            send_json(self, {"manager_id": manager_id}, status=201)
            return

        if path.startswith("/api/managers/") and self.session_token() in SESSIONS:
            item_id = path.rsplit("/", 1)[-1]
            if path.endswith("/block"):
                data = parse_json_body(self, length)
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("UPDATE team_managers SET blocked = ? WHERE manager_id = ?", (1 if data.get("blocked") else 0, item_id))
                send_json(self, {"success": True})
                return
            if path.endswith("/reset_password"):
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("UPDATE team_managers SET password_hash = ? WHERE manager_id = ?", (hashlib.sha256(b"reset").hexdigest(), item_id))
                send_json(self, {"success": True})
                return
            self.redirect("/")
            return

        if path == "/api/teams" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT MAX(CAST(SUBSTR(team_id, 3) AS INTEGER)) FROM teams").fetchone()
                max_index = row[0] if row and row[0] is not None else 0
                team_id = f"TE{max_index + 1:04d}"
                db.execute(
                    "INSERT INTO teams (team_id, team_name, tournament_id, address, manager_id, blocked) VALUES (?, ?, ?, ?, ?, 0)",
                    (team_id, data.get("team_name"), data.get("tournament_id"), data.get("address"), data.get("manager_id")),
                )
            send_json(self, {"team_id": team_id}, status=201)
            return

        if path.startswith("/api/teams/") and self.session_token() in SESSIONS:
            item_id = path.rsplit("/", 1)[-1]
            if path.endswith("/block"):
                data = parse_json_body(self, length)
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("UPDATE teams SET blocked = ? WHERE team_id = ?", (1 if data.get("blocked") else 0, item_id))
                send_json(self, {"success": True})
                return
            if path.endswith("/reset_password"):
                send_json(self, {"success": True})
                return
            self.redirect("/")
            return

        if path == "/api/players" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT MAX(CAST(SUBSTR(player_id, 3) AS INTEGER)) FROM players").fetchone()
                max_index = row[0] if row and row[0] is not None else 0
                player_id = f"PL{max_index + 1:04d}"
                chest_number = data.get("chest_number")
                if chest_number in (None, ""):
                    chest_number = None
                db.execute(
                    "INSERT INTO players (player_id, team_id, player_name, kkfi_number, chest_number, document_url, manager_id, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                    (player_id, data.get("team_id"), data.get("player_name"), data.get("kkfi_number"), chest_number, data.get("document_url"), data.get("manager_id")),
                )
            send_json(self, {"player_id": player_id}, status=201)
            return

        if path.startswith("/api/players/") and self.session_token() in SESSIONS:
            item_id = path.rsplit("/", 1)[-1]
            if path.endswith("/block"):
                data = parse_json_body(self, length)
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("UPDATE players SET blocked = ? WHERE player_id = ?", (1 if data.get("blocked") else 0, item_id))
                send_json(self, {"success": True})
                return
            if path.endswith("/reset_password"):
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("UPDATE players SET document_url = ? WHERE player_id = ?", ("reset", item_id))
                send_json(self, {"success": True})
                return
            self.redirect("/")
            return

        if path == "/api/umpires" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT MAX(CAST(SUBSTR(umpire_id, 3) AS INTEGER)) FROM umpires").fetchone()
                max_index = row[0] if row and row[0] is not None else 0
                umpire_id = f"UM{max_index + 1:04d}"
                db.execute(
                    "INSERT INTO umpires (umpire_id, name, kkfi_number, aadhar_photo_url, gender, age, phone, email, password_hash, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (umpire_id, data.get("name"), data.get("kkfi_number"), data.get("aadhar_photo_url"), data.get("gender"), data.get("age"), data.get("phone"), data.get("email"), hashlib.sha256(str(data.get("password", "")).encode()).hexdigest()),
                )
            send_json(self, {"umpire_id": umpire_id}, status=201)
            return

        if path.startswith("/api/umpires/") and self.session_token() in SESSIONS:
            item_id = path.rsplit("/", 1)[-1]
            if path.endswith("/block"):
                data = parse_json_body(self, length)
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("UPDATE umpires SET blocked = ? WHERE umpire_id = ?", (1 if data.get("blocked") else 0, item_id))
                send_json(self, {"success": True})
                return
            if path.endswith("/reset_password"):
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("UPDATE umpires SET password_hash = ? WHERE umpire_id = ?", (hashlib.sha256(b"reset").hexdigest(), item_id))
                send_json(self, {"success": True})
                return
            self.redirect("/")
            return

        if path == "/api/tournaments" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT MAX(CAST(SUBSTR(tournament_id, 3) AS INTEGER)) FROM tournaments").fetchone()
                max_index = row[0] if row and row[0] is not None else 0
                tournament_id = f"KT{max_index + 1:04d}"
                db.execute(
                    "INSERT INTO tournaments (tournament_id, name, start_date, end_date, status, tournament_for) VALUES (?, ?, ?, ?, ?, ?)",
                    (tournament_id, data.get("name"), data.get("start_date"), data.get("end_date"), data.get("status"), data.get("tournament_for")),
                )
            send_json(self, {"tournament_id": tournament_id}, status=201)
            return

        if path == "/api/matches/batch" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            tournament_id = data.get("tournament_id")
            draws = data.get("draws", [])
            scope = data.get("scope")
            if not tournament_id or not isinstance(draws, list):
                send_json(self, {"success": False, "error": "Invalid match save payload"}, status=400)
                return
            send_json(self, save_draws(tournament_id, {"draws": draws, "scope": scope or "group"}), status=201)
            return

        # Save draws for a tournament (group/manual payloads)
        if path.startswith("/api/tournaments/") and path.endswith("/draws") and self.session_token() in SESSIONS:
            segments = [segment for segment in path.split("/") if segment]
            if len(segments) == 4 and segments[0] == "api" and segments[1] == "tournaments" and segments[3] == "draws":
                tournament_id = segments[2]
                data = parse_json_body(self, length)
                send_json(self, save_draws(tournament_id, data), status=201)
                return

        if path == "/api/managers/links" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            expires = int(data.get("expires_seconds", 3600))
            token = create_invite(expires, "manager")
            link = f"http://{HOST}:{PORT}/register/manager?token={token}"
            send_json(self, {"link": link})
            return

        if path == "/api/umpires/links" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            expires = int(data.get("expires_seconds", 3600))
            token = create_invite(expires, "umpire")
            link = f"http://{HOST}:{PORT}/register/umpire?token={token}"
            send_json(self, {"link": link})
            return

        if path == "/api/teams/links" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            expires = int(data.get("expires_seconds", 3600))
            token = create_invite(expires, "team")
            link = f"http://{HOST}:{PORT}/register/team?token={token}"
            send_json(self, {"link": link})
            return

        if path == "/api/players/links" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            expires = int(data.get("expires_seconds", 3600))
            token = create_invite(expires, "player")
            link = f"http://{HOST}:{PORT}/register/player?token={token}"
            send_json(self, {"link": link})
            return

        if path == "/api/tournaments/links" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            expires = int(data.get("expires_seconds", 3600))
            token = create_invite(expires, "tournament")
            link = f"http://{HOST}:{PORT}/register/tournament?token={token}"
            send_json(self, {"link": link})
            return

        # Registration endpoints using invite tokens
        if path == "/register/umpire":
            token = parse_qs(parsed.query).get("token", [None])[0]
            invite = get_invite(token)
            if not invite or invite[1] < datetime.datetime.utcnow().isoformat() or invite[2] == 1 or invite[3] != "umpire":
                send_json(self, {"error": "Invalid or expired token"}, status=400)
                return
            data = parse_json_body(self, length)
            # Validate mandatory fields
            required = ["name", "password", "age", "gender", "kkfi_number", "phone", "email"]
            for field in required:
                if not data.get(field):
                    send_json(self, {"error": f"{field} is mandatory"}, status=400)
                    return
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT MAX(CAST(SUBSTR(umpire_id, 3) AS INTEGER)) FROM umpires").fetchone()
                max_index = row[0] if row and row[0] is not None else 0
                umpire_id = f"UM{max_index + 1:04d}"
                try:
                    db.execute(
                        "INSERT INTO umpires (umpire_id, name, kkfi_number, aadhar_photo_url, gender, age, phone, email, password_hash, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (umpire_id, data.get("name"), data.get("kkfi_number"), data.get("aadhar_photo_url"), data.get("gender"), data.get("age"), data.get("phone"), data.get("email"), hashlib.sha256(data.get("password", "").encode()).hexdigest()),
                    )
                    db.execute("UPDATE invites SET used = 1 WHERE token = ?", (token,))
                except sqlite3.IntegrityError as e:
                    send_json(self, {"error": f"Registration failed: {str(e)}"}, status=400)
                    return
            send_json(self, {"success": True, "umpire_id": umpire_id, "message": "Umpire registered successfully!"}, status=201)
            return

        if path == "/register/manager":
            token = parse_qs(parsed.query).get("token", [None])[0]
            invite = get_invite(token)
            if not invite or invite[1] < datetime.datetime.utcnow().isoformat() or invite[2] == 1 or invite[3] != "manager":
                send_json(self, {"error": "Invalid or expired token"}, status=400)
                return
            data = parse_json_body(self, length)
            # Validate mandatory fields
            required = ["name", "password", "age", "gender", "kkfi_number", "phone", "email", "address"]
            for field in required:
                if not data.get(field):
                    send_json(self, {"error": f"{field} is mandatory"}, status=400)
                    return
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT MAX(CAST(SUBSTR(manager_id, 3) AS INTEGER)) FROM team_managers").fetchone()
                max_index = row[0] if row and row[0] is not None else 0
                manager_id = f"TM{max_index + 1:04d}"
                try:
                    db.execute(
                        "INSERT INTO team_managers (manager_id, name, age, gender, kkfi_number, phone, email, password_hash, address, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (manager_id, data.get("name"), data.get("age"), data.get("gender"), data.get("kkfi_number"), data.get("phone"), data.get("email"), hashlib.sha256(data.get("password", "").encode()).hexdigest(), data.get("address")),
                    )
                    db.execute("UPDATE invites SET used = 1 WHERE token = ?", (token,))
                except sqlite3.IntegrityError as e:
                    send_json(self, {"error": f"Registration failed: {str(e)}"}, status=400)
                    return
            send_json(self, {"success": True, "manager_id": manager_id, "message": "Manager registered successfully!"}, status=201)
            return

        if path == "/register/team":
            token = parse_qs(parsed.query).get("token", [None])[0]
            invite = get_invite(token)
            if not invite or invite[1] < datetime.datetime.utcnow().isoformat() or invite[2] == 1 or invite[3] != "team":
                send_json(self, {"error": "Invalid or expired token"}, status=400)
                return
            data = parse_json_body(self, length)
            # Validate mandatory fields
            required = ["team_name", "tournament_id", "address", "manager_id"]
            for field in required:
                if not data.get(field):
                    send_json(self, {"error": f"{field} is mandatory"}, status=400)
                    return
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT MAX(CAST(SUBSTR(team_id, 3) AS INTEGER)) FROM teams").fetchone()
                max_index = row[0] if row and row[0] is not None else 0
                team_id = f"TE{max_index + 1:04d}"
                try:
                    db.execute(
                        "INSERT INTO teams (team_id, team_name, tournament_id, address, manager_id, blocked) VALUES (?, ?, ?, ?, ?, 0)",
                        (team_id, data.get("team_name"), data.get("tournament_id"), data.get("address"), data.get("manager_id")),
                    )
                    db.execute("UPDATE invites SET used = 1 WHERE token = ?", (token,))
                except sqlite3.IntegrityError as e:
                    send_json(self, {"error": f"Registration failed: {str(e)}"}, status=400)
                    return
            send_json(self, {"success": True, "team_id": team_id, "message": "Team registered successfully!"}, status=201)
            return

        if path == "/register/player":
            token = parse_qs(parsed.query).get("token", [None])[0]
            invite = get_invite(token)
            if not invite or invite[1] < datetime.datetime.utcnow().isoformat() or invite[2] == 1 or invite[3] != "player":
                send_json(self, {"error": "Invalid or expired token"}, status=400)
                return
            data = parse_json_body(self, length)
            # Validate mandatory fields
            required = ["team_id", "player_name", "kkfi_number", "manager_id"]
            for field in required:
                if not data.get(field):
                    send_json(self, {"error": f"{field} is mandatory"}, status=400)
                    return
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT MAX(CAST(SUBSTR(player_id, 3) AS INTEGER)) FROM players").fetchone()
                max_index = row[0] if row and row[0] is not None else 0
                player_id = f"PL{max_index + 1:04d}"
                chest_number = data.get("chest_number")
                if chest_number in (None, ""):
                    chest_number = None
                try:
                    db.execute(
                        "INSERT INTO players (player_id, team_id, player_name, kkfi_number, chest_number, document_url, manager_id, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                        (player_id, data.get("team_id"), data.get("player_name"), data.get("kkfi_number"), chest_number, data.get("document_url"), data.get("manager_id")),
                    )
                    db.execute("UPDATE invites SET used = 1 WHERE token = ?", (token,))
                except sqlite3.IntegrityError as e:
                    send_json(self, {"error": f"Registration failed: {str(e)}"}, status=400)
                    return
            send_json(self, {"success": True, "player_id": player_id, "message": "Player registered successfully!"}, status=201)
            return

        if path == "/register/tournament":
            token = parse_qs(parsed.query).get("token", [None])[0]
            invite = get_invite(token)
            if not invite or invite[1] < datetime.datetime.utcnow().isoformat() or invite[2] == 1 or invite[3] != "tournament":
                send_json(self, {"error": "Invalid or expired token"}, status=400)
                return
            data = parse_json_body(self, length)
            # Validate mandatory fields
            required = ["name", "start_date", "end_date", "status", "tournament_for"]
            for field in required:
                if not data.get(field):
                    send_json(self, {"error": f"{field} is mandatory"}, status=400)
                    return
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT MAX(CAST(SUBSTR(tournament_id, 3) AS INTEGER)) FROM tournaments").fetchone()
                max_index = row[0] if row and row[0] is not None else 0
                tournament_id = f"KT{max_index + 1:04d}"
                try:
                    db.execute(
                        "INSERT INTO tournaments (tournament_id, name, start_date, end_date, status, tournament_for) VALUES (?, ?, ?, ?, ?, ?)",
                        (tournament_id, data.get("name"), data.get("start_date"), data.get("end_date"), data.get("status"), data.get("tournament_for")),
                    )
                    db.execute("UPDATE invites SET used = 1 WHERE token = ?", (token,))
                except sqlite3.IntegrityError as e:
                    send_json(self, {"error": f"Registration failed: {str(e)}"}, status=400)
                    return
            send_json(self, {"success": True, "tournament_id": tournament_id, "message": "Tournament registered successfully!"}, status=201)
            return

        if path == "/api/matches" and self.session_token() in SESSIONS:
            data = parse_json_body(self, length)
            with sqlite3.connect(DB_PATH) as db:
                row = db.execute("SELECT MAX(CAST(SUBSTR(match_id, 3) AS INTEGER)) FROM matches").fetchone()
                max_index = row[0] if row and row[0] is not None else 0
                match_id = f"TM{max_index + 1:04d}"
                db.execute(
                    "INSERT INTO matches (match_id, match_number, tournament_id, group_name, team_a_id, team_b_id, match_status, is_follow_on_enforced, final_winner_id, win_type, win_margin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (match_id, data.get("match_number"), data.get("tournament_id"), data.get("group_name"), data.get("team_a_id"), data.get("team_b_id"), data.get("match_status"), 1 if data.get("is_follow_on_enforced") else 0, data.get("final_winner_id"), data.get("win_type"), data.get("win_margin")),
                )
            send_json(self, {"match_id": match_id}, status=201)
            return

        self.redirect("/")

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", "0"))
        if path.startswith("/api/matches/") and self.session_token() in SESSIONS:
            match_id = path.rsplit("/", 1)[-1]
            data = parse_json_body(self, length)
            with sqlite3.connect(DB_PATH) as db:
                db.execute(
                    "UPDATE matches SET match_number = ?, group_name = ?, team_a_id = ?, team_b_id = ?, match_status = ?, is_follow_on_enforced = ?, final_winner_id = ?, win_type = ?, win_margin = ? WHERE match_id = ?",
                    (data.get("match_number"), data.get("group_name"), data.get("team_a_id"), data.get("team_b_id"), data.get("match_status"), 1 if data.get("is_follow_on_enforced") else 0, data.get("final_winner_id"), data.get("win_type"), data.get("win_margin"), match_id),
                )
            send_json(self, {"success": True})
            return
        if path.startswith("/api/managers/") and self.session_token() in SESSIONS:
            manager_id = path.rsplit("/", 1)[-1]
            data = parse_json_body(self, length)
            with sqlite3.connect(DB_PATH) as db:
                if data.get("password"):
                    password_hash_value = hashlib.sha256(data["password"].encode()).hexdigest()
                    db.execute("UPDATE team_managers SET name = ?, age = ?, gender = ?, kkfi_number = ?, phone = ?, email = ?, address = ?, password_hash = ? WHERE manager_id = ?", (data.get("name"), data.get("age"), data.get("gender"), data.get("kkfi_number"), data.get("phone"), data.get("email"), data.get("address"), password_hash_value, manager_id))
                else:
                    db.execute("UPDATE team_managers SET name = ?, age = ?, gender = ?, kkfi_number = ?, phone = ?, email = ?, address = ? WHERE manager_id = ?", (data.get("name"), data.get("age"), data.get("gender"), data.get("kkfi_number"), data.get("phone"), data.get("email"), data.get("address"), manager_id))
            send_json(self, {"success": True})
            return
        if path.startswith("/api/teams/") and self.session_token() in SESSIONS:
            team_id = path.rsplit("/", 1)[-1]
            data = parse_json_body(self, length)
            with sqlite3.connect(DB_PATH) as db:
                db.execute("UPDATE teams SET team_name = ?, tournament_id = ?, address = ?, manager_id = ? WHERE team_id = ?", (data.get("team_name"), data.get("tournament_id"), data.get("address"), data.get("manager_id"), team_id))
            send_json(self, {"success": True})
            return
        if path.startswith("/api/players/") and self.session_token() in SESSIONS:
            player_id = path.rsplit("/", 1)[-1]
            data = parse_json_body(self, length)
            with sqlite3.connect(DB_PATH) as db:
                db.execute("UPDATE players SET team_id = ?, player_name = ?, kkfi_number = ?, chest_number = ?, document_url = ?, manager_id = ? WHERE player_id = ?", (data.get("team_id"), data.get("player_name"), data.get("kkfi_number"), data.get("chest_number"), data.get("document_url"), data.get("manager_id"), player_id))
            send_json(self, {"success": True})
            return
        if path.startswith("/api/umpires/") and self.session_token() in SESSIONS:
            umpire_id = path.rsplit("/", 1)[-1]
            data = parse_json_body(self, length)
            with sqlite3.connect(DB_PATH) as db:
                if data.get("password"):
                    password_hash_value = hashlib.sha256(data["password"].encode()).hexdigest()
                    db.execute("UPDATE umpires SET name = ?, kkfi_number = ?, aadhar_photo_url = ?, gender = ?, age = ?, phone = ?, email = ?, password_hash = ? WHERE umpire_id = ?", (data.get("name"), data.get("kkfi_number"), data.get("aadhar_photo_url"), data.get("gender"), data.get("age"), data.get("phone"), data.get("email"), password_hash_value, umpire_id))
                else:
                    db.execute("UPDATE umpires SET name = ?, kkfi_number = ?, aadhar_photo_url = ?, gender = ?, age = ?, phone = ?, email = ? WHERE umpire_id = ?", (data.get("name"), data.get("kkfi_number"), data.get("aadhar_photo_url"), data.get("gender"), data.get("age"), data.get("phone"), data.get("email"), umpire_id))
            send_json(self, {"success": True})
            return
        self.redirect("/")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/matches/") and self.session_token() in SESSIONS:
            match_id = path.rsplit("/", 1)[-1]
            with sqlite3.connect(DB_PATH) as db:
                db.execute("DELETE FROM matches WHERE match_id = ?", (match_id,))
            send_json(self, {"success": True})
            return
        if path.startswith("/api/managers/") and self.session_token() in SESSIONS:
            manager_id = path.rsplit("/", 1)[-1]
            with sqlite3.connect(DB_PATH) as db:
                db.execute("DELETE FROM team_managers WHERE manager_id = ?", (manager_id,))
            send_json(self, {"success": True})
            return
        if path.startswith("/api/teams/") and self.session_token() in SESSIONS:
            team_id = path.rsplit("/", 1)[-1]
            with sqlite3.connect(DB_PATH) as db:
                db.execute("DELETE FROM teams WHERE team_id = ?", (team_id,))
            send_json(self, {"success": True})
            return
        if path.startswith("/api/players/") and self.session_token() in SESSIONS:
            player_id = path.rsplit("/", 1)[-1]
            with sqlite3.connect(DB_PATH) as db:
                db.execute("DELETE FROM players WHERE player_id = ?", (player_id,))
            send_json(self, {"success": True})
            return
        if path.startswith("/api/umpires/") and self.session_token() in SESSIONS:
            umpire_id = path.rsplit("/", 1)[-1]
            with sqlite3.connect(DB_PATH) as db:
                db.execute("DELETE FROM umpires WHERE umpire_id = ?", (umpire_id,))
            send_json(self, {"success": True})
            return
        self.redirect("/")


def run():
    setup_database()
    print(f"Tech team app running at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    run()

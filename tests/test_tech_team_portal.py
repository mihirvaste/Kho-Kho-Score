import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path

from backend import tech_team_app


class TechTeamPortalTests(unittest.TestCase):
    def test_load_template_expands_includes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            template_dir = Path(tmp_dir)
            (template_dir / "partials").mkdir(parents=True, exist_ok=True)
            (template_dir / "partials" / "nav.html").write_text("NAV", encoding="utf-8")
            (template_dir / "page.html").write_text("<!-- include: partials/nav.html -->", encoding="utf-8")

            previous_dir = tech_team_app.TEMPLATE_DIR
            tech_team_app.TEMPLATE_DIR = template_dir
            try:
                rendered = tech_team_app.load_template("page.html")
            finally:
                tech_team_app.TEMPLATE_DIR = previous_dir

            self.assertIn(b"NAV", rendered)

    def test_dashboard_stats_returns_counts(self):
        tech_team_app.setup_database()
        stats = tech_team_app.get_dashboard_stats()
        self.assertIn("umpires", stats)
        self.assertIn("managers", stats)
        self.assertIn("players", stats)
        self.assertIn("tournaments", stats)

    def test_teams_template_contains_search_and_list_heading(self):
        rendered = tech_team_app.load_template("teams.html")
        self.assertIn(b"Teams", rendered)
        self.assertIn(b"Search team", rendered)

    def test_teams_template_returns_option_loader_promise_for_edit_modal(self):
        rendered = tech_team_app.load_template("teams.html")
        self.assertIn(b"return Promise.all([fetch('/api/tournaments')", rendered)
        self.assertIn(b"confirm-dialog", rendered)

    def test_manager_template_contains_confirmation_popup_styles(self):
        rendered = tech_team_app.load_template("managers.html")
        self.assertIn(b"confirm-dialog", rendered)
        self.assertIn(b"confirm-btn-yes", rendered)
        self.assertIn(b".confirm-dialog", rendered)

    def test_public_registration_templates_load_for_invite_links(self):
        for name in ["register_manager.html", "register_umpire.html", "register_player.html", "register_tournament.html"]:
            rendered = tech_team_app.load_template(name, title="Registration")
            self.assertIn(b"Registration", rendered)

    def test_tech_team_helpers_return_joined_entity_rows(self):
        tech_team_app.setup_database()
        manager_rows = tech_team_app.get_manager_rows()
        team_rows = tech_team_app.get_team_rows()
        player_rows = tech_team_app.get_player_rows()
        umpire_rows = tech_team_app.get_umpire_rows()

        self.assertTrue(manager_rows)
        self.assertIn("team_name", manager_rows[0])
        self.assertTrue(team_rows)
        self.assertIn("manager_name", team_rows[0])
        self.assertTrue(player_rows)
        self.assertIn("tournament_name", player_rows[0])
        self.assertTrue(umpire_rows)
        self.assertIn("umpire_id", umpire_rows[0])

    def test_save_draws_persists_group_and_manual_matches(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_db = Path(tmp_dir) / "login.db"
            previous_db = tech_team_app.DB_PATH
            tech_team_app.DB_PATH = temp_db
            try:
                tech_team_app.setup_database()
                group_payload = {
                    "draws": [
                        {"team_a_id": "TE0001", "team_b_id": "TE0002", "group_name": "Group A", "match_number": 1, "match_status": "Scheduled", "umpire_id": "UM0001"}
                    ],
                    "scope": "group"
                }
                result = tech_team_app.save_draws("KT0001", group_payload)
                self.assertTrue(result["success"])
                with closing(sqlite3.connect(temp_db)) as db:
                    rows = db.execute("SELECT group_name, team_a_id, team_b_id FROM matches WHERE tournament_id = ?", ("KT0001",)).fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][0], "Group A")

                second_group_payload = {
                    "draws": [
                        {"team_a_id": "TE0005", "team_b_id": "TE0006", "group_name": "Group B", "match_number": 1, "match_status": "Scheduled", "umpire_id": "UM0001"}
                    ],
                    "scope": "group"
                }
                result = tech_team_app.save_draws("KT0001", second_group_payload)
                self.assertTrue(result["success"])
                with closing(sqlite3.connect(temp_db)) as db:
                    rows = db.execute("SELECT team_a_id, team_b_id FROM matches WHERE tournament_id = ? AND group_name != 'Manual'", ("KT0001",)).fetchall()
                self.assertEqual(len(rows), 2)

                manual_payload = {
                    "draws": [
                        {"team_a_id": "TE0003", "team_b_id": "TE0004", "group_name": "Manual", "stage_name": "Manual", "match_number": 1, "match_status": "Scheduled", "umpire_id": "UM0002"}
                    ],
                    "scope": "manual"
                }
                result = tech_team_app.save_draws("KT0001", manual_payload)
                self.assertTrue(result["success"])
                with closing(sqlite3.connect(temp_db)) as db:
                    manual_rows = db.execute("SELECT draw_id, group_name, stage_name, team_a_id, team_b_id, umpire_id FROM matches WHERE tournament_id = ? AND group_name = 'Manual'", ("KT0001",)).fetchall()
                self.assertEqual(len(manual_rows), 1)
                self.assertEqual(manual_rows[0][1], "Manual")
                self.assertEqual(manual_rows[0][2], "Manual")
                self.assertEqual(manual_rows[0][5], "UM0002")
                self.assertTrue(manual_rows[0][0].startswith("DW"))

                second_manual_payload = {
                    "draws": [
                        {"team_a_id": "TE0005", "team_b_id": "TE0006", "group_name": "Manual", "stage_name": "Manual", "match_number": 1, "match_status": "Scheduled", "umpire_id": "UM0003"}
                    ],
                    "scope": "manual"
                }
                result = tech_team_app.save_draws("KT0001", second_manual_payload)
                self.assertTrue(result["success"])
                with closing(sqlite3.connect(temp_db)) as db:
                    manual_rows = db.execute("SELECT team_a_id, team_b_id FROM matches WHERE tournament_id = ? AND group_name = 'Manual'", ("KT0001",)).fetchall()
                self.assertEqual(len(manual_rows), 2)
            finally:
                tech_team_app.DB_PATH = previous_db

    def test_save_draws_persists_knockout_rounds(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_db = Path(tmp_dir) / "login.db"
            previous_db = tech_team_app.DB_PATH
            tech_team_app.DB_PATH = temp_db
            try:
                tech_team_app.setup_database()
                result = tech_team_app.save_draws("KT0001", {
                    "scope": "knockout",
                    "draws": [
                        {"team_a_id": "TE0001", "team_b_id": "TE0002", "group_name": "Quarter Final", "stage_name": "Knockout Stage - Quarter Final", "match_number": 1, "match_status": "Scheduled"},
                        {"team_a_id": "TE0003", "team_b_id": "TE0004", "group_name": "Semi Final", "stage_name": "Knockout Stage - Semi Final", "match_number": 101, "match_status": "Scheduled"},
                    ],
                })
                self.assertTrue(result["success"])
                with closing(sqlite3.connect(temp_db)) as db:
                    rows = db.execute("SELECT stage_name, team_a_id, team_b_id FROM matches ORDER BY match_number").fetchall()
                self.assertEqual(rows, [
                    ("Knockout Stage - Quarter Final", "TE0001", "TE0002"),
                    ("Knockout Stage - Semi Final", "TE0003", "TE0004"),
                ])
            finally:
                tech_team_app.DB_PATH = previous_db


if __name__ == "__main__":
    unittest.main()

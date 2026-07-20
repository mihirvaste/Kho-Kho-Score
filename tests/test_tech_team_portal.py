import tempfile
import unittest
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

    def test_draws_template_contains_tabs_and_wizard_sections(self):
        rendered = tech_team_app.load_template("draws.html", title="Create Draws - Test Tournament", tournament_name="Test Tournament", tournament_id="KT0001")
        self.assertIn(b"Group Stage", rendered)
        self.assertIn(b"Knockout Stage", rendered)
        self.assertIn(b"League", rendered)
        self.assertIn(b"Tournament Configuration", rendered)
        self.assertIn(b"Generate Fixtures", rendered)
        self.assertIn(b"Live Bracket", rendered)
        self.assertIn(b"Save Draw", rendered)
        self.assertIn(b"Download PNG", rendered)

    def test_get_tournament_draws_returns_defaults(self):
        tech_team_app.setup_database()
        result = tech_team_app.get_tournament_draws("KT0001")
        self.assertEqual(result["tournament_id"], "KT0001")
        self.assertEqual(result["draws"], [])


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path


class KnockoutTemplateTests(unittest.TestCase):
    def test_knockout_template_has_three_step_wizard(self):
        template = Path("templates_techteam/draws.html").read_text(encoding="utf-8")

        self.assertIn("Step 1 — How many teams will play in knockouts?", template)
        self.assertIn("Step 2 — Bye / Buy Mode", template)
        self.assertIn("Step 3 — Create knockout draws", template)
        self.assertIn('id="knockout-team-count"', template)
        self.assertIn('id="knockout-bye-mode"', template)
        self.assertIn("Reset Winners", template)
        self.assertIn("Save Knockout Matches", template)
        self.assertIn("Save Overall Draw", template)
        self.assertIn("Are you sure you want to save it or not?", template)

    def test_draws_view_template_has_score_sheet_button(self):
        template = Path("templates_techteam/draws_view.html").read_text(encoding="utf-8")
        self.assertIn("Download Score Sheet", template)


if __name__ == "__main__":
    unittest.main()

import importlib
import shutil
import tempfile
import unittest
from pathlib import Path


class RegistrationDataTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "login.db"
        self.module = importlib.import_module("backend.app")
        self.module.DB_PATH = self.db_path
        self.module.setup_database()

    def tearDown(self):
        try:
            self.tempdir.cleanup()
        except Exception:
            pass
        try:
            self.db_path.unlink(missing_ok=True)
        except Exception:
            pass

    def test_public_registration_lists_seeded_tournaments_and_managers(self):
        tournaments = self.module.get_public_tournaments()
        managers = self.module.get_team_managers()

        self.assertTrue(tournaments)
        self.assertTrue(managers)
        self.assertTrue(any(item["tournament_id"] == "KT0001" for item in tournaments))
        self.assertEqual(managers[0]["manager_id"], "TM0001")

    def test_manager_crud_helpers_work_with_block_status(self):
        manager = self.module.create_manager({
            "name": "Asha Rao",
            "age": 30,
            "gender": "F",
            "kkfi_number": "KKFI3001",
            "phone": "9999999999",
            "email": "asha@example.com",
            "password": "secret123",
            "address": "Pune",
        })

        self.assertTrue(manager["manager_id"].startswith("TM"))
        self.assertFalse(manager["blocked"])

        self.module.set_manager_block_status(manager["manager_id"], True)
        self.assertTrue(self.module.get_manager_by_id(manager["manager_id"])["blocked"])

        blocked_managers = self.module.get_managers(blocked=True)
        self.assertTrue(any(item["manager_id"] == manager["manager_id"] for item in blocked_managers))

    def test_techteam_creation_persists_to_database(self):
        created = self.module.create_techteam({
            "name": "Nina Shah",
            "password": "secret456",
            "age": 27,
            "gender": "F",
            "kkfi_number": "KKFI4001",
            "phone": "7777777777",
            "email": "nina@example.com",
        })

        self.assertTrue(created["tt_id"].startswith("TT"))
        self.assertEqual(created["name"], "Nina Shah")
        self.assertEqual(self.module.get_techteam_by_id(created["tt_id"])["email"], "nina@example.com")

    def test_player_creation_persists_to_database(self):
        team = self.module.create_team({
            "team_name": "City Hawks",
            "tournament_id": "KT0001",
            "address": "Delhi",
            "manager_id": "TM0001",
        })
        created = self.module.create_player({
            "team_id": team["team_id"],
            "player_name": "Rohan Das",
            "kkfi_number": "KKFI5001",
            "chest_number": 12,
            "document_url": "https://example.com/rohan.pdf",
            "manager_id": "TM0001",
        })

        self.assertTrue(created["player_id"].startswith("P"))
        self.assertEqual(created["player_name"], "Rohan Das")
        self.assertEqual(self.module.get_player_by_id(created["player_id"])["team_id"], team["team_id"])


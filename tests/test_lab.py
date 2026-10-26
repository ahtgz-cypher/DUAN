import csv
import io
import tempfile
import unittest
import uuid
from pathlib import Path

from lab import SimulationLab


def request_id():
    return str(uuid.uuid4())


class SimulationLabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.lab = SimulationLab(Path(self.temp.name) / "db")

    def tearDown(self):
        self.temp.cleanup()

    def create(self, **overrides):
        config = {"request_id": request_id(), "name": "Kiểm thử", "seed": 78,
                  "account_count": 6, "creator_count": 2, "videos_per_creator": 2,
                  "views_per_account": 5, "like_probability": 1,
                  "follow_probability": 1, "delay_ms": 0}
        config.update(overrides)
        return self.lab.create_run(config)

    def complete(self, run_id):
        for _ in range(1000):
            summary = self.lab.summary(run_id)
            if summary["status"] == "completed":
                return summary
            self.lab.tick(run_id)
        self.fail("Thử nghiệm không hoàn tất trong giới hạn bước")

    def test_full_lifecycle_unique_edges_and_repeatable_results(self):
        first = self.create()
        initial = self.lab.public_state(first["id"])["selected"]
        self.assertEqual(initial["metrics"]["emails"], 0)
        self.assertEqual(initial["progress"]["total"], 6)
        self.complete(first["id"])
        result1 = self.lab.report(first["id"])["run"]
        self.assertEqual(result1["metrics"]["emails"], 6)
        self.assertEqual(result1["metrics"]["verified_emails"], 6)
        self.assertEqual(result1["metrics"]["social_accounts"], 6)
        self.assertEqual(result1["metrics"]["views"], 30)  # repeated views are allowed
        self.assertLessEqual(result1["metrics"]["likes"], 6 * 4)
        self.assertLessEqual(result1["metrics"]["follows"], 6 * 2)

        second = self.lab.repeat_run(first["id"], request_id())
        self.complete(second["id"])
        comparison = self.lab.compare(first["id"], second["id"])
        self.assertTrue(comparison["same_config"])
        self.assertTrue(comparison["same_result"])
        self.assertEqual(comparison["delta"]["views"], 0)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.lab.accounts(first["id"])["total"], 6)

    def test_fake_local_mailbox_and_csv_exports(self):
        run = self.create(account_count=2, views_per_account=1)
        for _ in range(2):
            self.lab.tick(run["id"])
        account = self.lab.accounts(run["id"])["accounts"][0]
        self.assertTrue(account["fake_email"].endswith("@mail.test"))
        box = self.lab.mailbox(run["id"], account["id"])
        self.assertTrue(box["simulation_only"])
        self.assertEqual(len(box["messages"]), 1)
        self.assertIn("nội bộ", box["messages"][0]["body"])
        self.complete(run["id"])
        rows = list(csv.reader(io.StringIO(self.lab.events_csv(run["id"]))))
        self.assertEqual(rows[0][2], "event")
        self.assertGreater(len(rows), 1)
        accounts = list(csv.DictReader(io.StringIO(self.lab.accounts_csv(run["id"]))))
        self.assertEqual(len(accounts), 2)

    def test_pause_resume_and_restart_recovery(self):
        run = self.create(account_count=3)
        self.lab.tick(run["id"])
        paused = self.lab.pause_run(run["id"])
        self.assertEqual(paused["status"], "paused")
        restarted = SimulationLab(Path(self.temp.name) / "db")
        self.assertEqual(restarted.summary(run["id"])["status"], "paused")
        restarted.resume_run(run["id"])
        self.complete_with(restarted, run["id"])
        self.assertEqual(restarted.summary(run["id"])["metrics"]["emails"], 3)

    def complete_with(self, lab, run_id):
        for _ in range(1000):
            if lab.summary(run_id)["status"] == "completed":
                return
            lab.tick(run_id)
        self.fail("Thử nghiệm không hoàn tất trong giới hạn bước")

    def test_cancel_keeps_partial_metrics_and_request_is_idempotent(self):
        rid = request_id()
        first = self.create(request_id=rid, account_count=2)
        duplicate = self.create(request_id=rid, account_count=2)
        self.assertEqual(first["id"], duplicate["id"])
        self.lab.tick(first["id"])
        canceled = self.lab.cancel_run(first["id"])
        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual(canceled["metrics"]["emails"], 1)  # one created, one queued
        self.assertFalse(self.lab.tick(first["id"]))

    def test_unknown_external_inputs_and_invalid_limits_are_rejected(self):
        for invalid in (
            {"target_url": "https://example.com"}, {"cookie": "secret"},
            {"account_count": 501}, {"seed": -1}, {"like_probability": 1.5},
            {"request_id": "not-a-uuid"}, {"name": ""},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.create(**invalid)

    def test_cannot_compare_incomplete_runs_and_run_data_is_isolated(self):
        a = self.create(account_count=1)
        b = self.create(account_count=1, seed=79)
        with self.assertRaises(ValueError):
            self.lab.compare(a["id"], b["id"])
        self.complete(a["id"])
        self.complete(b["id"])
        self.assertNotEqual(self.lab.public_state(a["id"])["selected"]["fingerprint"],
                            self.lab.public_state(b["id"])["selected"]["fingerprint"])
        self.assertEqual(self.lab.accounts(a["id"])["total"], 1)
        self.assertTrue(self.lab.report(a["id"])["simulation_only"])


if __name__ == "__main__":
    unittest.main()

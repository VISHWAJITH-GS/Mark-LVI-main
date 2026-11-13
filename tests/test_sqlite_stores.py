"""
Tests for SQLite stores (Task 10):
- actions/reminder.py: SQLite DB transactions, single authoritative notifier, auto-migration.
- core/skill_learning/skill_storage.py: SQLite skills manifest, CRUD operations, concurrency, auto-migration from JSON.
"""

from datetime import datetime, timedelta
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

import actions.reminder as reminder_mod
from core.skill_learning import skill_storage
from core.skill_learning.models import SkillDefinition, SkillStatus


class TestReminderSQLiteStore(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_reminder_sqlite_")
        self.patch_dir = patch.object(reminder_mod, "_reminders_dir", return_value=reminder_mod.Path(self.test_dir))
        self.patch_dir.start()

    def tearDown(self):
        self.patch_dir.stop()
        try:
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:
            pass

    def test_json_to_sqlite_auto_migration(self):
        """Verify legacy reminders_db.json is automatically imported into SQLite."""
        json_file = reminder_mod.Path(self.test_dir) / "reminders_db.json"
        legacy_data = [
            {
                "id": "rem_migrated_1",
                "task_name": "task_1",
                "message": "Drink water",
                "target_time": "2026-11-13T20:00:00",
                "status": "pending",
                "recurrence": "none",
                "created_at": "2026-11-13T18:00:00"
            }
        ]
        json_file.write_text(json.dumps(legacy_data), encoding="utf-8")

        # Load DB should trigger init and migration
        items = reminder_mod._load_db()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "rem_migrated_1")
        self.assertEqual(items[0]["message"], "Drink water")

        # Check SQLite table directly
        db_path = reminder_mod._db_path()
        self.assertTrue(db_path.exists())
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, message, status FROM reminders WHERE id = 'rem_migrated_1'")
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "rem_migrated_1")
            self.assertEqual(row[1], "Drink water")
        finally:
            conn.close()

    def test_reminder_crud(self):
        """Verify create, list, complete, delete with SQLite store."""
        target = datetime.now() + timedelta(hours=2)
        with patch.object(reminder_mod, "_schedule_task", return_value=True):
            res = reminder_mod.create_reminder("Test SQLite Reminder", target, recurrence="none")
            self.assertTrue(res.success)
            self.assertIn("reminder", res.data)
            rem_id = res.data["reminder"]["id"]

        # List
        res_list = reminder_mod.list_reminders(scope="all")
        self.assertTrue(res_list.success)
        items = res_list.data.get("reminders", [])
        found = [r for r in items if r["id"] == rem_id]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["message"], "Test SQLite Reminder")

        # Complete
        comp_res = reminder_mod.complete_reminder(rem_id)
        self.assertTrue(comp_res.success)

        res_after = reminder_mod.list_reminders(scope="all")
        items_after = res_after.data.get("reminders", [])
        completed_rem = [r for r in items_after if r["id"] == rem_id][0]
        self.assertEqual(completed_rem["status"], "completed")

        # Delete
        with patch.object(reminder_mod, "_unschedule_task", return_value=True):
            del_res = reminder_mod.delete_reminder(rem_id)
            self.assertTrue(del_res.success)

        res_final = reminder_mod.list_reminders(scope="all")
        items_final = res_final.data.get("reminders", [])
        self.assertEqual(len([r for r in items_final if r["id"] == rem_id]), 0)

    def test_single_authoritative_notifier_generation(self):
        """Verify that _ensure_notifier_script creates a single authoritative notifier script."""
        script_path = reminder_mod._ensure_notifier_script()
        self.assertTrue(script_path.exists())
        self.assertEqual(script_path.name, "reminder_notifier.py")
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("JARVIS Authoritative Reminder Notifier", content)
        self.assertIn("--id", content)
        self.assertIn("--db", content)


class TestSkillsSQLiteStore(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_skills_sqlite_")
        self.p_skills_dir = patch.object(skill_storage, "SKILLS_DIR", reminder_mod.Path(self.test_dir))
        self.p_manifest_path = patch.object(skill_storage, "MANIFEST_PATH", reminder_mod.Path(self.test_dir) / "manifest.json")
        self.p_db_path = patch.object(skill_storage, "DB_PATH", reminder_mod.Path(self.test_dir) / "skills_manifest.db")
        self.p_skills_dir.start()
        self.p_manifest_path.start()
        self.p_db_path.start()

    def tearDown(self):
        self.p_skills_dir.stop()
        self.p_manifest_path.stop()
        self.p_db_path.stop()
        try:
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:
            pass

    def test_skills_manifest_json_auto_migration(self):
        """Verify legacy manifest.json is auto-migrated into skills_manifest.db."""
        (skill_storage.SKILLS_DIR).mkdir(parents=True, exist_ok=True)
        legacy_manifest = {
            "version": "1.0.0",
            "updated_at": "2026-11-13T10:00:00",
            "skills": {
                "skill_legacy_1": {
                    "id": "skill_legacy_1",
                    "name": "Legacy Skill",
                    "category": "application",
                    "application": "notepad",
                    "version": "1.0.0",
                    "status": "approved",
                    "permissions": "normal",
                    "file": "skills/application_skills/skill_legacy_1.json",
                    "updated_at": "2026-11-13T10:00:00"
                }
            }
        }
        skill_storage.MANIFEST_PATH.write_text(json.dumps(legacy_manifest), encoding="utf-8")

        # Initialize / read manifest
        manifest = skill_storage._read_manifest()
        self.assertIn("skill_legacy_1", manifest.get("skills", {}))
        self.assertEqual(manifest["skills"]["skill_legacy_1"]["application"], "notepad")

        # Check SQLite db created
        self.assertTrue(skill_storage.DB_PATH.exists())
        conn = sqlite3.connect(str(skill_storage.DB_PATH))
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, name, application FROM skills WHERE id = 'skill_legacy_1'")
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "skill_legacy_1")
            self.assertEqual(row[1], "Legacy Skill")
            self.assertEqual(row[2], "notepad")
        finally:
            conn.close()

    def test_skill_save_load_delete(self):
        """Verify save_skill, load_skill, and delete_skill via SQLite."""
        skill = SkillDefinition(
            id="skill_test_crud",
            name="Test CRUD Skill",
            description="Testing CRUD",
            category="workflow",
            application="system",
            version="1.0.0",
            status="draft",
            permissions="normal"
        )
        saved_path = skill_storage.save_skill(skill)
        self.assertTrue(saved_path.exists())

        # Load
        loaded = skill_storage.load_skill("skill_test_crud")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "Test CRUD Skill")
        self.assertEqual(loaded.category, "workflow")

        # Update status
        updated = skill_storage.update_skill_status("skill_test_crud", SkillStatus.VALIDATED)
        self.assertTrue(updated)
        loaded2 = skill_storage.load_skill("skill_test_crud")
        self.assertEqual(loaded2.status, "validated")

        # Delete
        deleted = skill_storage.delete_skill("skill_test_crud")
        self.assertTrue(deleted)
        self.assertIsNone(skill_storage.load_skill("skill_test_crud"))

    def test_concurrent_writes(self):
        """Verify concurrent skill saves do not corrupt SQLite database."""
        errors = []

        def worker(worker_id: int):
            try:
                for i in range(5):
                    s = SkillDefinition(
                        id=f"concurrent_{worker_id}_{i}",
                        name=f"Concurrent {worker_id}-{i}",
                        description="Concurrency test",
                        category="application",
                        application="app",
                        version="1.0.0",
                        status="draft",
                        permissions="normal"
                    )
                    skill_storage.save_skill(s)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors during concurrent writes: {errors}")
        skills = skill_storage.list_skills(category="application")
        self.assertEqual(len(skills), 20)


if __name__ == "__main__":
    unittest.main()

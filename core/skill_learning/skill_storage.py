"""
core/skill_learning/skill_storage.py — Persistent Storage & Manifest Manager for Learned Skills.

Saves, loads, versions, lists, and removes declarative skill definitions using
an SQLite database (skills_manifest.db) with automatic migration from legacy manifest.json.
Folders structure:
  skills/
    skills_manifest.db
    manifest.json (backward-compatibility mirror / legacy)
    application_skills/
    workflow_skills/
    user_skills/
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from threading import RLock
from typing import Dict, List, Optional

from core.skill_learning.models import SkillDefinition, SkillStatus


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


BASE_DIR = get_base_dir()
SKILLS_DIR = BASE_DIR / "skills"
MANIFEST_PATH = SKILLS_DIR / "manifest.json"
DB_PATH = SKILLS_DIR / "skills_manifest.db"

_storage_lock = RLock()


def _ensure_directories() -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    (SKILLS_DIR / "application_skills").mkdir(parents=True, exist_ok=True)
    (SKILLS_DIR / "workflow_skills").mkdir(parents=True, exist_ok=True)
    (SKILLS_DIR / "user_skills").mkdir(parents=True, exist_ok=True)
    (SKILLS_DIR / ".backups").mkdir(parents=True, exist_ok=True)
    _init_sqlite_db()


def _init_sqlite_db() -> None:
    """Initialize SQLite database for skills, create schema, and migrate from manifest.json if empty."""
    with _storage_lock:
        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS skills (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        category TEXT NOT NULL,
                        application TEXT,
                        version TEXT NOT NULL,
                        status TEXT NOT NULL,
                        permissions TEXT NOT NULL,
                        file TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_validated_at TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status)")

                # Auto-migration from legacy manifest.json
                if MANIFEST_PATH.exists():
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM skills")
                    count = cur.fetchone()[0]
                    if count == 0:
                        try:
                            legacy = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
                            skills_dict = legacy.get("skills", {})
                            for sid, meta in skills_dict.items():
                                cur.execute("""
                                    INSERT OR REPLACE INTO skills
                                    (id, name, category, application, version, status, permissions, file, updated_at, last_validated_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    meta.get("id", sid),
                                    meta.get("name", ""),
                                    meta.get("category", "user"),
                                    meta.get("application", ""),
                                    meta.get("version", "1.0.0"),
                                    meta.get("status", "draft"),
                                    meta.get("permissions", "normal"),
                                    meta.get("file", ""),
                                    meta.get("updated_at", datetime.now().isoformat()),
                                    meta.get("last_validated_at")
                                ))
                        except Exception as me:
                            print(f"[SkillStorage] ⚠️ Migration from manifest.json failed: {me}")
        finally:
            conn.close()


def _sync_legacy_manifest() -> None:
    """Sync state to legacy manifest.json for backward compatibility."""
    try:
        manifest_data = {
            "version": "1.0.0",
            "updated_at": datetime.now().isoformat(),
            "skills": {}
        }
        with _storage_lock:
            conn = sqlite3.connect(str(DB_PATH), timeout=15)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM skills")
                for r in cur.fetchall():
                    row = dict(r)
                    manifest_data["skills"][row["id"]] = row
            finally:
                conn.close()
            MANIFEST_PATH.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _read_manifest() -> dict:
    """Read skills manifest dictionary, backed by SQLite."""
    _ensure_directories()
    manifest_data = {
        "version": "1.0.0",
        "updated_at": datetime.now().isoformat(),
        "skills": {}
    }
    with _storage_lock:
        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM skills")
            for r in cur.fetchall():
                row = dict(r)
                manifest_data["skills"][row["id"]] = row
        finally:
            conn.close()
    return manifest_data


def _write_manifest(data: dict) -> None:
    """Write/replace manifest dictionary into SQLite and sync to manifest.json."""
    _ensure_directories()
    with _storage_lock:
        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM skills")
                for sid, meta in data.get("skills", {}).items():
                    cur.execute("""
                        INSERT OR REPLACE INTO skills
                        (id, name, category, application, version, status, permissions, file, updated_at, last_validated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        meta.get("id", sid),
                        meta.get("name", ""),
                        meta.get("category", "user"),
                        meta.get("application", ""),
                        meta.get("version", "1.0.0"),
                        meta.get("status", "draft"),
                        meta.get("permissions", "normal"),
                        meta.get("file", ""),
                        meta.get("updated_at", datetime.now().isoformat()),
                        meta.get("last_validated_at")
                    ))
        finally:
            conn.close()
    _sync_legacy_manifest()


def _get_category_folder(category: str) -> Path:
    cat = (category or "user").lower().strip()
    if cat == "application":
        return SKILLS_DIR / "application_skills"
    elif cat == "workflow":
        return SKILLS_DIR / "workflow_skills"
    return SKILLS_DIR / "user_skills"


def save_skill(skill: SkillDefinition) -> Path:
    """Save a declarative SkillDefinition to file and update SQLite manifest."""
    _ensure_directories()
    folder = _get_category_folder(skill.category)
    file_path = folder / f"{skill.id}.json"

    # Backup if modifying existing
    if file_path.exists():
        backup_skill(skill.id)

    with _storage_lock:
        file_path.write_text(json.dumps(skill.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            rel_file = str(file_path.relative_to(BASE_DIR)).replace("\\", "/")
        except Exception:
            rel_file = str(file_path).replace("\\", "/")
        now_str = datetime.now().isoformat()

        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO skills
                    (id, name, category, application, version, status, permissions, file, updated_at, last_validated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    skill.id,
                    skill.name,
                    skill.category,
                    skill.application,
                    skill.version,
                    skill.status,
                    skill.permissions,
                    rel_file,
                    now_str,
                    getattr(skill, "last_validated_at", None)
                ))
        finally:
            conn.close()

    _sync_legacy_manifest()
    print(f"[Storage] Skill saved: {skill.id}")
    return file_path


def load_skill(skill_id: str) -> Optional[SkillDefinition]:
    """Load a skill definition by ID."""
    _ensure_directories()
    with _storage_lock:
        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("SELECT file FROM skills WHERE id = ?", (skill_id,))
            row = cur.fetchone()
            if row and row["file"]:
                p = Path(row["file"])
                if not p.is_absolute():
                    p = BASE_DIR / p
                if p.exists():
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        return SkillDefinition.from_dict(data)
                    except Exception as e:
                        print(f"[SkillStorage] Error loading {p}: {e}")
        finally:
            conn.close()

    # Fallback scan across folders
    for folder in ("application_skills", "workflow_skills", "user_skills"):
        p = SKILLS_DIR / folder / f"{skill_id}.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return SkillDefinition.from_dict(data)
            except Exception:
                pass
    return None


def list_skills(
    category: Optional[str] = None,
    status: Optional[str] = None,
) -> list[SkillDefinition]:
    """Retrieve all stored skills matching optional category or status filters."""
    _ensure_directories()
    skills = []
    with _storage_lock:
        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            query = "SELECT id, category, status FROM skills"
            params = []
            clauses = []
            if category:
                clauses.append("LOWER(category) = LOWER(?)")
                params.append(category)
            if status:
                clauses.append("LOWER(status) = LOWER(?)")
                params.append(status)
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            cur.execute(query, params)
            rows = cur.fetchall()
            skill_ids = [r["id"] for r in rows]
        finally:
            conn.close()

    for sid in skill_ids:
        skill = load_skill(sid)
        if skill:
            skills.append(skill)
    return skills


def update_skill_status(skill_id: str, new_status: str | SkillStatus) -> bool:
    """Update status (draft -> validated -> approved -> disabled)."""
    st_val = new_status.value if isinstance(new_status, SkillStatus) else str(new_status).lower()
    skill = load_skill(skill_id)
    if not skill:
        return False
    skill.status = st_val
    if st_val == SkillStatus.VALIDATED.value:
        skill.last_validated_at = datetime.now().isoformat()
    save_skill(skill)
    return True


def delete_skill(skill_id: str) -> bool:
    """Permanently remove a skill and update SQLite manifest."""
    _ensure_directories()
    removed = False
    with _storage_lock:
        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("SELECT file FROM skills WHERE id = ?", (skill_id,))
            row = cur.fetchone()
            if row and row["file"]:
                p = Path(row["file"])
                if not p.is_absolute():
                    p = BASE_DIR / p
                if p.exists():
                    try:
                        p.unlink()
                        removed = True
                    except Exception as e:
                        print(f"[SkillStorage] Could not unlink {p}: {e}")
            with conn:
                cur.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
                if cur.rowcount > 0:
                    removed = True
        finally:
            conn.close()

    if removed:
        _sync_legacy_manifest()
    return removed


def backup_skill(skill_id: str) -> Optional[Path]:
    """Create a timestamped backup in skills/.backups/."""
    skill = load_skill(skill_id)
    if not skill:
        return None
    backup_folder = SKILLS_DIR / ".backups" / skill_id
    backup_folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_folder / f"{skill.id}_v{skill.version}_{ts}.json"
    with _storage_lock:
        backup_path.write_text(json.dumps(skill.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return backup_path


class SkillStorage:
    """Object-oriented wrapper around functional skill storage APIs."""
    save_skill = staticmethod(save_skill)
    load_skill = staticmethod(load_skill)
    list_skills = staticmethod(list_skills)
    delete_skill = staticmethod(delete_skill)
    update_skill_status = staticmethod(update_skill_status)
    backup_skill = staticmethod(backup_skill)

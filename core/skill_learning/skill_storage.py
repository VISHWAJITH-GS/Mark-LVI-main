"""
core/skill_learning/skill_storage.py — Persistent Storage & Manifest Manager for Learned Skills.

Saves, loads, versions, lists, and removes declarative skill definitions under:
  skills/
    manifest.json
    application_skills/
    workflow_skills/
    user_skills/
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
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

_storage_lock = RLock()


def _ensure_directories() -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    (SKILLS_DIR / "application_skills").mkdir(parents=True, exist_ok=True)
    (SKILLS_DIR / "workflow_skills").mkdir(parents=True, exist_ok=True)
    (SKILLS_DIR / "user_skills").mkdir(parents=True, exist_ok=True)
    (SKILLS_DIR / ".backups").mkdir(parents=True, exist_ok=True)
    with _storage_lock:
        if not MANIFEST_PATH.exists():
            MANIFEST_PATH.write_text(
                json.dumps({"version": "1.0.0", "updated_at": datetime.now().isoformat(), "skills": {}}, indent=2),
                encoding="utf-8",
            )


def _read_manifest() -> dict:
    _ensure_directories()
    with _storage_lock:
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"version": "1.0.0", "updated_at": datetime.now().isoformat(), "skills": {}}


def _write_manifest(data: dict) -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    with _storage_lock:
        MANIFEST_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_category_folder(category: str) -> Path:
    cat = (category or "user").lower().strip()
    if cat == "application":
        return SKILLS_DIR / "application_skills"
    elif cat == "workflow":
        return SKILLS_DIR / "workflow_skills"
    return SKILLS_DIR / "user_skills"


def save_skill(skill: SkillDefinition) -> Path:
    """Save a declarative SkillDefinition and update manifest."""
    _ensure_directories()
    folder = _get_category_folder(skill.category)
    file_path = folder / f"{skill.id}.json"

    # Backup if modifying existing
    if file_path.exists():
        backup_skill(skill.id)

    with _storage_lock:
        file_path.write_text(json.dumps(skill.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    manifest = _read_manifest()
    manifest["skills"][skill.id] = {
        "id": skill.id,
        "name": skill.name,
        "category": skill.category,
        "application": skill.application,
        "version": skill.version,
        "status": skill.status,
        "permissions": skill.permissions,
        "file": str(file_path.relative_to(BASE_DIR)).replace("\\", "/"),
        "updated_at": datetime.now().isoformat(),
    }
    manifest["updated_at"] = datetime.now().isoformat()
    _write_manifest(manifest)
    return file_path


def load_skill(skill_id: str) -> Optional[SkillDefinition]:
    """Load a skill definition by ID."""
    manifest = _read_manifest()
    meta = manifest.get("skills", {}).get(skill_id)
    if meta and "file" in meta:
        p = BASE_DIR / meta["file"]
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return SkillDefinition.from_dict(data)
            except Exception as e:
                print(f"[SkillStorage] Error loading {p}: {e}")

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
    manifest = _read_manifest()
    skills = []
    for sid in manifest.get("skills", {}):
        skill = load_skill(sid)
        if not skill:
            continue
        if category and skill.category.lower() != category.lower():
            continue
        if status and skill.status.lower() != status.lower():
            continue
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
    """Permanently remove a skill and update manifest."""
    manifest = _read_manifest()
    meta = manifest.get("skills", {}).get(skill_id)
    removed = False
    if meta and "file" in meta:
        p = BASE_DIR / meta["file"]
        if p.exists():
            try:
                p.unlink()
                removed = True
            except Exception as e:
                print(f"[SkillStorage] Could not unlink {p}: {e}")

    # Remove from manifest
    with _storage_lock:
        if skill_id in manifest.get("skills", {}):
            del manifest["skills"][skill_id]
            manifest["updated_at"] = datetime.now().isoformat()
            _write_manifest(manifest)
            removed = True
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

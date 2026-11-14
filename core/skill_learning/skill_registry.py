"""
core/skill_learning/skill_registry.py — In-Memory Registry & Semantic Skill Discovery.

Maintains active cache of learned skills and provides:
- Fast lookup by ID or application.
- Semantic matching between natural language user requests and learned skills.
- Automatic synchronization with persistent storage.
- Tool schema generation for exposing approved skills to Gemini Live & Orchestrator.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from core.skill_learning import skill_storage
from core.skill_learning.models import SkillDefinition, SkillStatus

logger = logging.getLogger(__name__)


class SkillRegistry:
    """In-memory cache and semantic discovery engine for learned skills."""

    _instance: Optional[SkillRegistry] = None

    def __init__(self):
        self._skills: dict[str, SkillDefinition] = {}
        self.reload()

    @classmethod
    def get_instance(cls) -> SkillRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def reload(self) -> None:
        """Reload all skills from persistent storage."""
        stored = skill_storage.list_skills()
        self._skills = {s.id: s for s in stored}
        logger.info("SkillRegistry loaded %d skills from storage.", len(self._skills))

    def get_skill(self, skill_id: str) -> Optional[SkillDefinition]:
        """Fetch a skill by ID."""
        if skill_id not in self._skills:
            # Check storage directly in case it was newly saved on disk
            loaded = skill_storage.load_skill(skill_id)
            if loaded:
                self._skills[loaded.id] = loaded
        return self._skills.get(skill_id)

    def get_all_skills(self, approved_only: bool = False) -> list[SkillDefinition]:
        """Return all skills, optionally filtered to only approved ones."""
        skills = list(self._skills.values())
        if approved_only:
            skills = [s for s in skills if s.status == SkillStatus.APPROVED.value]
        return skills

    def save_and_register(self, skill: SkillDefinition) -> None:
        """Persist skill to disk and update registry cache."""
        skill_storage.save_skill(skill)
        self._skills[skill.id] = skill

    def remove_skill(self, skill_id: str) -> bool:
        """Remove skill from disk and cache."""
        success = skill_storage.delete_skill(skill_id)
        if skill_id in self._skills:
            del self._skills[skill_id]
        return success

    def find_matching_skill(self, query: str, threshold: float = 0.4, approved_only: bool = False) -> Tuple[Optional[SkillDefinition], float, dict]:
        """Match natural language user input against learned skills using semantic heuristics.
        
        Returns:
            (best_skill, match_score, extracted_parameters)
        """
        q = query.lower().strip()
        q_norm = re.sub(r"[_\-\s]+", " ", q).strip()
        best_skill: Optional[SkillDefinition] = None
        best_score = 0.0
        best_params: dict = {}

        candidate_skills = self.get_all_skills(approved_only=approved_only)
        if not candidate_skills and approved_only:
            # Fallback to any available skill
            candidate_skills = self.get_all_skills(approved_only=False)

        for skill in candidate_skills:
            score = 0.0
            id_norm = re.sub(r"[_\-\s]+", " ", (skill.id or "").lower()).strip()
            name_norm = re.sub(r"[_\-\s]+", " ", (skill.name or "").lower()).strip()

            # 1. Exact or normalized name / ID match
            if q == (skill.id or "").lower() or q == (skill.name or "").lower():
                score = 1.0
            elif q_norm == id_norm or q_norm == name_norm:
                score = 0.95
            elif id_norm in q_norm or name_norm in q_norm or q_norm in id_norm or q_norm in name_norm:
                score = max(score, 0.8)
            elif (skill.name and skill.name.lower() in q) or (skill.id and skill.id in q):
                score = max(score, 0.7)

            # 2. Application match
            if skill.application and skill.application.lower() in q:
                score += 0.3

            # 3. Intent examples match
            for intent in skill.intent_examples:
                intent_words = set(intent.lower().split())
                q_words = set(q.split())
                overlap = len(intent_words.intersection(q_words)) / max(len(intent_words), 1)
                if overlap > 0.5:
                    score = max(score, overlap * 0.9)

            # 4. Description keywords
            desc_words = set(skill.description.lower().split())
            q_words = set(q.split())
            desc_overlap = len(desc_words.intersection(q_words)) / max(len(q_words), 1)
            score += desc_overlap * 0.2

            if score > best_score:
                best_score = score
                best_skill = skill

        if best_skill and best_score >= threshold:
            # Extract basic parameters from query
            extracted = self._extract_parameters_from_query(best_skill, query)
            return best_skill, round(best_score, 2), extracted

        return None, 0.0, {}

    def _extract_parameters_from_query(self, skill: SkillDefinition, query: str) -> dict:
        """Extract argument values from query based on parameter types."""
        extracted: dict = {}
        for param in skill.parameters:
            if param.type == "file_path":
                # Look for file paths (e.g. C:\... or filename.ext)
                match = re.search(r'([A-Za-z]:\\[^\s"\'\<\>]+|\b[\w\-]+\.[a-zA-Z0-9]{2,4}\b)', query)
                if match:
                    extracted[param.name] = match.group(1)
            elif param.type == "url":
                match = re.search(r'https?://[^\s]+', query)
                if match:
                    extracted[param.name] = match.group(0)
        return extracted


def get_default_skill_registry() -> SkillRegistry:
    return SkillRegistry.get_instance()


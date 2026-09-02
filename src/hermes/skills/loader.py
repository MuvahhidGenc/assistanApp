"""Skill/procedure hints — not hardcoded workflows (PHASE 3 stub)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_SKILLS_DIR = Path(__file__).resolve().parent / "procedures"


@dataclass
class SkillHint:
    skill_id: str
    title: str
    preferred_tools: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    security_level: str = "normal"
    validation_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "title": self.title,
            "preferred_tools": self.preferred_tools,
            "hints": self.hints,
            "security_level": self.security_level,
            "validation_rules": self.validation_rules,
        }


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, yaml.YAMLError):
        pass
    return {}


def load_all_skills() -> list[SkillHint]:
    skills: list[SkillHint] = []
    if not _SKILLS_DIR.exists():
        return skills
    for path in sorted(_SKILLS_DIR.glob("*.yaml")):
        data = _load_yaml(path)
        if not data.get("id"):
            continue
        skills.append(
            SkillHint(
                skill_id=str(data["id"]),
                title=str(data.get("title") or data["id"]),
                preferred_tools=[str(t) for t in (data.get("preferred_tools") or [])],
                hints=[str(h) for h in (data.get("hints") or [])],
                security_level=str(data.get("security_level") or "normal"),
                validation_rules=[str(r) for r in (data.get("validation_rules") or [])],
            )
        )
    return skills


def match_skills_for_goal(user_goal: str) -> list[SkillHint]:
    goal = (user_goal or "").casefold()
    if not goal:
        return []
    matched: list[SkillHint] = []
    for path in sorted(_SKILLS_DIR.glob("*.yaml")):
        data = _load_yaml(path)
        patterns = [str(p).casefold() for p in (data.get("goal_patterns") or [])]
        if any(pattern in goal for pattern in patterns):
            skill_id = str(data.get("id") or path.stem)
            matched.append(
                SkillHint(
                    skill_id=skill_id,
                    title=str(data.get("title") or skill_id),
                    preferred_tools=[str(t) for t in (data.get("preferred_tools") or [])],
                    hints=[str(h) for h in (data.get("hints") or [])],
                    security_level=str(data.get("security_level") or "normal"),
                    validation_rules=[str(r) for r in (data.get("validation_rules") or [])],
                )
            )
    return matched

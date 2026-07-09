"""Load the candidate profile (config + resume + context) into one object."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import load_yaml, ROOT


@dataclass
class Profile:
    cfg: dict
    resume_text: str = ""
    context_text: str = ""

    # convenience accessors -------------------------------------------------
    @property
    def name(self) -> str:
        return self.cfg.get("name", "")

    @property
    def target_roles(self) -> list[str]:
        return [r.lower() for r in self.cfg.get("target_roles", [])]

    @property
    def reject_role_keywords(self) -> list[str]:
        return [r.lower() for r in self.cfg.get("reject_role_keywords", [])]

    @property
    def too_senior_keywords(self) -> list[str]:
        return [r.lower() for r in self.cfg.get("too_senior_keywords", [])]

    @property
    def locations(self) -> list[str]:
        return [r.lower() for r in self.cfg.get("locations", [])]

    @property
    def skills_core(self) -> list[str]:
        return [s.lower() for s in self.cfg.get("skills_core", [])]

    @property
    def skills_secondary(self) -> list[str]:
        return [s.lower() for s in self.cfg.get("skills_secondary", [])]

    @property
    def max_years_required(self) -> int:
        return int((self.cfg.get("dealbreakers") or {}).get("max_years_required", 6))

    def summary_for_llm(self) -> str:
        """Compact profile block handed to the LLM scorer/personalizer."""
        c = self.cfg
        parts = [
            f"Name: {c.get('name')}",
            f"Headline: {c.get('headline')}",
            f"Experience level: {c.get('experience_level')} (~{c.get('years_experience')} yrs)",
            f"Target roles: {', '.join(c.get('target_roles', []))}",
            f"Core skills: {', '.join(c.get('skills_core', []))}",
            f"Locations: {', '.join(c.get('locations', []))}; remote pref: {c.get('remote_pref')}",
            f"Salary: {(c.get('salary') or {}).get('min_lpa')}-{(c.get('salary') or {}).get('target_lpa')} LPA INR",
            f"Strengths: {c.get('strengths', '').strip()}",
            f"Notable projects: {c.get('notable_projects', '').strip()}",
        ]
        return "\n".join(p for p in parts if p)


def load_profile(cfg_path: str = "config/profile.yaml") -> Profile:
    """Prefer local files (dev); if they're absent (e.g. code-only public repo in the
    cloud), load the profile from Neon's profile_docs table instead."""
    def _read(p: str) -> str:
        fp = ROOT / p
        return fp.read_text() if fp.exists() else ""

    local_cfg = ROOT / cfg_path
    if local_cfg.exists():
        return Profile(
            cfg=load_yaml(cfg_path),
            resume_text=_read("data/profile/resume.txt"),
            context_text=_read("data/profile/context.md"),
        )

    # Fallback: load from Neon (profile PII kept out of the repo).
    import os
    import yaml
    if os.environ.get("DATABASE_URL"):
        from .db import DB
        db = DB()
        try:
            py = db.get_profile_doc("profile_yaml")
            cfg = yaml.safe_load(py) if py else load_yaml("config/profile.example.yaml")
            return Profile(cfg=cfg,
                           resume_text=db.get_profile_doc("resume") or "",
                           context_text=db.get_profile_doc("context") or "")
        finally:
            db.close()

    # Last resort: the example template.
    return Profile(cfg=load_yaml("config/profile.example.yaml"))

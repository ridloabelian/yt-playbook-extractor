"""YAML Persona Configuration loader and starter generator."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class SpeakerRule:
    label: str
    role: str  # core | co_mentor | guest
    patterns: list[str] = field(default_factory=list)


@dataclass
class DomainSpec:
    id: str
    title: str
    keywords: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=lambda: ["2", "3", "4"])


@dataclass
class PersonaConfig:
    name: str
    handle: str
    domain_focus: str = "methodology"
    language: str = "en"
    aliases: list[str] = field(default_factory=list)
    collab_queries: list[str] = field(default_factory=list)
    excluded_channels: list[str] = field(default_factory=list)
    excluded_video_ids: list[str] = field(default_factory=list)
    speakers: list[SpeakerRule] = field(default_factory=list)
    extraction_sections: list[str] = field(
        default_factory=lambda: [
            "1. Identity & Speaker Attribution",
            "2. Core Concepts & Mental Models",
            "3. Screen Visuals, Diagrams & Exact Parameters",
            "4. Execution Workflows & Decision Rules",
            "5. Macro / Long-Horizon Framework",
            "6. Risk Management & Behavioral Discipline",
            "7. Key Quotes & Timestamps",
        ]
    )
    domains: list[DomainSpec] = field(default_factory=list)
    entity_standardizations: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PersonaConfig":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        speakers = [
            SpeakerRule(
                label=s["label"],
                role=s.get("role", "co_mentor"),
                patterns=list(s.get("patterns", [])),
            )
            for s in raw.get("speakers", [])
        ]
        domains = [
            DomainSpec(
                id=d["id"],
                title=d["title"],
                keywords=list(d.get("keywords", [])),
                sections=[str(x) for x in d.get("sections", ["2", "3", "4"])],
            )
            for d in raw.get("domains", [])
        ]
        return cls(
            name=raw["name"],
            handle=raw["handle"],
            domain_focus=raw.get("domain_focus", "methodology"),
            language=raw.get("language", "en"),
            aliases=list(raw.get("aliases", [])),
            collab_queries=list(raw.get("collab_queries", [])),
            excluded_channels=list(raw.get("excluded_channels", [])),
            excluded_video_ids=list(raw.get("excluded_video_ids", [])),
            speakers=speakers,
            extraction_sections=list(raw.get("extraction_sections", cls.__dataclass_fields__["extraction_sections"].default_factory())),
            domains=domains,
            entity_standardizations=list(raw.get("entity_standardizations", [])),
        )


def write_starter_config(path: str | Path, name: str, handle: str) -> Path:
    data = {
        "name": name,
        "handle": handle,
        "domain_focus": "technical methodology",
        "language": "en",
        "aliases": [name],
        "collab_queries": [f"{name} podcast", f"{name} interview"],
        "excluded_channels": ["clipper", "reaction", "shorts hub"],
        "excluded_video_ids": [],
        "speakers": [
            {"label": f"{name} (Core)", "role": "core", "patterns": [name.lower()]},
            {"label": "Guest / Host", "role": "guest", "patterns": ["guest", "host", "interview"]},
        ],
        "domains": [
            {"id": "01_philosophy", "title": "Philosophy & Core Mental Models", "keywords": ["philosophy", "principle", "macro"], "sections": ["2", "5"]},
            {"id": "02_execution", "title": "Execution Playbooks & Risk", "keywords": ["setup", "workflow", "risk", "entry"], "sections": ["3", "4", "6"]},
        ],
        "entity_standardizations": [
            "Always cite [VideoID @ MM:SS] for every concrete rule, parameter, or quote.",
            "Separate the core persona's own views from guest/co-host methods.",
        ],
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out

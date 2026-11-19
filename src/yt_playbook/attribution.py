"""Speaker Attribution Engine & Thematic Domain Slicer."""

from __future__ import annotations

import json
from pathlib import Path
import re

from yt_playbook.config import PersonaConfig


def parse_sections(text: str) -> dict[str, str]:
    """Split note markdown into numbered heading key -> body text."""
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"(?m)^##\s*(\d+)\.\s*(.+)$", text))
    for i, m in enumerate(matches):
        num = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[num] = text[start:end].strip()
    return sections


def classify_speaker(note_text: str, channel: str, cfg: PersonaConfig) -> str:
    """Classify speaker attribution strictly from Section 1 Identity block using PersonaConfig rules."""
    m = re.search(r"##\s*1\.[^\n]*\n(.*?)(?=\n##\s*2\.|\Z)", note_text, re.S | re.I)
    sec1 = m.group(1) if m else note_text[:1000]
    blob = sec1.lower()

    matched_roles: list[tuple[str, str]] = []
    for rule in cfg.speakers:
        if any(re.search(pat, blob, re.I) for pat in rule.patterns):
            matched_roles.append((rule.role, rule.label))

    core_hits = [lbl for role, lbl in matched_roles if role == "core"]
    co_hits = [lbl for role, lbl in matched_roles if role == "co_mentor"]
    guest_hits = [lbl for role, lbl in matched_roles if role == "guest"]

    if core_hits and co_hits:
        return f"{core_hits[0]} + {' / '.join(co_hits)}"
    if core_hits and guest_hits:
        return f"{core_hits[0]} + Guest ({guest_hits[0]})"
    if co_hits:
        return f"Co-Mentor: {' / '.join(co_hits)}"
    if core_hits:
        return core_hits[0]
    if guest_hits:
        return f"Guest: {guest_hits[0]}"
    if cfg.handle.lstrip("@").lower() in channel.lower().replace(" ", "_"):
        return f"{cfg.name} Team (Voiceover)"
    return "External / Unclassified"


def audit_and_bundle(
    cfg: PersonaConfig,
    catalog_path: str | Path,
    notes_dir: str | Path,
    speaker_out: str | Path,
    bundles_dir: str | Path,
) -> dict:
    """Classify every extracted note and slice relevant sections into domain bundles."""
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    notes_root = Path(notes_dir)
    out_bundles = Path(bundles_dir)
    out_bundles.mkdir(parents=True, exist_ok=True)

    classifications: list[dict] = []
    domain_buckets: dict[str, list[str]] = {d.id: [] for d in cfg.domains}

    for tier in ("tier_a_channel", "tier_b_collabs", "tier_c_shorts"):
        for it in catalog.get(tier, []):
            vid = it["video_id"]
            p = notes_root / tier / f"{vid}.md"
            if not p.exists():
                continue
            txt = p.read_text(encoding="utf-8")
            spk = classify_speaker(txt, it.get("channel", ""), cfg)
            classifications.append(
                {
                    "video_id": vid,
                    "tier": tier,
                    "channel": it.get("channel", ""),
                    "title": it.get("title", ""),
                    "speaker_class": spk,
                }
            )
            if tier == "tier_c_shorts":
                continue

            secs = parse_sections(txt)
            blob_low = (it.get("title", "") + "\n" + txt[:2500]).lower()
            for dom in cfg.domains:
                if not dom.keywords or any(k.lower() in blob_low for k in dom.keywords):
                    picked = [secs[s] for s in dom.sections if s in secs]
                    body = "\n\n".join(picked) if picked else txt[:1800]
                    hdr = f"### [{vid}] {it.get('title', '')} | Speaker: {spk} | Channel: {it.get('channel', '')}"
                    domain_buckets[dom.id].append(f"{hdr}\n{body}\n")

    Path(speaker_out).parent.mkdir(parents=True, exist_ok=True)
    Path(speaker_out).write_text(json.dumps(classifications, indent=2, ensure_ascii=False), encoding="utf-8")

    for dom in cfg.domains:
        parts = domain_buckets[dom.id]
        content = f"# Bundle: {dom.title} ({len(parts)} videos)\n\n" + "\n---\n\n".join(parts)
        (out_bundles / f"bundle_{dom.id}.md").write_text(content, encoding="utf-8")

    return {"classified_notes": len(classifications), "bundles_written": len(cfg.domains)}

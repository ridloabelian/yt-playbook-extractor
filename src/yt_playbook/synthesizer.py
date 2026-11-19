"""Playbook synthesis, deterministic VIDEO_INVENTORY_AND_INDEX generator, and fence-aware KaTeX/Mermaid sanitizer."""

from __future__ import annotations

import json
from pathlib import Path
import re

from yt_playbook.config import PersonaConfig
from yt_playbook.watcher import call_with_fallback, DEFAULT_LONG_MODELS, format_mmss


def generate_video_inventory(
    cfg: PersonaConfig,
    catalog_path: str | Path,
    speaker_path: str | Path,
    out_path: str | Path,
) -> Path:
    """Deterministically generate VIDEO_INVENTORY_AND_INDEX.md without LLM hallucinations."""
    cat = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    spk_rows = json.loads(Path(speaker_path).read_text(encoding="utf-8")) if Path(speaker_path).exists() else []
    spk_map = {r["video_id"]: r["speaker_class"] for r in spk_rows}

    lines = [
        f"# VIDEO INVENTORY & INDEX — {cfg.name} ({cfg.handle})",
        "",
        "Deterministically generated from verified catalog and speaker classification metadata.",
        "",
    ]
    for tier, heading in [
        ("tier_a_channel", "Tier A: Main Channel Long-Form Videos"),
        ("tier_b_collabs", "Tier B: Verified External Podcasts & Collaborations"),
        ("tier_c_shorts", "Tier C: YouTube Shorts"),
    ]:
        items = cat.get(tier, [])
        lines.extend(
            [
                f"## {heading} ({len(items)} videos)",
                "",
                "| # | Video | Channel | Duration | Speaker Attribution |",
                "|---:|---|---|---|---|",
            ]
        )
        for i, it in enumerate(items, 1):
            vid = it["video_id"]
            title = it.get("title", vid).replace("|", "/")
            url = f"https://www.youtube.com/watch?v={vid}"
            dur = format_mmss(it.get("duration_seconds"))
            spk = spk_map.get(vid, "Unclassified")
            lines.append(f"| {i} | [{title}]({url}) (`{vid}`) | {it.get('channel', '')} | `{dur}` | {spk} |")
        lines.append("")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sanitized, _ = sanitize_markdown("\n".join(lines))
    out.write_text(sanitized, encoding="utf-8")
    return out


def synthesize_bundles(
    cfg: PersonaConfig,
    bundles_dir: str | Path,
    out_dir: str | Path,
    api_key: str,
) -> list[Path]:
    """Synthesize each thematic domain bundle using Gemini REST API."""
    b_dir = Path(bundles_dir)
    o_dir = Path(out_dir)
    o_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    rules = "\n".join(f"- {r}" for r in cfg.entity_standardizations)

    for dom in cfg.domains:
        in_file = b_dir / f"bundle_{dom.id}.md"
        if not in_file.exists():
            continue
        bundle_txt = in_file.read_text(encoding="utf-8")
        prompt = (
            f"Synthesize **{dom.title}** for **{cfg.name}** from the attributed video notes below.\n"
            f"Strict Standards:\n{rules}\n\n{bundle_txt[:60000]}"
        )
        txt, _, _ = call_with_fallback(api_key, DEFAULT_LONG_MODELS, [{"text": prompt}], max_tokens=8192)
        clean_txt, _ = sanitize_markdown(txt)
        out_p = o_dir / f"domain_{dom.id}.md"
        out_p.write_text(clean_txt, encoding="utf-8")
        written.append(out_p)
    return written


def _sanitize_mermaid_block(lines: list[str]) -> tuple[list[str], list[str]]:
    """Strip disallowed HTML tags in Mermaid and quote labels containing special chars."""
    warnings: list[str] = []
    cleaned = []
    for line in lines:
        fixed = re.sub(r"<br\s*/?>", " / ", line, flags=re.I)
        fixed = re.sub(r"</?(b|strong|i|em)>", "", fixed, flags=re.I)
        if fixed != line:
            warnings.append("Stripped HTML tags from Mermaid block")
        # Quote unquoted node labels like A[Foo (Bar)] or C{Choice: Yes/No}
        fixed = re.sub(
            r'\b([A-Za-z0-9_]+)\[([^"\]\n]*[():,/&][^"\]\n]*)\]',
            lambda m: f'{m.group(1)}["{m.group(2).strip()}"]',
            fixed,
        )
        fixed = re.sub(
            r'\b([A-Za-z0-9_]+)\{([^"\}\n]*[():,/&][^"\}\n]*)\}',
            lambda m: f'{m.group(1)}{{"{m.group(2).strip()}"}}',
            fixed,
        )
        cleaned.append(fixed)
    return cleaned, warnings


def sanitize_markdown(text: str) -> tuple[str, dict]:
    """Fence-aware & line-local sanitizer for KaTeX `$` escaping and Mermaid blocks."""
    raw_lines = text.splitlines(keepends=True)
    out_lines: list[str] = []
    in_fence = False
    fence_lang = ""
    mermaid_buf: list[str] = []
    in_display_math = False

    stats = {"inline_code_unescaped": 0, "prose_currency_escaped": 0, "mermaid_warnings": []}

    for line in raw_lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                fence_lang = stripped[3:].strip().lower()
                if fence_lang == "mermaid":
                    mermaid_buf = []
                    out_lines.append(line)
                    continue
                out_lines.append(line)
                continue
            else:
                if fence_lang == "mermaid":
                    clean_mb, warns = _sanitize_mermaid_block(mermaid_buf)
                    stats["mermaid_warnings"].extend(warns)
                    out_lines.extend(clean_mb)
                    mermaid_buf = []
                in_fence = False
                fence_lang = ""
                out_lines.append(line)
                continue

        if in_fence:
            if fence_lang == "mermaid":
                mermaid_buf.append(line)
            else:
                out_lines.append(line)
            continue

        if stripped.startswith("$$"):
            in_display_math = not in_display_math
            out_lines.append(line)
            continue

        if in_display_math:
            out_lines.append(line)
            continue

        # Split line deterministically into prose vs inline backtick spans (line-local only)
        parts = re.split(r"(`[^`\n]+`)", line)
        new_parts = []
        for idx, part in enumerate(parts):
            if idx % 2 == 1:
                # Inside inline backticks: remove \$ -> $
                if r"\$" in part:
                    stats["inline_code_unescaped"] += part.count(r"\$")
                    part = part.replace(r"\$", "$")
                new_parts.append(part)
            else:
                # Outside inline backticks: escape bare currency $ (e.g. $100, ~$18, $35k, $1.5M)
                def _repl_currency(m: re.Match) -> str:
                    stats["prose_currency_escaped"] += 1
                    return r"\$"

                fixed = re.sub(r"(?<!\\)\$(?=\s*[0-9])", _repl_currency, part)
                new_parts.append(fixed)
        out_lines.append("".join(new_parts))

    return "".join(out_lines), stats

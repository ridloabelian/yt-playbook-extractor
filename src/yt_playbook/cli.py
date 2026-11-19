"""CLI interface for yt-playbook-extractor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yt_playbook.attribution import audit_and_bundle
from yt_playbook.catalog import build_catalog
from yt_playbook.config import PersonaConfig, write_starter_config
from yt_playbook.synthesizer import generate_video_inventory, sanitize_markdown, synthesize_bundles
from yt_playbook.watcher import load_api_key, run_watch_pipeline


def cmd_init(args: argparse.Namespace) -> int:
    out = write_starter_config(args.out, name=args.name, handle=args.handle)
    print(f"Created persona configuration template at {out}")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    cfg = PersonaConfig.from_yaml(args.config)
    cat = build_catalog(cfg, out_path=args.out, include_shorts=not args.no_shorts)
    print(
        f"Catalog written to {args.out}: "
        f"Tier A={len(cat['tier_a_channel'])}, Tier B={len(cat['tier_b_collabs'])}, Tier C={len(cat['tier_c_shorts'])}"
    )
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    cfg = PersonaConfig.from_yaml(args.config)
    api_key = load_api_key(args.env)
    return run_watch_pipeline(
        cfg=cfg,
        catalog_path=args.catalog,
        notes_dir=args.notes,
        progress_path=args.progress,
        api_key=api_key,
        workers=args.workers,
        limit=args.limit,
        force=args.force,
    )


def cmd_audit(args: argparse.Namespace) -> int:
    cfg = PersonaConfig.from_yaml(args.config)
    summary = audit_and_bundle(
        cfg=cfg,
        catalog_path=args.catalog,
        notes_dir=args.notes,
        speaker_out=args.speakers_out,
        bundles_dir=args.bundles_dir,
    )
    if args.inventory_out:
        generate_video_inventory(cfg, args.catalog, args.speakers_out, args.inventory_out)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_synthesize(args: argparse.Namespace) -> int:
    cfg = PersonaConfig.from_yaml(args.config)
    api_key = load_api_key(args.env)
    written = synthesize_bundles(cfg, args.bundles_dir, args.out_dir, api_key=api_key)
    print(f"Synthesized {len(written)} domain files into {args.out_dir}")
    return 0


def cmd_sanitize(args: argparse.Namespace) -> int:
    for fpath in args.files:
        p = Path(fpath)
        txt = p.read_text(encoding="utf-8")
        cleaned, stats = sanitize_markdown(txt)
        if not args.check and cleaned != txt:
            p.write_text(cleaned, encoding="utf-8")
        print(f"{p.name}: {json.dumps(stats)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yt-playbook", description="Generalized YouTube Methodology Reverse-Engineering CLI")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("init", help="Generate starter persona YAML config")
    pi.add_argument("--name", required=True)
    pi.add_argument("--handle", required=True)
    pi.add_argument("--out", default="persona.yaml")
    pi.set_defaults(func=cmd_init)

    pc = sub.add_parser("catalog", help="Discover Tier A, Tier B Collabs & Tier C Shorts")
    pc.add_argument("--config", required=True)
    pc.add_argument("--out", default="catalog.json")
    pc.add_argument("--no-shorts", action="store_true")
    pc.set_defaults(func=cmd_catalog)

    pw = sub.add_parser("watch", help="Extract multimodal audio+visual notes via Gemini file_uri")
    pw.add_argument("--config", required=True)
    pw.add_argument("--catalog", default="catalog.json")
    pw.add_argument("--notes", default="notes")
    pw.add_argument("--progress", default="progress.jsonl")
    pw.add_argument("--env", default=".env")
    pw.add_argument("--workers", type=int, default=3)
    pw.add_argument("--limit", type=int, default=None)
    pw.add_argument("--force", action="store_true")
    pw.set_defaults(func=cmd_watch)

    pa = sub.add_parser("audit", help="Run speaker attribution, domain slicing & inventory generation")
    pa.add_argument("--config", required=True)
    pa.add_argument("--catalog", default="catalog.json")
    pa.add_argument("--notes", default="notes")
    pa.add_argument("--speakers-out", default="speaker_classification.json")
    pa.add_argument("--bundles-dir", default="synthesis_inputs")
    pa.add_argument("--inventory-out", default="VIDEO_INVENTORY_AND_INDEX.md")
    pa.set_defaults(func=cmd_audit)

    ps = sub.add_parser("synthesize", help="Run domain deep-dive synthesis via Gemini API")
    ps.add_argument("--config", required=True)
    ps.add_argument("--bundles-dir", default="synthesis_inputs")
    ps.add_argument("--out-dir", default="synthesis_outputs")
    ps.add_argument("--env", default=".env")
    ps.set_defaults(func=cmd_synthesize)

    pz = sub.add_parser("sanitize", help="Sanitize KaTeX $ escaping and Mermaid syntax in Markdown files")
    pz.add_argument("files", nargs="+")
    pz.add_argument("--check", action="store_true")
    pz.set_defaults(func=cmd_sanitize)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

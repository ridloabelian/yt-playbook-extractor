import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from yt_playbook.attribution import audit_and_bundle, classify_speaker, parse_sections  # noqa: E402
from yt_playbook.catalog import (  # noqa: E402
    extract_lockups,
    extract_shorts,
    filter_collab_candidates,
    parse_duration_text,
)
from yt_playbook.cli import main as cli_main  # noqa: E402
from yt_playbook.config import PersonaConfig, write_starter_config  # noqa: E402
from yt_playbook.synthesizer import generate_video_inventory, sanitize_markdown  # noqa: E402
from yt_playbook.watcher import build_video_parts, build_video_prompt, format_mmss  # noqa: E402


def test_yaml_config_and_init_cli(tmp_path):
    cfg = PersonaConfig.from_yaml(ROOT / "examples" / "trading_mentor.yaml")
    assert cfg.handle == "@Mancing_Dollar"
    assert len(cfg.speakers) == 5
    assert len(cfg.domains) == 4

    new_yaml = tmp_path / "creator.yaml"
    rc = cli_main(["init", "--name", "Jane Doe", "--handle", "@janedoe", "--out", str(new_yaml)])
    assert rc == 0
    loaded = PersonaConfig.from_yaml(new_yaml)
    assert loaded.name == "Jane Doe"
    assert loaded.handle == "@janedoe"


def test_innertube_parsers_and_clipper_filter():
    assert parse_duration_text("PT1H15M30S") == 4530
    assert parse_duration_text("12:34") == 754
    assert parse_duration_text("1:02:03") == 3723
    assert parse_duration_text("invalid") is None

    mock_innertube = {
        "contents": [
            {
                "videoRenderer": {
                    "videoId": "abcdefghijk",
                    "title": {"runs": [{"text": "Deep Dive Podcast"}]},
                    "lengthText": {"simpleText": "45:10"},
                    "ownerText": {"runs": [{"text": "Finance Podcast"}]},
                }
            },
            {
                "videoRenderer": {
                    "videoId": "clipper1234",
                    "title": {"runs": [{"text": "Reupload Reaction"}]},
                    "lengthText": {"simpleText": "05:00"},
                    "ownerText": {"runs": [{"text": "Crypto Clipper Channel"}]},
                }
            },
            {
                "shortsLockupViewModel": {
                    "onTap": {"innertubeCommand": {"reelWatchEndpoint": {"videoId": "short123456"}}},
                    "overlayMetadata": {"primaryText": {"content": "Quick Tip"}},
                }
            },
        ]
    }
    longs = []
    extract_lockups(mock_innertube, longs)
    assert len(longs) == 2

    shorts = []
    extract_shorts(mock_innertube, shorts)
    assert len(shorts) == 1 and shorts[0]["video_id"] == "short123456"

    cfg = PersonaConfig.from_yaml(ROOT / "examples" / "trading_mentor.yaml")
    kept, rejected = filter_collab_candidates(longs, cfg, main_ids=set())
    assert len(kept) == 1 and kept[0]["video_id"] == "abcdefghijk"
    assert len(rejected) == 1 and "excluded_channel" in rejected[0]["reject_reason"]


def test_multimodal_payload_and_speaker_attribution(tmp_path):
    cfg = PersonaConfig.from_yaml(ROOT / "examples" / "trading_mentor.yaml")
    prompt = build_video_prompt(cfg, {"video_id": "v1234567890", "title": "Stingray Test", "duration_seconds": 900})
    parts = build_video_parts("https://www.youtube.com/watch?v=v1234567890", prompt, fps=0.5, start_s=0, end_s=600)
    assert parts[0]["file_data"]["file_uri"] == "https://www.youtube.com/watch?v=v1234567890"
    assert parts[0]["video_metadata"]["startOffset"] == "0s"
    assert format_mmss(3665) == "01:01:05"

    note_franz = "## 1. Identitas\nPembicara Utama: Franz (@franzz_md)\n## 2. Kerangka\nScalping 3 TF\n## 3. Chart\nM15/M5/M1"
    note_arist = "## 1. Identitas\nPembicara Utama: Arist MD\n## 2. Kerangka\nStingray 1.272 PRZ\n## 4. Eksekusi\nWait 15M"
    assert classify_speaker(note_franz, "Mancing Dollar", cfg) == "Co-Mentor: Franz (@franzz_md)"
    assert classify_speaker(note_arist, "Mancing Dollar", cfg) == "Arist MD Core"

    secs = parse_sections(note_arist)
    assert "2" in secs and "4" in secs

    # Full end-to-end audit + deterministic inventory test
    cat = {
        "tier_a_channel": [
            {"video_id": "v_arist_001", "title": "Stingray Harmonic Setup", "channel": "Mancing Dollar", "duration_seconds": 600},
            {"video_id": "v_franz_002", "title": "Scalping Naked", "channel": "Mancing Dollar", "duration_seconds": 480},
        ],
        "tier_b_collabs": [],
        "tier_c_shorts": [],
    }
    cat_p = tmp_path / "catalog.json"
    cat_p.write_text(json.dumps(cat), encoding="utf-8")
    (tmp_path / "notes" / "tier_a_channel").mkdir(parents=True)
    (tmp_path / "notes" / "tier_a_channel" / "v_arist_001.md").write_text(note_arist, encoding="utf-8")
    (tmp_path / "notes" / "tier_a_channel" / "v_franz_002.md").write_text(note_franz, encoding="utf-8")

    summary = audit_and_bundle(
        cfg,
        catalog_path=cat_p,
        notes_dir=tmp_path / "notes",
        speaker_out=tmp_path / "speakers.json",
        bundles_dir=tmp_path / "bundles",
    )
    assert summary["classified_notes"] == 2
    inv = generate_video_inventory(cfg, cat_p, tmp_path / "speakers.json", tmp_path / "INVENTORY.md")
    inv_txt = inv.read_text(encoding="utf-8")
    assert "v_arist_001" in inv_txt and "Co-Mentor: Franz" in inv_txt


def test_fence_aware_katex_and_mermaid_sanitizer():
    dirty = "\n".join(
        [
            "Prose cost $100 and ~$35k must escape, while `$10-$50` and `\\$90` normalize cleanly.",
            "```mermaid",
            "flowchart TD",
            "  A[Start (Init)<br/>Check] --> B{Ready? (Yes/No)}",
            "```",
            "```python",
            "price = '$100'  # untouched code fence",
            "```",
        ]
    )
    clean, stats = sanitize_markdown(dirty)
    lines = clean.splitlines()
    assert r"\$100" in lines[0] and r"\$35k" in lines[0]
    assert "`$10-$50`" in lines[0] and "`$90`" in lines[0]
    assert 'A["Start (Init) / Check"]' in clean
    assert 'B{"Ready? (Yes/No)"}' in clean
    assert "<br/>" not in clean
    assert "price = '$100'" in clean
    assert stats["prose_currency_escaped"] == 2
    assert stats["inline_code_unescaped"] == 1

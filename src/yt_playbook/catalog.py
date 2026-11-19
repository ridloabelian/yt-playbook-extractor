"""Zero-API-key YouTube InnerTube discovery engine with anti-clipper filtering."""

from __future__ import annotations

import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

from yt_playbook.config import PersonaConfig

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def fetch_html(url: str, data: bytes | None = None, headers: dict | None = None) -> str:
    hdrs = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    return urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")


def extract_json_var(html: str, var_name: str) -> dict:
    m = re.search(rf"var {var_name}\s*=\s*", html) or re.search(rf"{var_name}\s*=\s*", html)
    if not m:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, m.end())
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def extract_innertube_key(html: str) -> str:
    m = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    return m.group(1) if m else ""


def parse_duration_text(text: str) -> int | None:
    if not text:
        return None
    text = text.strip()
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", text)
    if m and any(m.groups()):
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)
    parts = re.split(r"[:.]", text)
    if all(p.isdigit() for p in parts) and 2 <= len(parts) <= 3:
        nums = [int(p) for p in parts]
        return nums[0] * 60 + nums[1] if len(nums) == 2 else nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


def find_continuations(obj, out: list[str]) -> None:
    if isinstance(obj, dict):
        if "continuationCommand" in obj:
            tok = obj["continuationCommand"].get("token")
            if tok:
                out.append(tok)
        for v in obj.values():
            find_continuations(v, out)
    elif isinstance(obj, list):
        for it in obj:
            find_continuations(it, out)


def extract_lockups(obj, out: list[dict], default_channel: str = "") -> None:
    if isinstance(obj, dict):
        if "lockupViewModel" in obj:
            lm = obj["lockupViewModel"]
            vid = lm.get("contentId")
            ctype = lm.get("contentType", "")
            meta = lm.get("metadata", {}).get("lockupMetadataViewModel", {})
            title = meta.get("title", {}).get("content", "")
            dur = ""

            def _find_dur(x):
                nonlocal dur
                if isinstance(x, dict):
                    if "thumbnailBadgeViewModel" in x:
                        t = x["thumbnailBadgeViewModel"].get("text", "")
                        if ":" in t or "." in t:
                            dur = t
                    for val in x.values():
                        _find_dur(val)
                elif isinstance(x, list):
                    for item in x:
                        _find_dur(item)

            _find_dur(lm.get("contentImage", {}))
            if vid and len(vid) == 11 and "PLAYLIST" not in ctype and "PODCAST" not in ctype:
                out.append(
                    {
                        "video_id": vid,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "title": title,
                        "channel": default_channel,
                        "duration_text": dur,
                        "duration_seconds": parse_duration_text(dur),
                    }
                )
        elif "videoRenderer" in obj:
            v = obj["videoRenderer"]
            vid = v.get("videoId")
            title = "".join(r.get("text", "") for r in v.get("title", {}).get("runs", [])) or v.get("title", {}).get("simpleText", "")
            dur = v.get("lengthText", {}).get("simpleText", "")
            chan = "".join(r.get("text", "") for r in v.get("ownerText", {}).get("runs", [])) or default_channel
            if vid and len(vid) == 11:
                out.append(
                    {
                        "video_id": vid,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "title": title,
                        "channel": chan,
                        "duration_text": dur,
                        "duration_seconds": parse_duration_text(dur),
                    }
                )
        for v in obj.values():
            extract_lockups(v, out, default_channel=default_channel)
    elif isinstance(obj, list):
        for it in obj:
            extract_lockups(it, out, default_channel=default_channel)


def extract_shorts(obj, out: list[dict], default_channel: str = "") -> None:
    if isinstance(obj, dict):
        if "shortsLockupViewModel" in obj:
            v = obj["shortsLockupViewModel"]
            vid = v.get("onTap", {}).get("innertubeCommand", {}).get("reelWatchEndpoint", {}).get("videoId")
            title = v.get("overlayMetadata", {}).get("primaryText", {}).get("content", "")
            if vid and len(vid) == 11:
                out.append(
                    {
                        "video_id": vid,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "title": title,
                        "channel": default_channel,
                        "duration_seconds": 60,
                    }
                )
        elif "reelItemRenderer" in obj:
            v = obj["reelItemRenderer"]
            vid = v.get("videoId")
            title = v.get("headline", {}).get("simpleText", "")
            if vid and len(vid) == 11:
                out.append(
                    {
                        "video_id": vid,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "title": title,
                        "channel": default_channel,
                        "duration_seconds": 60,
                    }
                )
        for val in obj.values():
            extract_shorts(val, out, default_channel=default_channel)
    elif isinstance(obj, list):
        for it in obj:
            extract_shorts(it, out, default_channel=default_channel)


def filter_collab_candidates(
    candidates: list[dict],
    cfg: PersonaConfig,
    main_ids: set[str],
    min_duration_s: int = 180,
) -> tuple[list[dict], list[dict]]:
    """Filter external search candidates against clipper/reaction blocklist & main-channel duplicates."""
    kept: list[dict] = []
    rejected: list[dict] = []
    excl_chan = [s.lower() for s in cfg.excluded_channels]
    excl_ids = set(cfg.excluded_video_ids)
    main_handle_low = cfg.handle.lstrip("@").lower()
    seen = set()

    for it in candidates:
        vid = it.get("video_id", "")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        chan_low = (it.get("channel") or "").lower()
        dur = it.get("duration_seconds")

        reason = None
        if vid in main_ids:
            reason = "duplicate_main_channel"
        elif vid in excl_ids:
            reason = "excluded_video_id"
        elif main_handle_low and main_handle_low in chan_low.replace(" ", "_"):
            reason = "main_channel_result"
        elif any(sub in chan_low for sub in excl_chan):
            reason = f"excluded_channel:{chan_low}"
        elif dur is not None and dur < min_duration_s:
            reason = f"short_clip_under_{min_duration_s}s"

        if reason:
            rejected.append({**it, "reject_reason": reason})
        else:
            kept.append({**it, "tier": "tier_b_collabs"})

    return kept, rejected


def scrape_channel_tab(handle: str, tab_name: str, is_shorts: bool = False) -> list[dict]:
    clean_handle = handle if handle.startswith("@") else f"@{handle}"
    html = fetch_html(f"https://www.youtube.com/{clean_handle}/{tab_name}")
    data = extract_json_var(html, "ytInitialData")
    key = extract_innertube_key(html)
    tabs = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {}).get("tabs", [])
    sel = next((t.get("tabRenderer", {}) for t in tabs if t.get("tabRenderer", {}).get("selected")), {})
    items: list[dict] = []
    if is_shorts:
        extract_shorts(sel, items, default_channel=clean_handle)
    else:
        extract_lockups(sel, items, default_channel=clean_handle)

    conts: list[str] = []
    find_continuations(sel, conts)
    seen_tok: set[str] = set()
    pages = 0
    while conts and key and pages < 25:
        tok = conts.pop(0)
        if tok in seen_tok:
            continue
        seen_tok.add(tok)
        body = json.dumps(
            {
                "context": {"client": {"clientName": "WEB", "clientVersion": "2.20240401.00.00"}},
                "continuation": tok,
            }
        ).encode("utf-8")
        try:
            resp = json.loads(
                fetch_html(
                    f"https://www.youtube.com/youtubei/v1/browse?key={key}",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
            )
            if is_shorts:
                extract_shorts(resp, items, default_channel=clean_handle)
            else:
                extract_lockups(resp, items, default_channel=clean_handle)
            find_continuations(resp, conts)
        except Exception:
            break
        pages += 1
        time.sleep(0.15)

    dedup: dict[str, dict] = {}
    tier_name = "tier_c_shorts" if is_shorts else "tier_a_channel"
    for it in items:
        dedup.setdefault(it["video_id"], {**it, "tier": tier_name})
    return list(dedup.values())


def search_youtube_collabs(query: str) -> list[dict]:
    html = fetch_html(f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}")
    data = extract_json_var(html, "ytInitialData")
    out: list[dict] = []
    extract_lockups(data, out)
    return out


def build_catalog(cfg: PersonaConfig, out_path: str | Path, include_shorts: bool = True) -> dict:
    tier_a = scrape_channel_tab(cfg.handle, "videos", is_shorts=False)
    tier_c = scrape_channel_tab(cfg.handle, "shorts", is_shorts=True) if include_shorts else []
    main_ids = {it["video_id"] for it in tier_a} | {it["video_id"] for it in tier_c}

    raw_collabs: list[dict] = []
    for q in cfg.collab_queries:
        try:
            raw_collabs.extend(search_youtube_collabs(q))
        except Exception:
            continue
    tier_b, rejected = filter_collab_candidates(raw_collabs, cfg, main_ids=main_ids)

    catalog = {
        "persona": cfg.name,
        "handle": cfg.handle,
        "tier_a_channel": tier_a,
        "tier_b_collabs": tier_b,
        "tier_c_shorts": tier_c,
        "rejected_tier_b_candidates": rejected,
    }
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
    return catalog

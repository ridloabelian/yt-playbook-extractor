"""Resumable Multimodal Gemini YouTube URI Watcher with auto time-chunking & quota circuit breaker."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

from yt_playbook.config import PersonaConfig

DEFAULT_LONG_MODELS = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"]
DEFAULT_SHORT_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash-lite"]
EXIT_CODE_QUOTA = 42

_LOCK = threading.Lock()
_STOP_EVENT = threading.Event()
_QUOTA_CONSECUTIVE = 0


def load_api_key(env_path: str | Path | None = None) -> str:
    if env_path and Path(env_path).exists():
        for line in Path(env_path).read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("GEMINI_API_KEY="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY not found in environment or .env")
    return key


def format_mmss(seconds: int | None) -> str:
    if not seconds:
        return "unknown"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def build_video_prompt(cfg: PersonaConfig, item: dict, chunk_note: str = "") -> str:
    sections_md = "\n".join(f"## {sec}" for sec in cfg.extraction_sections)
    rules_txt = "\n".join(f"- {r}" for r in cfg.entity_standardizations) or "- Cite exact [MM:SS] timestamps."
    chunk_hdr = f"\nSegment Scope: {chunk_note}\n" if chunk_note else ""
    return (
        f"You are conducting a thorough multimodal reverse-engineering analysis of **{cfg.name}** ({cfg.domain_focus}).\n"
        f"Watch BOTH audio and on-screen visuals (charts, slides, code, diagrams, numbers).\n\n"
        f"Metadata:\n"
        f"- Video ID: {item['video_id']}\n"
        f"- Title: {item.get('title', '')}\n"
        f"- Channel: {item.get('channel', '')}\n"
        f"- Duration: {format_mmss(item.get('duration_seconds'))}\n"
        f"- URL: https://www.youtube.com/watch?v={item['video_id']}{chunk_hdr}\n\n"
        f"Strict Extraction Rules:\n{rules_txt}\n\n"
        f"Produce structured Markdown using these exact headings:\n{sections_md}\n"
    )


def build_video_parts(yt_url: str, prompt: str, fps: float = 0.5, start_s: int | None = None, end_s: int | None = None) -> list[dict]:
    """Construct Gemini REST API payload parts with direct YouTube file_data.file_uri & optional offsets."""
    video_meta: dict = {"fps": fps}
    if start_s is not None and end_s is not None:
        video_meta["startOffset"] = f"{start_s}s"
        video_meta["endOffset"] = f"{end_s}s"
    return [
        {"file_data": {"file_uri": yt_url}, "video_metadata": video_meta},
        {"text": prompt},
    ]


def is_quota_error(code: int | None, msg: str) -> bool:
    low = msg.lower()
    return code == 429 and ("quota" in low or "resource_exhausted" in low or "limit" in low)


def gemini_call(api_key: str, model: str, parts: list[dict], max_tokens: int = 8192, timeout_s: int = 240) -> tuple[str, dict]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=timeout_s).read().decode("utf-8"))
    cands = resp.get("candidates", [])
    if not cands:
        raise RuntimeError(f"Empty candidates: {json.dumps(resp)[:300]}")
    text = "\n".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []) if "text" in p).strip()
    if not text:
        raise RuntimeError("Empty candidate text")
    return text, resp.get("usageMetadata", {})


def call_with_fallback(api_key: str, models: list[str], parts: list[dict], max_tokens: int = 8192) -> tuple[str, str, dict]:
    last_err = ""
    quota_hits = 0
    for model in models:
        for delay in (0, 5, 15):
            if _STOP_EVENT.is_set():
                raise RuntimeError("Stopped by global quota guard")
            if delay:
                time.sleep(delay)
            try:
                text, usage = gemini_call(api_key, model, parts, max_tokens=max_tokens)
                return text, model, usage
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")
                last_err = f"HTTP {e.code} ({model}): {body[:250]}"
                if is_quota_error(e.code, body):
                    quota_hits += 1
                    break
                if e.code in (400, 404):
                    break
            except Exception as e:
                last_err = f"{model}: {e}"
    if quota_hits >= len(models):
        raise RuntimeError(f"QUOTA_EXHAUSTED: {last_err}")
    raise RuntimeError(last_err)


def run_watch_pipeline(
    cfg: PersonaConfig,
    catalog_path: str | Path,
    notes_dir: str | Path,
    progress_path: str | Path,
    api_key: str,
    workers: int = 3,
    limit: int | None = None,
    force: bool = False,
) -> int:
    """Resumable worker runner processing Tier A/B/C catalog items via Gemini REST API."""
    global _QUOTA_CONSECUTIVE
    _STOP_EVENT.clear()
    _QUOTA_CONSECUTIVE = 0

    cat = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    items: list[dict] = []
    for tier in ("tier_a_channel", "tier_b_collabs", "tier_c_shorts"):
        for it in cat.get(tier, []):
            items.append({**it, "tier": tier})
    if limit:
        items = items[:limit]

    notes_root = Path(notes_dir)
    prog_file = Path(progress_path)

    def _process(item: dict) -> dict:
        global _QUOTA_CONSECUTIVE
        vid = item["video_id"]
        tier = item["tier"]
        out_path = notes_root / tier / f"{vid}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not force and out_path.exists() and out_path.stat().st_size > 120:
            return {"video_id": vid, "status": "skipped_existing"}

        yt_url = f"https://www.youtube.com/watch?v={vid}"
        dur = item.get("duration_seconds") or 0
        models = DEFAULT_SHORT_MODELS if tier == "tier_c_shorts" else DEFAULT_LONG_MODELS
        fps = 0.3 if tier == "tier_c_shorts" else (0.35 if dur > 2400 else 0.5)

        try:
            # Auto time-offset chunking for very long videos (>50 min)
            if dur > 3000 and tier != "tier_c_shorts":
                step = 1800
                chunk_texts = []
                model_used = models[0]
                for start_s in range(0, int(dur), step):
                    end_s = min(int(dur), start_s + step)
                    prompt = build_video_prompt(cfg, item, chunk_note=f"{format_mmss(start_s)}–{format_mmss(end_s)}")
                    parts = build_video_parts(yt_url, prompt, fps=fps, start_s=start_s, end_s=end_s)
                    txt, model_used, _ = call_with_fallback(api_key, models, parts)
                    chunk_texts.append(f"### Segment {format_mmss(start_s)}–{format_mmss(end_s)}\n{txt}")
                body_txt = "\n\n---\n\n".join(chunk_texts)
            else:
                parts = build_video_parts(yt_url, build_video_prompt(cfg, item), fps=fps)
                body_txt, model_used, _ = call_with_fallback(api_key, models, parts)

            with _LOCK:
                _QUOTA_CONSECUTIVE = 0
            hdr = f"# [{vid}] {item.get('title', '')}\n- Channel: {item.get('channel', '')}\n- URL: {yt_url}\n- Tier: {tier} | Model: {model_used}\n\n"
            out_path.write_text(hdr + body_txt + "\n", encoding="utf-8")
            return {"video_id": vid, "status": "ok", "model": model_used}
        except Exception as exc:
            err_s = str(exc)
            if "QUOTA_EXHAUSTED" in err_s:
                with _LOCK:
                    _QUOTA_CONSECUTIVE += 1
                    if _QUOTA_CONSECUTIVE >= 3:
                        _STOP_EVENT.set()
            return {"video_id": vid, "status": "error", "error": err_s[:240]}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_process, it) for it in items]
        for f in as_completed(futs):
            rec = f.result()
            with prog_file.open("a", encoding="utf-8") as pf:
                pf.write(json.dumps(rec) + "\n")

    return EXIT_CODE_QUOTA if _STOP_EVENT.is_set() else 0

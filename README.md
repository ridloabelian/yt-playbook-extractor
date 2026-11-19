# yt-playbook-extractor

Turn **any** YouTube creator's main channel, external podcast appearances, and Shorts into an attributed, timestamped, cross-checked **Playbook + Evidence Matrix**.

---

## Why Multimodal YouTube `file_uri` Beats Brittle Transcript Scrapers

Most YouTube summarizers break on `LOGIN_REQUIRED` anti-bot walls or transcript-less videos and are completely blind to charts, code editors, architecture diagrams, and slide formulas.

`yt-playbook-extractor` uses:
1. **Zero-API-Key InnerTube Discovery (`yt-playbook catalog`):** Crawls `@handle/videos`, `@handle/shorts`, and external podcast search queries, filters out clipper/reaction spam, and deduplicates main-channel repeats.
2. **Direct Multimodal YouTube URI Ingestion (`yt-playbook watch`):** Sends YouTube URLs natively via Gemini REST `file_data.file_uri` — observing **both audio and on-screen visuals** — with automatic `videoMetadata.startOffset/endOffset` chunking for long videos, resumable `progress.jsonl`, and a `429` quota circuit breaker (`EXIT_CODE 42`).
3. **Speaker Attribution & Domain Slicer (`yt-playbook audit`):** Prevents mixing co-hosts, co-mentors, or podcast guests into the core persona's DNA, slices long extractions by section to avoid LLM truncation, and builds `VIDEO_INVENTORY_AND_INDEX.md` deterministically.
4. **Fence-Aware Markdown/KaTeX/Mermaid Sanitizer (`yt-playbook sanitize`):** Escapes prose currency `$` for KaTeX, unescapes `\$` inside inline backticks, strips HTML tags from Mermaid blocks, and quotes special characters in Mermaid node labels.

---

## Included YAML Persona Examples

- [`examples/trading_mentor.yaml`](./examples/trading_mentor.yaml)
- [`examples/startup_founder.yaml`](./examples/startup_founder.yaml)
- [`examples/ai_researcher.yaml`](./examples/ai_researcher.yaml)

---

## Quickstart

```bash
pip install -e ".[dev]"
cp .env.example .env  # add your GEMINI_API_KEY

# 1. Create a starter persona YAML config
yt-playbook init --name "Creator Name" --handle "@CreatorHandle" --out persona.yaml

# 2. Discover main channel videos, collabs & Shorts
yt-playbook catalog --config persona.yaml --out catalog.json

# 3. Extract multimodal audio+visual notes (resumable)
yt-playbook watch --config persona.yaml --catalog catalog.json --workers 3

# 4. Run speaker attribution, build domain bundles & generate deterministic inventory
yt-playbook audit --config persona.yaml --catalog catalog.json --inventory-out VIDEO_INVENTORY_AND_INDEX.md

# 5. Synthesize domain deep-dives and sanitize Markdown outputs
yt-playbook synthesize --config persona.yaml
yt-playbook sanitize VIDEO_INVENTORY_AND_INDEX.md synthesis_outputs/*.md

# Run tests
pytest tests -v
```

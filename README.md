# reddit-pulse

A Reddit research tool for Claude Code and Claude Desktop. No auth, no API key. Only returns posts you can actually engage with — verified live, not just live-in-an-archive.

The crowded part of this space — `search_posts`, `get_post`, `get_user`, `get_subreddit_feed` — is solved by [a dozen other MCP servers](https://github.com/topics/reddit-mcp). What `reddit-parser` adds is a verification pipeline tuned for *engageable* leads, not just raw post listings.

## What it does that the other Reddit MCPs don't

1. **Auto-routing between two backends.** Tries Reddit's public `.json` first. When that fails (datacenter IPs, anti-bot blocks, rate limits) it transparently falls back to [Arctic Shift](https://github.com/ArthurHeitmann/arctic_shift), a Pushshift-successor archive that mirrors r/* in near real-time. You don't have to choose.
2. **Ground-truth status verification via Playwright.** Arctic Shift snapshots posts at creation time and never updates them — so a post that was later deleted, mod-removed, or auto-archived still looks "live" in its raw data. With `--verify-live all`, the tool opens each candidate URL in a headless Chromium and reads the actual deletion / removal / archive banner. Other no-auth Reddit clients miss this completely.
3. **Liveness heuristics** as a cheap pre-filter before Playwright. `score=0` with non-zero comments → likely mod-removed. Posted past `--max-age-days` → likely auto-archived. `selftext == "[removed]"` → removed. Drops the obvious dead posts so the browser only opens the maybes.

The intended use case is buyer-research / customer-discovery hunting in subreddits — finding humans you can actually DM today, not threads from 8 months ago that no longer accept comments.

## Install

Two completely separate runtimes, two install paths. Both back the same `scripts/reddit.py`.

### Claude Code (CLI)

```bash
git clone https://github.com/sambuild04/reddit-pulse.git ~/reddit-pulse
ln -s ~/reddit-pulse ~/.claude/skills/reddit-pulse
```

Picked up live in the same session — no restart needed.

### Claude Desktop (`.app`)

1. Build the bundle (one-time):
   ```bash
   cd ~/reddit-pulse/mcpb-src
   npm install --omit=dev
   (cd .. && zip -rq reddit-pulse.mcpb mcpb-src -x '*.DS_Store' 'mcpb-src/node_modules/.cache/*')
   ```
2. Open the `.mcpb` with Claude.app:
   ```bash
   open -a Claude ~/reddit-pulse/reddit-pulse.mcpb
   ```
3. In the install dialog, set Python path. Default `/usr/bin/python3` works on macOS for everything except `verify_live`. For Playwright, use whichever Python actually has it (`python3 -c 'import playwright; print(__import__("sys").executable)'`).

### Requirements

| Feature | Needed |
|---|---|
| Everything except `verify_live` | Python 3 |
| `verify_live` heuristic check | nothing extra |
| `verify_live=suspect` or `=all` | `pip3 install --user playwright && python3 -m playwright install chromium` |

`curl_cffi` is *not* required — research showed it doesn't actually bypass Reddit's 2026 anti-bot, only Playwright reliably does.

## Quick start

```bash
# Subreddit rules
python3 scripts/reddit.py rules askscience

# What's hot in r/X
python3 scripts/reddit.py feed askscience --sort hot --limit 10

# A specific post with comments
python3 scripts/reddit.py post https://www.reddit.com/r/askscience/comments/abc123/

# A user's recent activity
python3 scripts/reddit.py user spez --what overview --limit 20

# Bare keyword search
python3 scripts/reddit.py search "founder mode" --sort top --t year

# Buyer-research hunt — drop dead posts, ground-truth survivors, inline context
python3 scripts/reddit.py search "tired of anki" \
    --sub LearnJapanese --t year \
    --liveness exclude --max-age-days 180 \
    --verify-live all \
    --with-context 5 --limit 15
```

## The verification pipeline

```
Arctic Shift / Reddit          (backend)
        │
        ▼
filter_archived (snapshot flag)
        │
        ▼
annotate_liveness              (cheap heuristic)
   ├─ selftext == "[removed]"      → removed
   ├─ selftext or author == "[deleted]" → deleted
   ├─ created_utc > N days         → likely_archived
   └─ score=0 && num_comments>5    → suspicious
        │
        ▼
verify_live   (optional, Playwright)
   ├─ opens each suspect URL in headless Chromium
   └─ parses banner: deleted / removed / archived / locked / live
        │
        ▼
filter_by_liveness   (drop / keep-only / annotate inline)
        │
        ▼
render markdown
```

`--liveness exclude` is the right default for buyer-research lists. `--liveness flag` keeps everything but annotates suspect posts inline.

## Backends, in plain language

| Endpoint | Reddit `.json` | Arctic Shift |
|---|---|---|
| Authoritative live data | yes | yes (≤24h lag) |
| Works from datacenter / VPN / sandbox IPs | no (403) | yes |
| Requires no auth | yes | yes |
| Has subreddit rules / about / wiki | yes | **no** |
| Has hot/top/rising rankings | yes | **no** |
| Has historical posts forever | partially | yes |
| Status (deleted/archived) is current | yes | **no** — snapshot at create time |

`--via auto` (default) picks Arctic Shift where it's supported, Reddit elsewhere, and falls back to Reddit if Arctic Shift fails.

## Files

```
reddit-pulse/
├── README.md                   # this file
├── SKILL.md                    # Claude Code skill manifest (markdown)
├── scripts/
│   └── reddit.py               # the CLI — all logic lives here
└── mcpb-src/                   # source for the Claude.app extension
    ├── manifest.json
    ├── server/index.js         # thin Node.js MCP wrapper around reddit.py
    ├── scripts/reddit.py       # copy of the CLI (kept in sync at build time)
    ├── package.json
    └── README.md               # bundle-specific README
```

The built `.mcpb` bundle and `mcpb-src/node_modules/` are gitignored — build locally with `npm install --omit=dev` inside `mcpb-src/`.

## Limits

- **Programmatic live verification only works through a real browser** in 2026. Reddit blocks `curl`, `curl_cffi` (TLS-impersonation), HTTP/2-fingerprint-matched clients, and most public reddit-frontend mirrors. Playwright bypasses all of this because it *is* a real browser.
- **Arctic Shift may go away.** It's community-run, not Anthropic-affiliated. If it disappears, `--via reddit` still works from residential IPs, but the sandbox/datacenter path breaks.
- **Reddit's archive policy is per-subreddit.** Default is 180 days but some subs disable archive entirely. `--max-age-days` is a safe approximation, not ground truth — `--verify-live` is.

## License

MIT.

#!/usr/bin/env python3
"""Parse Reddit content via public .json endpoints.

Subcommands:
  rules <sub>                            subreddit rules
  about <sub>                            subreddit metadata + sidebar
  wiki  <sub> [page]                     wiki page (default: index)
  feed  <sub> [--sort ...] [--t ...]     subreddit feed
  post  <url-or-id>                      post body + top comments
  user  <name> [--what ...]              user profile + activity
  search <query> [--sub ...]             search results
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = "reddit-parser-skill/0.2 (Claude skill, stdlib-only)"
BASE = "https://www.reddit.com"
ARCTIC = "https://arctic-shift.photon-reddit.com"


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def _http_get(url, params=None, timeout=15):
    if params:
        q = {k: v for k, v in params.items() if v is not None}
        if q:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_reddit(path, params=None):
    url = path if path.startswith("http") else f"{BASE}{path}"
    if not url.endswith(".json"):
        url = url.rstrip("/") + ".json"
    try:
        return _http_get(url, params)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            die("Reddit rate-limited (HTTP 429). Wait ~60s, or try --via arctic-shift.")
        if e.code == 404:
            die(f"Not found: {url}")
        if e.code == 403:
            die(
                f"Forbidden (HTTP 403): {url}\n"
                "Likely Reddit blocking this IP (datacenter / VPN / sandbox). "
                "Try --via arctic-shift for live data with no auth, or run from a residential IP."
            )
        die(f"HTTP {e.code}: {url}")
    except urllib.error.URLError as e:
        die(f"network error: {e}")


def fetch_arctic_try(endpoint, params=None):
    """Returns (data, err). For use when caller wants to fall back."""
    url = f"{ARCTIC}{endpoint}"
    try:
        return _http_get(url, params, timeout=20), None
    except Exception as e:
        return None, e


def fetch_arctic(endpoint, params=None):
    data, err = fetch_arctic_try(endpoint, params)
    if err is not None:
        die(f"Arctic Shift: {err}")
    return data


# Backwards-compat shim for code paths that have not been routed yet.
fetch = fetch_reddit


def pick_via(args, command, sort=None):
    """Resolve --via auto to a concrete backend based on what each backend supports."""
    via = getattr(args, "via", "reddit")
    if via != "auto":
        return via
    if command in ("rules", "about", "wiki"):
        return "reddit"
    if command == "feed" and sort != "new":
        return "reddit"  # only Reddit computes hot/top/rising
    return "arctic-shift"


_TIME_WINDOWS = {
    "hour": 3600, "day": 86400, "week": 86400 * 7, "month": 86400 * 30,
    "year": 86400 * 365, "all": None,
}


def arctic_after_from_t(t):
    if not t or t == "all":
        return None
    secs = _TIME_WINDOWS.get(t)
    if secs is None:
        return None
    return int(datetime.now(timezone.utc).timestamp()) - secs


def arctic_to_children(arctic_resp, kind):
    """Wrap Arctic Shift's flat `data: [...]` into Reddit's listing children shape."""
    return [{"kind": kind, "data": item} for item in (arctic_resp or {}).get("data", [])]


def build_comment_tree(flat_comments):
    """Reconstruct Reddit-style nested children from a flat Arctic Shift comment list."""
    for c in flat_comments:
        c["replies"] = ""
    by_id = {c["id"]: c for c in flat_comments}
    roots = []
    for c in flat_comments:
        pid = c.get("parent_id", "") or ""
        if pid.startswith("t1_"):
            parent = by_id.get(pid[3:])
            if parent:
                if parent["replies"] == "":
                    parent["replies"] = {"data": {"children": []}}
                parent["replies"]["data"]["children"].append({"kind": "t1", "data": c})
                continue
        roots.append(c)
    roots.sort(key=lambda c: c.get("score", 0), reverse=True)
    return [{"kind": "t1", "data": c} for c in roots]


def extract_post_id(target):
    target = target.strip()
    if "/comments/" in target:
        return target.split("/comments/")[1].split("/")[0].split("?")[0]
    if target.startswith("t3_"):
        return target[3:]
    return target


def render_comment_tree(children, max_depth, body_chars=500):
    """Render a Reddit-style nested comment listing as markdown bullets."""
    lines = []
    def render(c, depth):
        if c.get("kind") == "more":
            return
        d = c["data"]
        if not d.get("body"):
            return
        indent = "  " * depth
        meta = f"u/{d.get('author', '?')} • {d.get('score', 0)} pts • {fmt_when(d.get('created_utc'))}"
        body = truncate(d["body"].replace("\n", " "), body_chars)
        lines.append(f"{indent}- **{meta}**")
        lines.append(f"{indent}  {body}")
        replies = d.get("replies")
        if isinstance(replies, dict) and depth + 1 < max_depth:
            for child in replies.get("data", {}).get("children", []):
                render(child, depth + 1)
    for c in children:
        render(c, 0)
    return lines


def fetch_comments_for_post(post_id, limit):
    """Fetch top comments for a post via Arctic Shift, return Reddit-shaped children list."""
    c_resp, err = fetch_arctic_try(
        "/api/comments/search",
        {"link_id": post_id, "limit": min(limit * 4, 100), "sort": "desc"},
    )
    if err is not None:
        return None, err
    flat = (c_resp or {}).get("data", [])
    flat.sort(key=lambda c: c.get("score", 0), reverse=True)
    return build_comment_tree(flat)[:limit], None


def fmt_when(utc_seconds):
    if not utc_seconds:
        return "unknown"
    dt = datetime.fromtimestamp(float(utc_seconds), tz=timezone.utc)
    iso = dt.strftime("%Y-%m-%d %H:%M UTC")
    s = int((datetime.now(timezone.utc) - dt).total_seconds())
    if s < 60: rel = f"{s}s ago"
    elif s < 3600: rel = f"{s//60}m ago"
    elif s < 86400: rel = f"{s//3600}h ago"
    elif s < 86400 * 30: rel = f"{s//86400}d ago"
    elif s < 86400 * 365: rel = f"{s//(86400*30)}mo ago"
    else: rel = f"{s//(86400*365)}y ago"
    return f"{iso} ({rel})"


def normalize_sub(s):
    return re.sub(r"^/?r/", "", s.strip(), flags=re.I).strip("/")


def normalize_user(u):
    return re.sub(r"^/?u(ser)?/", "", u.strip(), flags=re.I).strip("/")


def truncate(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n].rstrip() + "…"


def filter_archived(children, mode):
    if mode == "exclude":
        return [c for c in children if not c.get("data", {}).get("archived")]
    if mode == "only":
        return [c for c in children if c.get("data", {}).get("archived")]
    return children


REDDIT_DEFAULT_ARCHIVE_DAYS = 180  # Reddit auto-archives posts at ~6 months by default


def liveness_check(item_data, max_age_days=None):
    """Heuristic live-status guess for an Arctic Shift snapshot.
    Returns (status, reason) where status is one of: 'live', 'removed', 'deleted',
    'likely_archived', 'suspicious'.
    """
    selftext = (item_data.get("selftext") or item_data.get("body") or "").strip()
    author = (item_data.get("author") or "").strip()
    if selftext in ("[removed]", "[ Removed by Reddit ]"):
        return "removed", "selftext is [removed]"
    if selftext == "[deleted]" or author == "[deleted]":
        return "deleted", "author or selftext is [deleted]"
    if max_age_days is not None:
        created = item_data.get("created_utc")
        if created:
            age_days = (datetime.now(timezone.utc).timestamp() - float(created)) / 86400
            if age_days > max_age_days:
                return "likely_archived", f"posted {int(age_days)}d ago (>{max_age_days}d archive threshold)"
    score = item_data.get("score")
    num_comments = item_data.get("num_comments", 0)
    if score == 0 and num_comments and num_comments > 5:
        return "suspicious", f"score=0 with {num_comments} comments (likely mod-removed or snapshot pre-vote)"
    return "live", ""


def annotate_liveness(children, max_age_days=None):
    """Populate ._liveness on every child from heuristic check."""
    for c in children:
        c["_liveness"] = liveness_check(c.get("data", {}), max_age_days)
    return children


def filter_by_liveness(children, mode):
    """mode: include (no-op), exclude (drop non-live), only (keep non-live), flag (annotate inline)."""
    if mode == "include":
        return children
    out = []
    for c in children:
        status = (c.get("_liveness") or ("live", ""))[0]
        if mode == "exclude" and status == "live":
            out.append(c)
        elif mode == "only" and status != "live":
            out.append(c)
        elif mode == "flag":
            out.append(c)
    return out


_PLAYWRIGHT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


def _classify_live_text(text):
    t = (text or "").lower()
    if "deleted by the person who originally posted" in t or "deleted by the user" in t:
        return "deleted", "live page: user-deleted"
    if "removed by the moderators" in t or "removed by reddit" in t or "removed by a moderator" in t:
        return "removed", "live page: mod-removed"
    if "archived post" in t:
        return "archived", "live page: archived"
    if "locked post" in t or "comment locked" in t:
        return "locked", "live page: locked"
    return "live", "live page: verified"


def verify_live_status(children, mode):
    """Open each candidate's permalink in a headless browser and overwrite ._liveness
    with ground truth. mode: 'suspect' (only heuristic non-live) or 'all'.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        die(
            "--verify-live requires Playwright. Install:\n"
            "  pip3 install --user playwright\n"
            "  python3 -m playwright install chromium"
        )
    indices = []
    for i, c in enumerate(children):
        status = (c.get("_liveness") or ("live", ""))[0]
        if mode == "all" or (mode == "suspect" and status != "live"):
            indices.append(i)
    if not indices:
        return children
    print(f"# verify-live: opening {len(indices)} post(s) in headless browser…", file=sys.stderr)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=_PLAYWRIGHT_UA, viewport={"width": 1280, "height": 800})
        for n, i in enumerate(indices, 1):
            permalink = children[i]["data"].get("permalink", "")
            url = "https://www.reddit.com" + permalink if permalink.startswith("/") else permalink
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1500)
                text = page.evaluate("() => document.body.innerText")
                children[i]["_liveness"] = _classify_live_text(text)
            except Exception as e:
                children[i]["_liveness"] = ("unknown", f"verify failed: {str(e)[:60]}")
            finally:
                page.close()
            if n % 5 == 0:
                print(f"#   {n}/{len(indices)} verified", file=sys.stderr)
        browser.close()
    return children


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_rules(args):
    sub = normalize_sub(args.sub)
    data = fetch(f"/r/{sub}/about/rules")
    rules = data.get("rules", [])
    site_rules = data.get("site_rules", [])
    out = [f"# Rules for r/{sub}", ""]
    if not rules:
        out.append("_No subreddit-specific rules listed._")
        out.append("")
    for i, rule in enumerate(rules, 1):
        out.append(f"## {i}. {rule.get('short_name') or '(no title)'}")
        kind = rule.get("kind")
        if kind:
            out.append(f"_Applies to: {kind}_")
        desc = (rule.get("description") or "").strip()
        if desc:
            out.append("")
            out.append(desc)
        created = rule.get("created_utc")
        if created:
            out.append("")
            out.append(f"_Added: {fmt_when(created)}_")
        out.append("")
    if site_rules:
        out.append("## Reddit-wide site rules")
        for sr in site_rules:
            out.append(f"- {sr}")
    print("\n".join(out))


def cmd_about(args):
    sub = normalize_sub(args.sub)
    j = fetch(f"/r/{sub}/about")
    d = j["data"]
    out = [
        f"# r/{d.get('display_name', sub)}",
        f"**{d.get('title', '') or ''}**",
        "",
        f"- Subscribers: {d.get('subscribers', 0):,}",
        f"- Active right now: {d.get('active_user_count') or 'n/a'}",
        f"- Created: {fmt_when(d.get('created_utc'))}",
        f"- NSFW: {d.get('over18', False)}",
        f"- Type: {d.get('subreddit_type', 'public')}",
        f"- URL: https://www.reddit.com{d.get('url', f'/r/{sub}/')}",
        "",
        "## Short description",
        (d.get("public_description") or "").strip() or "_(none)_",
        "",
        "## Sidebar",
        (d.get("description") or "").strip() or "_(none)_",
    ]
    print("\n".join(out))


def cmd_wiki(args):
    sub = normalize_sub(args.sub)
    page = args.page or "index"
    j = fetch(f"/r/{sub}/wiki/{page}")
    d = j.get("data", {})
    content = (d.get("content_md") or "").strip()
    out = [f"# Wiki: r/{sub}/{page}", ""]
    if d.get("revision_date"):
        out.append(f"_Last revised: {fmt_when(d['revision_date'])}_")
        if d.get("revision_by", {}).get("data", {}).get("name"):
            out[-1] += f" by u/{d['revision_by']['data']['name']}"
        out.append("")
    out.append(content or "_(empty)_")
    print("\n".join(out))


def cmd_feed(args):
    sub = normalize_sub(args.sub)
    via = pick_via(args, "feed", sort=args.sort)
    children = None

    if via == "arctic-shift":
        params = {"subreddit": sub, "limit": min(args.limit, 100), "sort": "desc"}
        resp, err = fetch_arctic_try("/api/posts/search", params)
        if err is None:
            children = arctic_to_children(resp, "t3")
        elif args.via == "auto":
            print(f"# arctic-shift failed ({err}); falling back to reddit", file=sys.stderr)
            via = "reddit"
        else:
            die(f"Arctic Shift: {err}")

    if children is None:
        params = {"limit": args.limit}
        if args.sort == "top":
            params["t"] = args.t
        j = fetch_reddit(f"/r/{sub}/{args.sort}", params)
        children = j["data"]["children"]

    children = filter_archived(children, args.archived)
    children = annotate_liveness(children, args.max_age_days or None)
    if args.verify_live != "none":
        children = verify_live_status(children, args.verify_live)
    children = filter_by_liveness(children, args.liveness)
    suffix = (f" ({args.t})" if args.sort == "top" else "") + f" — via {via}"
    if args.archived != "include":
        suffix += f", {args.archived} archived"
    if args.liveness != "include":
        suffix += f", liveness={args.liveness}"
    out = [f"# r/{sub} — {args.sort}{suffix}", ""]
    for c in children:
        p = c["data"]
        bits = []
        if p.get("link_flair_text"): bits.append(f"[{p['link_flair_text']}]")
        if p.get("stickied"): bits.append("📌")
        if p.get("over_18"): bits.append("🔞")
        if p.get("archived"): bits.append("📦")
        if c.get("_liveness") and c["_liveness"][0] != "live":
            bits.append(f"`[{c['_liveness'][0]}]`")
        suffix = (" " + " ".join(bits)) if bits else ""
        out.append(f"## {p['title']}{suffix}")
        out.append(
            f"by u/{p['author']} • score {p['score']} • {p['num_comments']} comments • {fmt_when(p['created_utc'])}"
        )
        out.append(f"https://www.reddit.com{p['permalink']}")
        if p.get("selftext"):
            out.append("")
            out.append(truncate(p["selftext"], 400))
        elif p.get("url") and not p["url"].startswith("https://www.reddit.com"):
            out.append(f"link → {p['url']}")
        out.append("")
    print("\n".join(out))


def cmd_post(args):
    target = args.target.strip()
    via = pick_via(args, "post")
    post = comments = None

    if via == "arctic-shift":
        post_id = extract_post_id(target)
        post_resp, err = fetch_arctic_try("/api/posts/ids", {"ids": post_id})
        if err is None:
            posts = (post_resp or {}).get("data", [])
            if not posts:
                if args.via == "auto":
                    print(f"# arctic-shift: post {post_id} not in archive yet; falling back to reddit", file=sys.stderr)
                    via = "reddit"
                else:
                    die(f"Arctic Shift: no post with id {post_id}")
            else:
                post = posts[0]
                c_resp, c_err = fetch_arctic_try(
                    "/api/comments/search",
                    {"link_id": post_id, "limit": min(args.limit * 4, 100), "sort": "desc"},
                )
                if c_err is not None and args.via == "auto":
                    print(f"# arctic-shift comments fetch failed ({c_err}); falling back to reddit", file=sys.stderr)
                    via = "reddit"
                    post = None
                elif c_err is not None:
                    die(f"Arctic Shift: {c_err}")
                else:
                    flat = (c_resp or {}).get("data", [])
                    comments = build_comment_tree(flat)[: args.limit]
        elif args.via == "auto":
            print(f"# arctic-shift failed ({err}); falling back to reddit", file=sys.stderr)
            via = "reddit"
        else:
            die(f"Arctic Shift: {err}")

    if post is None:
        if target.startswith("http"):
            path = target
        elif "/comments/" in target:
            path = target if target.startswith("/") else "/" + target
        else:
            path = f"/comments/{target}"
        j = fetch_reddit(path, {"limit": args.limit, "depth": args.depth, "raw_json": 1})
        post = j[0]["data"]["children"][0]["data"]
        comments = j[1]["data"]["children"]

    out = [f"# {post['title']}  _(via {via})_", ""]
    out.append(
        f"r/{post['subreddit']} • by u/{post['author']} • score {post['score']} • {post['num_comments']} comments"
    )
    out.append(f"Posted: {fmt_when(post['created_utc'])}")
    if post.get("edited") and isinstance(post["edited"], (int, float)):
        out.append(f"Edited: {fmt_when(post['edited'])}")
    if post.get("archived"):
        out.append("📦 **Archived** — no new comments accepted.")
    if post.get("locked"):
        out.append("🔒 **Locked** — no new comments accepted.")
    out.append(f"URL: https://www.reddit.com{post['permalink']}")
    if post.get("link_flair_text"):
        out.append(f"Flair: {post['link_flair_text']}")
    out.append("")
    if post.get("selftext"):
        out.append("## Body")
        out.append(post["selftext"].strip())
        out.append("")
    elif post.get("url"):
        out.append(f"Linked URL: {post['url']}")
        out.append("")

    out.append(f"## Top comments (up to {args.limit}, depth {args.depth})")
    out.append("")
    out.extend(render_comment_tree(comments, args.depth))
    print("\n".join(out))


def cmd_user(args):
    name = normalize_user(args.name)
    via = pick_via(args, "user")
    out = [f"# u/{name}  _(activity via {via})_"]

    # Profile is Reddit-only (Arctic Shift has no /user/about endpoint).
    if via == "reddit":
        prof = fetch_reddit(f"/user/{name}/about")["data"]
        out += [
            f"- Created: {fmt_when(prof.get('created_utc'))}",
            f"- Comment karma: {prof.get('comment_karma', 0):,}",
            f"- Post karma: {prof.get('link_karma', 0):,}",
        ]
        if prof.get("subreddit", {}).get("public_description"):
            out.append(f"- Bio: {prof['subreddit']['public_description'].strip()}")

    children = None
    if via == "arctic-shift":
        children = []
        if args.what in ("submitted", "overview"):
            resp, err = fetch_arctic_try(
                "/api/posts/search",
                {"author": name, "limit": min(args.limit, 100), "sort": "desc"},
            )
            if err is None:
                children += arctic_to_children(resp, "t3")
            elif args.via == "auto":
                print(f"# arctic-shift posts fetch failed ({err}); falling back to reddit", file=sys.stderr)
                children = None
            else:
                die(f"Arctic Shift: {err}")
        if children is not None and args.what in ("comments", "overview"):
            resp, err = fetch_arctic_try(
                "/api/comments/search",
                {"author": name, "limit": min(args.limit, 100), "sort": "desc"},
            )
            if err is None:
                children += arctic_to_children(resp, "t1")
            elif args.via == "auto":
                print(f"# arctic-shift comments fetch failed ({err}); falling back to reddit", file=sys.stderr)
                children = None
            else:
                die(f"Arctic Shift: {err}")
        if children is not None:
            children.sort(key=lambda c: c["data"].get("created_utc", 0), reverse=True)
            children = children[: args.limit]
        else:
            via = "reddit"

    if children is None:
        j = fetch_reddit(f"/user/{name}/{args.what}", {"limit": args.limit, "sort": "new", "raw_json": 1})
        children = j["data"]["children"]

    children = filter_archived(children, args.archived)
    children = annotate_liveness(children, args.max_age_days or None)
    if args.verify_live != "none":
        children = verify_live_status(children, args.verify_live)
    children = filter_by_liveness(children, args.liveness)
    out.append("")
    header = f"## Recent {args.what}"
    if args.archived != "include":
        header += f" ({args.archived} archived)"
    if args.liveness != "include":
        header += f" (liveness={args.liveness})"
    out.append(header)
    out.append("")
    for c in children:
        d = c["data"]
        tag = " 📦" if d.get("archived") else ""
        if c.get("_liveness") and c["_liveness"][0] != "live":
            tag += f" `[{c['_liveness'][0]}]`"
        if c["kind"] == "t3":  # post
            out.append(f"### [post]{tag} {d['title']}")
            out.append(f"r/{d['subreddit']} • score {d['score']} • {fmt_when(d['created_utc'])}")
            out.append(f"https://www.reddit.com{d['permalink']}")
            if d.get("selftext"):
                out.append(truncate(d["selftext"], 300))
        elif c["kind"] == "t1":  # comment
            out.append(f"### [comment]{tag} in r/{d['subreddit']}")
            on_post = d.get("link_title") or ""
            out.append(f'on "{on_post}" • {d["score"]} pts • {fmt_when(d["created_utc"])}')
            out.append(f"https://www.reddit.com{d['permalink']}")
            out.append(truncate(d.get("body", ""), 300))
        out.append("")
    print("\n".join(out))


def cmd_search(args):
    sub_norm = normalize_sub(args.sub) if args.sub else None
    via = pick_via(args, "search")
    children = None

    if via == "arctic-shift":
        params = {
            "query": args.query,
            "limit": min(args.limit, 100),
            "sort": "desc",
        }
        if sub_norm:
            params["subreddit"] = sub_norm
        after = arctic_after_from_t(args.t)
        if after:
            params["after"] = after
        resp, err = fetch_arctic_try("/api/posts/search", params)
        if err is None:
            children = arctic_to_children(resp, "t3")
        elif args.via == "auto":
            print(f"# arctic-shift failed ({err}); falling back to reddit", file=sys.stderr)
            via = "reddit"
        else:
            die(f"Arctic Shift: {err}")

    if children is None:
        params = {"q": args.query, "limit": args.limit, "sort": args.sort, "t": args.t, "raw_json": 1}
        if sub_norm:
            params["restrict_sr"] = "on"
            path = f"/r/{sub_norm}/search"
        else:
            path = "/search"
        j = fetch_reddit(path, params)
        children = j["data"]["children"]

    children = filter_archived(children, args.archived)
    children = annotate_liveness(children, args.max_age_days or None)
    if args.verify_live != "none":
        children = verify_live_status(children, args.verify_live)
    children = filter_by_liveness(children, args.liveness)
    header = f"# Reddit search" + (f" in r/{sub_norm}" if sub_norm else "") + f": {args.query}"
    header += f" — via {via}"
    if args.archived != "include":
        header += f", {args.archived} archived"
    if args.liveness != "include":
        header += f", liveness={args.liveness}"
    out = [header, ""]
    for c in children:
        d = c["data"]
        tag = " 📦" if d.get("archived") else ""
        if c.get("_liveness"):
            status, _ = c["_liveness"]
            if status != "live":
                tag += f" `[{status}]`"
        out.append(f"## {d['title']}{tag}")
        out.append(
            f"r/{d['subreddit']} • by u/{d['author']} • score {d.get('score', 0)} • {d.get('num_comments', 0)} comments • {fmt_when(d['created_utc'])}"
        )
        out.append(f"https://www.reddit.com{d['permalink']}")
        if d.get("selftext"):
            body = d["selftext"].strip() if args.with_context else truncate(d["selftext"], 300)
            out.append("")
            out.append(body)
        if args.with_context and args.with_context > 0:
            post_id = d.get("id") or extract_post_id(d.get("permalink", ""))
            if post_id:
                tree, c_err = fetch_comments_for_post(post_id, args.with_context)
                if c_err is None and tree:
                    out.append("")
                    out.append(f"**Top {len(tree)} comments:**")
                    out.extend(render_comment_tree(tree, max_depth=2, body_chars=400))
                elif c_err is not None:
                    out.append(f"\n_(comments unavailable: {c_err})_")
        out.append("")
    print("\n".join(out))


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(prog="reddit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)

    via_parent = argparse.ArgumentParser(add_help=False)
    via_parent.add_argument(
        "--via", choices=["reddit", "arctic-shift", "auto"], default="auto",
        help="data source. auto picks arctic-shift where supported (search, post, user, feed --sort new), "
             "reddit elsewhere, and falls back to reddit on arctic-shift failure.",
    )

    liveness_parent = argparse.ArgumentParser(add_help=False)
    liveness_parent.add_argument(
        "--liveness", choices=["include", "exclude", "only", "flag"], default="include",
        help="heuristic check for likely-dead posts (removed/deleted/archived/suspicious). "
             "exclude=drop dead, only=show dead only, flag=keep all but annotate. "
             "Useful when Arctic Shift's status snapshot is stale.",
    )
    liveness_parent.add_argument(
        "--max-age-days", type=int, default=0,
        help="treat posts older than N days as likely_archived (Reddit auto-archives at ~180). 0 disables.",
    )
    liveness_parent.add_argument(
        "--verify-live", choices=["none", "suspect", "all"], default="none",
        help="open each post in a headless browser to ground-truth its current status. "
             "suspect=verify only heuristic-flagged posts (fast); all=verify every result (slow). "
             "Requires playwright + chromium installed.",
    )

    r = sp.add_parser("rules", help="subreddit rules")
    r.add_argument("sub")
    r.set_defaults(fn=cmd_rules)

    a = sp.add_parser("about", help="subreddit metadata + sidebar")
    a.add_argument("sub")
    a.set_defaults(fn=cmd_about)

    w = sp.add_parser("wiki", help="subreddit wiki page")
    w.add_argument("sub")
    w.add_argument("page", nargs="?", default=None)
    w.set_defaults(fn=cmd_wiki)

    f = sp.add_parser("feed", help="subreddit feed", parents=[via_parent, liveness_parent])
    f.add_argument("sub")
    f.add_argument("--sort", choices=["hot", "new", "top", "rising"], default="hot")
    f.add_argument("--t", choices=["hour", "day", "week", "month", "year", "all"], default="day",
                   help="time window (only used with --sort top)")
    f.add_argument("--limit", type=int, default=25)
    f.add_argument("--archived", choices=["include", "exclude", "only"], default="include",
                   help="filter by archived status (default: include all)")
    f.set_defaults(fn=cmd_feed)

    po = sp.add_parser("post", help="post body + top comments", parents=[via_parent, liveness_parent])
    po.add_argument("target", help="post URL or bare post id")
    po.add_argument("--limit", type=int, default=20)
    po.add_argument("--depth", type=int, default=2)
    po.set_defaults(fn=cmd_post)

    u = sp.add_parser("user", help="user profile + activity", parents=[via_parent, liveness_parent])
    u.add_argument("name")
    u.add_argument("--what", choices=["submitted", "comments", "overview"], default="overview")
    u.add_argument("--limit", type=int, default=15)
    u.add_argument("--archived", choices=["include", "exclude", "only"], default="include",
                   help="filter by archived status (default: include all)")
    u.set_defaults(fn=cmd_user)

    s = sp.add_parser("search", help="search reddit", parents=[via_parent, liveness_parent])
    s.add_argument("query")
    s.add_argument("--sub", default=None, help="restrict to a subreddit")
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--sort", choices=["relevance", "hot", "top", "new", "comments"], default="relevance")
    s.add_argument("--t", choices=["hour", "day", "week", "month", "year", "all"], default="all",
                   help="time window for results")
    s.add_argument("--archived", choices=["include", "exclude", "only"], default="include",
                   help="filter by archived status (default: include all)")
    s.add_argument("--with-context", type=int, default=0, metavar="N",
                   help="inline the full body and top N comments per result. 0 disables.")
    s.set_defaults(fn=cmd_search)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

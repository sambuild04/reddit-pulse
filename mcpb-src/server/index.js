#!/usr/bin/env node
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { spawn } from "node:child_process";

const PY = (process.env.REDDIT_PARSER_PYTHON || "").trim() || "/usr/bin/python3";
const SCRIPT = process.env.REDDIT_PARSER_SCRIPT;

if (!SCRIPT) {
  console.error("REDDIT_PARSER_SCRIPT env var must point to reddit.py");
  process.exit(1);
}

function run(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(PY, [SCRIPT, ...args], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString()));
    child.stderr.on("data", (d) => (stderr += d.toString()));
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`reddit.py exited ${code}: ${stderr || stdout}`));
    });
  });
}

function flagArgs(input, mapping) {
  const out = [];
  for (const [key, flag] of Object.entries(mapping)) {
    const v = input[key];
    if (v === undefined || v === null || v === "") continue;
    if (typeof v === "boolean") {
      if (v) out.push(flag);
    } else {
      out.push(flag, String(v));
    }
  }
  return out;
}

const TOOLS = {
  search_reddit: {
    description:
      "Search Reddit by keyword with optional live-status verification. By default opens every result in a headless browser to ground-truth its current status and excludes mod-removed, OP-deleted, and archived posts — so you only see threads you can actually engage with. Returns markdown with post titles, dates (UTC + relative), scores, comment counts, and (with `with_context`) full body + top N comments per result.",
    inputSchema: {
      type: "object",
      properties: {
        source: { type: "string", enum: ["reddit", "hn"], default: "reddit", description: "Which platform to search. 'reddit' (default) uses Reddit's JSON / Arctic Shift. 'hn' uses Hacker News's Algolia search. When 'hn', `subreddit` is ignored and `verify_live` is skipped (HN's API returns deleted/dead flags directly)." },
        query: { type: "string", description: "Search keywords" },
        subreddit: { type: "string", description: "Restrict to a subreddit (Reddit only — ignored if source='hn')" },
        sort: { type: "string", enum: ["relevance", "hot", "top", "new", "comments"], default: "relevance" },
        t: { type: "string", enum: ["hour", "day", "week", "month", "year", "all"], default: "all", description: "Time window" },
        limit: { type: "integer", default: 15, minimum: 1, maximum: 100 },
        via: { type: "string", enum: ["auto", "reddit", "arctic-shift"], default: "auto" },
        archived: { type: "string", enum: ["include", "exclude", "only"], default: "include" },
        liveness: { type: "string", enum: ["include", "exclude", "only", "flag"], default: "exclude", description: "Filter posts the system judges as non-live (heuristic + verify_live combined). 'exclude' (default) drops them; 'flag' keeps but annotates; 'only' returns only the dead ones; 'include' returns everything raw." },
        max_age_days: { type: "integer", default: 180, description: "Treat posts older than this as likely_archived (180 = Reddit's default archive window). 0 disables this check." },
        verify_live: { type: "string", enum: ["none", "suspect", "all"], default: "all", description: "Open each result in headless Chromium to read its real current status (deleted / removed by mods / removed by Reddit's filters / archived / live). 'all' (default) verifies every result — slow (~3s/post) but catches mod-removals invisible to the heuristic. 'suspect' verifies only heuristic-flagged posts (fast, but misses normally-scored mod-removed posts). 'none' skips verification entirely and you WILL get stale-removed posts back. Requires playwright + chromium installed." },
        with_context: { type: "integer", default: 0, description: "Inline full body + top N comments per result. 0 disables." },
      },
      required: ["query"],
    },
    build: (i) => [
      "search",
      i.query,
      ...flagArgs(i, {
        source: "--source",
        subreddit: "--sub",
        sort: "--sort",
        t: "--t",
        limit: "--limit",
        via: "--via",
        archived: "--archived",
        liveness: "--liveness",
        max_age_days: "--max-age-days",
        verify_live: "--verify-live",
        with_context: "--with-context",
      }),
    ],
  },
  get_post: {
    description:
      "Fetch a single Reddit post — full body + top comments tree — by URL or bare post id. By default verifies the post's current live status by opening the URL in a headless browser. If the post was deleted by OP, mod-removed, filter-removed, or archived since the Arctic Shift snapshot, a prominent warning header is prepended so you know the body shown is from an archived snapshot and the post is no longer engageable.",
    inputSchema: {
      type: "object",
      properties: {
        source: { type: "string", enum: ["reddit", "hn"], default: "reddit", description: "Platform. 'hn' fetches via Hacker News Firebase API; target should be an HN id, news.ycombinator.com URL, or item link." },
        target: { type: "string", description: "Reddit post URL/id (source=reddit) or HN item id/URL (source=hn)" },
        limit: { type: "integer", default: 20, description: "Max number of top comments" },
        depth: { type: "integer", default: 2, description: "Max comment nesting depth" },
        via: { type: "string", enum: ["auto", "reddit", "arctic-shift"], default: "auto", description: "Reddit backend selection — ignored if source='hn'" },
        verify_live: {
          type: "string",
          enum: ["none", "all"],
          default: "all",
          description:
            "Ground-truth the post's live status via headless Chromium. 'all' (default) opens the URL and reads the current status banner — adds ~5-8s but tells you whether the post is still engageable. 'none' skips the check and returns the archived body without any status info (faster but you WILL get OP-deleted / mod-removed posts back without warning). Requires playwright + chromium.",
        },
      },
      required: ["target"],
    },
    build: (i) => [
      "post",
      i.target,
      ...flagArgs(i, {
        source: "--source",
        limit: "--limit",
        depth: "--depth",
        via: "--via",
        verify_live: "--verify-live",
      }),
    ],
  },
  get_subreddit_feed: {
    description: "Get a subreddit's feed (hot/new/top/rising) as a markdown list.",
    inputSchema: {
      type: "object",
      properties: {
        source: { type: "string", enum: ["reddit", "hn"], default: "reddit", description: "Platform. When 'hn', the `subreddit` arg is ignored and sort can include best/ask/show/job." },
        subreddit: { type: "string", description: "Subreddit name (Reddit only; ignored if source='hn')" },
        sort: { type: "string", enum: ["hot", "new", "top", "rising", "best", "ask", "show", "job"], default: "hot", description: "hot/new/top/rising for both. best/ask/show/job are HN-only." },
        t: { type: "string", enum: ["hour", "day", "week", "month", "year", "all"], default: "day", description: "Time window (only meaningful with sort=top)" },
        limit: { type: "integer", default: 25 },
        via: { type: "string", enum: ["auto", "reddit", "arctic-shift"], default: "auto" },
        archived: { type: "string", enum: ["include", "exclude", "only"], default: "include" },
        liveness: { type: "string", enum: ["include", "exclude", "only", "flag"], default: "exclude", description: "Drop posts judged non-live (heuristic + verify_live combined)." },
        max_age_days: { type: "integer", default: 180, description: "Treat posts older than this as likely_archived. 0 disables." },
        verify_live: { type: "string", enum: ["none", "suspect", "all"], default: "all", description: "Ground-truth each result via headless browser. 'all' (default) verifies every post — catches mod-removals invisible to the heuristic. 'none' returns whatever the snapshot said. Requires playwright + chromium." },
      },
      required: ["subreddit"],
    },
    build: (i) => [
      "feed",
      ...(i.source === "hn" ? [] : [i.subreddit || ""]),
      ...flagArgs(i, {
        source: "--source",
        sort: "--sort",
        t: "--t",
        limit: "--limit",
        via: "--via",
        archived: "--archived",
        liveness: "--liveness",
        max_age_days: "--max-age-days",
        verify_live: "--verify-live",
      }),
    ],
  },
  get_user_activity: {
    description: "Get a Reddit user's recent submissions, comments, or overview, plus profile karma and signup date.",
    inputSchema: {
      type: "object",
      properties: {
        source: { type: "string", enum: ["reddit", "hn"], default: "reddit", description: "Platform. 'hn' fetches a HN profile (karma, created, about) and recent submissions/comments via Firebase." },
        username: { type: "string", description: "Reddit username or HN handle (no u/ prefix needed)" },
        what: { type: "string", enum: ["submitted", "comments", "overview"], default: "overview" },
        limit: { type: "integer", default: 15 },
        via: { type: "string", enum: ["auto", "reddit", "arctic-shift"], default: "auto" },
        archived: { type: "string", enum: ["include", "exclude", "only"], default: "include" },
        liveness: { type: "string", enum: ["include", "exclude", "only", "flag"], default: "exclude", description: "Drop posts/comments judged non-live (heuristic + verify_live combined)." },
        max_age_days: { type: "integer", default: 180, description: "Treat items older than this as likely_archived. 0 disables." },
        verify_live: { type: "string", enum: ["none", "suspect", "all"], default: "all", description: "Ground-truth each item via headless browser. 'all' (default) verifies every result. Requires playwright + chromium." },
      },
      required: ["username"],
    },
    build: (i) => [
      "user",
      i.username,
      ...flagArgs(i, {
        source: "--source",
        what: "--what",
        limit: "--limit",
        via: "--via",
        archived: "--archived",
        liveness: "--liveness",
        max_age_days: "--max-age-days",
        verify_live: "--verify-live",
      }),
    ],
  },
  get_subreddit_rules: {
    description: "Get a subreddit's rules list plus Reddit-wide site rules.",
    inputSchema: {
      type: "object",
      properties: { subreddit: { type: "string" } },
      required: ["subreddit"],
    },
    build: (i) => ["rules", i.subreddit],
  },
  get_subreddit_about: {
    description: "Get a subreddit's metadata: subscribers, active users, description, sidebar.",
    inputSchema: {
      type: "object",
      properties: { subreddit: { type: "string" } },
      required: ["subreddit"],
    },
    build: (i) => ["about", i.subreddit],
  },
  get_subreddit_wiki: {
    description: "Fetch a subreddit wiki page (default: index).",
    inputSchema: {
      type: "object",
      properties: {
        subreddit: { type: "string" },
        page: { type: "string", default: "index" },
      },
      required: ["subreddit"],
    },
    build: (i) => ["wiki", i.subreddit, ...(i.page ? [i.page] : [])],
  },
};

const server = new Server(
  { name: "forum-pulse", version: "0.6.2" },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: Object.entries(TOOLS).map(([name, t]) => ({
    name,
    description: t.description,
    inputSchema: t.inputSchema,
  })),
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const tool = TOOLS[req.params.name];
  if (!tool) {
    return {
      content: [{ type: "text", text: `Unknown tool: ${req.params.name}` }],
      isError: true,
    };
  }
  try {
    const args = tool.build(req.params.arguments || {});
    const out = await run(args);
    return { content: [{ type: "text", text: out }] };
  } catch (e) {
    return {
      content: [{ type: "text", text: String(e.message || e) }],
      isError: true,
    };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);

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
      "Search Reddit by keyword. Returns markdown with post titles, dates (UTC + relative), scores, comment counts, and (optionally) full body + top comments per result.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search keywords" },
        subreddit: { type: "string", description: "Restrict to a subreddit (e.g. 'LearnJapanese' or 'r/LearnJapanese')" },
        sort: { type: "string", enum: ["relevance", "hot", "top", "new", "comments"], default: "relevance" },
        t: { type: "string", enum: ["hour", "day", "week", "month", "year", "all"], default: "all", description: "Time window" },
        limit: { type: "integer", default: 15, minimum: 1, maximum: 100 },
        via: { type: "string", enum: ["auto", "reddit", "arctic-shift"], default: "auto" },
        archived: { type: "string", enum: ["include", "exclude", "only"], default: "include" },
        liveness: { type: "string", enum: ["include", "exclude", "only", "flag"], default: "include", description: "Filter posts that look deleted/removed/archived via heuristic." },
        max_age_days: { type: "integer", default: 0, description: "Treat posts older than this as likely_archived (180 = Reddit default)." },
        verify_live: { type: "string", enum: ["none", "suspect", "all"], default: "none", description: "Open posts in headless Chromium to ground-truth status. Requires playwright + chromium." },
        with_context: { type: "integer", default: 0, description: "Inline full body + top N comments per result. 0 disables." },
      },
      required: ["query"],
    },
    build: (i) => [
      "search",
      i.query,
      ...flagArgs(i, {
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
    description: "Fetch a single Reddit post (full body + top comments tree) by URL or bare post id.",
    inputSchema: {
      type: "object",
      properties: {
        target: { type: "string", description: "Post URL (full reddit.com link) or bare id like 'abc123'" },
        limit: { type: "integer", default: 20, description: "Max number of top comments" },
        depth: { type: "integer", default: 2, description: "Max comment nesting depth" },
        via: { type: "string", enum: ["auto", "reddit", "arctic-shift"], default: "auto" },
      },
      required: ["target"],
    },
    build: (i) => [
      "post",
      i.target,
      ...flagArgs(i, { limit: "--limit", depth: "--depth", via: "--via" }),
    ],
  },
  get_subreddit_feed: {
    description: "Get a subreddit's feed (hot/new/top/rising) as a markdown list.",
    inputSchema: {
      type: "object",
      properties: {
        subreddit: { type: "string", description: "Subreddit name (no r/ prefix needed)" },
        sort: { type: "string", enum: ["hot", "new", "top", "rising"], default: "hot" },
        t: { type: "string", enum: ["hour", "day", "week", "month", "year", "all"], default: "day", description: "Time window (only meaningful with sort=top)" },
        limit: { type: "integer", default: 25 },
        via: { type: "string", enum: ["auto", "reddit", "arctic-shift"], default: "auto" },
        archived: { type: "string", enum: ["include", "exclude", "only"], default: "include" },
        liveness: { type: "string", enum: ["include", "exclude", "only", "flag"], default: "include" },
        max_age_days: { type: "integer", default: 0 },
        verify_live: { type: "string", enum: ["none", "suspect", "all"], default: "none" },
      },
      required: ["subreddit"],
    },
    build: (i) => [
      "feed",
      i.subreddit,
      ...flagArgs(i, {
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
        username: { type: "string", description: "Reddit username (no u/ prefix needed)" },
        what: { type: "string", enum: ["submitted", "comments", "overview"], default: "overview" },
        limit: { type: "integer", default: 15 },
        via: { type: "string", enum: ["auto", "reddit", "arctic-shift"], default: "auto" },
        archived: { type: "string", enum: ["include", "exclude", "only"], default: "include" },
        liveness: { type: "string", enum: ["include", "exclude", "only", "flag"], default: "include" },
        max_age_days: { type: "integer", default: 0 },
        verify_live: { type: "string", enum: ["none", "suspect", "all"], default: "none" },
      },
      required: ["username"],
    },
    build: (i) => [
      "user",
      i.username,
      ...flagArgs(i, {
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
  { name: "reddit-parser", version: "0.5.0" },
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

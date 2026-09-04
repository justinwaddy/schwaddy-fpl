/*
 * Punctual trigger for the refresh workflow.
 *
 * GitHub's own `schedule:` crons are queued on shared runners and are
 * routinely hours late - measured over twelve consecutive scheduled runs
 * of update.yml, the median was 152 minutes late, the worst 305, none
 * within five minutes, and the 13:45 UTC slot never fired at all, because
 * GitHub silently drops a scheduled run when the queue is busy. That is
 * fine for a nightly job and useless for a dashboard whose whole point is
 * being right before a deadline.
 *
 * A workflow dispatched through the API is not queued that way: it starts
 * within seconds. So this worker holds the real schedule, and GitHub's
 * cron block stays only as a backstop for the day Cloudflare is down.
 *
 * Cloudflare Cron Triggers fire on time and cost nothing on the free
 * plan. Deploy: see cron/README.md. The only secret is GH_TOKEN, a
 * fine-grained token limited to this one repo with Actions: read+write.
 */
const REPO = "justinwaddy/schwaddy-fpl";
const WORKFLOW = "update.yml";
const REF = "main";

// The suggest-a-roast box on the per-manager sites posts here. The pages
// are static, so they cannot store anything themselves; this hands the
// suggestion to a workflow, which appends it to data/roasts.json. That
// needs no permission beyond the Actions write this token already has -
// the workflow does the committing with its own credentials.
const SUGGEST_WORKFLOW = "suggest.yml";
const MAX_TEXT = 500;
// Only these six are in the league, so only these six can be named. A
// dispatch is cheap but not free, and this is a public endpoint.
const ENTRIES = {
  45811: "Edward", 282287: "Ben C", 299912: "Marcus",
  363607: "Ben D", 372099: "Justin", 421435: "Robert",
};
// Browsers send Origin on a cross-origin POST. This will not stop anyone
// determined, since the sites are public and so is this worker, but it
// turns away everything casual.
const ALLOWED_ORIGINS = [
  "https://justinwaddy.github.io",
  "https://justinwaddy.co.uk",
  "https://www.justinwaddy.co.uk",
];

// If a run is already this new, the dispatch is skipped: GitHub's own
// backstop cron, or a push, has already done the work. Keeps the two
// triggers from stacking two refits on top of each other.
const FRESH_MIN = 20;

// The schedule, and what each slot is for. Keys are the cron expressions
// in wrangler.toml; Cloudflare hands the matched one to scheduled().
const SLOTS = {
  "35 8 * * *":  { news_only: false, what: "morning full refresh, after scores finalise" },
  "45 13 * * *": { news_only: true,  what: "after the 12:30 kick-off finishes" },
  "0 17 * * *":  { news_only: true,  what: "after the 15:00 kick-off finishes" },
  "45 17 * * 4": { news_only: true,  what: "Thursday, just after waivers process" },
  "20 22 * * *": { news_only: true,  what: "after the 20:00 kick-off finishes" },
};

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(run(event.cron, env));
  },
  // A plain status page, so "is the scheduler alive" is one click. It
  // makes no GitHub call, so it cannot be used to burn the rate limit and
  // it never touches the token.
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname === "/suggest") return suggest(req, env);
    if (url.pathname !== "/" && url.pathname !== "/status") {
      return json({ error: "not found" }, 404);
    }
    return json({
      repo: REPO, workflow: WORKFLOW, ref: REF,
      token_configured: !!env.GH_TOKEN,
      fresh_minutes: FRESH_MIN,
      slots: Object.entries(SLOTS).map(([cron, s]) => ({ cron, ...s })),
      now: new Date().toISOString(),
    });
  },
};

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body, null, 2), {
    status, headers: { "Content-Type": "application/json; charset=utf-8", ...extra },
  });
}

function cors(origin) {
  const ok = origin && ALLOWED_ORIGINS.includes(origin);
  return {
    "Access-Control-Allow-Origin": ok ? origin : ALLOWED_ORIGINS[0],
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
  };
}

/* A suggested headline or roast, on its way to data/roasts.json. */
async function suggest(req, env) {
  const origin = req.headers.get("Origin") || "";
  const h = cors(origin);
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: h });
  if (req.method !== "POST") return json({ error: "post only" }, 405, h);
  if (origin && !ALLOWED_ORIGINS.includes(origin)) {
    return json({ error: "not allowed from there" }, 403, h);
  }
  if (!env.GH_TOKEN) return json({ error: "not configured yet" }, 503, h);

  let body;
  try { body = await req.json(); } catch { return json({ error: "send JSON" }, 400, h); }

  const text = String(body && body.text || "").trim().replace(/\s+/g, " ");
  if (text.length < 4) return json({ error: "say a bit more than that" }, 400, h);
  if (text.length > MAX_TEXT) return json({ error: `keep it under ${MAX_TEXT} characters` }, 400, h);

  const from = ENTRIES[body.from];
  if (!from) return json({ error: "unknown sender" }, 400, h);
  const about = body.about == null ? "" : ENTRIES[body.about];
  if (body.about != null && !about) return json({ error: "unknown target" }, 400, h);

  try {
    await gh(`/repos/${REPO}/actions/workflows/${SUGGEST_WORKFLOW}/dispatches`, env, {
      method: "POST",
      body: JSON.stringify({ ref: REF, inputs: { from_name: from, about_name: about, text } }),
    });
  } catch (e) {
    console.log(`suggest dispatch failed: ${e.message}`);
    return json({ error: "could not file that, try again in a minute" }, 502, h);
  }
  console.log(`suggestion from ${from} about ${about || "the league"}: ${text.slice(0, 80)}`);
  return json({ ok: true }, 200, h);
}

async function gh(path, env, init = {}) {
  const r = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "schwaddy-cron",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`.slice(0, 300));
  return r.status === 204 ? null : r.json();
}

/* Minutes since the newest run of the workflow, or Infinity if none. */
export async function minutesSinceLastRun(env, now = Date.now()) {
  const j = await gh(`/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=1`, env);
  const run = j && j.workflow_runs && j.workflow_runs[0];
  if (!run) return Infinity;
  return (now - Date.parse(run.created_at)) / 60000;
}

export async function run(cron, env, now = Date.now()) {
  const slot = SLOTS[cron];
  if (!slot) { console.log(`no slot for cron ${cron}, nothing to do`); return "unknown-slot"; }
  if (!env.GH_TOKEN) { console.log("GH_TOKEN is not set: cannot dispatch"); return "no-token"; }

  const age = await minutesSinceLastRun(env, now);
  if (age < FRESH_MIN) {
    console.log(`skip ${cron} (${slot.what}): a run started ${age.toFixed(1)} min ago`);
    return "skipped-fresh";
  }

  // A dispatch that fails is worth retrying: the whole point is that this
  // slot happens now, not whenever GitHub's own cron gets round to it.
  const body = JSON.stringify(
    slot.news_only ? { ref: REF, inputs: { news_only: true } } : { ref: REF });
  let last;
  for (let i = 0; i < 3; i++) {
    try {
      await gh(`/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, env,
               { method: "POST", body });
      console.log(`dispatched ${cron} (${slot.what}), news_only=${slot.news_only}`);
      return "dispatched";
    } catch (e) {
      last = e;
      console.log(`dispatch attempt ${i + 1} failed: ${e.message}`);
      if (i < 2) await new Promise(r => setTimeout(r, 2000 * (i + 1)));
    }
  }
  console.log(`DISPATCH FAILED for ${cron}: ${last && last.message}`);
  return "failed";
}

/*
 * Live-score proxy for the dashboard. A Cloudflare Worker (free tier).
 *
 * The FPL Draft API sends no CORS headers, so the page on GitHub Pages
 * cannot read it directly. This worker reads it server-side, trims the
 * ~600-player live feed down to the six squads in the league, bundles
 * everything the Live tab needs into ONE response, and caches that for
 * SNAP_TTL seconds. So however many people have the page open, FPL sees
 * at most one round of requests every 15s, and each viewer costs one
 * request per poll against the 100k/day free allowance.
 *
 * It does no scoring. Points, provisional bonus and provisional subs are
 * worked out in the page, so the worker never needs redeploying when the
 * rules or the presentation change - only when the API shape does.
 *
 * Deploy: see live/README.md. Nothing here is secret; the API is public.
 */
const DRAFT = "https://draft.premierleague.com/api";
// The draft fixtures endpoint reports minutes: 0 for the whole of a live
// match - checked against a real one, 0-2 after 35 minutes and still
// saying zero - while the classic game's fixture list has the clock. Same
// fixture ids, same team ids, so the minute is read across from there and
// everything else still comes from the draft side.
const CLASSIC = "https://fantasy.premierleague.com/api";
const LEAGUE = 9450;
const SNAP_TTL = 15;      // seconds a composed snapshot is served from cache;
                          // the page polls at this rate while a match is on
const BOOT_TTL = 3600;    // names and clubs change rarely; cache for an hour
const ETYPE = { 1: "GKP", 2: "DEF", 3: "MID", 4: "FWD" };

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export default {
  async fetch(req, env, ctx) {
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
    const url = new URL(req.url);
    if (url.pathname !== "/" && url.pathname !== "/snapshot") {
      return json({ error: "not found" }, 404);
    }
    const cache = caches.default;
    const key = new Request(`${url.origin}/snapshot`);
    const hit = await cache.match(key);
    if (hit) return hit;
    try {
      const snap = await compose(cache);
      const res = json(snap, 200, { "Cache-Control": `public, max-age=${SNAP_TTL}` });
      ctx.waitUntil(cache.put(key, res.clone()));
      return res;
    } catch (e) {
      // never cache a failure: the next poll should retry upstream
      return json({ error: String(e && e.message || e) }, 502,
                  { "Cache-Control": "no-store" });
    }
  },
};

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS, ...extra },
  });
}

async function get(url) {
  const r = await fetch(url, { headers: { "User-Agent": "schwaddy-fpl-live" } });
  if (!r.ok) throw new Error(`${r.status} from ${url}`);
  return r.json();
}

/* bootstrap-static is ~1MB; keep only what the page names players with */
async function bootstrap(cache, origin) {
  const key = new Request(`${origin}/boot`);
  const hit = await cache.match(key);
  if (hit) return hit.json();
  const b = await get(`${DRAFT}/bootstrap-static`);
  const elements = {};
  for (const e of b.elements) {
    elements[e.id] = [e.web_name, e.team, ETYPE[e.element_type] || "MID"];
  }
  const teams = {};
  for (const t of b.teams) teams[t.id] = t.short_name || t.name;
  const s = (b.settings && b.settings.squad) || {};
  const rules = { play: s.play || 11 };
  for (const k of ["GKP", "DEF", "MID", "FWD"]) {
    rules[`min_${k}`] = s[`min_play_${k}`];
    rules[`max_${k}`] = s[`max_play_${k}`];
  }
  const out = { elements, teams, rules };
  await cache.put(key, json(out, 200, { "Cache-Control": `public, max-age=${BOOT_TTL}` }));
  return out;
}

export async function compose(cache, origin = "https://live.invalid") {
  const game = await get(`${DRAFT}/game`);
  const gw = game.current_event;
  if (!gw) throw new Error("no current gameweek");

  const [boot, det, fixtures, live, clock] = await Promise.all([
    bootstrap(cache, origin),
    get(`${DRAFT}/league/${LEAGUE}/details`),
    get(`${DRAFT}/event/${gw}/fixtures`),
    get(`${DRAFT}/event/${gw}/live`),
    // never fatal: without it the page says LIVE instead of a minute
    get(`${CLASSIC}/fixtures/?event=${gw}`).catch(() => null),
  ]);

  // id first, then the pair of teams, so a mismatch in one numbering does
  // not silently hand a match somebody else's clock
  const clocks = {};
  for (const f of clock || []) {
    if (typeof f.minutes !== "number") continue;
    clocks[`i${f.id}`] = f.minutes;
    clocks[`t${f.team_h}v${f.team_a}`] = f.minutes;
  }
  const minuteOf = f => {
    const m = clocks[`i${f.id}`] ?? clocks[`t${f.team_h}v${f.team_a}`];
    return typeof m === "number" ? m : (f.minutes || 0);
  };

  // manager names the way weekly.py does it: first name, with a last
  // initial only when two managers share one (Ben C / Ben D)
  const entries = det.league_entries || [];
  const firsts = entries.map(e => e.player_first_name);
  const lentry = {};
  for (const e of entries) lentry[e.id] = e.entry_id;
  const standing = {};
  for (const s of det.standings || []) {
    const ent = lentry[s.league_entry];
    if (ent != null) standing[ent] = s;
  }

  const picks = await Promise.all(entries.map(e =>
    get(`${DRAFT}/entry/${e.entry_id}/event/${gw}`)
      .then(j => (j && j.picks) || []).catch(() => [])));

  const owned = new Set();
  const managers = entries.map((e, i) => {
    let name = e.player_first_name || e.entry_name;
    if (firsts.filter(f => f === e.player_first_name).length > 1 && e.player_last_name) {
      name = `${name} ${e.player_last_name[0]}`;
    }
    const s = standing[e.entry_id] || {};
    const ps = picks[i].map(p => [p.element, p.position]);
    for (const [id] of ps) owned.add(id);
    return {
      entry: e.entry_id, name, team: e.entry_name,
      rank: s.rank ?? null, total: s.total ?? null, event_total: s.event_total ?? null,
      picks: ps,
    };
  });

  // per-player live line, owned players only, plus which fixtures scored it
  // and how. `ex` is the API's own breakdown, [name, value, points] per
  // scoring stat per fixture, which is what lets the page show why a
  // player has the points he has rather than just the total.
  const elements = {};
  for (const id of owned) {
    const l = live.elements[id] || live.elements[String(id)] || {};
    const st = l.stats || {};
    const meta = boot.elements[id] || ["?", null, "MID"];
    const ex = {};
    for (const block of l.explain || []) {
      const [stats, fid] = block;
      if (fid == null) continue;
      ex[fid] = (stats || [])
        .filter(x => x && (x.points || x.value))
        .map(x => [x.name || x.stat, x.value ?? 0, x.points ?? 0]);
    }
    elements[id] = {
      n: meta[0], t: meta[1], p: meta[2],
      pts: st.total_points || 0, min: st.minutes || 0,
      bonus: st.bonus || 0, bps: st.bps || 0,
      // whether he was in the starting eleven, which is the only way to
      // tell a man who came on from one who has been substituted off:
      // both sit on fewer minutes than the match has been played
      starts: st.starts || 0,
      fx: Object.keys(ex).map(Number), ex,
    };
  }

  // fixtures with the whole bps table (a bonus can go to an unowned
  // player, so the ranking needs everyone) and whether bonus is official
  const fx = (fixtures || []).map(f => {
    const stat = k => ((live.fixtures || []).find(x => x.id === f.id) || f).stats
      ?.find(s => s.s === k);
    const bpsStat = stat("bps"), bonusStat = stat("bonus");
    const bps = [];
    for (const side of ["h", "a"]) {
      for (const r of (bpsStat && bpsStat[side]) || []) bps.push([r.element, r.value]);
    }
    const bonusIn = !!bonusStat && (bonusStat.h.length + bonusStat.a.length) > 0;
    return {
      id: f.id, h: f.team_h, a: f.team_a,
      hs: f.team_h_score, as: f.team_a_score,
      started: !!f.started, fin: !!(f.finished || f.finished_provisional),
      min: minuteOf(f), ko: f.kickoff_time, bonus_in: bonusIn, bps,
    };
  });

  return {
    gw, finished: !!game.current_event_finished,
    fetched: new Date().toISOString().slice(0, 19) + "Z",
    ttl: SNAP_TTL,
    teams: boot.teams, rules: boot.rules,
    fixtures: fx, elements, managers,
  };
}

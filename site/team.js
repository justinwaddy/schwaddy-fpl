/* Shared app for the per-manager sites under site/<name>/.
 *
 * Each site is an index.html of about twenty lines that sets window.TEAM
 * and loads this file, so there is one copy of the logic rather than six.
 *
 * It reads data/public.json and nothing else from the pipeline. That file
 * is built by src/schwaddy/public.py from a whitelist of fields the game
 * itself publishes, so none of the engine's projections can reach these
 * pages even through the network tab. The Live tab reads the same
 * Cloudflare worker the main dashboard uses, which is a proxy for FPL's
 * own live feed and carries no model either.
 *
 * Prices are fetched only where window.TEAM.prices is true.
 */
const RAW = "https://raw.githubusercontent.com/justinwaddy/schwaddy-fpl/main/data/";
const PUBLIC_URL = RAW + "public.json";
const NEWS_URL = RAW + "league_news.json";
const PRICES_URL = RAW + "prices.json";
// the live-score proxy, shared with the main dashboard (live/worker.js)
const LIVE_URL = "https://schwaddy-live.justinl-waddy.workers.dev/";
// where a suggested roast is posted (cron/worker.js). Empty disables the button.
const SUGGEST_URL = "https://schwaddy-cron.justinl-waddy.workers.dev/suggest";

const ME = (window.TEAM || {}).me;
const SHOW_PRICES = !!(window.TEAM || {}).prices;
let PUB = null, NEWS = null, PRICES = null, ERR = {};
// Every finished gameweek's squads. A quarter of a megabyte by May and
// most visits never look back, so it is fetched the first time somebody
// presses an arrow rather than on load - the same bargain as the stats
// file behind the player card.
let HIST = null, HISTREQ = false, GWVIEW = null;

const esc = x => String(x ?? "").replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const $ = id => document.getElementById(id);
const POSORD = { GKP: 0, DEF: 1, MID: 2, FWD: 3 };
// What every column means, in a hover. The tables are dense and most of
// the headings are three letters, so this is the difference between a
// table you can read and one you have to guess at.
const TIPS = {
  player: "The player. Click the name for his season and match by match.",
  pos: "Position: goalkeeper, defender, midfielder or forward.",
  next: "His club's next fixture. Upper case means at home, lower case away, a dash means no game that week.",
  pts: "Total points this season.",
  season: "His total points this season, across every gameweek.",
  GW: "Points in this gameweek, with provisional bonus and substitutions applied.",
  G: "Goals scored this season.",
  A: "Assists this season.",
  CS: "Clean sheets this season. Worth points to goalkeepers and defenders.",
  SV: "Saves this season. Goalkeepers earn a point for every three.",
  B: "Bonus points this season, the extra one to three awarded to the best performers in a match.",
  mins: "Minutes played this season.",
  form: "The game's form figure: average points per match over the last 30 days.",
  PPG: "Points per game: his season total divided by the games he has appeared in.",
  value: "His price in the classic game, in millions. Not used in draft, but a fair read on how the market rates him.",
  owner: "Which manager holds him, or free agent if nobody does.",
  played: "Minutes played in this gameweek, or when his match kicks off if it is still to come.",
  manager: "The manager and his team name.",
  bench: "Points sitting on the bench this gameweek. They do not count unless a substitution brings them on.",
  "to play": "How many of his eleven have a match still to come this gameweek.",
  "on pitch": "How many of his eleven are playing right now.",
  "GW#": "Where he finished in this gameweek alone, whatever the season table says.",
  "played": "How many of his fifteen got on the pitch this gameweek.",
  "0 min": "How many of his fifteen never got a minute - injured, suspended, dropped or an unused substitute.",
  "subs": "Automatic substitutions the game made for him: a bench player brought on because a starter did not play.",
  "top": "His highest scorer among the eleven that counted this gameweek.",
  "cost": "What team selection cost him: the best legal eleven out of his fifteen, less what his actual eleven scored. Zero means he could not have done better.",
  "best XI": "What his eleven would have scored with perfect hindsight - the best legal eleven out of all fifteen. The gap to GW is what team selection cost him.",
  "unfit": "Players in his squad as it stands now who are injured, suspended, doubtful or otherwise not available. This one is about today, not the gameweek above.",
};
const th = (label, cls, tip0) => {
  const tip = tip0 || TIPS[label];
  return `<th class="${cls || ""}"${tip ? ` title="${esc(tip)}"` : ""}>${esc(label)}</th>`;
};
const ths = (...cols) => cols.map(c => Array.isArray(c) ? th(c[0], c[1], c[2]) : th(c)).join("");
const S = (p, k) => (p.s && p.s[k] != null ? p.s[k] : 0);

function get(url, onOk, key) {
  fetch(url, { cache: "no-store" })
    .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(onOk)
    .catch(e => { ERR[key] = String(e.message || e); draw(); });
}

/* ---------------- shared bits ---------------- */
function club(p) {
  const t = PUB && PUB.teams && PUB.teams[p.team];
  return t ? t[0] : (typeof p.team === "string" ? p.team : "");
}
function clubLong(id) {
  const t = PUB && PUB.teams && PUB.teams[id];
  return t ? t[1] : id;
}
// Two Bens, so the game calls them Ben C and Ben D, which nobody in the
// league does. Renaming here rather than in the data covers all three
// sources - public.json, the live feed and the gameweek archive - from
// one place, and the feed's prose gets the same treatment because the
// news is written with the game's names in it.
const RENAME = { 282287: "Small Ben", 363607: "Big Ben" };
const RENAME_TEXT = [[/\bBen C\b/g, "Small Ben"], [/\bBen D\b/g, "Big Ben"]];
function who(entry, fallback) {
  return RENAME[entry] || fallback || null;
}
function reword(t) {
  let out = String(t == null ? "" : t);
  for (const [re, to] of RENAME_TEXT) out = out.replace(re, to);
  return out;
}
function mgrName(entry) {
  if (RENAME[entry]) return RENAME[entry];
  const m = (PUB && PUB.managers || []).find(x => x.entry === entry);
  return m ? m.name : null;
}
function priceOf(code) {
  if (!PRICES || !PRICES.players) return null;
  const row = PRICES.players[String(code)];
  return row ? row[0] : null;
}
function me() { return (PUB && PUB.managers || []).find(m => m.entry === ME) || null; }
function counting(p) { return (p.slot <= 11 && !p.subbed_out) || p.subbed_in; }
// Every player name on every table routes to the same card. public.json
// players carry `code`; the squads that come out of league.json and the
// live feed carry the element `id`, so either one resolves.
function nameTag(p) {
  const a = p.code != null ? `data-pk="${esc(p.code)}"`
    : (p.id != null ? `data-pkid="${esc(p.id)}"` : "");
  return `<span class="nm${a ? " pk" : ""}" ${a}>${esc(p.name)}</span>`;
}
function nameCell(p, extra) {
  return `<td>${extra || ""}${nameTag(p)}
    <span class="tm">${esc(club(p))}</span>${p.news ? `<span class="flag">${esc(p.news)}</span>` : ""}</td>`;
}

/* ---------------- the player card ----------------
 * Season totals and the match log, both straight from the game. The stats
 * file is a quarter of a megabyte and most visits never open a card, so it
 * is fetched the first time somebody asks for one rather than on load.
 */
const STATS_URL = RAW + "player_stats.json";
let STATS = null, STATSREQ = false, ID2CODE = {}, CARD = null;
function ensureStats(then) {
  if (STATS) { then(); return; }
  if (!STATSREQ) {
    STATSREQ = true;
    fetch(STATS_URL, { cache: "no-store" })
      .then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(j => {
        STATS = j;
        for (const c in j.players || {}) ID2CODE[j.players[c].id] = c;
        drawCard();
      })
      .catch(e => { ERR.stats = String(e.message || e); drawCard(); });
  }
  then();
}
document.addEventListener("click", ev => {
  const el = ev.target && ev.target.closest && ev.target.closest("[data-pk],[data-pkid]");
  if (!el) return;
  const code = el.dataset.pk || ID2CODE[el.dataset.pkid];
  CARD = code || { id: el.dataset.pkid };
  ensureStats(drawCard);
});
function closeCard() { CARD = null; drawCard(); }
window.addEventListener("keydown", e => { if (e.key === "Escape" && CARD) closeCard(); });
function drawCard() {
  let back = $("pkback");
  if (!CARD) { if (back) back.remove(); document.body.style.overflow = ""; return; }
  // clicked from a squad before the stats file landed: resolve the id now
  if (typeof CARD !== "string" && STATS) CARD = ID2CODE[CARD.id] || CARD;
  if (!back) {
    back = document.createElement("div");
    back.id = "pkback"; back.className = "back";
    back.addEventListener("click", e => { if (e.target === back) closeCard(); });
    document.body.appendChild(back);
  }
  document.body.style.overflow = "hidden";
  back.innerHTML = `<div class="modal">${cardHTML()}</div>`;
  const x = back.querySelector(".x");
  if (x) x.addEventListener("click", closeCard);
}
const tile = (l, v) => `<div class="pkt"><b>${v}</b><span>${esc(l)}</span></div>`;
// only worth a tile if the game actually reports it for him
const tileIf = (l, v, dp) => v == null ? "" :
  tile(l, dp == null ? v : (Math.round(v * 10 ** dp) / 10 ** dp).toFixed(dp));
/* Where he sits among everybody, and among his own position. Computed off
   the stats file on the fly - it is 652 players and one click. */
function pkRanks(code, S) {
  if (!STATS || !STATS.players || !S) return null;
  const all = Object.values(STATS.players);
  const pts = p => (p.s && p.s.total_points) || 0;
  const mine = pts(S);
  const played = all.filter(p => (p.s && p.s.minutes) || 0);
  const samePos = played.filter(p => p.pos === S.pos);
  const rank = list => list.filter(p => pts(p) > mine).length + 1;
  return { overall: rank(played), of: played.length,
           pos: rank(samePos), posOf: samePos.length, draft: S.draft_rank || null };
}
const PKSTATUS = { d: "A doubt", i: "Injured", s: "Suspended", u: "Unavailable", n: "Not available" };
const POSNAME = { GKP: "goalkeepers", DEF: "defenders", MID: "midfielders", FWD: "forwards" };
function cardHTML() {
  const code = typeof CARD === "string" ? CARD : null;
  const S = (STATS && STATS.players && code) ? STATS.players[code] : null;
  const P = ((PUB && PUB.players) || []).find(x => String(x.code) === String(code)) || null;
  if (!S && !P) {
    return `<div class="pkhd"><div><h2>Player</h2></div><button class="x">&#10005;</button></div>
      <div class="pkempty">${ERR.stats ? "Could not load the stats file (" + esc(ERR.stats) + ")."
        : "Loading his numbers&hellip;"}</div>`;
  }
  const name = (S && S.name) || (P && P.name) || "Player";
  const pos = (S && S.pos) || (P && P.pos) || "";
  const tid = P ? P.team : (S && S.team);
  const own = P && P.owner ? (mgrName(P.owner) || "owned") : "free agent";
  const st = (S && S.s) || {};
  const g = k => st[k] != null ? st[k] : 0;
  // The league's own headshots, keyed on the same code this file is keyed
  // on. Plenty of players have no photo - a summer signing usually does
  // not for a few weeks - and the request simply 404s, so the element
  // removes itself and the header closes up rather than showing a gap.
  const shot = code
    ? `<img class="pkface" alt="" loading="lazy" onerror="this.remove()"
        src="https://resources.premierleague.com/premierleague/photos/players/250x250/p${esc(code)}.png">`
    : "";
  let h = `<div class="pkhd">${shot}<div>
    <h2>${esc(name)} <span class="pos ${pos}">${pos}</span></h2>
    <div class="sub">${[S && S.full, clubLong(tid), own].filter(Boolean).map(esc).join(" &middot; ")}</div>
    </div><button class="x" title="Close">&#10005;</button></div>`;
  if (S && (S.news || (S.status && S.status !== "a"))) {
    h += `<div class="pkalert">${esc(PKSTATUS[S.status] || "Fitness note")}${
      S.news ? ": " + esc(S.news) : ""}</div>`;
  }
  h += `<div class="pkg">
    ${tile("points", g("total_points"))}${tile("per game", g("points_per_game"))}
    ${tile("form", g("form"))}${tile("minutes", g("minutes"))}
    ${tile("starts", g("starts"))}${tile("goals", g("goals_scored"))}
    ${tile("assists", g("assists"))}${tile(pos === "GKP" || pos === "DEF" ? "clean sheets" : "bonus",
      pos === "GKP" || pos === "DEF" ? g("clean_sheets") : g("bonus"))}
    ${pos === "GKP" ? tile("saves", g("saves")) : ""}
    ${P && P.next ? tile("next", esc(P.next)) : ""}</div>`;

  const R = pkRanks(code, S);
  if (R) {
    h += `<div class="pkrank">${[
      `<b>${ordinal(R.overall)}</b> of ${R.of} who have played, on points`,
      `<b>${ordinal(R.pos)}</b> of ${R.posOf} ${esc(POSNAME[S.pos] || S.pos)}`,
      R.draft ? `drafted <b>${ordinal(R.draft)}</b> overall by the game's own ranking` : "",
    ].filter(Boolean).join(" &middot; ")}</div>`;
  }

  const cols = (STATS && STATS.log_cols) || [];
  const log = (S && S.log) || [];

  // The underlying numbers: what he is doing rather than what he has been
  // paid for. Only the ones the game reports for him appear, so a keeper
  // does not get an empty expected-assists box.
  const per90 = k => st.minutes ? (g(k) * 90) / st.minutes : null;
  const und = [
    tileIf("pts / 90", st.minutes ? (g("total_points") * 90) / st.minutes : null, 1),
    tileIf("xG", st.expected_goals, 2),
    tileIf("xA", st.expected_assists, 2),
    st.expected_goals != null && st.goals_scored != null
      ? tile("vs xG", (g("goals_scored") - g("expected_goals") >= 0 ? "+" : "") +
          (g("goals_scored") - g("expected_goals")).toFixed(2)) : "",
    tileIf("threat", st.threat, 0),
    tileIf("creativity", st.creativity, 0),
    tileIf("influence", st.influence, 0),
    tileIf("ICT", st.ict_index, 1),
    tileIf("BPS", st.bps, 0),
    tileIf("def. actions", st.defensive_contribution, 0),
    tileIf("tackles", st.tackles, 0),
    tileIf("clearances, blocks, interceptions", st.clearances_blocks_interceptions, 0),
    tileIf("recoveries", st.recoveries, 0),
    pos === "GKP" ? tileIf("xG conceded", st.expected_goals_conceded, 2) : "",
  ].filter(Boolean);
  if (und.length) {
    h += `<div class="pksec">Underlying numbers</div><div class="pkg">${und.join("")}</div>`;
  }

  if (log.length) {
    const ip = k => cols.indexOf(k);
    const best = log.reduce((a, r) => (a && a[ip("pts")] >= r[ip("pts")] ? a : r), null);
    const blanks = log.filter(r => (r[ip("pts")] || 0) <= 2).length;
    const starts = log.filter(r => (r[ip("min")] || 0) >= 60).length;
    h += `<div class="pkrank">${[
      best ? `best week <b>${best[ip("pts")]}</b> in GW${best[ip("gw")]}` : "",
      `<b>${blanks}</b> of ${log.length} appearance${log.length > 1 ? "s" : ""} returned two points or fewer`,
      `<b>${starts}</b> full hour${starts === 1 ? "" : "s"} or more`,
    ].filter(Boolean).join(" &middot; ")}</div>`;
  }
  if (log.length) {
    const i = k => cols.indexOf(k);
    h += `<div class="wrap"><table class="pklog">
      <tr>${ths(["GW", "num"])}<th>opponent</th>${ths(["mins", "num"], ["pts", "num"],
        ["G", "num"], ["A", "num"], ["CS", "num"], ["B", "num"])}</tr>`;
    const tname = id => ((STATS && STATS.teams && STATS.teams[id]) || [])[0] || String(id);
    for (const r of [...log].reverse()) {
      const opp = tname(r[i("opp")]);
      h += `<tr><td class="num">${esc(r[i("gw")])}</td>
        <td><span class="run">${esc(r[i("home")] ? opp.toUpperCase() : opp.toLowerCase())}</span>
          <span class="tm">${r[i("home")] ? "home" : "away"}</span></td>
        <td class="num">${esc(r[i("min")])}</td><td class="num"><b>${esc(r[i("pts")])}</b></td>
        <td class="num">${esc(r[i("g")])}</td><td class="num">${esc(r[i("a")])}</td>
        <td class="num">${esc(r[i("cs")])}</td><td class="num">${esc(r[i("b")])}</td></tr>`;
    }
    h += `</table></div><div class="note">Every match he has appeared in this season, newest first.
      Upper case is a home game.</div>`;
  } else {
    h += `<div class="pkempty">${STATS ? "No appearances this season yet."
      : "Loading his match log&hellip;"}</div>`;
  }
  return h;
}

/* ---------------- My squad ---------------- */
function wantHistory() {
  if (HIST || HISTREQ) return;
  HISTREQ = true;
  get(RAW + "gw_history.json", j => { HIST = j; renderSquad(); }, "hist");
}
function histGWs() {
  return Object.keys((HIST && HIST.gws) || {}).map(Number).sort((a, b) => a - b);
}
/* The gameweek being looked at, and the squad that played it. Null view
   means the one public.json scored, which is what the page opens on. */
function gwBlock(m) {
  if (GWVIEW == null || !HIST) return null;
  const blk = (HIST.gws || {})[String(GWVIEW)];
  const row = blk && blk.managers && blk.managers[String(m.entry)];
  return row ? { gw: GWVIEW, ...row } : null;
}
function gwNav(cur) {
  const gws = histGWs();
  const all = gws.includes(cur) ? gws : gws.concat(cur).sort((a, b) => a - b);
  const at = all.indexOf(cur);
  const btn = (g, label, on) =>
    `<button class="gwb" ${on ? `data-gw="${g}"` : "disabled"}>${label}</button>`;
  return `<span class="gwnav">
    ${btn(all[at - 1], "&#9666;", at > 0)}
    <select class="gwsel">${all.map(g =>
      `<option value="${g}"${g === cur ? " selected" : ""}>GW${g}</option>`).join("")}</select>
    ${btn(all[at + 1], "&#9656;", at >= 0 && at < all.length - 1)}
  </span>`;
}
function renderSquad() {
  const sec = $("squad");
  if (!PUB) { sec.innerHTML = loading("squad"); return; }
  const m = me();
  if (!m) { sec.innerHTML = `<div class="card"><div class="note">No squad found for this team yet.</div></div>`; return; }
  const byCode = {}; (PUB.players || []).forEach(p => { byCode[p.id] = p; });
  wantHistory();
  // the gameweek on show: the one public.json scored unless an arrow has
  // moved us somewhere else in the archive
  const past = gwBlock(m);
  const view = past || { gw: PUB.gw, live: m.live, bench: m.bench, subs: m.subs,
                         squad: m.squad, gw_rank: m.gw_rank };
  const sq = view.squad || [];
  const xi = sq.filter(counting), bn = sq.filter(p => !counting(p));
  // The roster is who he holds right now; the gameweek table below is who
  // scored last week. Between waivers processing and the next deadline
  // those are different squads, so the current one leads.
  const roster = m.roster || [];
  const rcell = p => {
    const f = byCode[p.id] || {};
    return `<tr>${nameCell(p)}<td><span class="pos ${p.pos}">${p.pos}</span></td>
      <td class="run">${esc(p.next || "")}</td>
      <td class="num">${S(f, "total_points")}</td><td class="num">${S(f, "goals_scored")}</td>
      <td class="num">${S(f, "assists")}</td><td class="num">${S(f, "clean_sheets")}</td>
      <td class="num">${S(f, "bonus")}</td><td class="num">${S(f, "minutes")}</td>
      <td class="num">${S(f, "form")}</td><td class="num">${S(f, "points_per_game")}</td>
      ${SHOW_PRICES ? `<td class="num">${priceOf(p.code) != null ? "&pound;" + priceOf(p.code).toFixed(1) : "-"}</td>` : ""}</tr>`;
  };
  const rhead = `<tr>${th("player")}<th></th>${ths("next", ["pts", "num"], ["G", "num"],
    ["A", "num"], ["CS", "num"], ["B", "num"], ["mins", "num"], ["form", "num"], ["PPG", "num"])}
    ${SHOW_PRICES ? th("value", "num") : ""}</tr>`;
  const cell = p => {
    const full = byCode[p.id] || {};
    const mark = p.subbed_in ? `<span class="subin">IN</span> ` :
      p.subbed_out ? `<span class="subout">OUT</span> ` : "";
    const st = p.to_play ? `<span class="togo">to play</span>` :
      (p.mins ? `${p.mins}'` : `<span class="tm">did not play</span>`);
    return `<tr>${nameCell(p, mark)}
      <td><span class="pos ${p.pos}">${p.pos}</span></td>
      <td class="run">${esc(p.next || "")}</td>
      <td class="num">${st}</td><td class="num">${p.pts}</td>
      <td class="num">${S(full, "total_points")}</td>
      <td class="num">${S(full, "goals_scored")}</td>
      <td class="num">${S(full, "assists")}</td>
      <td class="num">${S(full, "minutes")}</td></tr>`;
  };
  const head = `<tr>${th("player")}<th></th>${ths("next", ["played", "num"], ["GW", "num"],
    ["season", "num"], ["G", "num"], ["A", "num"], ["mins", "num"])}</tr>`;
  sec.innerHTML = `${roster.length ? `<div class="card"><b class="h">Squad · ${esc(m.team)}</b>
    <div class="note">The fifteen you hold now, with this season's totals as the game reports them.</div>
    <div class="wrap"><table>${rhead}${roster.map(rcell).join("")}</table></div></div>` : ""}
    <div class="card scorecard"><b class="h">GW${view.gw} · ${view.live} pts ${gwNav(view.gw)}</b>
    <div class="lvstatus">${sq.filter(p => p.mins).length} of ${sq.length} played &middot;
      bench ${view.bench} &middot; ${view.subs || 0} automatic sub${view.subs === 1 ? "" : "s"}
      ${view.gw_rank ? `&middot; ${ordinal(view.gw_rank)} that week` : ""}
      ${past ? "" : `&middot; season ${m.total ?? "-"} pts &middot; ${ordinal(m.rank)} in the league`}</div>
    <div class="wrap"><table>${head}${xi.map(cell).join("")}
    <tr class="benchsep"><td colspan="9">Bench · ${view.bench} pts</td></tr>
    ${bn.map(cell).join("")}</table></div>
    <div class="note">The eleven that scored gameweek ${view.gw}, with the substitutions the game
    makes automatically when a week ends.${past ? "" : ` Waivers process the day before a deadline,
    so just after they run this is last week's team and the squad above is the current one.`}
    ${HIST ? "" : ERR.hist ? " (Earlier gameweeks unavailable: " + esc(ERR.hist) + ".)"
      : " Loading the earlier gameweeks&hellip;"}</div></div>
    <div class="card"><b class="h">What the columns mean</b>
    ${legend([["next", "his club's next fixture - capitals at home, lower case away, a dash means no game"],
      ["pts", "his points this season"], ["G", "goals"], ["A", "assists"],
      ["CS", "clean sheets"], ["B", "bonus points"], ["mins", "minutes this season"],
      ["form", "average points per match over the last thirty days"],
      ["PPG", "points per game he has appeared in"],
      ["played", "minutes in the gameweek below, or when his match kicks off"],
      ["GW", "his points in that gameweek"], ["season", "his points across the season"],
      ["IN / OUT", "an automatic substitution brought him on, or took him off"]]
      .concat(SHOW_PRICES ? [["value", "his price in the classic game, in millions"]] : []))}
    </div>`;
  sec.querySelectorAll("[data-gw]").forEach(b => b.addEventListener("click", () => {
    const g = +b.dataset.gw;
    GWVIEW = g === PUB.gw ? null : g; renderSquad();
  }));
  const sel = sec.querySelector(".gwsel");
  if (sel) sel.addEventListener("change", () => {
    const g = +sel.value;
    GWVIEW = g === PUB.gw ? null : g; renderSquad();
  });
}
function ordinal(n) {
  if (n == null) return "unranked";
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
// A hover tooltip is nothing on a phone, and these tables are mostly read
// on one. So every table also says what its columns are, in a line above
// it, short enough to skim past once you know.
function legend(pairs) {
  return `<div class="legend">${pairs.map(([k, v]) =>
    `<span><b>${esc(k)}</b> ${esc(v)}</span>`).join("")}</div>`;
}

/* ---------------- League ---------------- */
let LGOPEN = null;
// The best legal eleven out of all fifteen, scored with hindsight. In
// draft the only selection lever is who you leave out and in what bench
// order, so the gap between this and what he actually scored is the whole
// of what team selection cost him that week.
function bestEleven(squad) {
  const by = { GKP: [], DEF: [], MID: [], FWD: [] };
  for (const p of squad) (by[p.pos] || by.MID).push(p.pts || 0);
  for (const k in by) by[k].sort((a, b) => b - a);
  const take = (k, n) => by[k].slice(0, n).reduce((a, b) => a + b, 0);
  const enough = (k, n) => by[k].length >= n;
  let best = null;
  for (let d = 3; d <= 5; d++) for (let m = 2; m <= 5; m++) {
    const f = 11 - 1 - d - m;
    if (f < 1 || f > 3) continue;
    if (!enough("GKP", 1) || !enough("DEF", d) || !enough("MID", m) || !enough("FWD", f)) continue;
    const t = take("GKP", 1) + take("DEF", d) + take("MID", m) + take("FWD", f);
    if (best == null || t > best) best = t;
  }
  return best;
}
// The cron writes public.json four times a day. On a Saturday that is not
// often enough for a table, and the live worker already knows - so
// whenever the feed has a started fixture in a gameweek at least as new as
// the one public.json scored, the gameweek half of this table comes from
// the feed and moves with it. The season column stays on public.json,
// because the game only moves that when it processes the week.
function leagueLive() {
  if (!LIVE || !PUB) return null;
  if ((LIVE.gw ?? -1) < (PUB.gw ?? 0)) return null;
  if (!(LIVE.fixtures || []).some(f => f.started)) return null;
  // when the cron has already scored the gameweek the feed is reporting
  // and nothing is in play, public.json is the game's own word for it -
  // including the tie-breaks in its gameweek ranking
  const t = liveTable(LIVE);
  if (LIVE.gw === PUB.gw && !t.inplay.length) return null;
  const by = {};
  t.mgrs.forEach((m, i) => { by[m.entry] = { ...m, gw_rank: i + 1 }; });
  return { gw: LIVE.gw, by, inplay: t.inplay.length };
}
// Who counts as "ours": anyone one of the six holds, plus the fifty best
// players in the game on points, because a hamstring at the top of the
// board is a waiver claim whoever currently owns him.
const TOPN = 50;
function relevant() {
  const ps = (PUB && PUB.players) || [];
  const by = new Map();
  const add = p => { if (!by.has(p.code)) by.set(p.code, p); };
  for (const p of ps) if (p.owner != null) add(p);
  [...ps].sort((a, b) => (S(b, "total_points") || 0) - (S(a, "total_points") || 0))
    .slice(0, TOPN).forEach(add);
  return by;
}
/* Does this line of text name one of them? Built once per render. */
function relevantNames() {
  const names = [];
  for (const p of relevant().values()) {
    for (const nm of [p.name, p.full]) {
      const k = String(nm || "").trim();
      if (k.length >= 3) names.push(k.toLowerCase());
    }
  }
  if (!names.length) return null;
  return t => {
    const low = String(t || "").toLowerCase();
    return names.some(nm => low.includes(nm));
  };
}
function renderLeague() {
  const sec = $("league");
  if (!PUB) { sec.innerHTML = loading("league"); return; }
  const byId = {}; (PUB.players || []).forEach(p => { byId[p.id] = p; });
  const LV = leagueLive();
  // On the day of the first kick-off the table stops being about last
  // week. Nobody has scored yet, so every gameweek column reads zero and
  // opening a manager shows the fifteen he holds rather than the eleven
  // that played six days ago. The live feed takes over at kick-off.
  const ko = ((PUB.fixtures || [])[0] || [])[2];
  const soon = ko && (Date.parse(ko) - Date.now()) < 24 * 3600e3;
  const AHEAD = !LV && soon && PUB.next_gw > PUB.gw;
  const rows = [...(PUB.managers || [])].sort((a, b) => (b.total ?? 0) - (a.total ?? 0));
  const value = m => {
    if (!SHOW_PRICES || !PRICES) return null;
    let t = 0, n = 0;
    for (const p of m.squad) { const c = byId[p.id]; const v = c && priceOf(c.code); if (v) { t += v; n++; } }
    return n ? t : null;
  };
  const cols = 10 + (SHOW_PRICES ? 1 : 0);
  let h = `<div class="card"><b class="h">Gameweek ${LV ? LV.gw : AHEAD ? PUB.next_gw : PUB.gw}</b>
    ${AHEAD ? `<div class="lvstatus">Not started &middot; first kick-off ${esc(koText(ko))}</div>` : ""}
    ${LV && LV.inplay ? `<div class="lvstatus"><span class="livedot"></span><b>LIVE</b> &middot;
      ${LV.inplay} match${LV.inplay > 1 ? "es" : ""} in play &middot; updating as points land</div>` : ""}
    ${legend([["GW", "points this gameweek"], ["GW#", "where he finished it"],
      ["played", "of his fifteen who got minutes"], ["0 min", "who got none"],
      ["subs", "automatic substitutions made for him"], ["top", "his best scorer"],
      ["cost", "what the best legal eleven of his fifteen would have added"],
      ["unfit", "injured or doubtful in his squad right now"],
      ["season", "his total, as the game has it"]])}
    <div class="wrap"><table>
    <tr><th></th>${ths("manager", ["GW", "num"], ["GW#", "num"], ["played", "num"],
      ["0 min", "num"], ["subs", "num"], "top", ["cost", "num"], ["unfit", "num"],
      ["season", "num"])}${SHOW_PRICES ? th("value", "num") : ""}<th></th></tr>`;
  rows.forEach((m0, i) => {
    const lv = LV && LV.by[m0.entry];
    // roster and prices are public.json's; everything about the gameweek
    // comes from whichever of the two is further along
    const m = lv ? { ...m0, ...lv }
      : AHEAD ? { ...m0, live: 0, bench: 0, subs: 0, gw_rank: null,
                  squad: (m0.roster || []).map(p => ({ ...p, pts: 0, mins: 0, slot: 99 })) }
        : m0;
    const v = value(m0);
    const xi = (m.squad || []).filter(counting);
    // across all fifteen, not the eleven: after the automatic
    // substitutions the counting eleven has almost always all played, so
    // that version of the column would sit on 11 and 0 every week
    const played = (m.squad || []).filter(p => p.mins).length;
    const blanks = (m.squad || []).length - played;
    const top = xi.reduce((a, p) => (a && a.pts >= p.pts ? a : p), null);
    const best = bestEleven(m.squad || []);
    const cost = best == null ? null : Math.max(0, best - (m.live ?? 0));
    const unfit = (m.roster || []).filter(p => p.status && p.status !== "a").length;
    h += `<tr class="clk ${m.entry === ME ? "mine-row" : ""}" data-e="${m.entry}">
      <td class="tm">${i + 1}</td>
      <td><span class="nm">${esc(who(m.entry, m.name))}</span> <span class="tm">${esc(m.team)}</span></td>
      <td class="num">${m.live}</td>
      <td class="num"><span class="tm">${m.gw_rank ? ordinal(m.gw_rank) : "-"}</span></td>
      <td class="num">${played}</td>
      <td class="num${blanks ? " warn" : ""}">${blanks}</td>
      <td class="num"><span class="tm">${m.subs || 0}</span></td>
      <td>${top && top.pts ? `${nameTag(top)} <span class="tm">${top.pts}</span>` : `<span class="tm">-</span>`}</td>
      <td class="num${cost ? " warn" : ""}">${cost ? "-" + cost : "0"}</td>
      <td class="num${unfit ? " warn" : ""}">${unfit}</td>
      <td class="num">${m.total ?? "-"}</td>
      ${SHOW_PRICES ? `<td class="num">${v != null ? "&pound;" + v.toFixed(1) : "-"}</td>` : ""}
      <td class="chev">${LGOPEN === m.entry ? "&#9662;" : "&#9656;"}</td></tr>`;
    if (LGOPEN === m.entry) {
      h += `<tr><td colspan="${cols + 2}" style="padding:0 0 6px">${
        AHEAD ? rosterHTML(m0, byId) : squadHTML(m, byId)}</td></tr>`;
    }
  });
  h += `</table></div><div class="note">Click a manager for his squad.</div></div>`;
  sec.innerHTML = h;
  sec.querySelectorAll("[data-e]").forEach(r => r.addEventListener("click", () => {
    const e = +r.dataset.e; LGOPEN = (LGOPEN === e ? null : e); renderLeague();
  }));
}
/* The fifteen he holds now, for the gameweek that has not started. There
   is no eleven yet - the game only fixes one at the deadline - so this is
   the roster in position order, not an XI and a bench. */
function rosterHTML(m, byId) {
  const cell = p => {
    const f = byId[p.id] || {};
    return `<tr>${nameCell(p)}<td><span class="pos ${p.pos}">${p.pos}</span></td>
      <td class="run">${esc(p.next || "")}</td>
      <td class="num">${S(f, "total_points")}</td><td class="num">${S(f, "goals_scored")}</td>
      <td class="num">${S(f, "assists")}</td><td class="num">${S(f, "minutes")}</td>
      ${SHOW_PRICES ? `<td class="num">${priceOf(f.code) != null ? "&pound;" + priceOf(f.code).toFixed(1) : "-"}</td>` : ""}</tr>`;
  };
  return `<div class="det"><div class="wrap"><table>
    <tr><th title="${esc(TIPS.player)}">${esc(who(m.entry, m.name))}'s fifteen</th><th></th>
    ${ths("next", ["season", "num"], ["G", "num"], ["A", "num"], ["mins", "num"])}
    ${SHOW_PRICES ? th("value", "num") : ""}</tr>
    ${(m.roster || []).map(cell).join("")}</table></div></div>`;
}
function squadHTML(m, byId) {
  const cell = p => {
    const f = byId[p.id] || {};
    const st = p.to_play ? `<span class="togo">to play</span>` : (p.mins ? `${p.mins}'` : `<span class="tm">-</span>`);
    return `<tr>${nameCell(p)}<td><span class="pos ${p.pos}">${p.pos}</span></td>
      <td class="run">${esc(p.next || "")}</td><td class="num">${st}</td>
      <td class="num">${p.pts}</td><td class="num">${S(f, "total_points")}</td>
      <td class="num">${S(f, "goals_scored")}</td><td class="num">${S(f, "assists")}</td>
      ${SHOW_PRICES ? `<td class="num">${priceOf(f.code) != null ? "&pound;" + priceOf(f.code).toFixed(1) : "-"}</td>` : ""}</tr>`;
  };
  const xi = m.squad.filter(counting), bn = m.squad.filter(p => !counting(p));
  return `<div class="det"><div class="wrap"><table>
    <tr><th title="${esc(TIPS.player)}">${esc(who(m.entry, m.name))}'s XI</th><th></th>
    ${ths("next", ["played", "num"], ["GW", "num"], ["season", "num"], ["G", "num"], ["A", "num"])}
    ${SHOW_PRICES ? th("value", "num") : ""}</tr>
    ${xi.map(cell).join("")}
    <tr class="benchsep"><td colspan="${SHOW_PRICES ? 9 : 8}">Bench · ${m.bench} pts</td></tr>
    ${bn.map(cell).join("")}</table></div></div>`;
}

/* ---------------- All players ---------------- */
let PSORT = { k: "total_points", dir: -1 }, PQ = "";
// The search box is built once and never rewritten. Rendering the whole
// section on every keystroke replaced the input with a new node, so the
// phone keyboard closed on the first letter typed and the caret went with
// it; refocusing afterwards was focusing the element that had just been
// thrown away.
function playersShell(sec) {
  if (sec.querySelector("#pq")) return;
  sec.innerHTML = `<input type="search" id="pq" placeholder="Search a player or club"
    value="${esc(PQ)}" autocomplete="off" autocapitalize="off" autocorrect="off"
    spellcheck="false"><div id="pbody"></div>`;
  $("pq").addEventListener("input", e => { PQ = e.target.value; renderPlayers(); });
}
function renderPlayers() {
  const sec = $("players");
  if (!PUB) { sec.innerHTML = loading("players"); return; }
  const cols = [
    ["name", "player", 0], ["pos", "", 0], ["owner", "owner", 0], ["next", "next", 0],
    ["minutes", "mins", 1], ["total_points", "pts", 1], ["goals_scored", "G", 1],
    ["assists", "A", 1], ["clean_sheets", "CS", 1], ["saves", "SV", 1],
    ["bonus", "B", 1], ["form", "form", 1], ["points_per_game", "PPG", 1],
  ];
  if (SHOW_PRICES) cols.push(["price", "value", 1]);
  const q = PQ.toLowerCase();
  let list = (PUB.players || []).filter(p =>
    !q || (p.name || "").toLowerCase().includes(q) || (p.full || "").toLowerCase().includes(q) ||
    (club(p) || "").toLowerCase().includes(q));
  const val = p => PSORT.k === "name" ? (p.name || "") : PSORT.k === "pos" ? POSORD[p.pos] :
    PSORT.k === "owner" ? (mgrName(p.owner) || "~") : PSORT.k === "next" ? (p.next || "") :
      PSORT.k === "price" ? (priceOf(p.code) ?? -1) : S(p, PSORT.k);
  list = list.sort((a, b) => {
    const x = val(a), y = val(b);
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * PSORT.dir;
  }).slice(0, 400);
  playersShell(sec);
  let h = `<div class="card">
    ${legend([["pos", "goalkeeper, defender, midfielder, forward"], ["owner", "who holds him"],
      ["next", "his club's next fixture - capitals at home, lower case away"],
      ["mins", "minutes this season"], ["pts", "points this season"], ["G", "goals"],
      ["A", "assists"], ["CS", "clean sheets"], ["SV", "saves"], ["B", "bonus points"],
      ["form", "points per match over the last thirty days"], ["PPG", "points per appearance"]]
      .concat(SHOW_PRICES ? [["value", "his classic-game price"]] : []))}
    <div class="wrap"><table><tr>` +
    cols.map(([k, lab, num]) => `<th class="s ${num ? "num" : ""}" data-k="${k}"
      title="${esc((TIPS[lab] || "") + (TIPS[lab] ? " " : "") + "Click to sort.")}">${esc(lab)}${
      PSORT.k === k ? (PSORT.dir < 0 ? " &darr;" : " &uarr;") : ""}</th>`).join("") + `</tr>`;
  for (const p of list) {
    const own = mgrName(p.owner);
    h += `<tr class="${p.owner === ME ? "mine-row" : ""}">${nameCell(p)}
      <td><span class="pos ${p.pos}">${p.pos}</span></td>
      <td class="tm">${own ? esc(own) : "free agent"}</td>
      <td class="run">${esc(p.next || "")}</td>
      <td class="num">${S(p, "minutes")}</td><td class="num">${S(p, "total_points")}</td>
      <td class="num">${S(p, "goals_scored")}</td><td class="num">${S(p, "assists")}</td>
      <td class="num">${S(p, "clean_sheets")}</td><td class="num">${S(p, "saves")}</td>
      <td class="num">${S(p, "bonus")}</td><td class="num">${S(p, "form")}</td>
      <td class="num">${S(p, "points_per_game")}</td>
      ${SHOW_PRICES ? `<td class="num">${priceOf(p.code) != null ? "&pound;" + priceOf(p.code).toFixed(1) : "-"}</td>` : ""}</tr>`;
  }
  h += `</table></div><div class="note">Every player in the game, ${list.length} shown, sorted by
    the column you click. All figures are this season's totals as the game reports them.</div></div>`;
  $("pbody").innerHTML = h;
  $("pbody").querySelectorAll("[data-k]").forEach(th => th.addEventListener("click", () => {
    const k = th.dataset.k;
    PSORT = { k, dir: PSORT.k === k ? -PSORT.dir : (k === "name" || k === "owner" || k === "next" ? 1 : -1) };
    renderPlayers();
  }));
}

function loading(what) {
  return ERR[what] || ERR.pub
    ? `<div class="card"><div class="note">Could not load the ${what} data (${esc(ERR[what] || ERR.pub)}). It is fetched
       straight from the repository, so a refresh usually sorts it.</div></div>`
    : `<div class="card"><div class="note">Loading&hellip;</div></div>`;
}

function draw() { renderSquad(); renderLeague(); renderPlayers(); renderNews(); renderLive(); }

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("on"));
    document.querySelectorAll("section").forEach(x => x.classList.remove("on"));
    t.classList.add("on"); $(t.dataset.v).classList.add("on");
  }));
  get(PUBLIC_URL, j => {
    PUB = j;
    const m = me();
    $("title").innerHTML = m ? `${esc(m.team)}` : "27 Richmond Road Cup";
    $("meta").textContent = `${m ? who(m.entry, m.name) + " · " : ""}GW${j.gw} · updated ${(j.generated || "").slice(0, 16).replace("T", " ")} UTC`;
    draw();
  }, "pub");
  get(NEWS_URL, j => { NEWS = j; renderNews(); }, "news");
  if (SHOW_PRICES) get(PRICES_URL, j => { PRICES = j; draw(); }, "prices");
  pollLive();
});

/* ---------------- News, and suggesting a roast ----------------
 * The feed is data/league_news.json, written once a day by a scheduled
 * Claude session from a reputable outlet, every item carrying its source
 * so any claim is one click from the article it came from. Items marked
 * "opinion" are that session's own, written on matchdays, and draw on the
 * suggestions posted from the dialog below.
 *
 * The pages are static, so a suggestion cannot be saved by the site
 * itself. It is POSTed to the Cloudflare worker, which asks GitHub to run
 * a workflow that appends it to data/roasts.json. The worker needs no
 * more permission than it already had for the schedule.
 */
// Two feeds land on one page. The daily round-up and the matchday opinion
// come from league_news.json; the running record of the league - who
// claimed whom, who is injured, who hauled, how the table moved - is
// carried in public.json, already stripped of the engine's forecasts and
// of anything written from one manager's chair.
// Two ways to read the feed: what happened to the six of you, and what
// happened to footballers. A waiver is a manager's doing, an opinion is
// about a manager, a scoreline is the competition; a hamstring, a hat
// trick and a transfer are the player's.
const NEWSGROUP = {
  move: "league", score: "league", live: "league", wrap: "league",
  overtake: "league", race: "league", bench: "league", pint: "league",
  squad: "league", opinion: "league",
  injury: "players", recovery: "players", haul: "players", flop: "players",
  lowlight: "players", freeagent: "players", headline: "players",
  news: "players",
};
// The League chip opens the tab. It is not only the league's own doings:
// it also carries every player item about somebody one of the six holds,
// or one of the fifty best in the game, because that is the news this
// league acts on. The Players chip is still everything, ours or not.
let NEWSFILTER = "league";
// Player names in the feed become links to the card. The text is escaped
// first and the names are matched against the escaped form, so the
// replacement can never introduce markup the feed did not have.
//
// The trap is a name that is also somebody's first name: a run once wrote
// "Enzo Maresca" and the naive version linked "Enzo" to the Chelsea
// midfielder. So a bare single-word name followed by another capitalised
// word is left alone, unless the two words together are themselves a
// player the game knows.
let NAMERE = null, NAMEMAP = null;
function nameIndex() {
  if (NAMERE || !PUB) return;
  // A manager's name wins over a player's. James Justin is a real
  // Leicester defender and "Justin" in this feed is always the manager,
  // twelve times on one screen; the same would go for a Marcus or an
  // Edward. Those words are left as text, and the players keep their
  // full names, which are unambiguous.
  const mgrs = new Set();
  for (const m of PUB.managers || []) {
    for (const nm of [m.name, who(m.entry, m.name)]) {
      for (const word of String(nm || "").split(/\s+/)) {
        if (word.length >= 3) mgrs.add(word.toLowerCase());
      }
    }
  }
  NAMEMAP = {};
  for (const p of PUB.players || []) {
    for (const nm of [p.name, p.full]) {
      const k = String(nm || "").trim();
      if (k.length < 3 || mgrs.has(k.toLowerCase())) continue;
      if (!(k.toLowerCase() in NAMEMAP)) NAMEMAP[k.toLowerCase()] = p.code;
    }
  }
  const keys = Object.keys(NAMEMAP).sort((a, b) => b.length - a.length)
    .map(k => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  NAMERE = keys.length ? new RegExp(`(^|[^\\w&;])(${keys.join("|")})(?![\\w'])`, "gi") : null;
}
function linkPlayers(escaped) {
  nameIndex();
  if (!NAMERE) return escaped;
  return escaped.replace(NAMERE, (all, pre, nm, at) => {
    const rest = escaped.slice(at + all.length);
    const next = /^\s+([A-Z][\w'-]+)/.exec(rest);
    if (next && !nm.includes(" ") && !(`${nm} ${next[1]}`.toLowerCase() in NAMEMAP)) {
      return all;                        // "Enzo Maresca" is not our Enzo
    }
    const code = NAMEMAP[nm.toLowerCase()];
    return code == null ? all : `${pre}<span class="nm pk" data-pk="${esc(code)}">${nm}</span>`;
  });
}
function newsFeed() {
  const out = [];
  for (const e of (NEWS && NEWS.items) || []) {
    out.push({ ts: e.ts, kind: e.kind === "opinion" ? "opinion" : "news",
               text: e.text, source: e.source });
  }
  for (const e of (PUB && PUB.news) || []) {
    // editorial items carry the article they came from; the rest do not
    out.push({ ts: e.ts, kind: e.type, text: e.text,
               source: e.url ? { title: "Source", url: e.url } : null });
  }
  return out.sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || "")));
}
// When line-ups lock. Worked out on the page from the deadline in
// public.json rather than written into the feed by anybody, so it cannot
// say "closes today" on a Sunday. Shows the waiver time too once that is
// the next clock rather than the one just gone.
function deadlineHTML() {
  const d = PUB && PUB.deadline ? new Date(PUB.deadline) : null;
  if (!d || isNaN(d)) return "";
  const ms = d - Date.now(), gw = PUB.next_gw || "";
  const uk = o => d.toLocaleString("en-GB", { timeZone: "Europe/London", ...o });
  const at = uk({ hour: "2-digit", minute: "2-digit" });
  const day = uk({ weekday: "long", day: "numeric", month: "long" });
  const today = uk({ year: "numeric", month: "2-digit", day: "2-digit" }) ===
    new Date().toLocaleString("en-GB", { timeZone: "Europe/London", year: "numeric", month: "2-digit", day: "2-digit" });
  let when, note;
  if (ms <= 0) {
    when = `<em>closed</em> ${today ? "earlier today" : day} at ${at}`;
    note = `Gameweek ${gw} is under way, so the eleven you had at the deadline is the eleven that scores.`;
  } else {
    const h = Math.floor(ms / 3600e3), m = Math.round(ms % 3600e3 / 60e3);
    const left = h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : h ? `${h}h ${m}m` : `${m}m`;
    when = `closes <em>${today ? "today" : day}</em> at <em>${at}</em> UK, in ${left}`;
    note = `Anything you change after that will not count. Substitutions are applied automatically
      when the gameweek ends: a starter who does not play is replaced by the first eligible player
      on your bench, so the bench order matters.`;
  }
  return `<div class="card dl"><b class="h">Bench and line-up deadline</b>
    <div class="dlbig">Gameweek ${gw} ${when}</div>
    <div class="note">${note}</div></div>`;
}
function renderNews() {
  const sec = $("news");
  const btn = SUGGEST_URL
    ? `<button class="btn" id="suggest" title="Suggest a headline, or a line about one of the other five">Suggest a roast</button>`
    : "";
  let h = deadlineHTML();
  const all = newsFeed();
  if (!all.length) {
    h += `<div class="chiprow">${btn}</div>`;
    h += (ERR.news || ERR.pub)
      ? `<div class="card"><div class="note">Could not load the feed (${esc(ERR.news || ERR.pub)}).</div></div>`
      : `<div class="card"><div class="note">Loading the feed&hellip;</div></div>`;
  } else {
    const F = [["all", "All news"], ["league", "League"], ["players", "Players"]];
    h += `<div class="chiprow">` + F.map(([k, lab]) =>
      `<button class="chip ${NEWSFILTER === k ? "on" : ""}" data-f="${k}">${lab}</button>`).join("")
      + btn + `</div>`;
    const ours = NEWSFILTER === "league" ? relevantNames() : null;
    const items = all.filter(e => {
      if (NEWSFILTER === "all") return true;
      const g = NEWSGROUP[e.kind];
      if (g === NEWSFILTER) return true;
      return NEWSFILTER === "league" && g === "players" && ours && ours(e.text);
    });
    if (!items.length) {
      h += `<div class="card"><div class="note">Nothing under that filter yet.</div></div>`;
    } else {
      h += `<div class="card">`;
      let day = "";
      for (const e of items.slice(0, 120)) {
        const d = String(e.ts || "").slice(0, 10);
        if (d !== day) { day = d; h += `<div class="daysep">${esc(d)}</div>`; }
        const src = e.source && e.source.url
          ? `<a class="src" href="${esc(e.source.url)}" target="_blank" rel="noopener">${esc(e.source.title || e.source.url)} &rsaquo;</a>`
          : "";
        h += `<div class="ev"><span class="badge b-${esc(e.kind)}">${esc(String(e.kind).toUpperCase())}</span>
          <span style="flex:1">${linkPlayers(esc(reword(e.text)))}${src}</span>
          <span class="when">${esc(String(e.ts || "").slice(11, 16))}</span></div>`;
      }
      h += `</div>`;
    }
  }
  sec.innerHTML = h;
  const b = $("suggest");
  if (b) b.addEventListener("click", openSuggest);
  sec.querySelectorAll("[data-f]").forEach(c => c.addEventListener("click",
    () => { NEWSFILTER = c.dataset.f; renderNews(); }));
}

function openSuggest() {
  // Everybody is fair game, the manager whose page this is included. The
  // pages are public and shared around, so excluding the one you happen to
  // be looking at only meant Ed could not be roasted from Ed's page.
  const others = (PUB && PUB.managers || []);
  const back = document.createElement("div");
  back.className = "back";
  back.innerHTML = `<div class="modal">
    <div class="row" style="padding:0"><h2>Suggest a headline</h2><button class="x" id="cx">&#10005;</button></div>
    <div class="note" style="padding:4px 0 0">Fair game: form, a questionable transfer, a bet hedged
    against your own team. It goes into the pile the matchday opinion pieces are written from, so it
    may turn up on this page with your name nowhere near it.</div>
    <label for="about">Who is it about</label>
    <select id="about">
      <option value="">The league in general</option>
      ${others.map(m => `<option value="${m.entry}">${esc(who(m.entry, m.name))} &middot; ${esc(m.team)}</option>`).join("")}
    </select>
    <label for="text">Your suggestion</label>
    <textarea id="text" maxlength="500" placeholder="Rob has one of his own strikers benched and the opposition keeper starting..."></textarea>
    <div class="row"><button class="btn" id="send">Send it</button>
      <span class="msg" id="msg"></span></div>
  </div>`;
  document.body.appendChild(back);
  document.body.style.overflow = "hidden";
  const close = () => { back.remove(); document.body.style.overflow = ""; };
  back.addEventListener("click", e => { if (e.target === back) close(); });
  $("cx").addEventListener("click", close);
  $("send").addEventListener("click", () => submitSuggestion(close));
  $("text").focus();
}

function submitSuggestion(close) {
  const text = $("text").value.trim();
  const msg = $("msg"), send = $("send");
  const set = (cls, s) => { msg.className = "msg " + cls; msg.textContent = s; };
  if (text.length < 4) { set("err", "Type something first."); return; }
  const sel = $("about");
  const about = sel.value ? +sel.value : null;
  const m = me();
  send.disabled = true; set("", "Sending...");
  fetch(SUGGEST_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      from: ME, from_name: m ? who(m.entry, m.name) : String(ME),
      about, about_name: about ? (mgrName(about) || String(about)) : null,
      text,
    }),
  }).then(r => r.json().catch(() => ({ error: "HTTP " + r.status })))
    .then(j => {
      if (j && j.ok) { set("ok", "In the pile. Thanks."); setTimeout(close, 1200); }
      else { set("err", (j && j.error) || "Could not send that."); send.disabled = false; }
    })
    .catch(e => { set("err", String(e.message || e)); send.disabled = false; });
}

/* ---------------- Live ----------------
 * The same arithmetic the main dashboard does, on the same worker feed:
 * provisional bonus off the bps table and provisional substitutions under
 * the game's own formation rules, so this tab and the League tab agree
 * once a gameweek settles. None of it is a projection.
 */
let LIVE = null, LIVEERR = null, LIVETIMER = null, LIVEOPEN = null, LIVECTX = null;
const RULES = { play: 11, min_GKP: 1, max_GKP: 1, min_DEF: 3, max_DEF: 5, min_MID: 2, max_MID: 5, min_FWD: 1, max_FWD: 3 };
function provBonus(f) {
  const out = {};
  if (!f.started || f.bonus_in) return out;
  const s = f.bps.filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]);
  let i = 0;
  while (i < s.length && i < 3) {
    const v = s[i][1], grp = s.filter(x => x[1] === v), pts = [3, 2, 1][i];
    grp.forEach(x => out[x[0]] = pts); i += grp.length;
  }
  return out;
}
function applySubs(squad, R) {
  const start = squad.filter(p => p.slot <= R.play), bench = squad.filter(p => p.slot > R.play);
  let counts = {}; start.forEach(p => counts[p.pos] = (counts[p.pos] || 0) + 1);
  const breach = c => ["GKP", "DEF", "MID", "FWD"].reduce((a, k) =>
    a + Math.max(0, R["min_" + k] - (c[k] || 0)) + Math.max(0, (c[k] || 0) - R["max_" + k]), 0);
  const used = new Set();
  for (const gone of start.filter(p => p.settled && !p.played)) {
    for (const c of bench) {
      if (used.has(c.id) || !c.played) continue;
      if ((gone.pos === "GKP") !== (c.pos === "GKP")) continue;
      const t = { ...counts }; t[gone.pos] = (t[gone.pos] || 0) - 1; t[c.pos] = (t[c.pos] || 0) + 1;
      if (breach(t) > breach(counts)) continue;
      gone.subbed_out = true; c.subbed_in = true; used.add(c.id); counts = t; break;
    }
  }
}
function liveTable(L) {
  const R = { ...RULES };
  for (const k in (L.rules || {})) if (Number.isInteger(L.rules[k])) R[k] = L.rules[k];
  const teamFx = {};
  L.fixtures.forEach(f => { (teamFx[f.h] = teamFx[f.h] || []).push(f); (teamFx[f.a] = teamFx[f.a] || []).push(f); });
  const bonus = {};
  L.fixtures.forEach(f => { const b = provBonus(f); for (const k in b) bonus[k] = (bonus[k] || 0) + b[k]; });
  const settled = t => { const fs = teamFx[t] || []; return !fs.length || fs.every(f => f.fin); };
  const playing = t => (teamFx[t] || []).some(f => f.started && !f.fin);
  const mgrs = L.managers.map(m => {
    const squad = m.picks.map(([id, slot]) => {
      const e = L.elements[id] || { n: String(id), t: null, p: "MID", pts: 0, min: 0 };
      const pb = bonus[id] || 0;
      return {
        id, slot, name: e.n, pos: e.p, tid: e.t, team: L.teams[e.t] || "?", pts: e.pts + pb, pb,
        mins: e.min, played: e.min > 0, settled: settled(e.t), to_play: !settled(e.t),
        playing: playing(e.t), subbed_in: false, subbed_out: false,
      };
    }).sort((a, b) => a.slot - b.slot);
    applySubs(squad, R);
    const c = squad.filter(p => (p.slot <= R.play && !p.subbed_out) || p.subbed_in);
    const togo = c.filter(p => p.to_play);
    return {
      ...m, squad, counting: c, live: c.reduce((a, p) => a + p.pts, 0),
      bench: squad.filter(p => !c.includes(p)).reduce((a, p) => a + p.pts, 0),
      to_play: togo.length, inplay: c.filter(p => p.playing).length,
      played: c.length - togo.length, subs: squad.filter(p => p.subbed_in).length,
    };
  });
  mgrs.sort((a, b) => b.live - a.live || (a.rank ?? 99) - (b.rank ?? 99));
  return {
    mgrs, teamFx, inplay: L.fixtures.filter(f => f.started && !f.fin),
    next: L.fixtures.filter(f => !f.started).map(f => f.ko).sort()[0] || null,
    allDone: L.fixtures.length > 0 && L.fixtures.every(f => f.fin),
  };
}
function koText(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleDateString("en-GB", { weekday: "short" }) + " " +
    d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
// The Live tab is a match view, not a table. It is always there, because
// the gameweek's fixture list is worth looking at before anything kicks
// off; what comes and goes is the live part above it, one card per game
// actually being played, ordered by how many of your own players are in it.
//
// Points shown are the league's players only, both sides, each one opening
// to show how he got them. Provisional bonus is folded in and labelled as
// provisional until the official award lands, the same arithmetic the
// League tab uses, so the two never disagree.
let LVOPEN = new Set(), TICK = "";
function fixtureState(f) {
  if (f.fin) return `<span class="mtmin ft">FT</span>`;
  if (!f.started) return `<span class="mtmin ht">${esc(koText(f.ko))}</span>`;
  return `<span class="mtmin">${f.min || 0}'</span>`;
}
function crest(tid, name) {
  const t = PUB && PUB.teams && PUB.teams[tid];
  const code = t && t[2];
  const alt = esc(name || (t && t[0]) || "");
  return code
    ? `<img src="https://resources.premierleague.com/premierleague/badges/70/t${code}.png"
        alt="${alt}" loading="lazy" onerror="this.remove()">`
    : "";
}
/* Who owns whom, and the provisional bonus, for one snapshot. */
function liveIndex(L) {
  const owner = {}, mgr = {};
  for (const m of L.managers || []) {
    mgr[m.entry] = m;
    for (const [id] of m.picks || []) owner[id] = m.entry;
  }
  const bonus = {};
  for (const f of L.fixtures || []) {
    const b = provBonus(f);
    for (const k in b) bonus[k] = (bonus[k] || 0) + b[k];
  }
  return { owner, mgr, bonus, roles: liveRoles(L) };
}
// The feed spells its scoring reasons out at length. On one line in half
// a column they need to be short, and the collapsible underneath still
// carries the full name, the count and the points.
const EVENT_SHORT = {
  "minutes played": null,            // the minute count says this already
  "goals scored": "goal", "assists": "assist", "clean sheets": "clean sheet",
  "goals conceded": "conceded", "own goals": "own goal", "saves": "saves",
  "penalties saved": "pen save", "penalties missed": "pen miss",
  "yellow cards": "yellow", "red cards": "red", "bonus": "bonus",
  "defensive contribution": "def. actions",
};
function eventChips(rows, pb) {
  const out = [];
  for (const [name, val, pts] of rows) {
    const key = String(name).toLowerCase();
    if (!pts) continue;
    if (key in EVENT_SHORT && EVENT_SHORT[key] === null) continue;
    const label = EVENT_SHORT[key] || String(name).toLowerCase();
    // "2 bonus +2" says the same thing twice; a count only helps where it
    // differs from the points, as saves and defensive actions do
    const text = (val > 1 && val !== Math.abs(pts)) ? `${val} ${label}` : label;
    out.push(`<span class="evp${pts < 0 ? " bad" : ""}">${esc(text)} ${
      pts > 0 ? "+" : ""}${pts}</span>`);
  }
  if (pb) out.push(`<span class="evp prov">bonus, provisional +${pb}</span>`);
  return out.join("");
}
/* Who is in each manager's counting eleven once the automatic
   substitutions are applied, so a row can say XI or bench honestly rather
   than reading the slot number and hoping. */
function liveRoles(L) {
  const roles = {};
  const R = { ...RULES };
  for (const k in (L.rules || {})) if (Number.isInteger(L.rules[k])) R[k] = L.rules[k];
  for (const m of L.managers || []) {
    const squad = (m.picks || []).map(([id, slot]) => {
      const e = L.elements[id] || { p: "MID", t: null, min: 0 };
      return { id, slot, pos: e.p, tid: e.t, mins: e.min, played: e.min > 0,
               settled: !(L.fixtures || []).some(x => (x.h === e.t || x.a === e.t) && !x.fin),
               subbed_in: false, subbed_out: false };
    }).sort((a, b) => a.slot - b.slot);
    applySubs(squad, R);
    for (const p of squad) {
      roles[p.id] = { xi: (p.slot <= R.play && !p.subbed_out) || p.subbed_in,
                      slot: p.slot, sub_in: p.subbed_in, sub_out: p.subbed_out };
    }
  }
  return roles;
}
/* What he is doing in the match itself. `starts` comes from the feed; the
   fallback guess is only used against an older worker that does not send
   it, and it cannot tell a substituted man from one who came on. */
function matchRole(e, f) {
  const on = f.started && !f.fin, played = e.min > 0;
  const started = e.starts == null
    ? (played && f.min && e.min >= f.min - 2) : !!e.starts;
  if (!f.started) return { t: "", c: "" };
  if (!played) return on ? { t: "not on", c: "off" } : { t: "did not play", c: "off" };
  if (started && on && f.min && e.min < f.min - 2) return { t: "subbed off", c: "off" };
  if (!started) return { t: on ? "on as sub" : "sub", c: "on" };
  return { t: on ? "on" : "played", c: "on" };
}
function playerRow(id, e, f, ix) {
  const ent = ix.owner[id], m = ix.mgr[ent];
  const key = f.id + ":" + id;
  const role = (ix.roles || {})[id] || {};
  const mr = matchRole(e, f);
  const rows = ((e.ex || {})[f.id] || []).filter(x => x[2] || x[1]);
  // provBonus only fires while the fixture says bonus is not in, but the
  // two signals come from different parts of the feed and could disagree
  // for a poll or two around the award. If the breakdown already names a
  // bonus, that is the official one and it is in his points already.
  const official = rows.some(r => /bonus/i.test(String(r[0])));
  const pb = official ? 0 : (ix.bonus[id] || 0);
  const pts = (e.pts || 0) + pb;
  let body = rows.map(([name, val, p]) =>
    `<div><span>${esc(name)}</span><span class="tm">${esc(val)}</span><b>${p > 0 ? "+" : ""}${esc(p)}</b></div>`).join("");
  if (pb) {
    body += `<div class="prov"><span>Bonus, provisional</span><span class="tm">${e.bps || 0} bps</span><b>+${pb}</b></div>`;
  }
  if (!body) {
    body = `<div><span>${e.min ? "No scoring events yet." : "Not on the pitch yet."}</span></div>`;
  }
  const meta = [
    `<span class="who">${esc(m ? who(m.entry, m.name) : "")}</span>`,
    role.xi === undefined ? "" :
      `<span class="tag ${role.xi ? "xi" : "bn"}">${role.xi ? "XI" : "bench"}${
        role.sub_in ? " &uarr;" : role.sub_out ? " &darr;" : ""}</span>`,
    mr.t ? `<span class="tag ${mr.c}">${mr.t}</span>` : "",
    e.min ? `<span class="mins">${e.min}'</span>` : "",
    eventChips(rows, pb),
  ].filter(Boolean).join("");
  return `<details class="pl" data-k="${esc(key)}"${LVOPEN.has(key) ? " open" : ""}>
    <summary>
      <span class="plmain"><span class="nm">${esc(e.n)}</span>
        <span class="pts">${pts}${pb ? `<span class="provb">+${pb}b</span>` : ""}</span></span>
      <span class="plmeta">${meta}</span></summary>
    <div class="contrib">${body}</div></details>`;
}
function sideHTML(tid, f, L, ix) {
  const ids = Object.keys(L.elements || {})
    .filter(id => (L.elements[id].t === tid) && ix.owner[id] != null)
    .sort((a, b) => ((L.elements[b].pts || 0) + (ix.bonus[b] || 0)) -
                    ((L.elements[a].pts || 0) + (ix.bonus[a] || 0)));
  if (!ids.length) return `<div class="mtnone">Nobody in the league owns a player here.</div>`;
  return ids.map(id => playerRow(id, L.elements[id], f, ix)).join("");
}
function matchHTML(f, L, ix) {
  const hn = L.teams[f.h] || f.h, an = L.teams[f.a] || f.a;
  const mine = Object.keys(L.elements || {})
    .some(id => ix.owner[id] === ME && (L.elements[id].t === f.h || L.elements[id].t === f.a));
  return `<div class="mt ${mine ? "mine" : ""}">
    <div class="mthead">
      <span class="mtside">${crest(f.h, hn)}<span class="nm">${esc(hn)}</span></span>
      <span class="mtmid"><span class="mtscore">${f.started ? `${f.hs ?? 0} - ${f.as ?? 0}` : "v"}</span>
        <div>${fixtureState(f)}</div></span>
      <span class="mtside away">${crest(f.a, an)}<span class="nm">${esc(an)}</span></span>
    </div>
    <div class="mtcols">
      <div class="mtcol">${sideHTML(f.h, f, L, ix)}</div>
      <div class="mtcol">${sideHTML(f.a, f, L, ix)}</div>
    </div></div>`;
}
/* One line in the gameweek's fixture list, and under it everyone in the
   league who is in that match. A count would fit in less room, but which
   of your own players have a game is the thing worth knowing, and only
   the names say it. chips(f) yields {p, who, mine, pts, pb} so the same
   row serves the live feed and the pre-deadline preview. */
function fxRow(f, name, chips) {
  const hn = name(f.h), an = name(f.a);
  const mid = f.started ? `${f.hs ?? 0} - ${f.as ?? 0}` : "v";
  const men = chips(f);
  const body = men.length ? men.map(c =>
    `<span class="fxchip${c.mine ? " mine" : ""}">${nameTag(c.p)}${
      c.pts == null ? "" : ` ${c.pts}`}${
      c.pb ? `<span class="provb">+${c.pb}</span>` : ""}<span class="who">&middot; ${esc(c.who)}</span></span>`
  ).join("") : `<span class="fxnone">Nobody in the league owns a player here.</span>`;
  return `<div class="fxi${f.started && !f.fin ? " on" : ""}">
    <div class="fxr">
      <span class="fxs"><span class="nm">${esc(hn)}</span>${crest(f.h, hn)}</span>
      <span class="fxm"><b>${mid}</b>${fixtureState(f)}</span>
      <span class="fxs away">${crest(f.a, an)}<span class="nm">${esc(an)}</span></span>
    </div>
    <div class="fxplayers">${body}</div>
  </div>`;
}
/* The coming gameweek, from public.json, for the stretch between the last
   whistle and the next deadline when the live feed still reports the old
   one. No scores exist yet, so these rows are the schedule only. */
function previewFixtures() {
  if (!PUB || !(PUB.fixtures || []).length) return "";
  const owner = {};
  for (const m of PUB.managers || []) owner[m.entry] = who(m.entry, m.name);
  const name = tid => ((PUB.teams || {})[tid] || [])[0] || String(tid);
  const chips = f => (PUB.players || [])
    .filter(p => (p.team === f.h || p.team === f.a) && p.owner != null)
    .map(p => ({ p, who: owner[p.owner] || "", mine: p.owner === ME, pts: null, pb: 0 }))
    .sort((a, b) => (b.mine - a.mine) || String(a.p.name).localeCompare(String(b.p.name)));
  return PUB.fixtures
    .map(([h, a, ko]) => fxRow({ h, a, ko, started: false, fin: false }, name, chips))
    .join("");
}
/* Everyone's gameweek points as they stand, highest first. The Live tab
   uses it twice: the reader's own line in the header, and the whole table
   in the ticker. */
function liveStandings(L, ix) {
  return (L.managers || []).map(m => {
    const squad = (m.picks || []).map(([id, slot]) => {
      const e = L.elements[id] || { p: "MID", t: null, pts: 0, min: 0 };
      return { id, slot, pos: e.p, tid: e.t, pts: (e.pts || 0) + (ix.bonus[id] || 0),
               mins: e.min, played: e.min > 0,
               settled: !(L.fixtures || []).some(f => (f.h === e.t || f.a === e.t) && !f.fin),
               subbed_in: false, subbed_out: false };
    }).sort((a, b) => a.slot - b.slot);
    const R = { ...RULES };
    for (const k in (L.rules || {})) if (Number.isInteger(L.rules[k])) R[k] = L.rules[k];
    applySubs(squad, R);
    const c = squad.filter(p => (p.slot <= R.play && !p.subbed_out) || p.subbed_in);
    return { m, live: c.reduce((a, p) => a + p.pts, 0) };
  }).sort((a, b) => b.live - a.live);
}
function tickerText(rows) {
  return rows.map((r, i) =>
    `<span class="${r.m.entry === ME ? "me" : ""}">${i + 1}. ${esc(who(r.m.entry, r.m.name))} <b>${r.live}</b></span>`)
    .join(`<em>&middot;</em>`);
}
function renderLive() {
  const sec = $("live");
  if (!sec) return;
  if (!LIVE) {
    sec.innerHTML = `<div class="card"><b class="h">Live</b><div class="note">${LIVEERR
      ? `Cannot reach the live feed (${esc(LIVEERR)}). Retrying.`
      : "Connecting to the live feed&hellip;"}</div></div>`;
    TICK = "";
    return;
  }
  const ix = liveIndex(LIVE);
  const fx = LIVE.fixtures || [];
  const inplay = fx.filter(f => f.started && !f.fin);
  // once the feed's gameweek is done the tab is about the next one, which
  // only public.json knows about until the deadline rolls the feed
  // forward. The weekly pot is per gameweek, so it reads zero for
  // everybody until a ball is kicked - last week's total is last week's.
  const ahead = fx.length > 0 && fx.every(f => f.fin) &&
    PUB && PUB.next_gw > LIVE.gw && (PUB.fixtures || []).length > 0;
  const gwNum = ahead ? PUB.next_gw : LIVE.gw;
  // the ticker is left alone unless its text changes, so the scroll does
  // not jump back to the start every fifteen seconds
  if (!sec.querySelector("#lvticker")) {
    sec.innerHTML = `<div class="ticker"><div class="tickrow" id="lvticker"></div></div>
      <div class="lvstatus" id="lvstat"></div><div id="lvbody"></div>
      <div class="card"><b class="h" id="lvfxh"></b><div class="fxl" id="lvfxb"></div></div>`;
  }
  const rows = ahead
    ? [...(PUB.managers || [])].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99))
        .map(m => ({ m, live: 0 }))
    : liveStandings(LIVE, ix);
  const t = tickerText(rows);
  if (t !== TICK) { TICK = t; $("lvticker").innerHTML = t + `<em>&middot;</em>` + t; }

  // the header leads with what the reader came for: his own points this
  // gameweek, and where that puts him
  const meAt = rows.findIndex(r => r.m.entry === ME);
  const head = meAt < 0 ? `<b class="lvpts">Gameweek ${gwNum}</b>`
    : `<b class="lvpts">Gameweek ${gwNum}: you have ${rows[meAt].live}</b>
       <span class="lvrank">${ahead ? "nobody has scored yet"
         : `${ordinal(meAt + 1)} of ${rows.length}${
             meAt ? `, ${rows[0].live - rows[meAt].live} behind ${esc(rows[0].m.name)}` : ""}`}</span>`;

  if (inplay.length) {
    $("lvstat").innerHTML = `${head}<span class="lvsep"><span class="livedot"></span><b>LIVE</b> &middot;
      ${inplay.length} match${inplay.length > 1 ? "es" : ""} in play &middot;
      feed ${esc((LIVE.fetched || "").slice(11, 19))} UTC</span>`;
    // most relevant first: where you have the most players
    const mineIn = f => Object.keys(LIVE.elements || {})
      .filter(id => ix.owner[id] === ME &&
        (LIVE.elements[id].t === f.h || LIVE.elements[id].t === f.a)).length;
    const order = [...inplay].sort((a, b) => mineIn(b) - mineIn(a) ||
      String(a.ko || "").localeCompare(String(b.ko || "")));
    $("lvbody").innerHTML = order.map(f => matchHTML(f, LIVE, ix)).join("");
    $("lvbody").querySelectorAll("details[data-k]").forEach(d =>
      d.addEventListener("toggle", () => {
        if (d.open) LVOPEN.add(d.dataset.k); else LVOPEN.delete(d.dataset.k);
      }));
  } else {
    const next = fx.filter(f => !f.started).map(f => f.ko).sort()[0] ||
      (PUB && PUB.next_gw > LIVE.gw ? ((PUB.fixtures || [])[0] || [])[2] : null);
    $("lvstat").innerHTML = head + `<span class="lvsep">${next
      ? `Nothing in play. Next kick-off ${esc(koText(next))}.`
      : `Every match in gameweek ${gwNum} has finished.`}</span>`;
    $("lvbody").innerHTML = "";
  }

  const name = tid => LIVE.teams[tid] || String(tid);
  const chips = f => Object.keys(LIVE.elements || {})
    .filter(id => {
      const e = LIVE.elements[id];
      return (e.t === f.h || e.t === f.a) && ix.owner[id] != null;
    })
    .map(id => {
      const e = LIVE.elements[id], m = ix.mgr[ix.owner[id]];
      const rows = ((e.ex || {})[f.id] || []);
      const pb = rows.some(r => /bonus/i.test(String(r[0]))) ? 0 : (ix.bonus[id] || 0);
      return { p: { name: e.n, id: +id }, who: m ? who(m.entry, m.name) : "",
               mine: ix.owner[id] === ME,
               pts: f.started ? (e.pts || 0) + pb : null, pb: f.started ? pb : 0 };
    })
    .sort((a, b) => (b.mine - a.mine) || ((b.pts || 0) - (a.pts || 0)) ||
      String(a.p.name).localeCompare(String(b.p.name)));
  $("lvfxh").textContent = `Gameweek ${gwNum} fixtures`;
  $("lvfxb").innerHTML = (ahead ? previewFixtures() : [...fx]
    .sort((a, b) => String(a.ko || "").localeCompare(String(b.ko || "")))
    .map(f => fxRow(f, name, chips)).join("")) ||
    `<div class="note">No fixtures in this gameweek.</div>`;
}
function liveDelay() {
  if (!LIVE || !LIVECTX) return 60e3;
  if (LIVECTX.inplay.length) return Math.max(15, LIVE.ttl || 30) * 1000;
  if (!LIVECTX.next) return null;
  return Math.min(6 * 3600e3, Math.max(60e3, new Date(LIVECTX.next) - Date.now() + 45e3));
}
async function pollLive() {
  clearTimeout(LIVETIMER);
  if (!LIVE_URL) return;
  try {
    const ab = new AbortController();
    const to = setTimeout(() => ab.abort(new Error("no answer in 10s")), 10000);
    const r = await fetch(LIVE_URL, { cache: "no-store", signal: ab.signal }).finally(() => clearTimeout(to));
    if (!r.ok) throw new Error("HTTP " + r.status);
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    LIVE = j; LIVEERR = null;
  } catch (e) { LIVEERR = String(e.message || e); }
  renderLive(); renderLeague();
  const d = liveDelay();
  if (d != null && !document.hidden) LIVETIMER = setTimeout(pollLive, d);
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(LIVETIMER); else pollLive();
});

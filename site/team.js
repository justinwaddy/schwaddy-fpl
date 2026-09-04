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

const esc = x => String(x ?? "").replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const $ = id => document.getElementById(id);
const POSORD = { GKP: 0, DEF: 1, MID: 2, FWD: 3 };
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
function mgrName(entry) {
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
function nameCell(p, extra) {
  return `<td>${extra || ""}<span class="nm">${esc(p.name)}</span>
    <span class="tm">${esc(club(p))}</span>${p.news ? `<span class="flag">${esc(p.news)}</span>` : ""}</td>`;
}

/* ---------------- My squad ---------------- */
function renderSquad() {
  const sec = $("squad");
  if (!PUB) { sec.innerHTML = loading("squad"); return; }
  const m = me();
  if (!m) { sec.innerHTML = `<div class="card"><div class="note">No squad found for this team yet.</div></div>`; return; }
  const byCode = {}; (PUB.players || []).forEach(p => { byCode[p.id] = p; });
  const xi = m.squad.filter(counting), bn = m.squad.filter(p => !counting(p));
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
  const rhead = `<tr><th>player</th><th></th><th>next</th><th class="num">pts</th>
    <th class="num">G</th><th class="num">A</th><th class="num">CS</th><th class="num">B</th>
    <th class="num">mins</th><th class="num">form</th><th class="num">PPG</th>
    ${SHOW_PRICES ? `<th class="num">value</th>` : ""}</tr>`;
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
  const head = `<tr><th>player</th><th></th><th>next</th><th class="num">played</th>
    <th class="num">GW</th><th class="num">season</th><th class="num">G</th>
    <th class="num">A</th><th class="num">mins</th></tr>`;
  sec.innerHTML = `${roster.length ? `<div class="card"><b class="h">Squad · ${esc(m.team)}</b>
    <div class="note">The fifteen you hold now, with this season's totals as the game reports them.</div>
    <div class="wrap"><table>${rhead}${roster.map(rcell).join("")}</table></div></div>` : ""}
    <div class="card scorecard"><b class="h">GW${PUB.gw} · ${m.live} pts</b>
    <div class="lvstatus">${m.played} played, ${m.to_play} to come · bench ${m.bench} · season ${m.total ?? "-"} pts · ${ordinal(m.rank)} in the league</div>
    <div class="wrap"><table>${head}${xi.map(cell).join("")}
    <tr class="benchsep"><td colspan="9">Bench · ${m.bench} pts</td></tr>
    ${bn.map(cell).join("")}</table></div>
    <div class="note">The eleven that scored gameweek ${PUB.gw}, with provisional substitutions applied
    the way the game applies them when a week ends. Waivers process the day before a deadline, so
    just after they run this is last week's team and the squad above is the current one.</div></div>`;
}
function ordinal(n) {
  if (n == null) return "unranked";
  return n + (n === 1 ? "st" : n === 2 ? "nd" : n === 3 ? "rd" : "th");
}

/* ---------------- League ---------------- */
let LGOPEN = null;
function renderLeague() {
  const sec = $("league");
  if (!PUB) { sec.innerHTML = loading("league"); return; }
  const byId = {}; (PUB.players || []).forEach(p => { byId[p.id] = p; });
  const rows = [...(PUB.managers || [])].sort((a, b) => (b.total ?? 0) - (a.total ?? 0));
  const value = m => {
    if (!SHOW_PRICES || !PRICES) return null;
    let t = 0, n = 0;
    for (const p of m.squad) { const c = byId[p.id]; const v = c && priceOf(c.code); if (v) { t += v; n++; } }
    return n ? t : null;
  };
  let h = `<div class="card"><b class="h">Gameweek ${PUB.gw}</b><div class="wrap"><table>
    <tr><th></th><th>manager</th><th class="num">GW</th><th class="num">bench</th>
    <th class="num">to play</th><th class="num">season</th>${SHOW_PRICES ? `<th class="num">value</th>` : ""}<th></th></tr>`;
  rows.forEach((m, i) => {
    const v = value(m);
    h += `<tr class="clk ${m.entry === ME ? "mine-row" : ""}" data-e="${m.entry}">
      <td class="tm">${i + 1}</td>
      <td><span class="nm">${esc(m.name)}</span> <span class="tm">${esc(m.team)}</span></td>
      <td class="num">${m.live}</td><td class="num"><span class="tm">${m.bench}</span></td>
      <td class="num">${m.to_play || ""}</td><td class="num">${m.total ?? "-"}</td>
      ${SHOW_PRICES ? `<td class="num">${v != null ? "&pound;" + v.toFixed(1) : "-"}</td>` : ""}
      <td class="chev">${LGOPEN === m.entry ? "&#9662;" : "&#9656;"}</td></tr>`;
    if (LGOPEN === m.entry) h += `<tr><td colspan="${SHOW_PRICES ? 8 : 7}" style="padding:0 0 6px">${squadHTML(m, byId)}</td></tr>`;
  });
  h += `</table></div><div class="note">Season is the table as the game has it; it moves when the
    gameweek is processed. Click a manager for the squad.</div></div>`;
  sec.innerHTML = h;
  sec.querySelectorAll("[data-e]").forEach(r => r.addEventListener("click", () => {
    const e = +r.dataset.e; LGOPEN = (LGOPEN === e ? null : e); renderLeague();
  }));
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
    <tr><th>${esc(m.name)}'s XI</th><th></th><th>next</th><th class="num">played</th>
    <th class="num">GW</th><th class="num">season</th><th class="num">G</th><th class="num">A</th>
    ${SHOW_PRICES ? `<th class="num">value</th>` : ""}</tr>
    ${xi.map(cell).join("")}
    <tr class="benchsep"><td colspan="${SHOW_PRICES ? 9 : 8}">Bench · ${m.bench} pts</td></tr>
    ${bn.map(cell).join("")}</table></div></div>`;
}

/* ---------------- All players ---------------- */
let PSORT = { k: "total_points", dir: -1 }, PQ = "";
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
  let h = `<input type="search" id="pq" placeholder="Search a player or club" value="${esc(PQ)}">
    <div class="card"><div class="wrap"><table><tr>` +
    cols.map(([k, lab, num]) => `<th class="s ${num ? "num" : ""}" data-k="${k}">${lab}${PSORT.k === k ? (PSORT.dir < 0 ? " &darr;" : " &uarr;") : ""}</th>`).join("") + `</tr>`;
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
  sec.innerHTML = h;
  sec.querySelectorAll("[data-k]").forEach(th => th.addEventListener("click", () => {
    const k = th.dataset.k;
    PSORT = { k, dir: PSORT.k === k ? -PSORT.dir : (k === "name" || k === "owner" || k === "next" ? 1 : -1) };
    renderPlayers();
  }));
  const box = $("pq");
  if (box) box.addEventListener("input", () => { PQ = box.value; renderPlayers(); box.focus(); });
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
    $("meta").textContent = `${m ? m.name + " · " : ""}GW${j.gw} · updated ${(j.generated || "").slice(0, 16).replace("T", " ")} UTC`;
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
const NEWSGROUP = {
  move: "moves", injury: "injuries", recovery: "injuries",
  haul: "results", flop: "results", lowlight: "results", score: "results",
  live: "results", wrap: "results", overtake: "results", race: "results",
  bench: "results", pint: "results", freeagent: "results",
  news: "news", opinion: "news", headline: "news", squad: "results",
};
let NEWSFILTER = "all";
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
function renderNews() {
  const sec = $("news");
  const btn = SUGGEST_URL
    ? `<button class="btn" id="suggest">Suggest a headline or a roast</button>`
    : "";
  let h = `<div class="card"><b class="h">League news</b>
    <div class="note">A round-up a day with the article behind every reported item, the odd opinion on
    a matchday, and the league's own running record: waivers, injuries, hauls and the table.</div>
    <div class="row">${btn}</div></div>`;
  const all = newsFeed();
  if (!all.length) {
    h += (ERR.news || ERR.pub)
      ? `<div class="card"><div class="note">Could not load the feed (${esc(ERR.news || ERR.pub)}).</div></div>`
      : `<div class="card"><div class="note">Loading the feed&hellip;</div></div>`;
  } else {
    const F = [["all", "Everything"], ["news", "News"], ["moves", "Waivers"],
               ["injuries", "Injuries"], ["results", "Results"]];
    h += F.map(([k, lab]) => `<button class="chip ${NEWSFILTER === k ? "on" : ""}" data-f="${k}">${lab}</button>`).join("");
    const items = all.filter(e => NEWSFILTER === "all" || NEWSGROUP[e.kind] === NEWSFILTER);
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
          <span style="flex:1">${esc(e.text)}${src}</span>
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
  const others = (PUB && PUB.managers || []).filter(m => m.entry !== ME);
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
      ${others.map(m => `<option value="${m.entry}">${esc(m.name)} &middot; ${esc(m.team)}</option>`).join("")}
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
      from: ME, from_name: m ? m.name : String(ME),
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
function renderLive() {
  const sec = $("live");
  if (!sec) return;
  if (!LIVE) {
    sec.innerHTML = `<div class="card"><b class="h">Live scores</b><div class="note">${LIVEERR
      ? `Cannot reach the live feed (${esc(LIVEERR)}). Retrying.` : "Connecting to the live feed&hellip;"}</div></div>`;
    return;
  }
  const ctx = liveTable(LIVE); LIVECTX = ctx;
  const status = ctx.inplay.length
    ? `<span class="livedot"></span><b>LIVE</b> · ${ctx.inplay.length} match${ctx.inplay.length > 1 ? "es" : ""} in play`
    : ctx.allDone ? `GW${LIVE.gw} ${LIVE.finished ? "final" : "all played, awaiting bonus"}`
      : ctx.next ? `Next kick-off ${koText(ctx.next)}` : `GW${LIVE.gw}`;
  let h = `<div class="card scorecard"><b class="h">Gameweek ${LIVE.gw}</b>
    <div class="lvstatus">${status} · feed ${esc((LIVE.fetched || "").slice(11, 19))} UTC</div>
    <div class="wrap"><table><tr><th></th><th>manager</th><th class="num">GW</th>
    <th class="num">on pitch</th><th class="num">to play</th><th class="num">bench</th><th></th></tr>`;
  ctx.mgrs.forEach((m, i) => {
    h += `<tr class="clk ${m.entry === ME ? "mine-row" : ""}" data-l="${m.entry}"><td class="tm">${i + 1}</td>
      <td><span class="nm">${esc(m.name)}</span> <span class="tm">${esc(m.team)}</span></td>
      <td class="num">${m.live}</td><td class="num">${m.inplay || ""}</td>
      <td class="num">${m.to_play || ""}</td><td class="num"><span class="tm">${m.bench}</span></td>
      <td class="chev">${LIVEOPEN === m.entry ? "&#9662;" : "&#9656;"}</td></tr>`;
    if (LIVEOPEN === m.entry) h += `<tr><td colspan="7" style="padding:0 0 6px">${liveSquad(m)}</td></tr>`;
  });
  h += `</table></div><div class="note">Provisional bonus and provisional substitutions are applied
    the way the game will apply them at the end of the week.</div></div>`;
  const order = f => f.started && !f.fin ? 0 : !f.started ? 1 : 2;
  h += `<div class="card"><b class="h">Fixtures</b><div class="fxwrap">`;
  for (const f of [...LIVE.fixtures].sort((a, b) => order(a) - order(b) || (a.ko < b.ko ? -1 : 1))) {
    const chips = [];
    for (const m of ctx.mgrs) for (const p of m.counting) if (p.tid === f.h || p.tid === f.a)
      chips.push({ p, who: m.name, mine: m.entry === ME });
    chips.sort((a, b) => b.p.pts - a.p.pts);
    const st = f.fin ? `<span class="fxmin ft">FT</span>` : f.started
      ? `<span class="fxmin">${f.min || 0}'</span>` : `<span class="fxmin ko">${koText(f.ko)}</span>`;
    h += `<div><div class="fxrow"><span class="fxscore"><span class="tm">${esc(LIVE.teams[f.h] || f.h)}</span>
      ${f.started ? `${f.hs ?? 0} - ${f.as ?? 0}` : "v"} <span class="tm">${esc(LIVE.teams[f.a] || f.a)}</span></span>${st}</div>
      <div class="fxplayers">${chips.length ? chips.map(c => `<span class="fxchip ${c.mine ? "mine" : ""}">${esc(c.p.name)}
        ${f.started ? c.p.pts : ""}${c.p.pb ? `<span class="provb">+${c.p.pb}</span>` : ""}
        <span class="who">· ${esc(c.who)}</span></span>`).join("") :
      `<span class="tm" style="font-size:11px">nobody in the league has a starter here</span>`}</div></div>`;
  }
  h += `</div></div>`;
  sec.innerHTML = h;
  sec.querySelectorAll("[data-l]").forEach(r => r.addEventListener("click", () => {
    const e = +r.dataset.l; LIVEOPEN = (LIVEOPEN === e ? null : e); renderLive();
  }));
}
function liveSquad(m) {
  const cell = p => {
    const mark = p.subbed_in ? `<span class="subin">IN</span> ` : p.subbed_out ? `<span class="subout">OUT</span> ` : "";
    const st = p.playing ? (p.mins ? `<span class="fxmin">${p.mins}'</span>` : `<span class="tm">on bench</span>`)
      : p.settled ? (p.mins ? `${p.mins}'` : `<span class="tm">did not play</span>`) : `<span class="togo">to play</span>`;
    return `<tr><td>${mark}<span class="nm">${esc(p.name)}</span> <span class="tm">${esc(p.team)}</span></td>
      <td><span class="pos ${p.pos}">${p.pos}</span></td><td class="num">${st}</td>
      <td class="num">${p.pts}${p.pb ? ` <span class="provb">+${p.pb}b</span>` : ""}</td></tr>`;
  };
  const xi = m.squad.filter(p => m.counting.includes(p)), bn = m.squad.filter(p => !m.counting.includes(p));
  return `<div class="det"><div class="wrap"><table>
    <tr><th>${esc(m.name)}'s XI</th><th></th><th class="num">played</th><th class="num">pts</th></tr>
    ${xi.map(cell).join("")}<tr class="benchsep"><td colspan="4">Bench · ${m.bench} pts</td></tr>
    ${bn.map(cell).join("")}</table></div></div>`;
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
  renderLive();
  const d = liveDelay();
  if (d != null && !document.hidden) LIVETIMER = setTimeout(pollLive, d);
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(LIVETIMER); else pollLive();
});

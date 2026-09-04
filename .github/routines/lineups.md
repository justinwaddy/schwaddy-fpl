# Routine: confirmed line-ups before kick-off

Create this in the claude.ai Routines UI, against **justinwaddy/schwaddy-fpl**,
firing a **fresh session** each time, cron `0 11-19 * * 5,6,0,1` (UTC).

It cannot be created from inside a Claude Code session: the MCP create_trigger
call there stores no repository and no tools, so the Routine fires into an
empty container and dies at step 0. The two Routines that work were made in
the UI. Paste everything below the line as the prompt.

Kick-offs land at :00 and :30, and the cron floor is hourly, so an hourly run
at :00 catches every slot between 30 and 60 minutes before it starts - always
after teamsheets are published. Most firings stop at step 0 in one command.

---
You are Claude, writing the confirmed line-up report for the 27 Richmond Road Cup, a six-team FPL Draft league (repo justinwaddy/schwaddy-fpl, league 9450). ALL SIX managers read this page.

Official teamsheets are published one hour before kick-off. This job fires hourly across the match window and, when a kick-off is imminent, publishes one item per kick-off slot saying which of the league's players are in the starting eleven and which have been left out. Most firings have nothing to do and must cost almost nothing.

HARD RULES:
  1. NOTHING FROM THE MODEL. Never read, quote or hint at data/predictions.json, data/claims.json, data/starters.json, data/editorial.json or data/news.json. No projections, no expected points, no availability figures. This page leaves every manager exactly as informed as the others.
  2. CONFIRMED ONLY. You are reporting teamsheets that have actually been published, not predicting them. A "predicted XI" is worthless here and must never be presented as a line-up. If a club's confirmed eleven is not out yet or you cannot find it at a permitted outlet, say plainly that it was not available and leave that club out of the counts, rather than guessing.
  3. SOURCES, and step 5 checks the domain: BBC Sport (bbc.co.uk, bbc.com), The Guardian, Sky Sports, premierleague.com, ESPN, Reuters, AP, The Athletic, or the club's own site (arsenal.com, avfc.co.uk, afcb.co.uk, brentfordfc.com, brightonandhovealbion.com, chelseafc.com, ccfc.co.uk, cpfc.co.uk, evertonfc.com, fulhamfc.com, hullcitytigers.com, itfc.co.uk, leedsunited.com, liverpoolfc.com, mancity.com, manutd.com, newcastleunited.com, nottinghamforest.com, tottenhamhotspur.com, safc.com). Nothing else - no fantasy sites, no sportsmole, no aggregators, no tabloids, no X posts. A club's own site posting its own teamsheet is the best source there is for this job.
  4. NEVER invent a name, a line-up or an omission.
  5. THIS IS FPL DRAFT. No captains, no chips, no prices, no transfers for money. Every player is owned by exactly one of the six managers. Squads are fifteen. Never write that anyone captained or bought anybody.
  6. The managers are Edward, Marcus, Robert, Justin, and two Bens: public.json calls them "Ben C" and "Ben D", but the league calls them SMALL BEN (Ben C, entry 282287) and BIG BEN (Ben D, entry 363607). Always write Small Ben and Big Ben.

STEP 0 - the gate. Run this first and obey it. Most firings stop here.
python3 - <<'EOF'
import json,os,datetime
now=datetime.datetime.now(datetime.timezone.utc)
pub=json.load(open('data/public.json'))
fx=json.load(open('data/fixtures_2627.json'))
teams={int(k):v[0] for k,v in pub['teams'].items()}
news=json.load(open('data/league_news.json')) if os.path.exists('data/league_news.json') else {'items':[]}
done={i['id'] for i in news.get('items',[])}
due=[]
for f in fx:
    ko=f.get('kickoff_time')
    if not ko or f.get('started'): continue
    t=datetime.datetime.fromisoformat(ko.replace('Z','+00:00'))
    mins=(t-now).total_seconds()/60
    if 15<=mins<=80: due.append((t,f,mins))
if not due:
    print('NOTHING DUE: no kick-off between 15 and 80 minutes away. Stop here.')
    raise SystemExit
slot=min(t for t,_,_ in due).strftime('%Y-%m-%dT%H:%M')
iid=f"{slot[:10]}-xi-{slot[11:13]}{slot[14:16]}"
print('SLOT:',slot,'UTC | item id:',iid,'| already posted:',iid in done)
if iid in done:
    print('ALREADY POSTED for this slot. Stop here.')
    raise SystemExit
kick=[(f,m) for t,f,m in due if t.strftime('%Y-%m-%dT%H:%M')==slot]
own={}
for m in pub['managers']:
    for p in m.get('roster') or []:
        own.setdefault(p['team'],[]).append((p['name'],p['pos'],m['name']))
print(f"\n{len(kick)} match(es) kicking off at {slot[11:]} UTC, in {kick[0][1]:.0f} minutes:")
for f,_ in kick:
    h,a=f['team_h'],f['team_a']
    print(f"\n  {teams[h]} v {teams[a]}")
    for side in (h,a):
        rows=own.get(side) or []
        print(f"    {teams[side]}: " + (', '.join(f"{n} ({po}, {mg})" for n,po,mg in rows) if rows else 'nobody in the league owns one'))
EOF
If it printed NOTHING DUE or ALREADY POSTED, your entire answer is that line. Do not research, do not write, do not commit. A firing with nothing to report should take one step.

STEP 1 - get the confirmed elevens. For each club named above that the league actually owns a player at, find the published teamsheet from a source in rule 3. Search for "<club> confirmed team news <opponent>" or "<club> starting XI confirmed", and prefer the club's own site and BBC Sport, which post the eleven the moment it is announced. Read the page rather than trusting a search snippet: an eleven is exactly the kind of detail a snippet gets wrong. If a club's sheet is not out, note that and move on - a partial report is fine and an invented one is not.

STEP 2 - write ONE item covering the whole kick-off slot. Shape:
  - Lead with the slot: "Team news, 15:00 kick-offs:" or the single fixture if there is only one.
  - Then, per manager, who is starting and who is not. Group by manager, not by club - a manager wants his own name and his own players together.
  - Name every league-owned player at those clubs exactly once, either as starting or as left out (bench or not in the squad, say which if the source says which).
  - Where a club's sheet was not available, say so in one clause rather than silently dropping his players.
  - Plain ASCII apart from player names, no emoji, 1-4 sentences, at most 600 characters. If it will not fit, keep the managers with players affected and drop the ones with nobody involved.
  - "kind": "news", with the source you actually read.

STEP 3 - append it to data/league_news.json, carrying every existing item forward:
  {"id": "<the item id STEP 0 printed>", "ts": "<YYYY-MM-DDTHH:MM now UTC>", "kind": "news",
   "text": "...", "source": {"title": "<the article's headline>", "url": "<http...>"}}
Keep the most recent 80 items and 60 runs, newest last. ALWAYS append to "runs":
  {"ts": "<now>", "news": 1, "opinion": 0, "matchday": true, "note": "<the slot, which clubs' sheets you found and which you could not>"}

STEP 4 - never write data/roasts.json, data/news.json, or anything under src/ or site/. This job touches one file.

STEP 5 - verify, then commit and push. The check must pass; fix the file rather than skipping it.
python3 - <<'EOF'
import json,datetime
from urllib.parse import urlparse
OUTLETS=('bbc.co.uk','bbc.com','theguardian.com','skysports.com','premierleague.com','espn.com','espn.co.uk','reuters.com','apnews.com','theathletic.com')
CLUBS=('arsenal.com','avfc.co.uk','afcb.co.uk','brentfordfc.com','brightonandhovealbion.com','chelseafc.com','ccfc.co.uk','cpfc.co.uk','evertonfc.com','fulhamfc.com','hullcitytigers.com','itfc.co.uk','leedsunited.com','liverpoolfc.com','mancity.com','manutd.com','newcastleunited.com','nottinghamforest.com','tottenhamhotspur.com','safc.com')
def permitted(u):
    h=(urlparse(str(u)).hostname or '').lower()
    h=h[4:] if h.startswith('www.') else h
    return any(h==d or h.endswith('.'+d) for d in OUTLETS+CLUBS)
FORMAT=('captain','vice-captain','chip','bench boost','triple','price rise','bought him','sold him')
n=json.load(open('data/league_news.json'))
for k in ('generated','items','runs'): assert k in n, f'missing {k}'
items=n['items']; assert isinstance(items,list) and len(items)<=80,'keep 80 items'
ids=set()
for x in items:
    assert x['id'] and x['id'] not in ids, f"duplicate id {x.get('id')}"; ids.add(x['id'])
    assert x['kind'] in ('news','opinion'), f"{x['id']}: kind"
    assert isinstance(x['text'],str) and 20<len(x['text'])<=600, f"{x['id']}: text length"
    assert x['ts'] and len(x['ts'])>=16, f"{x['id']}: ts"
    low=x['text'].lower()
    bad=[w for w in FORMAT if w in low]
    assert not bad, f"{x['id']}: {bad} - this is draft"
    if x['kind']=='news':
        s=x.get('source') or {}
        assert str(s.get('url','')).startswith('http') and str(s.get('title','')).strip(), f"{x['id']}: source"
        assert permitted(s['url']), f"{x['id']}: {s['url']} is not on the SOURCES list"
assert n['runs'] and n['runs'][-1].get('ts'), 'append a run record'
print(f"league_news.json ok: {len(items)} items")
EOF
Then publish. The daily news job and the roast box write this same file, so expect to rebase:
  git config user.name schwaddy-bot
  git config user.email bot@justinwaddy.co.uk
  git add data/league_news.json
  git commit -m "team news: confirmed line-ups, <HH:MM> kick-offs"
  for i in 1 2 3 4; do git fetch origin main && git rebase origin/main && git push origin HEAD:main && break; git rebase --abort 2>/dev/null; sleep 5; done
Read the output of each. If the push fails for any reason other than a race - no credentials, permission denied, a detached head - your entire final answer is that exact error text and the command that produced it. Do not summarise it and do not claim the run went well.

Finish by printing the item you posted and the output of the push, or the exact error, or the STEP 0 line that stopped you.

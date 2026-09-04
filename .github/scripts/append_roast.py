"""Append one suggestion to data/roasts.json, or one report to data/feedback.json.

Called by .github/workflows/suggest.yml, which is dispatched by the
Cloudflare worker when somebody uses the box on a per-manager site. The
text arrives from a public endpoint, so nothing here trusts it: it is
scrubbed of control characters, capped, matched against the six managers
by name, and written as data rather than interpolated into anything.

The file is the archive the matchday opinion pieces are written from. It
keeps the most recent KEEP suggestions and drops exact repeats, so a
double-tap on the send button cannot fill it up.
"""
import datetime
import hashlib
import json
import os
import re

PATH = "data/roasts.json"
FEEDBACK = "data/feedback.json"
MAX_TEXT = 500
KEEP = 300
MANAGERS = {"Edward", "Ben C", "Marcus", "Ben D", "Justin", "Robert"}
# The bug-and-idea button on every page posts through the same endpoint as
# the roast box, tagging the kind on the front of the text. A new endpoint
# would have meant another hand redeploy of the worker for nothing this
# does not already do. Reports must never reach the roast archive: that
# file is what the matchday opinion pieces are written from, and "the
# ticker jumps on mobile" is not a joke about anybody.
KINDS = {"bug": "bug", "idea": "feature", "feature": "feature"}
KIND_RE = re.compile(r"^\[(\w+)\]\s*")


def clean(s, limit):
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()[:limit]


def main():
    text = clean(os.environ.get("TEXT", ""), MAX_TEXT)
    frm = clean(os.environ.get("FROM_NAME", ""), 40)
    about = clean(os.environ.get("ABOUT_NAME", ""), 40)

    if len(text) < 4:
        raise SystemExit("nothing usable in the text")
    if frm not in MANAGERS:
        raise SystemExit(f"unknown sender {frm!r}")
    if about and about not in MANAGERS:
        raise SystemExit(f"unknown target {about!r}")

    tag = KIND_RE.match(text)
    if tag and tag.group(1).lower() in KINDS:
        return feedback(KINDS[tag.group(1).lower()], text[tag.end():].strip(), frm)

    try:
        d = json.load(open(PATH))
    except Exception:
        d = {}
    items = d.get("items") if isinstance(d.get("items"), list) else []
    runs = d.get("runs") if isinstance(d.get("runs"), list) else []

    now = datetime.datetime.now(datetime.timezone.utc)
    key = hashlib.sha256(f"{frm}|{about}|{text.lower()}".encode()).hexdigest()[:10]
    if any(isinstance(i, dict) and i.get("key") == key for i in items):
        print("already have that one, nothing to do")
        return

    items.append(dict(id=f"{now:%Y-%m-%d}-{key}", key=key,
                      ts=now.strftime("%Y-%m-%dT%H:%M"),
                      frm=frm, about=about or None, text=text, used=False))
    runs.append(dict(ts=now.strftime("%Y-%m-%dT%H:%M"), frm=frm,
                     about=about or None, chars=len(text)))
    json.dump(dict(items=items[-KEEP:], runs=runs[-KEEP:]),
              open(PATH, "w"), indent=1, ensure_ascii=False)
    print(f"filed a suggestion from {frm} about {about or 'the league'} "
          f"({len(text)} chars); {len(items)} in the archive")


def feedback(kind, text, frm):
    """A bug report or a feature idea, for the end-of-week sort through."""
    if len(text) < 4:
        raise SystemExit("nothing usable in the report")
    try:
        d = json.load(open(FEEDBACK))
    except Exception:
        d = {}
    items = d.get("items") if isinstance(d.get("items"), list) else []

    now = datetime.datetime.now(datetime.timezone.utc)
    key = hashlib.sha256(f"{kind}|{text.lower()}".encode()).hexdigest()[:10]
    if any(isinstance(i, dict) and i.get("key") == key and i.get("state") == "open"
           for i in items):
        print("already open, nothing to do")
        return

    items.append(dict(id=f"{now:%Y-%m-%d}-{key}", key=key,
                      ts=now.strftime("%Y-%m-%dT%H:%M"),
                      kind=kind, frm=frm, text=text, state="open"))
    json.dump(dict(items=items[-KEEP:]), open(FEEDBACK, "w"),
              indent=1, ensure_ascii=False)
    n = sum(1 for i in items if i.get("state") == "open")
    print(f"filed a {kind} report from {frm} ({len(text)} chars); {n} open")


if __name__ == "__main__":
    main()

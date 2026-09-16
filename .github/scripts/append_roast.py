"""Append one suggestion to data/roasts.json, or one report to data/feedback.json.

Called by .github/workflows/suggest.yml, which is dispatched by the
Cloudflare worker when somebody uses the box on a per-manager site. The
text arrives from a public endpoint, so nothing here trusts it: it is
scrubbed of control characters, capped, matched against the six managers
by name, and written as data rather than interpolated into anything.

The file is the archive the matchday opinion pieces are written from. It
keeps the most recent KEEP suggestions and drops exact repeats, so a
double-tap on the send button cannot fill it up.

A suggestion may also carry a picture - a screenshot of somebody's bench,
a graph, the man himself. It arrives base64 in a dispatch input, already
shrunk by the browser, and is written beside the archive under a name
this script chooses, so nothing the sender typed ever reaches a path.
"""
import base64
import datetime
import hashlib
import json
import os
import re

PATH = "data/roasts.json"
FEEDBACK = "data/feedback.json"
NEWS = "data/league_news.json"
IMG_DIR = "data/roast_img"
MAX_TEXT = 500
# The worker caps the base64 at 46000 before it dispatches. This is the
# same cap applied by the thing actually holding the pen: the worker is a
# convenience, not a boundary, and a dispatch can be made without it.
MAX_IMG_B64 = 46000
# What that cap is worth once decoded, checked separately so a change to
# either one cannot quietly let a larger file through.
MAX_IMG_BYTES = 36000
# What a JPEG and a PNG start with. Anything else is not written, whatever
# the sender called it.
MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"))
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


def picture(day, key):
    """Write the suggestion's picture, and return its name relative to data/.

    Everything here came through a public endpoint, so nothing is taken on
    trust: the base64 is decoded strictly, the bytes are capped, and the
    file is only written once its first bytes say JPEG or PNG. The name is
    built from the day and the archive key rather than from anything the
    sender sent, which is what keeps this away from paths altogether.

    Returns None when the suggestion came without one, which is most of
    them.
    """
    raw = re.sub(r"\s+", "", os.environ.get("IMAGE_B64", ""))
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    if not raw:
        return None
    if len(raw) > MAX_IMG_B64:
        raise SystemExit(f"the picture is over {MAX_IMG_B64} base64 characters")
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception:
        raise SystemExit("the picture is not valid base64")
    if len(blob) > MAX_IMG_BYTES:
        raise SystemExit(f"the picture is over {MAX_IMG_BYTES} bytes")
    kind = next((ext for sig, ext in MAGIC if blob.startswith(sig)), None)
    if not kind:
        raise SystemExit("the picture is neither a JPEG nor a PNG")

    os.makedirs(IMG_DIR, exist_ok=True)
    name = f"{day}-{key}.{kind}"
    with open(os.path.join(IMG_DIR, name), "wb") as fh:
        fh.write(blob)
    print(f"wrote {IMG_DIR}/{name} ({len(blob)} bytes)")
    return f"{os.path.basename(IMG_DIR)}/{name}"


def prune(items):
    """Delete pictures nothing points at any more.

    KEEP trims the oldest suggestions off the front of the archive, and
    without this their pictures would sit in the repository forever, which
    defeats the point of having a cap. A picture a news item has taken up
    is kept whatever the archive has done with it, because the site loads
    that file from this repository and a missing one is a broken image on
    the page.
    """
    if not os.path.isdir(IMG_DIR):
        return
    keep = set()
    for lot in (items, _news_items()):
        for i in lot:
            src = (i.get("image") or {}).get("src") if isinstance(i, dict) else None
            if src:
                keep.add(os.path.basename(str(src)))
    for f in sorted(os.listdir(IMG_DIR)):
        if f not in keep:
            os.remove(os.path.join(IMG_DIR, f))
            print(f"dropped {IMG_DIR}/{f}: nothing points at it now")


def _news_items():
    try:
        d = json.load(open(NEWS))
    except Exception:
        return []
    return d.get("items") if isinstance(d.get("items"), list) else []


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
        readable = True
    except Exception:
        d, readable = {}, False
    items = d.get("items") if isinstance(d.get("items"), list) else []
    runs = d.get("runs") if isinstance(d.get("runs"), list) else []

    now = datetime.datetime.now(datetime.timezone.utc)
    key = hashlib.sha256(f"{frm}|{about}|{text.lower()}".encode()).hexdigest()[:10]
    if any(isinstance(i, dict) and i.get("key") == key for i in items):
        print("already have that one, nothing to do")
        return

    # Only now, once the suggestion is known to be new: a double-tap on
    # the send button should not leave a second copy of the picture behind.
    src = picture(f"{now:%Y-%m-%d}", key)
    item = dict(id=f"{now:%Y-%m-%d}-{key}", key=key,
                ts=now.strftime("%Y-%m-%dT%H:%M"),
                frm=frm, about=about or None, text=text, used=False)
    if src:
        # The feed will not show a picture without alt text, and the
        # sender was not asked for any. The suggestion itself is the best
        # thing to hand - it says what the picture is for, if not what is
        # in it - and the run that publishes one writes a proper one.
        item["image"] = dict(src=src, alt=text[:160])
    items.append(item)
    runs.append(dict(ts=now.strftime("%Y-%m-%dT%H:%M"), frm=frm,
                     about=about or None, chars=len(text), img=bool(src)))
    items = items[-KEEP:]
    # An archive that would not parse gives an empty item list, and pruning
    # against that would take every picture in the folder with it. The
    # suggestion still gets filed; the sweeping waits for a run that can
    # actually see what is in use.
    if readable:
        prune(items)
    json.dump(dict(items=items, runs=runs[-KEEP:]),
              open(PATH, "w"), indent=1, ensure_ascii=False)
    print(f"filed a suggestion from {frm} about {about or 'the league'} "
          f"({len(text)} chars{', with a picture' if src else ''}); "
          f"{len(items)} in the archive")


def feedback(kind, text, frm):
    """A bug report or a feature idea, for the end-of-week sort through.

    No picture: the bug-and-idea box does not offer one, so anything
    arriving here with one was not sent by the site and is dropped rather
    than written to disk.
    """
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

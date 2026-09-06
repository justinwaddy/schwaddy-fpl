"""Rewrite today's flop lines, and nothing else.

news.py picks the flops - an hour on the pitch for two points or fewer -
and dresses each one in a canned line off a list of seven. The list is
short, it repeats within a weekend, and by the third "Excellent cardio"
nobody is reading them. The evening wrap can write better ones; this is
the only door it gets to do it through.

The point of a script rather than "edit the file" is that the file also
holds the engine's own state and the injury and transfer feed. So this
takes a list of {"old": ..., "new": ...}, touches only events of type
"flop" stamped today, and refuses the whole batch if anything else in
either file would move. It also refuses a rewrite that drops the
player's name or any number out of the original: a flop line is a
factual claim about minutes and points, and the joke goes around the
facts rather than through them.

data/news.json is the source the next refresh rebuilds from, and
data/public.json is what the six pages actually read, so both are
written; public.py reproduces the same text from news.json on its next
run, which keeps them in step.
"""
import datetime
import json
import re
import sys

NEWS = "data/news.json"
PUBLIC = "data/public.json"
MAX = 400            # public.py's own cap on an item


def _facts(s):
    """The bits a rewrite has to keep: the player, and every number."""
    name = s.split(" (")[0].strip().rstrip(":")
    return name, re.findall(r"-?\d+", s)


def main():
    edits = json.load(sys.stdin)
    if not isinstance(edits, list) or not edits:
        raise SystemExit("expected a non-empty JSON list of {old, new}")
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    news = json.load(open(NEWS))
    pub = json.load(open(PUBLIC))
    before = json.dumps(news, sort_keys=True), json.dumps(pub, sort_keys=True)

    todays = [e for e in news.get("events") or []
              if e.get("type") == "flop" and str(e.get("ts", ""))[:10] == today]
    if not todays:
        print("no flop lines from today; nothing to rewrite")
        return

    for ed in edits:
        old, new = str(ed.get("old", "")), " ".join(str(ed.get("new", "")).split())
        new = re.sub(r"[\x00-\x1f\x7f]", " ", new).strip()
        hit = [e for e in todays if e.get("text") == old]
        if len(hit) != 1:
            raise SystemExit(f"{len(hit)} of today's flop lines match {old!r} - want exactly 1")
        if not 20 < len(new) <= MAX:
            raise SystemExit(f"rewrite is {len(new)} characters, want 21 to {MAX}: {new!r}")
        name, nums = _facts(old)
        if name and name not in new:
            raise SystemExit(f"rewrite drops {name!r}: {new!r}")
        missing = [n for n in nums if n not in new]
        if missing:
            raise SystemExit(f"rewrite drops the numbers {missing} from {old!r}: {new!r}")
        hit[0]["text"] = new
        for item in pub.get("news") or []:
            if item.get("type") == "flop" and item.get("text") == old:
                item["text"] = new

    # nothing but flop text may have moved
    def shape(d, key):
        return [(e.get("ts"), e.get("type")) for e in d.get(key) or []]
    a, b = json.loads(before[0]), json.loads(before[1])
    assert shape(a, "events") == shape(news, "events"), "the event list itself changed"
    assert shape(b, "news") == shape(pub, "news"), "the published feed changed shape"
    assert a.get("state") == news.get("state"), "the engine's state block changed"
    for was, now in ((a, news), (b, pub)):
        key = "events" if "events" in was else "news"
        for x, y in zip(was[key], now[key]):
            if x.get("text") != y.get("text"):
                assert x.get("type") == "flop", f"{x.get('type')} item was rewritten"

    json.dump(news, open(NEWS, "w"))
    json.dump(pub, open(PUBLIC, "w"), separators=(",", ":"))
    print(f"rewrote {len(edits)} flop line(s) of today's {len(todays)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Find and follow accounts on Bluesky.

  accounts.py discover   Search public profiles by topic and print candidates (read-only).
  accounts.py follow     Follow every handle in follow.json (skips ones already followed).

Add --dry-run to `follow` to preview. Follows are capped per run so the account
never looks like a follow-bot.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from post import HERE, api

PUBLIC = "https://public.api.bsky.app/xrpc"
MAX_FOLLOWS_PER_RUN = 40

TOPICS = {
    "architecture": ["architecture", "architect", "architectural history", "brutalism", "mid-century modern", "historic preservation",
                     "architectural historian", "preservationist", "landmarks", "modernist architecture", "architecture photography",
                     "interior design", "architecture critic", "historic buildings", "urban planning", "adaptive reuse", "art deco",
                     "midcentury architecture", "architecture student", "built environment"],
    "design": ["urbanism", "industrial design", "graphic design", "typography", "product design", "graphic designer", "design history",
               "type design", "poster design", "industrial designer", "ux design", "design studio", "illustrator", "design museum"],
    "ios": ["indie ios developer", "swiftui", "indie app developer", "apple developer", "ios developer", "swift developer",
            "mac developer", "app store", "indie dev", "apple platforms", "xcode", "wwdc", "swiftdata", "solo founder", "indie hacker"],
    "collecting": ["vinyl collector", "record collecting", "book collector", "trading cards collector", "record store", "vinyl records",
                   "used bookstore", "bibliophile", "zine", "sneaker collector", "record shop", "thrifting", "antiques",
                   "lego collector", "comic collector", "watch collector", "collection"],
    "roads": ["road trip", "scenic drives", "roadside america", "independent bookshop", "coffee shop", "scenic byway", "road trips",
              "national parks", "roadside attractions", "diner", "coffee roaster", "small town travel", "travel photography", "bookshop"],
    "making": ["laser cutter", "3d printing", "maker", "woodworking", "laser engraving", "3d printer", "makerspace", "cnc",
               "fabrication", "woodworker", "product design maker", "craft"],
}


BAD = re.compile(r"trump|republican|maga|\bdem\b|democrat|progressive|lefty|liberal|resist|fuck|shit|sweary|fascis|anti-|vote|biden|"
                 r"politic|activist|nsfw|18\+|🔞|dm me|follow back|follows? back|crypto|nft|onlyfans|\bbot\b|snark|🍉|genocide|gaza|"
                 r"kink|porn|fetish|furry|fursuit|commissions? open|ai art|prompt", re.I)
GOOD = re.compile(r"architect|preserv|building|landmark|brutal|modernis|urban|planner|design|heritage|historic|typograph|industrial|"
                  r"graphic|poster|studio|\bios\b|swift|\bapp|indie|developer|apple|xcode|vinyl|record|collect|book|crate|zine|"
                  r"road|drive|trip|travel|coffee|cafe|diner|roadside|laser|3d|print|maker|woodwork|fabricat|cnc|craft|workshop", re.I)


def get(path, **params):
    url = f"{PUBLIC}/{path}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers={"User-Agent": "ezgstudio-social/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt == 2:
                print(f"  ! {path} failed: {e}", file=sys.stderr)
                return {}
            time.sleep(2)


def discover():
    seen, found = set(), {}
    for theme, queries in TOPICS.items():
        for q in queries:
            for a in get("app.bsky.actor.searchActors", q=q, limit=25).get("actors", []):
                if a["did"] not in seen:
                    seen.add(a["did"])
                    found[a["did"]] = (theme, q)
            time.sleep(0.3)
    print(f"{len(found)} unique profiles found; checking activity…", file=sys.stderr)

    dids = list(found)
    profiles = []
    for i in range(0, len(dids), 25):
        profiles += get("app.bsky.actor.getProfiles", actors=dids[i:i + 25]).get("profiles", [])
        time.sleep(0.3)

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    skip = {h.lstrip("@").lower() for h in json.loads((HERE / "follow.json").read_text())}
    pool = []
    for p in profiles:
        text = f"{p.get('displayName', '')} {p.get('description', '')}"
        fol, posts = p.get("followersCount", 0), p.get("postsCount", 0)
        if p.get("labels") or not p.get("description") or p["handle"].lower() in skip:
            continue
        if BAD.search(text) or not GOOD.search(text):
            continue
        if not (200 <= fol <= 300_000) or posts < 50:
            continue
        pool.append(p)
    print(f"{len(pool)} pass the filters; checking recent activity…", file=sys.stderr)

    def active(p):
        feed = get("app.bsky.feed.getAuthorFeed", actor=p["did"], limit=1, filter="posts_no_replies").get("feed", [])
        if not feed:
            return None
        when = feed[0]["post"]["record"].get("createdAt", "")
        try:
            ok = datetime.fromisoformat(when.replace("Z", "+00:00")) >= cutoff
        except ValueError:
            return None
        if not ok:
            return None
        theme, q = found[p["did"]]
        bio = " ".join(p["description"].split())[:110]
        return (theme, q, p["handle"], p.get("displayName", ""), p.get("followersCount", 0), p.get("postsCount", 0), bio)

    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = [r for r in ex.map(active, pool) if r]

    rows.sort(key=lambda r: (r[0], -r[4]))
    print("theme | query | handle | name | followers | posts | bio")
    for r in rows:
        print(" | ".join(str(x) for x in r))
    print(f"\n{len(rows)} active candidates", file=sys.stderr)


def follow(dry):
    handles = [h.lstrip("@").strip().lower() for h in json.loads((HERE / "follow.json").read_text())]
    session = api("com.atproto.server.createSession", {
        "identifier": os.environ["BLUESKY_HANDLE"],
        "password": os.environ["BLUESKY_APP_PASSWORD"],
    })
    token, me = session["accessJwt"], session["did"]

    already, cursor = set(), None
    while True:
        q = f"com.atproto.repo.listRecords?repo={me}&collection=app.bsky.graph.follow&limit=100"
        res = api(q + (f"&cursor={cursor}" if cursor else ""), token=token)
        already |= {r["value"]["subject"] for r in res.get("records", [])}
        cursor = res.get("cursor")
        if not cursor:
            break

    done = 0
    for h in handles:
        res = api(f"com.atproto.identity.resolveHandle?handle={urllib.parse.quote(h)}", allow_missing=True)
        if not res:
            print(f"  ? {h}: handle not found, skipped")
            continue
        did = res["did"]
        if did == me or did in already:
            print(f"  = {h}: already following, skipped")
            continue
        if done >= MAX_FOLLOWS_PER_RUN:
            print(f"  … {h}: over the per-run cap, left for the next run")
            continue
        if dry:
            print(f"  + {h}: would follow")
            done += 1
            continue
        api("com.atproto.repo.createRecord", {
            "repo": me, "collection": "app.bsky.graph.follow",
            "record": {"$type": "app.bsky.graph.follow", "subject": did,
                       "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
        }, token=token)
        print(f"  + {h}: followed")
        done += 1
        time.sleep(1)
    print(f"{done} new follows" if not dry else f"dry run, {done} would be followed")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "discover":
        discover()
    elif mode == "follow":
        follow("--dry-run" in sys.argv)
    else:
        sys.exit(__doc__)

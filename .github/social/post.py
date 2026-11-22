#!/usr/bin/env python3
"""Publish due posts from queue.json to Bluesky.

A post goes out when: approved is true, its `at` time has passed, and its id
is not already in posted.json. Run with --dry-run to validate without posting.

Secrets (GitHub → Settings → Secrets and variables → Actions):
  BLUESKY_HANDLE        e.g. ezgstudio.bsky.social
  BLUESKY_APP_PASSWORD  an app password, never the account password
"""
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
QUEUE = HERE / "queue.json"
POSTED = HERE / "posted.json"
PDS = "https://bsky.social/xrpc"
MAX_CHARS = 300
MAX_BLOB = 976_000  # Bluesky image limit (~976 KB)

URL_RE = re.compile(r"https?://[^\s)]+[^\s).,!?;:'\"]")
TAG_RE = re.compile(r"(?:^|\s)(#[A-Za-z][A-Za-z0-9_]*)")


def api(method, body=None, token=None, raw=None, content_type="application/json", allow_missing=False):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"{PDS}/{method}", data=data, method="POST" if data else "GET")
    if data is not None:
        req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if allow_missing and e.code == 400:
            return None
        sys.exit(f"Bluesky {method} failed: {e.code} {e.read().decode(errors='replace')}")


def facets(text):
    """Make links and hashtags clickable (Bluesky uses UTF-8 byte offsets)."""
    out = []

    def span(start, end):
        return {"byteStart": len(text[:start].encode()), "byteEnd": len(text[:end].encode())}

    for m in URL_RE.finditer(text):
        out.append({"index": span(m.start(), m.end()),
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group()}]})
    for m in TAG_RE.finditer(text):
        out.append({"index": span(m.start(1), m.end(1)),
                    "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)[1:]}]})
    return out


def upload(path, token):
    p = REPO / path
    data = p.read_bytes()
    if len(data) > MAX_BLOB:
        sys.exit(f"{path} is {len(data)} bytes; Bluesky's limit is {MAX_BLOB}")
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    return api("com.atproto.repo.uploadBlob", raw=data, token=token, content_type=mime)["blob"]


def validate(post):
    errs = []
    for key in ("id", "at", "text"):
        if not post.get(key):
            errs.append(f"missing '{key}'")
    if len(post.get("text", "")) > MAX_CHARS:
        errs.append(f"text is {len(post['text'])} chars (max {MAX_CHARS})")
    for img in post.get("images", []):
        if not (REPO / img["path"]).is_file():
            errs.append(f"image not found: {img['path']}")
        if not img.get("alt"):
            errs.append(f"image needs alt text: {img['path']}")
    if len(post.get("images", [])) > 4:
        errs.append("max 4 images")
    card = post.get("card")
    if card and card.get("thumb") and not (REPO / card["thumb"]).is_file():
        errs.append(f"card thumb not found: {card['thumb']}")
    try:
        datetime.fromisoformat(post.get("at", ""))
    except ValueError:
        errs.append(f"bad 'at' time: {post.get('at')!r} (use e.g. 2026-11-24T12:00:00-05:00)")
    return errs


def build_record(post, token):
    record = {
        "$type": "app.bsky.feed.post",
        "text": post["text"],
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "langs": ["en"],
    }
    f = facets(post["text"])
    if f:
        record["facets"] = f
    if post.get("images"):
        images = []
        for img in post["images"]:
            entry = {"image": upload(img["path"], token), "alt": img["alt"]}
            if img.get("width") and img.get("height"):
                entry["aspectRatio"] = {"width": img["width"], "height": img["height"]}
            images.append(entry)
        record["embed"] = {"$type": "app.bsky.embed.images", "images": images}
    elif post.get("card"):
        c = post["card"]
        external = {"uri": c["uri"], "title": c.get("title", ""), "description": c.get("description", "")}
        if c.get("thumb"):
            external["thumb"] = upload(c["thumb"], token)
        record["embed"] = {"$type": "app.bsky.embed.external", "external": external}
    return record


def main():
    dry = "--dry-run" in sys.argv
    queue = json.loads(QUEUE.read_text())
    posted = json.loads(POSTED.read_text()) if POSTED.exists() else {}

    problems = {p.get("id", "?"): validate(p) for p in queue}
    problems = {k: v for k, v in problems.items() if v}
    if problems:
        for pid, errs in problems.items():
            print(f"[{pid}] " + "; ".join(errs))
        sys.exit(1)

    now = datetime.now(timezone.utc)
    due = [p for p in queue
           if p.get("approved") and p["id"] not in posted
           and datetime.fromisoformat(p["at"]) <= now]

    pending = [p for p in queue if p["id"] not in posted]
    print(f"{len(queue)} in queue · {len(pending)} unposted · {len(due)} due now")
    if dry or not due:
        for p in pending:
            flag = "DUE" if p in due else ("approved" if p.get("approved") else "awaiting approval")
            print(f"  {p['at']}  {p['id']:<28} {flag}  ({len(p['text'])} chars)")
        return

    session = api("com.atproto.server.createSession", {
        "identifier": os.environ["BLUESKY_HANDLE"],
        "password": os.environ["BLUESKY_APP_PASSWORD"],
    })
    token, did = session["accessJwt"], session["did"]

    # Post at most one per run so a backlog never floods the feed.
    p = sorted(due, key=lambda x: x["at"])[0]
    res = api("com.atproto.repo.createRecord", {
        "repo": did, "collection": "app.bsky.feed.post", "record": build_record(p, token),
    }, token=token)
    rkey = res["uri"].rsplit("/", 1)[-1]
    posted[p["id"]] = {
        "uri": res["uri"],
        "url": f"https://bsky.app/profile/{session['handle']}/post/{rkey}",
        "posted_at": now.isoformat(timespec="seconds"),
    }
    POSTED.write_text(json.dumps(posted, indent=2) + "\n")
    print(f"Posted {p['id']} → {posted[p['id']]['url']}")


if __name__ == "__main__":
    main()

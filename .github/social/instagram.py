#!/usr/bin/env python3
"""Instagram publishing.

  instagram.py check              Verify the token + account ID work (read-only; prints no secrets).
  instagram.py test               Create an image container for the next approved post, confirm Instagram
                                  accepts it, and STOP (nothing is published).
  instagram.py publish [--dry-run]  Publish the oldest approved, due, not-yet-posted Instagram post
                                  (at most one per run). Results are logged to posted.json as "instagram:<id>".

Secret (GitHub Actions): INSTAGRAM_TOKEN. The account ID lives in instagram.json (an identifier, not a credential).
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import time
from datetime import datetime, timezone

from post import POSTED, QUEUE

HOSTS = ["https://graph.facebook.com", "https://graph.instagram.com"]
GRAPH = HOSTS[0]  # the token from "API setup with Unified login" is accepted here
SITE = "https://ezgstudio.github.io"


def call(host, path, params=None, method="GET"):
    params = dict(params or {})
    params["access_token"] = os.environ["INSTAGRAM_TOKEN"]
    url = f"{host}/{path}"
    data = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"error": {"message": "unparseable error body"}}


def account_id():
    """Account ID is an identifier, not a credential: env var wins, else instagram.json."""
    val = os.environ.get("INSTAGRAM_ACCOUNT_ID", "").strip()
    if val:
        return val
    from pathlib import Path
    cfg = Path(__file__).resolve().parent / "instagram.json"
    return json.loads(cfg.read_text())["account_id"].strip() if cfg.exists() else ""


def check():
    acct = account_id()
    tok = os.environ["INSTAGRAM_TOKEN"].strip()
    print(f"token length: {len(tok)} chars · account id: {acct}")
    ok = False
    for host in HOSTS:
        status, body = call(host, acct, {"fields": "id,username"})
        err = body.get("error", {})
        print(f"{host} → HTTP {status}: " + (f"{err.get('message')} (code {err.get('code')})" if err else json.dumps(body)))
        ok = ok or (status == 200 and "username" in body)
    sys.exit(0 if ok else 1)


def die(msg):
    print(f"ERROR: {msg}")
    sys.exit(1)


def due_posts(now):
    queue = json.loads(QUEUE.read_text())
    posted = json.loads(POSTED.read_text()) if POSTED.exists() else {}
    out = []
    for p in queue:
        ig = p.get("instagram")
        if not ig or not ig.get("approved") or f"instagram:{p['id']}" in posted:
            continue
        if len(ig.get("caption", "")) > 2200:
            die(f"{p['id']}: caption over 2,200 characters")
        if datetime.fromisoformat(ig["at"]) <= now:
            out.append(p)
    return sorted(out, key=lambda p: p["instagram"]["at"]), queue, posted


def wait_for_image(url, tries=9):
    for i in range(tries):
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ezgstudio-social/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                ctype = r.headers.get("Content-Type", "")
                if r.status == 200 and ctype.startswith("image/jpeg"):
                    return
                die(f"{url} returned HTTP {r.status} / {ctype}; Instagram needs a public JPEG")
        except urllib.error.HTTPError as e:
            if e.code != 404 or i == tries - 1:
                die(f"{url} returned HTTP {e.code}")
        print(f"  image not live yet, waiting ({i + 1}/{tries})…")
        time.sleep(20)


def make_container(acct, post):
    ig = post["instagram"]
    url = f"{SITE}/{ig['image']}"
    wait_for_image(url)
    status, body = call(GRAPH, f"{acct}/media", {"image_url": url, "caption": ig["caption"]}, "POST")
    if status != 200 or "id" not in body:
        err = body.get("error", {})
        die(f"container creation failed: HTTP {status} {err.get('message')} (code {err.get('code')}, subcode {err.get('error_subcode')})")
    cid = body["id"]
    for _ in range(30):
        status, body = call(GRAPH, cid, {"fields": "status_code"})
        code = body.get("status_code")
        if code == "FINISHED":
            return cid
        if code in ("ERROR", "EXPIRED"):
            die(f"container {cid} ended in state {code}")
        time.sleep(4)
    die(f"container {cid} not ready after 2 minutes")


def test():
    acct = account_id()
    now = datetime.now(timezone.utc)
    # For a test, use the next approved unposted post whether or not it is due yet.
    queue = json.loads(QUEUE.read_text())
    posted = json.loads(POSTED.read_text()) if POSTED.exists() else {}
    cand = [p for p in queue if p.get("instagram", {}).get("approved") and f"instagram:{p['id']}" not in posted]
    if not cand:
        die("no approved, unposted Instagram posts in the queue")
    post = sorted(cand, key=lambda p: p["instagram"]["at"])[0]
    print(f"testing with '{post['id']}' (scheduled {post['instagram']['at']}); this will NOT publish")
    cid = make_container(acct, post)
    print(f"OK: Instagram accepted the image and caption. Container {cid} is FINISHED and will expire unused.")


def publish(dry):
    acct = account_id()
    now = datetime.now(timezone.utc)
    due, queue, posted = due_posts(now)
    print(f"{len(due)} Instagram post(s) due now")
    if not due:
        return
    post = due[0]
    ig = post["instagram"]
    print(f"next: '{post['id']}' scheduled {ig['at']}, image {ig['image']}")
    if dry:
        print("dry run: nothing published")
        return
    cid = make_container(acct, post)
    status, body = call(GRAPH, f"{acct}/media_publish", {"creation_id": cid}, "POST")
    if status != 200 or "id" not in body:
        err = body.get("error", {})
        die(f"publish failed: HTTP {status} {err.get('message')} (code {err.get('code')})")
    media_id = body["id"]
    # Log immediately so a later hiccup can never cause a duplicate post.
    posted = json.loads(POSTED.read_text()) if POSTED.exists() else {}
    posted[f"instagram:{post['id']}"] = {"media_id": media_id, "posted_at": now.isoformat(timespec="seconds")}
    POSTED.write_text(json.dumps(posted, indent=2) + "\n")
    _, info = call(GRAPH, media_id, {"fields": "permalink"})
    if info.get("permalink"):
        posted[f"instagram:{post['id']}"]["url"] = info["permalink"]
        POSTED.write_text(json.dumps(posted, indent=2) + "\n")
    print(f"Published '{post['id']}' → {info.get('permalink', media_id)}")


if __name__ == "__main__":
    mode = (sys.argv[1:] or [""])[0]
    if mode == "check":
        check()
    elif mode == "test":
        test()
    elif mode == "publish":
        publish("--dry-run" in sys.argv)
    else:
        sys.exit(__doc__)

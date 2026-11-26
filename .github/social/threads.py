#!/usr/bin/env python3
"""Threads publishing (same pattern as instagram.py).

  threads.py check               Verify the token works (prints the username only).
  threads.py test                Create a container for the next approved post and STOP (nothing is published).
  threads.py publish [--dry-run] Publish the oldest approved, due, not-yet-posted Threads post (one per run).

Secret (GitHub Actions): THREADS_TOKEN. Results are logged to posted.json as "threads:<id>".
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from post import POSTED, QUEUE

API = "https://graph.threads.net/v1.0"
SITE = "https://ezgstudio.github.io"


def call(path, params=None, method="GET"):
    params = dict(params or {})
    params["access_token"] = os.environ["THREADS_TOKEN"].strip()
    url = f"{API}/{path}"
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


def die(msg):
    print(f"ERROR: {msg}")
    sys.exit(1)


def me():
    status, body = call("me", {"fields": "id,username"})
    if status != 200 or "id" not in body:
        err = body.get("error", {})
        die(f"token check failed: HTTP {status} {err.get('message')} (code {err.get('code')})")
    return body


def check():
    b = me()
    print(f"OK: token works for @{b.get('username')} (user id {b['id']})")


def make_container(uid, post):
    th = post["threads"]
    params = {"text": th["text"]}
    if th.get("image"):
        url = f"{SITE}/{th['image']}"
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ezgstudio-social/1.0"})
        for i in range(9):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    if r.status == 200 and r.headers.get("Content-Type", "").startswith("image/"):
                        break
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    die(f"{url} returned HTTP {e.code}")
            print(f"  image not live yet, waiting ({i + 1}/9)…")
            time.sleep(20)
        else:
            die(f"{url} never became available")
        params.update({"media_type": "IMAGE", "image_url": url})
    else:
        params["media_type"] = "TEXT"
    status, body = call(f"{uid}/threads", params, "POST")
    if status != 200 or "id" not in body:
        err = body.get("error", {})
        die(f"container creation failed: HTTP {status} {err.get('message')} (code {err.get('code')})")
    cid = body["id"]
    for _ in range(30):
        status, b = call(cid, {"fields": "status,error_message"})
        st = b.get("status")
        if st == "FINISHED":
            return cid
        if st in ("ERROR", "EXPIRED"):
            die(f"container {cid} ended in {st}: {b.get('error_message')}")
        time.sleep(4)
    die(f"container {cid} not ready after 2 minutes")


def load():
    return json.loads(QUEUE.read_text()), (json.loads(POSTED.read_text()) if POSTED.exists() else {})


def test():
    uid = me()["id"]
    queue, posted = load()
    cand = [p for p in queue if p.get("threads", {}).get("approved") and f"threads:{p['id']}" not in posted]
    if not cand:
        die("no approved, unposted Threads posts in the queue")
    post = sorted(cand, key=lambda p: p["threads"]["at"])[0]
    print(f"testing with '{post['id']}' (scheduled {post['threads']['at']}); this will NOT publish")
    cid = make_container(uid, post)
    print(f"OK: Threads accepted the post. Container {cid} is FINISHED and will expire unused.")


def publish(dry):
    now = datetime.now(timezone.utc)
    queue, posted = load()
    due = sorted(
        [p for p in queue if p.get("threads", {}).get("approved") and f"threads:{p['id']}" not in posted
         and datetime.fromisoformat(p["threads"]["at"]) <= now],
        key=lambda p: p["threads"]["at"])
    print(f"{len(due)} Threads post(s) due now")
    if not due:
        return
    post = due[0]
    print(f"next: '{post['id']}' scheduled {post['threads']['at']}")
    if dry:
        print("dry run: nothing published")
        return
    uid = me()["id"]
    cid = make_container(uid, post)
    time.sleep(5)
    status, body = call(f"{uid}/threads_publish", {"creation_id": cid}, "POST")
    if status != 200 or "id" not in body:
        err = body.get("error", {})
        die(f"publish failed: HTTP {status} {err.get('message')} (code {err.get('code')})")
    media_id = body["id"]
    _, posted = load()  # re-read, in case Instagram logged in the same run
    posted[f"threads:{post['id']}"] = {"media_id": media_id, "posted_at": now.isoformat(timespec="seconds")}
    POSTED.write_text(json.dumps(posted, indent=2) + "\n")
    _, info = call(media_id, {"fields": "permalink"})
    if info.get("permalink"):
        posted[f"threads:{post['id']}"]["url"] = info["permalink"]
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

#!/usr/bin/env python3
"""Apply profile.json (name, bio, avatar, banner) to the Bluesky account.

Keeps any profile fields not managed here (e.g. a pinned post).
Run with --dry-run to validate without changing anything.
"""
import json
import os
import sys

from post import HERE, REPO, MAX_BLOB, api, upload

LIMITS = {"displayName": 64, "description": 256}


def main():
    dry = "--dry-run" in sys.argv
    cfg = json.loads((HERE / "profile.json").read_text())

    errs = [f"{k} is {len(cfg[k])} chars (max {n})" for k, n in LIMITS.items() if len(cfg.get(k, "")) > n]
    for key in ("avatar", "banner"):
        if cfg.get(key):
            p = REPO / cfg[key]
            if not p.is_file():
                errs.append(f"{key} not found: {cfg[key]}")
            elif p.stat().st_size > MAX_BLOB:
                errs.append(f"{key} too large: {p.stat().st_size} bytes")
    if errs:
        sys.exit("\n".join(errs))

    print(f"displayName: {cfg['displayName']}\ndescription ({len(cfg['description'])} chars):\n{cfg['description']}")
    if dry:
        return

    session = api("com.atproto.server.createSession", {
        "identifier": os.environ["BLUESKY_HANDLE"],
        "password": os.environ["BLUESKY_APP_PASSWORD"],
    })
    token, did = session["accessJwt"], session["did"]

    current = api(f"com.atproto.repo.getRecord?repo={did}&collection=app.bsky.actor.profile&rkey=self",
                  token=token, allow_missing=True)
    record = (current or {}).get("value", {"$type": "app.bsky.actor.profile"})
    record["displayName"] = cfg["displayName"]
    record["description"] = cfg["description"]
    for key in ("avatar", "banner"):
        if cfg.get(key):
            record[key] = upload(cfg[key], token)

    body = {"repo": did, "collection": "app.bsky.actor.profile", "rkey": "self", "record": record}
    if current and current.get("cid"):
        body["swapRecord"] = current["cid"]
    api("com.atproto.repo.putRecord", body, token=token)
    print(f"Profile updated → https://bsky.app/profile/{session['handle']}")


if __name__ == "__main__":
    main()

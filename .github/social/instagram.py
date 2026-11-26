#!/usr/bin/env python3
"""Instagram publishing helpers.

  instagram.py check     Verify the token + account ID work (read-only; prints no secrets).

Secrets (GitHub Actions): INSTAGRAM_TOKEN, INSTAGRAM_ACCOUNT_ID
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HOSTS = ["https://graph.facebook.com", "https://graph.instagram.com"]


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
        status, body = call(host, acct, {"fields": "id,username,account_type"})
        err = body.get("error", {})
        print(f"{host} → HTTP {status}: " + (f"{err.get('message')} (code {err.get('code')})" if err else json.dumps(body)))
        ok = ok or (status == 200 and "username" in body)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if (sys.argv[1:] or [""])[0] == "check":
        check()
    else:
        sys.exit(__doc__)

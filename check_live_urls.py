#!/usr/bin/env python3
"""Check whether the original (non-Wayback) product URLs still resolve.

Writes data/archive_live_status.json: id -> status / final URL / live_ok.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "data/archive_products_raw.json"
OUT = ROOT / "data/archive_live_status.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"


def check(url: str) -> tuple[int | None, str, str]:
    # HEAD first; fall back to a one-byte GET if the server rejects HEAD.
    for method in ("HEAD", "GET"):
        try:
            headers = {"User-Agent": UA}
            if method == "GET":
                headers["Range"] = "bytes=0-0"
            req = urllib.request.Request(url, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=35) as resp:
                return resp.status, resp.geturl(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            if method == "GET":
                return exc.code, url, ""
        except Exception:
            if method == "GET":
                return None, url, ""
    return None, url, ""


def main() -> int:
    records = json.loads(SRC.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    if OUT.exists():
        out = json.loads(OUT.read_text(encoding="utf-8"))

    for i, rec in enumerate(records, 1):
        pid = str(rec["id"])
        url = rec.get("live_url") or rec.get("url")
        if not url:
            continue
        if pid in out and out[pid].get("live_status"):
            continue
        status, final, ctype = check(url)
        # A product page should still contain the product id (or be an /archive/ page).
        useful = bool(status == 200 and (f"/{rec['id']}.html" in final or "/archive/" in final))
        out[pid] = {
            "live_url": url,
            "live_status": status,
            "live_final_url": final,
            "live_ok": useful,
            "content_type": ctype,
        }
        print(f"[{i}/{len(records)}] {pid} {status} {final}")
        OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        time.sleep(0.35)

    ok = sum(1 for v in out.values() if v.get("live_ok"))
    print(f"wrote {OUT}: {ok}/{len(out)} useful live pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

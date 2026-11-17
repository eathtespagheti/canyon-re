#!/usr/bin/env python3
"""Recover older Canyon gravity product pages from the Wayback Machine.

Reads data/cdx_old_mtb.json (CDX output for /en-it/mountain-bikes/*), fetches
each archived product page, extracts the product name / model year / image,
then asks Canyon's PID-fallback endpoint which BOM code belongs to that
product ID.

Output: data/archive_products_raw.json (resumable).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CDX = ROOT / "data/cdx_old_mtb.json"
OUT = ROOT / "data/archive_products_raw.json"
# Wayback rate-limits browser-like UAs on this endpoint; a research UA is fine.
WB_UA = "canyon-bom-research/1.0 (+https://www.canyon.com/)"
CANYON_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
ENDPOINT = (
    "https://www.canyon.com/on/demandware.store/Sites-US-Site/en_US/"
    "Utils-SearchBike?pidFallback=true&template=explosionDrawings&serialNumber={pid}"
)


def load_cdx() -> dict[str, str]:
    base: dict[str, list[str]] = {}
    for path in sorted((ROOT / "data").glob("cdx_old_*.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))[1:]
        except Exception:
            continue
        for orig, ts, status in rows:
            if str(status) != "200":
                continue
            parsed = urllib.parse.urlparse(orig)
            clean = parsed._replace(query="", fragment="").geturl()
            if not re.search(r"/\d+\.html$", parsed.path):
                continue
            if not re.search(r"spectral|strive|torque|sender", parsed.path, re.I):
                continue
            base.setdefault(clean, []).append(ts)
    return {url: min(stamps) for url, stamps in base.items()}


def fetch(url: str, timeout: int = 60, retries: int = 3, ua: str = WB_UA, wayback: bool = False) -> str:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
            if wayback and ("Temporarily Offline" in body or (
                "Internet Archive" in body and len(body) < 60_000
            )):
                raise RuntimeError("Wayback temporarily offline")
            return body
        except urllib.error.HTTPError as exc:
            last = exc
            time.sleep(8 * (attempt + 1) if exc.code == 429 else 3 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(str(last))


def endpoint(pid: str) -> tuple[str | None, str | None, str | None]:
    try:
        body = fetch(ENDPOINT.format(pid=pid), timeout=30, retries=2, ua=CANYON_UA)
    except Exception:
        return None, None, None
    m = re.search(r"explosiondrawing-([MRU]\d{3,4})-(\d{2})\.pdf", body)
    if m:
        return m.group(1), m.group(2), f"explosiondrawings/explosiondrawing-{m.group(1)}-{m.group(2)}.pdf"
    return None, None, None


def parse(pid: str, live_url: str, ts: str, body: str) -> dict:
    title = ""
    m = re.search(r"<title>(.*?)</title>", body, re.S | re.I)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).replace("| CANYON IT", "").strip()

    product = None
    for sm in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', body, re.S | re.I):
        try:
            d = json.loads(sm.group(1).strip())
        except Exception:
            continue
        if isinstance(d, dict) and d.get("@type") in ("Product", "ProductGroup"):
            product = d
            break

    name = (product or {}).get("name") or title
    image = (product or {}).get("image") or ""
    if isinstance(image, list):
        image = image[0] if image else ""
    image = str(image)

    year = None
    ym = re.search(r"(20\d{2})[_/]", image)
    if ym:
        year = ym.group(1)
    if not year:
        ym = re.search(r"(20\d{2})", title)
        if ym:
            year = ym.group(1)

    code = None
    cm = re.search(r"_([MRU]\d{3,4})(?:_|\.|-)", image)
    if cm:
        code = cm.group(1)

    offers = (product or {}).get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = offers.get("price") if isinstance(offers, dict) else None

    bom_code, bom_rev, bom_path = endpoint(pid)
    return {
        "id": pid,
        "name": name,
        "title": title,
        "url": live_url,
        "product_url": f"https://web.archive.org/web/{ts}/{live_url}",
        "live_url": live_url,
        "archive_url": f"https://web.archive.org/web/{ts}/{live_url}",
        "page_source": "wayback",
        "archive_timestamp": ts,
        "image": image,
        "image_year": year,
        "image_code": code,
        "skus": [],
        "price_min": price,
        "bom_code": bom_code,
        "bom_rev": bom_rev,
        "bom_path": bom_path,
        "bom_url": (
            f"https://www.canyon.com/on/demandware.static/-/Library-Sites-canyon-shared/default/{bom_path}"
            if bom_path
            else None
        ),
        "seen": ["wayback"],
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, help="only process the first N URLs")
    args = ap.parse_args()

    urls = load_cdx()
    existing: dict[str, dict] = {}
    if OUT.exists():
        for rec in json.loads(OUT.read_text(encoding="utf-8")):
            existing[str(rec["id"])] = rec
    print(f"{len(urls)} archived product URLs; {len(existing)} already enriched", flush=True)

    results = dict(existing)
    items = sorted(urls.items())
    if args.limit:
        items = items[: args.limit]
    for i, (live_url, ts) in enumerate(items, 1):
        pid = re.search(r"/(\d+)\.html$", live_url).group(1)
        if pid in results and results[pid].get("name") and not results[pid].get("error"):
            continue
        wb_url = f"https://web.archive.org/web/{ts}id_/{live_url}"
        print(f"[{i}/{len(items)}] {pid} {urllib.parse.urlparse(live_url).path.split('/')[-2]}", flush=True)
        try:
            body = fetch(wb_url, ua=WB_UA, wayback=True)
            rec = parse(pid, live_url, ts, body)
            results[pid] = rec
            print(f"    {rec['name']} | {rec['image_year']} | BOM {rec['bom_code'] or '-'}", flush=True)
        except Exception as exc:  # noqa: BLE001
            results[pid] = {
                "id": pid,
                "url": live_url,
                "live_url": live_url,
                "archive_url": f"https://web.archive.org/web/{ts}/{live_url}",
                "page_source": "wayback",
                "archive_timestamp": ts,
                "error": str(exc),
                "seen": ["wayback"],
            }
            print(f"    ERROR {exc}", flush=True)
        OUT.write_text(json.dumps(list(results.values()), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        time.sleep(0.8)

    OUT.write_text(json.dumps(list(results.values()), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ok = sum(1 for r in results.values() if r.get("name") and not r.get("error"))
    print(f"wrote {OUT} ({len(results)} records, {ok} parsed)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

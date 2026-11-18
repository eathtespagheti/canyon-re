#!/usr/bin/env python3
"""Enrich all Canyon bike product pages across every family.

Reads data/all_product_urls.json (from the current + archived sitemaps) and
enriches live or archived bike-category pages: name, model year, image, frame
code, variants and the exact BOM code from Canyon's PID-fallback endpoint.

Output: data/all_products_raw.json (resumable).
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URLS = ROOT / "data/all_product_urls.json"
OUT = ROOT / "data/all_products_raw.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
WB_UA = "canyon-bom-research/1.0 (+https://www.canyon.com/)"
ARCHIVE_TS = {
    "2022-06": "20220624025705",
    "2023-06": "20230601020435",
    "2024-06": "20240601045629",
    "2025-06": "20250601045840",
    "2026-01": "20260101065929",
}
ENDPOINT = (
    "https://www.canyon.com/on/demandware.store/Sites-US-Site/en_US/"
    "Utils-SearchBike?pidFallback=true&template=explosionDrawings&serialNumber={pid}"
)

BIKE_CATEGORIES = {
    "mountain-bikes", "road-bikes", "gravel-bikes", "electric-bikes",
    "hybrid-bikes", "sale",
}
FAMILIES = [
    "Aeroad", "Ultimate", "Endurace", "Speedmax", "Grail", "Grizl", "Inflite",
    "Roadlite", "Commuter", "Precede", "Spectral", "Strive", "Torque", "Sender",
    "Lux", "Neuron", "Exceed", "Grand Canyon", "Pathlite", "Dude", "Stitched",
    "Offspring",
]
FAMILY_RE = re.compile("|".join(re.escape(f) for f in sorted(FAMILIES, key=len, reverse=True)), re.I)


def fetch(url: str, timeout: int = 45, retries: int = 2, ua: str = UA, wayback: bool = False) -> str:
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
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(str(last))


def endpoint(pid: str) -> tuple[str | None, str | None, str | None]:
    try:
        body = fetch(ENDPOINT.format(pid=pid), timeout=25, retries=1)
    except Exception:
        return None, None, None
    m = re.search(r"explosiondrawing-([MRU]\d{3,4})-(\d{2})\.pdf", body)
    if m:
        return m.group(1), m.group(2), f"explosiondrawings/explosiondrawing-{m.group(1)}-{m.group(2)}.pdf"
    return None, None, None


def parse(pid: str, url: str, body: str) -> dict:
    title = ""
    m = re.search(r"<title>(.*?)</title>", body, re.S | re.I)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).split("|")[0].strip()

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

    variants = (product or {}).get("hasVariant") or []
    skus = [v.get("sku") for v in variants if v.get("sku")]
    prices = []
    for v in variants:
        o = v.get("offers") or {}
        if isinstance(o, dict) and o.get("price"):
            try:
                prices.append(float(o["price"]))
            except ValueError:
                pass

    bom_code, bom_rev, bom_path = endpoint(pid)
    return {
        "id": pid,
        "name": name,
        "title": title,
        "url": url,
        "product_url": url,
        "live_url": url,
        "page_source": "live",
        "image": image,
        "image_year": year,
        "image_code": code,
        "skus": skus,
        "price_min": min(prices) if prices else None,
        "bom_code": bom_code,
        "bom_rev": bom_rev,
        "bom_path": bom_path,
        "bom_url": (
            f"https://www.canyon.com/on/demandware.static/-/Library-Sites-canyon-shared/default/{bom_path}"
            if bom_path else None
        ),
        "seen": ["live"],
    }


def top_category(url: str) -> str:
    parts = urllib.parse.urlparse(url).path.strip("/").split("/")
    return parts[1] if len(parts) > 1 else ""


def earliest_archive_ts(seen: list[str]) -> str | None:
    labels = [s for s in seen if s in ARCHIVE_TS]
    return ARCHIVE_TS[sorted(labels, key=lambda s: ARCHIVE_TS[s])[0]] if labels else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--include-outlet", action="store_true")
    ap.add_argument("--archived", action="store_true", help="enrich archived (non-live) bike URLs")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--jobs", type=int, default=3)
    args = ap.parse_args()

    urls = json.loads(URLS.read_text(encoding="utf-8"))
    existing: dict[str, dict] = {}
    if OUT.exists():
        for rec in json.loads(OUT.read_text(encoding="utf-8")):
            existing[str(rec["id"])] = rec

    selected = []
    for rec in urls.values():
        cat = top_category(rec["url"])
        if cat not in BIKE_CATEGORIES and not (args.include_outlet and cat == "outlet-bikes"):
            continue
        if args.archived:
            if "live" in rec.get("seen", []):
                continue
            if str(rec["id"]) in existing and existing[str(rec["id"])].get("name"):
                continue
            if not earliest_archive_ts(rec.get("seen", [])):
                continue
        else:
            if "live" not in rec.get("seen", []):
                continue
        selected.append(rec)
    selected.sort(key=lambda r: int(r["id"]))
    if args.limit:
        selected = selected[: args.limit]
    print(f"{len(selected)} {'archived' if args.archived else 'live'} bike-category product URLs", flush=True)

    def job(rec: dict) -> dict:
        pid = str(rec["id"])
        if pid in existing and existing[pid].get("name") and not existing[pid].get("error"):
            return existing[pid]

        if args.archived:
            ts = earliest_archive_ts(rec.get("seen", []))
            wb_url = f"https://web.archive.org/web/{ts}id_/{rec['url']}"
            try:
                body = fetch(wb_url, ua=WB_UA, wayback=True, retries=2)
                out = parse(pid, rec["url"], body)
                out.update({
                    "page_source": "wayback",
                    "product_url": f"https://web.archive.org/web/{ts}/{rec['url']}",
                    "archive_url": f"https://web.archive.org/web/{ts}/{rec['url']}",
                    "live_url": rec["url"],
                    "archive_timestamp": ts,
                    "seen": ["wayback"],
                })
                if not FAMILY_RE.search(out.get("name") or ""):
                    out["error"] = "not a bike family"
                return out
            except Exception:
                try:
                    body = fetch(rec["url"], ua=UA, retries=1)
                    out = parse(pid, rec["url"], body)
                    if not FAMILY_RE.search(out.get("name") or ""):
                        out["error"] = "not a bike family"
                    return out
                except Exception as exc:  # noqa: BLE001
                    return {"id": pid, "url": rec["url"], "error": str(exc), "seen": ["wayback"]}
        try:
            body = fetch(rec["url"])
            out = parse(pid, rec["url"], body)
            if not FAMILY_RE.search(out.get("name") or ""):
                out["error"] = "not a bike family"
            return out
        except Exception as exc:  # noqa: BLE001
            return {"id": pid, "url": rec["url"], "error": str(exc), "seen": ["live"]}

    results = dict(existing)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(job, rec): rec for rec in selected}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            results[str(rec["id"])] = rec
            print(f"[{i}/{len(selected)}] {rec['id']} {rec.get('name') or rec.get('error')}", flush=True)
            if i % 10 == 0:
                OUT.write_text(json.dumps(list(results.values()), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    OUT.write_text(json.dumps(list(results.values()), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ok = sum(1 for r in results.values() if r.get("name") and not r.get("error"))
    print(f"wrote {OUT} ({len(results)} records, {ok} bikes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

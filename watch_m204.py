#!/usr/bin/env python3
"""Watch for the public release of the Canyon M204 exploded drawing/BOM.

M204 is the frame code for the 2027 Spectral:ON CF / CF LTD (Avinox M2S).
At the time of writing the bike is "coming soon" (pre-orders 4 Dec 2026) and
Canyon returns "no exploded drawing" for product IDs 4388 and 4389.

This script checks every known location and downloads the PDF as soon as it
appears.  Run it with --once from cron/systemd, or leave it running.

    python3 watch_m204.py --once
    python3 watch_m204.py --interval 21600
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from scrape_canyon_boms import BASE, STATIC_ROOT, UA, download, fetch, to_uri

ROOT = f"{BASE}{STATIC_ROOT}/explosiondrawings"
PIDS = ("4388", "4389")
PAGE_URLS = (
    "https://www.canyon.com/en-it/electric-bikes/electric-mountain-bikes/spectral-on/spectral-on-cf/spectral-on-cf-9-axs/4388.html",
    "https://www.canyon.com/en-it/electric-bikes/electric-mountain-bikes/spectral-on/spectral-on-cf/spectral-on-cf-ltd/4389.html",
)
FAMILIES = ("spectral_on", "spectral")
NAMES = (
    "Spectral ON",
    "Spectral:ON",
    "Spectral ON CF",
    "Spectral ON CF 9",
    "Spectral ON CF 9 AXS",
    "Spectral ON CF LTD",
    "Spectral ON CF 9 AXS LTD",
)
YEARS = ("2026", "2027", "2028")
REVS = ("01", "02")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_pdf(url: str) -> bool:
    req = urllib.request.Request(
        to_uri(url), headers={"User-Agent": UA, "Range": "bytes=0-0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return "pdf" in resp.headers.get("Content-Type", "").lower()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def candidates() -> list[tuple[str, str]]:
    """Return (label, url) candidates, ordered most-likely first."""
    out: list[tuple[str, str]] = []
    for rev in ("01", "02", "03", "04", "05", "00"):
        out.append((f"root revision {rev}", f"{ROOT}/explosiondrawing-M204-{rev}.pdf"))
    for year in YEARS:
        for family in FAMILIES:
            for name in NAMES:
                for rev in REVS:
                    filename = f"M204-{rev}_BOM_{name}.pdf"
                    out.append(
                        (
                            f"{year}/{family}/{filename}",
                            f"{ROOT}/{year}/{family}/{urllib.parse.quote(filename)}",
                        )
                    )
    return out


def endpoint_links(pid: str) -> list[str]:
    url = (
        "https://www.canyon.com/on/demandware.store/Sites-US-Site/en_US/"
        f"Utils-SearchBike?pidFallback=true&template=explosionDrawings&serialNumber={pid}"
    )
    try:
        body = fetch(url, timeout=45)
    except SystemExit:
        return []
    if "drawingsnotfound" in body or "couldn't find any exploded drawing" in body.lower():
        return []
    links = re.findall(
        r"(https?://[^\"'\s<>]*explosiondrawing[^\"'\s<>]*\.pdf|/[^\"'\s<>]*explosiondrawing[^\"'\s<>]*\.pdf)",
        body,
        re.I,
    )
    return [urllib.parse.urljoin(BASE, link.replace("&amp;", "&")) for link in links]


def page_links(url: str) -> list[str]:
    try:
        html = fetch(url, timeout=45)
    except SystemExit:
        return []
    links = re.findall(
        r"(https?://[^\"'\s<>]*explosiondrawing[^\"'\s<>]*\.pdf|/[^\"'\s<>]*explosiondrawing[^\"'\s<>]*\.pdf)",
        html,
        re.I,
    )
    return [urllib.parse.urljoin(BASE, link.replace("&amp;", "&")) for link in links]


def run_once(out_dir: Path, log_path: Path) -> bool:
    found: list[tuple[str, str]] = []

    for pid in PIDS:
        for link in endpoint_links(pid):
            found.append((f"endpoint pid {pid}", link))
    for page in PAGE_URLS:
        for link in page_links(page):
            found.append(("product page", link))

    for label, url in candidates():
        if is_pdf(url):
            found.append((label, url))

    # De-duplicate while preserving order.
    unique: list[tuple[str, str]] = []
    seen = set()
    for label, url in found:
        key = re.sub(r"/dw[0-9a-f]{8}/", "/default/", url)
        if key not in seen:
            seen.add(key)
            unique.append((label, url))

    stamp = now()
    if not unique:
        line = f"{stamp} M204: not published (checked {len(candidates())} URLs + {len(PIDS)} PIDs)"
        print(line)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return False

    print(f"{stamp} M204: FOUND {len(unique)} link(s)")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for label, url in unique:
        filename = "explosiondrawing-M204-01.pdf" if "explosiondrawing-M204" in url else Path(urllib.parse.urlparse(url).path).name
        dest = out_dir / filename
        try:
            status, size = download(url, dest)
            print(f"  {status} {size:>9}  {dest}  ({label})")
            manifest.append({"found_at": stamp, "label": label, "url": url, "file": str(dest), "bytes": size})
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED {url}: {exc}")
    if manifest:
        (out_dir / "m204_found.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} M204: downloaded {len(manifest)} file(s): {manifest[0]['url']}\n")
    return bool(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="pdfs/post2024", help="download directory")
    parser.add_argument("--log", default="m204_watch.log")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--interval", type=int, default=21600, help="seconds between checks")
    args = parser.parse_args()

    out_dir = Path(args.out)
    log_path = Path(args.log)
    while True:
        found = run_once(out_dir, log_path)
        if args.once or found:
            return 0 if found else 1
        time.sleep(max(300, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())

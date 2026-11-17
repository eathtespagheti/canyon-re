#!/usr/bin/env python3
"""Scrape Canyon's exploded-drawing / BOM archive.

Canyon publishes one page per model year under /support-articles/exploded_drawings_YYYY.html.
Those pages contain direct links to PDFs on the Demandware static library.  This
tool discovers the year pages, extracts every BOM link, builds a deduplicated
manifest, and can optionally download the PDFs or dump their text.

Examples
--------
    python3 scrape_canyon_boms.py catalog
    python3 scrape_canyon_boms.py catalog --csv boms.csv --json boms.json
    python3 scrape_canyon_boms.py download --model M147
    python3 scrape_canyon_boms.py download --year 2022 --out pdfs
    python3 scrape_canyon_boms.py verify
    python3 scrape_canyon_boms.py extract-text --dir pdfs
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "https://www.canyon.com"
DEFAULT_LOCALE = "en-us"
STATIC_ROOT = "/on/demandware.static/-/Library-Sites-canyon-shared/default"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
DISCIPLINE = {"M": "MTB", "R": "Road", "U": "Urban", "K": "Kids"}
CODE_RE = re.compile(r"(?<![A-Za-z0-9])([MRUK]\d{1,4}(?:-\d{2})?)(?![A-Za-z0-9])")
HASH_RE = re.compile(r"/default/dw[0-9a-f]{8}/")
GRAVITY_RE = re.compile(r"spectral|strive|torque|sender", re.I)
GRAVITY_FAMILIES = {"spectral", "strive", "torque", "torque_al", "sender", "spectral_on", "strive_on", "torque_on"}


# --------------------------------------------------------------------------- #
# Gravity-MTB classification
# --------------------------------------------------------------------------- #
def is_gravity(rec: dict) -> bool:
    """True for Spectral / Strive / Torque / Sender, including :ON e-MTBs.

    Labels win over categories: a record labelled "Neuron AL" that sits under a
    combined "Neuron/Spectral" category is not a gravity bike.
    """
    if any(GRAVITY_RE.search(label) for label in rec.get("labels", [])):
        return True
    return any(cat.lower() in GRAVITY_FAMILIES for cat in rec.get("categories", []))


def canonical_family(rec: dict) -> str:
    text = " ".join(rec.get("labels", []) + rec.get("categories", []))
    for name in ("Spectral", "Strive", "Torque", "Sender"):
        if re.search(name, text, re.I):
            return name + (":ON" if gravity_kind(rec) == "e-MTB" else "")
    return ""


def gravity_kind(rec: dict) -> str:
    """MTB, e-MTB, or MTB/e-MTB for frames shared by both catalog entries."""
    labels = rec.get("labels", []) or rec.get("categories", [])
    text = " ".join(labels)
    e_marker = re.compile(r":ON\b|\bON\b|Hybrid", re.I)
    electric = any(e_marker.search(label) for label in labels)
    acoustic = any(
        re.search(r"spectral|strive|torque|sender", label, re.I) and not e_marker.search(label)
        for label in labels
    )
    if electric and acoustic:
        return "MTB/e-MTB"
    return "e-MTB" if electric else "MTB"


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def to_uri(url: str) -> str:
    """Percent-encode an IRI while preserving existing escapes and reserved chars."""
    return urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")


def fetch(url: str, timeout: int = 60, retries: int = 3) -> str:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(to_uri(url), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"failed to fetch {url}: {last}")


def download(url: str, dest: Path, timeout: int = 120, retries: int = 3) -> tuple[int, int]:
    """Download url to dest. Returns (status, bytes)."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(to_uri(url), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".part")
                size = 0
                with open(tmp, "wb") as fh:
                    while chunk := resp.read(1024 * 256):
                        fh.write(chunk)
                        size += len(chunk)
                tmp.replace(dest)
                return resp.status, size
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


# --------------------------------------------------------------------------- #
# Catalog extraction
# --------------------------------------------------------------------------- #
def stable_url(url: str) -> str:
    """Drop the optional Demandware content-version segment (dwXXXXXXXX)."""
    return HASH_RE.sub("/default/", url)


def parse_asset(url: str) -> tuple[str | None, str | None, str]:
    """Return (asset_year, asset_family, filename) from an explosiondrawings URL."""
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
    m = re.search(r"/explosiondrawings/(\d{4})/([^/]+)/([^/]+)$", path)
    if not m:
        return None, None, os.path.basename(path)
    return m.group(1), m.group(2), m.group(3)


def extract_codes(filename: str) -> list[str]:
    out: list[str] = []
    for code in CODE_RE.findall(filename):
        if code not in out:
            out.append(code)
    return out


def parse_year_page(year: str, html: str) -> list[dict]:
    """Extract category / label / href rows from one year page."""
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        main = soup.find("main") or soup
        elements = main.find_all(["h2", "a"])
    except ImportError:  # pragma: no cover - bs4 is normally present
        # Minimal fallback: split on h2 and scan the following anchors.
        elements = []
        for chunk in re.split(r"(<h2[^>]*>.*?</h2>)", html, flags=re.S | re.I):
            if re.match(r"<h2", chunk, re.I):
                text = re.sub(r"<[^>]+>", " ", chunk)
                elements.append(("h2", re.sub(r"\s+", " ", text).strip()))
            else:
                for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S | re.I):
                    label = re.sub(r"<[^>]+>", " ", m.group(2))
                    elements.append(("a", (m.group(1), re.sub(r"\s+", " ", label).strip())))

    rows: list[dict] = []
    category = None
    for el in elements:
        if isinstance(el, tuple):
            name, value = el
            if name == "h2":
                category = value
            else:
                href, label = value
                if "explosiondrawings" in href and href.lower().split("?")[0].endswith(".pdf"):
                    rows.append(
                        {
                            "catalog_year": year,
                            "category": category,
                            "label": label,
                            "href": urllib.parse.unquote(href.replace("&amp;", "&")),
                        }
                    )
            continue

        if el.name == "h2":
            text = el.get_text(" ", strip=True)
            if text:
                category = text
        elif el.name == "a":
            href = el.get("href", "")
            if "explosiondrawings" not in href:
                continue
            if not href.lower().split("?")[0].endswith(".pdf"):
                continue
            rows.append(
                {
                    "catalog_year": year,
                    "category": category,
                    "label": el.get_text(" ", strip=True),
                    "href": urllib.parse.unquote(href.replace("&amp;", "&")),
                }
            )

    # A few legacy pages contain typos such as hhref="…".  Catch any link the
    # DOM parser skipped so the manifest stays complete.
    seen = {row["href"] for row in rows}
    for href in re.findall(r'h{1,2}ref="([^"]*explosiondrawings[^"]*)"', html):
        href = urllib.parse.unquote(href.replace("&amp;", "&"))
        if href not in seen:
            rows.append({"catalog_year": year, "category": None, "label": "", "href": href})
            seen.add(href)
    return rows


def discover_years(locale: str) -> list[int]:
    url = f"{BASE}/{locale}/support-articles/exploded_drawings_overview.html"
    html = fetch(url)
    years = sorted({int(y) for y in re.findall(r"exploded_drawings_(\d{4})\.html", html)})
    if not years:
        years = list(range(2009, 2025))
    print(f"discovered years: {years[0]}-{years[-1]} ({len(years)})", file=sys.stderr)
    return years


def build_catalog(locale: str) -> list[dict]:
    years = discover_years(locale)
    raw: list[dict] = []
    for year in years:
        url = f"{BASE}/{locale}/support-articles/exploded_drawings_{year}.html"
        try:
            html = fetch(url)
        except SystemExit as exc:
            print(f"  {year}: {exc}", file=sys.stderr)
            continue
        rows = parse_year_page(str(year), html)
        print(f"  {year}: {len(rows)} links", file=sys.stderr)
        raw.extend(rows)
        time.sleep(0.4)

    merged: dict[str, dict] = {}
    for row in raw:
        href = row["href"]
        key = stable_url(href)
        asset_year, asset_family, filename = parse_asset(href)
        codes = extract_codes(filename)
        disciplines = sorted({DISCIPLINE[c[0]] for c in codes if c[0] in DISCIPLINE})
        rec = merged.setdefault(
            key,
            {
                "url": href,  # first encountered, keeps the dw… version segment
                "url_stable": key,  # hash-free form that also resolves
                "asset_year": asset_year,
                "asset_family": asset_family,
                "filename": filename,
                "model_codes": codes,
                "disciplines": disciplines,
                "catalog_years": [],
                "categories": [],
                "labels": [],
            },
        )
        if row["catalog_year"] not in rec["catalog_years"]:
            rec["catalog_years"].append(row["catalog_year"])
        if row["category"] and row["category"] not in rec["categories"]:
            rec["categories"].append(row["category"])
        if row["label"] and row["label"] not in rec["labels"]:
            rec["labels"].append(row["label"])
        for c in codes:
            if c not in rec["model_codes"]:
                rec["model_codes"].append(c)

    out = list(merged.values())
    for rec in out:
        rec["catalog_years"].sort()
        rec["categories"].sort()
        rec["labels"].sort()
        rec["disciplines"] = sorted(
            {DISCIPLINE[c[0]] for c in rec["model_codes"] if c[0] in DISCIPLINE}
        )
    out.sort(key=lambda r: (r["asset_year"] or "", r["asset_family"] or "", r["filename"]))
    return out


def write_json(rows: list[dict], path: Path) -> None:
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(rows: list[dict], path: Path) -> None:
    fields = [
        "asset_year",
        "asset_family",
        "filename",
        "model_codes",
        "disciplines",
        "catalog_years",
        "categories",
        "labels",
        "url_stable",
        "url",
    ]
    extra = [k for k in ("family", "kind") if any(k in rec for rec in rows)]
    fields = extra + fields
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for rec in rows:
            writer.writerow(
                {
                    **{k: rec.get(k, "") for k in fields if k not in ("model_codes", "disciplines", "catalog_years", "categories", "labels")},
                    "model_codes": ";".join(rec["model_codes"]),
                    "disciplines": ";".join(rec["disciplines"]),
                    "catalog_years": ";".join(rec["catalog_years"]),
                    "categories": ";".join(rec["categories"]),
                    "labels": ";".join(rec["labels"]),
                }
            )


def cmd_catalog(args: argparse.Namespace) -> int:
    rows = build_catalog(args.locale)
    write_json(rows, Path(args.json))
    write_csv(rows, Path(args.csv))
    print(f"\n{len(rows)} unique PDFs -> {args.json}, {args.csv}")
    by_year: dict[str, int] = {}
    for rec in rows:
        by_year[rec["asset_year"] or "?"] = by_year.get(rec["asset_year"] or "?", 0) + 1
    print("assets by folder year:", ", ".join(f"{y}:{n}" for y, n in sorted(by_year.items())))
    return 0


def cmd_gravity(args: argparse.Namespace) -> int:
    rows = [r for r in build_catalog(args.locale) if is_gravity(r)]
    for rec in rows:
        rec["family"] = canonical_family(rec)
        rec["kind"] = gravity_kind(rec)
    write_json(rows, Path(args.json))
    write_csv(rows, Path(args.csv))
    print(f"\n{len(rows)} gravity-MTB PDFs -> {args.json}, {args.csv}")
    counts: dict[tuple[str, str], int] = {}
    for rec in rows:
        key = (rec["family"], rec["kind"])
        counts[key] = counts.get(key, 0) + 1
    for (family, kind), n in sorted(counts.items()):
        print(f"  {family:14} {kind:6} {n}")
    return 0


# --------------------------------------------------------------------------- #
# Download / verify / text extraction
# --------------------------------------------------------------------------- #
def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip()


def load_catalog(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"{path} not found; run the 'catalog' command first")
    return json.loads(path.read_text(encoding="utf-8"))


def select(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    out = rows
    if getattr(args, "gravity", False):
        out = [r for r in out if is_gravity(r)]
    if args.year:
        wanted = {str(y) for y in args.year}
        out = [
            r
            for r in out
            if str(r.get("asset_year")) in wanted
            or any(y in wanted for y in r.get("catalog_years", []))
        ]
    if args.model:
        needle = args.model.upper()
        out = [r for r in out if any(needle in c.upper() for c in r.get("model_codes", []))]
    if args.family:
        needle = args.family.lower()
        out = [r for r in out if needle in (r.get("asset_family") or "").lower()]
    if args.limit:
        out = out[: args.limit]
    return out


def cmd_download(args: argparse.Namespace) -> int:
    rows = select(load_catalog(Path(args.catalog)), args)
    if not rows:
        print("nothing matched", file=sys.stderr)
        return 1
    root = Path(args.out)
    print(f"{len(rows)} files -> {root}")

    def job(rec: dict) -> tuple[str, str]:
        dest = root / str(rec["asset_year"] or "unknown") / safe_name(rec["asset_family"] or "unknown") / safe_name(rec["filename"])
        if dest.exists() and dest.stat().st_size > 0:
            return str(dest), "exists"
        if args.dry_run:
            return str(dest), "dry-run"
        status, size = download(rec["url_stable"], dest)
        return str(dest), f"{status} {size}"

    failures = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(job, rec): rec for rec in rows}
        for i, fut in enumerate(as_completed(futures), 1):
            rec = futures[fut]
            try:
                dest, status = fut.result()
                print(f"[{i}/{len(rows)}] {status:>12}  {dest}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"[{i}/{len(rows)}] FAILED       {rec['filename']}: {exc}", file=sys.stderr)
    return 1 if failures else 0


def cmd_verify(args: argparse.Namespace) -> int:
    rows = select(load_catalog(Path(args.catalog)), args)

    def head(rec: dict) -> tuple[str, str]:
        req = urllib.request.Request(
            to_uri(rec["url_stable"]),
            headers={"User-Agent": UA, "Range": "bytes=0-0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return rec["filename"], f"{resp.status} {resp.headers.get('Content-Type', '')} {resp.headers.get('Content-Length', '')}"
        except urllib.error.HTTPError as exc:
            return rec["filename"], f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            return rec["filename"], f"ERR {exc}"

    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for i, fut in enumerate(as_completed([pool.submit(head, r) for r in rows]), 1):
            name, status = fut.result()
            results.append({"filename": name, "status": status})
            print(f"[{i}/{len(rows)}] {status:>20}  {name}")
    Path(args.output).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


def cmd_serial(args: argparse.Namespace) -> int:
    """Resolve a bike serial number through Canyon's public search endpoint.

    Canyon exposes this for bikes released from 2024 onwards; the yearly
    exploded-drawing pages only cover 2009-2024.  The serial is printed on the
    frame (top tube / bottom bracket area), e.g. M06201H19K0551.
    """
    overview = fetch(f"{BASE}/{args.locale}/support-articles/exploded_drawings_overview.html")
    m = re.search(r'data-href="([^"]*Utils-SearchBike[^"]*)"', overview)
    if not m:
        raise SystemExit("could not find the serial-search endpoint on the overview page")
    endpoint = m.group(1).replace("&amp;", "&")
    parts = urllib.parse.urlsplit(urllib.parse.urljoin(BASE, endpoint))
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if k not in ("serialNumber", "attributes")
    ]
    query += [("serialNumber", args.serial), ("attributes", "null")]
    url = urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
    )

    req = urllib.request.Request(
        to_uri(url),
        headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8", "replace")
        content_type = resp.headers.get("Content-Type", "")

    # A serial lookup normally returns JSON.  The pidFallback path returns a
    # raw HTML fragment, so treat non-JSON bodies as successful HTML.
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {"success": True, "html": body, "content_type": content_type}

    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"raw response -> {args.json}")

    if not payload.get("success"):
        message = re.sub(r"<style.*?</style>", " ", payload.get("resourceMessage", ""), flags=re.S | re.I)
        message = re.sub(r"<[^>]+>", " ", message)
        print(re.sub(r"\s+", " ", message).strip() or "serial not found")
        return 1

    html = payload.get("html", "") or body
    if "drawingsnotfound" in html or "couldn't find any exploded drawing" in html.lower():
        print("Canyon has no exploded drawing registered for this serial/product yet")
        return 1

    links = []
    for href in re.findall(
        r'(https?://[^"\'\s<>]*explosiondrawing[^"\'\s<>]*\.pdf|/[^"\'\s<>]*explosiondrawing[^"\'\s<>]*\.pdf)',
        html,
        re.I,
    ):
        href = urllib.parse.urljoin(BASE, href.replace("&amp;", "&"))
        if href not in links:
            links.append(href)
    if not links:
        print("search succeeded but no exploded-drawing PDF link was returned")
        print(re.sub(r"<[^>]+>", " ", html)[:1000])
        return 1

    print(f"{len(links)} drawing(s) for {args.serial}:")
    for link in links:
        print(" ", link)
    if args.dry_run:
        return 0

    out = Path(args.out)
    for link in links:
        filename = safe_name(os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(link).path)))
        dest = out / args.serial / filename
        status, size = download(link, dest)
        print(f"  {status} {size:>9}  {dest}")
    return 0


def cmd_extract_text(args: argparse.Namespace) -> int:
    exe = shutil.which("pdftotext")
    if not exe:
        raise SystemExit("pdftotext not found (install poppler-utils)")
    root = Path(args.dir)
    pdfs = sorted(root.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no PDFs under {root}")
    for pdf in pdfs:
        txt = pdf.with_suffix(".txt")
        if txt.exists() and not args.force:
            continue
        subprocess.run([exe, "-layout", str(pdf), str(txt)], check=False)
        print(txt)
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", default="boms.json", help="manifest path (default: boms.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    cat = sub.add_parser("catalog", help="discover and build the manifest")
    cat.add_argument("--locale", default=DEFAULT_LOCALE)
    cat.add_argument("--json", default="boms.json")
    cat.add_argument("--csv", default="boms.csv")
    cat.set_defaults(func=cmd_catalog)

    grav = sub.add_parser("gravity", help="build a Spectral/Strive/Torque/Sender-only manifest")
    grav.add_argument("--locale", default=DEFAULT_LOCALE)
    grav.add_argument("--json", default="gravity_boms.json")
    grav.add_argument("--csv", default="gravity_boms.csv")
    grav.set_defaults(func=cmd_gravity)

    dl = sub.add_parser("download", help="download PDFs from the manifest")
    dl.add_argument("--catalog", default=argparse.SUPPRESS)
    dl.add_argument("--gravity", action="store_true", help="only Spectral/Strive/Torque/Sender")
    dl.add_argument("--out", default="pdfs")
    dl.add_argument("--year", type=int, action="append", help="catalog or asset year (repeatable)")
    dl.add_argument("--model", help="model code substring, e.g. M147")
    dl.add_argument("--family", help="asset folder substring, e.g. strive")
    dl.add_argument("--limit", type=int)
    dl.add_argument("--jobs", type=int, default=4)
    dl.add_argument("--dry-run", action="store_true")
    dl.set_defaults(func=cmd_download)

    ver = sub.add_parser("verify", help="check that every manifest URL resolves")
    ver.add_argument("--catalog", default=argparse.SUPPRESS)
    ver.add_argument("--gravity", action="store_true", help="only Spectral/Strive/Torque/Sender")
    ver.add_argument("--year", type=int, action="append")
    ver.add_argument("--model")
    ver.add_argument("--family")
    ver.add_argument("--limit", type=int)
    ver.add_argument("--jobs", type=int, default=6)
    ver.add_argument("--output", default="verification.json")
    ver.set_defaults(func=cmd_verify)

    ser = sub.add_parser("serial", help="look up the BOM for a bike by frame serial number")
    ser.add_argument("serial", help="frame serial, e.g. M06201H19K0551")
    ser.add_argument("--locale", default=DEFAULT_LOCALE)
    ser.add_argument("--out", default="pdfs/serial")
    ser.add_argument("--json", help="save the raw JSON response here")
    ser.add_argument("--dry-run", action="store_true", help="show links without downloading")
    ser.set_defaults(func=cmd_serial)

    ext = sub.add_parser("extract-text", help="run pdftotext -layout over downloaded PDFs")
    ext.add_argument("--dir", default="pdfs")
    ext.add_argument("--force", action="store_true")
    ext.set_defaults(func=cmd_extract_text)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Join Canyon gravity-MTB product pages to their exploded-drawing/BOM codes.

Inputs
------
data/gravity_products_raw.json
    Enriched product pages: name, URL, image year, image frame code, and the
    BOM code returned by Canyon's Utils-SearchBike pid-fallback endpoint.
gravity_boms.json
    The 2009-2024 exploded-drawing archive built by scrape_canyon_boms.py.

Outputs
-------
gravity_products.csv
    One row per product page (bike/tier combo) with the best BOM match.
gravity_product_bom_map.csv
    One row per product page -> BOM pair, including candidate/confidence.
gravity_boms_extended.json
    Archive BOMs plus the root-level post-2024 BOMs (M178/M180/M185/M188/M204).
"""

from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = "https://www.canyon.com/on/demandware.static/-/Library-Sites-canyon-shared/default/explosiondrawings"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"

# BOMs published only at the root-level path.  M204 is intentionally absent:
# the bike is not on sale yet and Canyon returns "no exploded drawing".
POST2024 = {
    "M178": {"label": "Spectral AL", "family": "Spectral", "year": "2025", "file": "pdfs/post2024/explosiondrawing-M178-01.pdf"},
    "M180": {"label": "Sender CFR", "family": "Sender", "year": "2025", "file": "pdfs/post2024/explosiondrawing-M180-01.pdf"},
    "M185": {"label": "Torque AL", "family": "Torque", "year": "2026", "file": "pdfs/post2024/explosiondrawing-M185-01.pdf"},
    "M188": {"label": "StriveON", "family": "Strive", "year": "2026", "file": "pdfs/post2024/explosiondrawing-M188-01.pdf"},
    "M204": {"label": "Spectral ON", "family": "Spectral:ON", "year": "2027", "file": ""},
}

FAMILY_RE = re.compile(r"spectral|strive|torque|sender", re.I)
MATERIAL_RE = re.compile(r"\b(CFR|CF|AL|SLX|SL)\b", re.I)
TIER_RE = re.compile(r"\b(?:AL|CF|CFR)?\s*([5-9])\b|\b(LTD|CLLCTV|DH|Team|Underdog)\b", re.I)


def parse_name(name: str, extra: str = "") -> dict:
    """Parse a marketing name.  `extra` (image/URL) is used only for material.

    Electric detection must stay on the name: every Canyon image URL contains
    the path segment ``/on/demandware.static`` and would falsely match ``ON``.
    """
    name = name or ""
    family = FAMILY_RE.search(name)
    family_name = family.group(0).title() if family else ""
    electric = bool(re.search(r":ON\b|\bON\b|ONfly|Hybrid", name, re.I))
    material = MATERIAL_RE.search(f"{name} {extra}")
    return {
        "family": (family_name + (":ON" if electric and family_name else "")).strip(),
        "family_base": family_name,
        "electric": electric,
        "material": material.group(1).upper() if material else "",
        "series_125": bool(re.search(r"\b125\b", f"{name} {extra}")),
        "wmn": bool(re.search(r"\bWMN\b|\bWomen\b", name, re.I)),
        "variant": re.sub(r"\s+", " ", re.sub(r"[:\-]", " ", name)).strip(),
    }


def code_base(code: str | None) -> str:
    return re.sub(r"-\d{2}$", "", code or "")


def code_rev(code: str | None) -> str:
    m = re.search(r"-(\d{2})$", code or "")
    return m.group(1) if m else "01"


def root_url(code: str, rev: str = "01") -> str:
    return f"{STATIC}/explosiondrawing-{code}-{rev}.pdf"


def exists(url: str) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return "pdf" in resp.headers.get("Content-Type", "").lower()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def infer_year(rec: dict) -> tuple[str, str]:
    if rec.get("image_year"):
        return rec["image_year"], "image"
    if rec.get("archive_timestamp"):
        return rec["archive_timestamp"][:4], "archive snapshot"
    seen = rec.get("seen") or []
    if "2022-06" in seen:
        return "2022", "sitemap"
    if "2025-06" in seen:
        return "2025", "sitemap"
    if "live" in seen:
        return "2027", "sitemap"
    return "", "unknown"


def bom_code_number(b: dict) -> int:
    m = re.search(r"[MRU](\d{1,4})", (b.get("model_codes") or [""])[0])
    return int(m.group(1)) if m else 0


def best_bom(
    rec: dict, bom_index: dict[str, list[dict]], sibling_code: str | None = None
) -> tuple[dict | None, str, str]:
    code = code_base(rec.get("bom_code") or rec.get("image_code"))
    year = int(rec["model_year"]) if rec.get("model_year", "").isdigit() else 0
    parsed = parse_name(
        rec.get("name", ""),
        " ".join(filter(None, [rec.get("image", ""), rec.get("url", "")])),
    )

    # The Spectral:ONfly uses a different lightweight frame; Canyon exposes no
    # code for it, so don't let the heavier Spectral:ON BOM leak in as a guess.
    if "ONfly" in (rec.get("name") or ""):
        return None, "none", "ONfly frame code not exposed"

    if code and code in bom_index:
        candidates = bom_index[code]
        # Prefer a BOM whose label material matches the product (CFR vs CF vs AL).
        def score(b: dict) -> tuple:
            label = " ".join(b.get("labels", []) + b.get("categories", [])).upper()
            material_match = 1 if parsed["material"] and parsed["material"] in label else 0
            byear = int(b.get("asset_year") or 0)
            return (material_match, -abs(byear - year) if year else 0)

        chosen = max(candidates, key=score)
        basis = "endpoint code" if rec.get("bom_code") else "image frame code"
        return chosen, "exact", basis

    if code:
        # A new frame not present in the 2009-2024 archive.
        synthetic = {
            "asset_year": rec.get("model_year") or "",
            "asset_family": "",
            "filename": f"explosiondrawing-{code}-01.pdf",
            "model_codes": [code],
            "disciplines": ["MTB"],
            "catalog_years": [],
            "categories": [],
            "labels": [rec.get("name", "")],
            "url": root_url(code),
            "url_stable": root_url(code),
            "post2024": True,
            "published": bool(POST2024.get(code, {}).get("file")),
        }
        return synthetic, "exact", "image frame code"

    # No frame code.  Prefer a code already proven by a sibling tier in the
    # same family/material/model year (e.g. Torque CF 7 -> Torque CF 9).
    if sibling_code and sibling_code in bom_index:
        chosen = max(
            bom_index[sibling_code], key=lambda b: (int(b.get("asset_year") or 0), bom_code_number(b))
        )
        return chosen, "inferred-sibling", f"same family/material/year as {sibling_code}"

    # No frame code and no sibling.  We can only infer from family/material/year,
    # so keep the result explicitly marked for verification.
    if "ONfly" in (rec.get("name") or ""):
        return None, "none", "ONfly frame code not exposed"

    family = parsed["family_base"]
    material = parsed["material"]
    electric = parsed["electric"]
    candidates = []
    for b in bom_index.get("__all__", []):
        text = " ".join(b.get("labels", []) + b.get("categories", [])).upper()
        if family and family.upper() not in text:
            continue
        bom_electric = bool(re.search(r"\bON\b|HYBRID", text))
        if electric != bom_electric:
            continue
        if material and material not in text:
            continue
        if parsed["series_125"] != bool(re.search(r"\b125\b", text)):
            continue
        if parsed["wmn"] != bool(re.search(r"\bWMN\b|\bWOMEN\b", text)):
            continue
        candidates.append(b)
    if not candidates:
        return None, "none", "no match"
    before = [b for b in candidates if year and int(b.get("asset_year") or 0) <= year]
    if before:
        chosen = max(
            before, key=lambda b: (int(b.get("asset_year") or 0), bom_code_number(b))
        )
    else:
        chosen = min(
            candidates,
            key=lambda b: abs(int(b.get("asset_year") or year or 0) - year) if year else 0,
        )
    basis = "family+material+year" if material else "family+year (material unknown)"
    return chosen, "inferred", basis


def main() -> int:
    raw = json.loads((ROOT / "data/gravity_products_raw.json").read_text(encoding="utf-8"))
    archived_path = ROOT / "data/archive_products_raw.json"
    if archived_path.exists():
        archived = json.loads(archived_path.read_text(encoding="utf-8"))
        by_id = {str(r["id"]): r for r in raw}
        for rec in archived:
            if rec.get("error") or not rec.get("name"):
                continue
            pid = str(rec["id"])
            if pid in by_id:
                # Keep the live record, but remember the archived snapshot.
                by_id[pid].setdefault("archive_url", rec.get("archive_url"))
            else:
                by_id[pid] = rec
        raw = list(by_id.values())
    live_status: dict[str, dict] = {}
    live_path = ROOT / "data/archive_live_status.json"
    if live_path.exists():
        live_status = json.loads(live_path.read_text(encoding="utf-8"))
    archive = json.loads((ROOT / "gravity_boms.json").read_text(encoding="utf-8"))

    # Build code -> BOM records index.
    bom_index: dict[str, list[dict]] = {}
    for b in archive:
        for code in b.get("model_codes", []):
            bom_index.setdefault(code_base(code), []).append(b)
    bom_index["__all__"] = archive

    extended = list(archive)
    for code, meta in POST2024.items():
        url = root_url(code)
        published = bool(meta["file"]) or exists(url)
        rec = {
            "asset_year": meta["year"],
            "asset_family": meta["family"].lower().replace(":", "_"),
            "filename": f"explosiondrawing-{code}-01.pdf",
            "model_codes": [code],
            "disciplines": ["MTB"],
            "catalog_years": [],
            "categories": [],
            "labels": [meta["label"]],
            "url": url,
            "url_stable": url,
            "post2024": True,
            "published": published,
            "local_file": meta["file"],
        }
        if published:
            extended.append(rec)
            bom_index.setdefault(code, []).append(rec)

    # Pass 1: resolve every product that has a frame code (endpoint or image).
    prepared = []
    for rec in raw:
        year, year_source = infer_year(rec)
        parsed = parse_name(
            rec.get("name", ""),
            " ".join(filter(None, [rec.get("image", ""), rec.get("url", "")])),
        )
        code = code_base(rec.get("bom_code") or rec.get("image_code"))
        if code:
            bom, confidence, basis = best_bom({**rec, "model_year": year}, bom_index)
        else:
            bom, confidence, basis = None, "pending", ""
        prepared.append(
            {
                "rec": rec,
                "year": year,
                "year_source": year_source,
                "parsed": parsed,
                "code": code,
                "bom": bom,
                "confidence": confidence,
                "basis": basis,
            }
        )

    # Exact frame codes establish the BOM for sibling tiers without a code.
    siblings: dict[tuple, str] = {}
    for p in prepared:
        if p["bom"] and p["confidence"] == "exact":
            key = (
                p["parsed"]["family_base"],
                p["parsed"]["electric"],
                p["parsed"]["material"],
                p["parsed"]["series_125"],
                p["parsed"]["wmn"],
                p["year"],
            )
            code = code_base((p["bom"].get("model_codes") or [""])[0])
            previous = siblings.get(key)
            if not previous or bom_code_number(p["bom"]) > bom_code_number(
                {"model_codes": [previous]}
            ):
                siblings[key] = code

    # Pass 2: fill products without a code from a sibling or family inference.
    rows = []
    for p in prepared:
        rec = p["rec"]
        code = p["code"]
        bom = p["bom"]
        confidence = p["confidence"]
        basis = p["basis"]
        if bom is None and not code:
            key = (
                p["parsed"]["family_base"],
                p["parsed"]["electric"],
                p["parsed"]["material"],
                p["parsed"]["series_125"],
                p["parsed"]["wmn"],
                p["year"],
            )
            bom, confidence, basis = best_bom(
                {**rec, "model_year": p["year"]},
                bom_index,
                sibling_code=siblings.get(key),
            )

        if bom:
            bom_codes = [code_base(c) for c in bom.get("model_codes", [])]
            # Multi-code BOMs (e.g. M065/M066) must keep the product's own code.
            bom_code = code if code and code in bom_codes else (bom_codes[0] if bom_codes else "")
            rev = code_rev(
                next(
                    (c for c in bom.get("model_codes", []) if code_base(c) == bom_code),
                    "",
                )
            )
            bom_url = rec.get("bom_url") or bom.get("url_stable") or bom.get("url") or root_url(bom_code, rev)
            published = bool(bom.get("published", True))
            if not published:
                bom_url = ""
        else:
            bom_code = code
            bom_url = ""
            published = False

        notes = ""
        if confidence == "inferred":
            notes = "verify against the frame code on the bike"
        elif confidence == "inferred-sibling":
            notes = "code inferred from a sibling tier; verify against the frame"
        elif not published:
            notes = "no published exploded drawing yet"

        live = live_status.get(str(rec.get("id")), {})
        live_ok = bool(live.get("live_ok"))
        live_final = live.get("live_final_url") or rec.get("live_url") or rec.get("url") or ""
        archive_url = rec.get("archive_url") or ""
        if rec.get("page_source") == "wayback":
            if live_ok:
                product_url, link_source = live_final, "live"
            else:
                product_url, link_source = archive_url or live_final, "wayback"
        else:
            product_url, link_source = rec.get("product_url") or rec.get("url"), "live"

        rows.append(
            {
                "product_id": rec.get("id"),
                "product_name": rec.get("name"),
                "product_url": product_url,
                "link_source": link_source,
                "family": p["parsed"]["family"],
                "material": p["parsed"]["material"],
                "electric": p["parsed"]["electric"],
                "model_year": p["year"],
                "year_source": p["year_source"],
                "frame_code": code,
                "bom_code": bom_code,
                "bom_label": " / ".join((bom or {}).get("labels", [])),
                "bom_year": (bom or {}).get("asset_year", ""),
                "bom_url": bom_url,
                "bom_published": published,
                "match_confidence": confidence,
                "match_basis": basis,
                "needs_verification": confidence != "exact",
                "notes": notes,
                "first_seen": ",".join(rec.get("seen") or []),
                "skus": ";".join(rec.get("skus") or []),
                "price_min": rec.get("price_min") or "",
                "page_source": rec.get("page_source", ""),
                "live_url": live_final,
                "archive_url": archive_url,
                "archive_timestamp": rec.get("archive_timestamp") or "",
            }
        )

    # Product table.
    fields = list(rows[0].keys())
    with open(ROOT / "gravity_products.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["family"], r["model_year"], r["product_name"] or "")))

    # Product -> BOM map (best match plus all same-code candidates).
    map_rows = []
    for r in rows:
        map_rows.append(
            {
                "product_id": r["product_id"],
                "product_name": r["product_name"],
                "product_year": r["model_year"],
                "product_url": r["product_url"],
                "frame_code": r["frame_code"],
                "bom_code": r["bom_code"],
                "bom_label": r["bom_label"],
                "bom_year": r["bom_year"],
                "bom_url": r["bom_url"],
                "bom_published": r["bom_published"],
                "confidence": r["match_confidence"],
                "match_basis": r["match_basis"],
            }
        )
    with open(ROOT / "gravity_product_bom_map.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(map_rows[0].keys()))
        writer.writeheader()
        writer.writerows(sorted(map_rows, key=lambda r: (r["bom_code"], r["product_year"], r["product_name"] or "")))

    (ROOT / "gravity_boms_extended.json").write_text(
        json.dumps(extended, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"products: {len(rows)} -> gravity_products.csv")
    print(f"map rows: {len(map_rows)} -> gravity_product_bom_map.csv")
    print(f"BOMs: {len(extended)} -> gravity_boms_extended.json")
    by_code: dict[str, int] = {}
    for r in rows:
        by_code[r["bom_code"] or "?"] = by_code.get(r["bom_code"] or "?", 0) + 1
    print("products per BOM code:", ", ".join(f"{k}:{v}" for k, v in sorted(by_code.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

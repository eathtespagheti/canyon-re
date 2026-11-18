#!/usr/bin/env python3
"""Join all Canyon bike product pages to the full exploded-drawing archive.

Inputs:
  data/all_products_raw.json       live + archived products, all families
  data/archive_products_raw.json   additional legacy Wayback pages
  boms.json                        all 2009-2024 BOM PDFs
  POST2024                         adds post-2024 root-level BOMs

Outputs:
  all_products.csv
  all_product_bom_map.csv
  all_boms_extended.json
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

FAMILIES = {
    "Aeroad": "Road", "Ultimate": "Road", "Endurace": "Road", "Speedmax": "Road",
    "Grail": "Gravel", "Grizl": "Gravel", "Inflite": "Gravel",
    "Roadlite": "Urban", "Commuter": "Urban", "Precede": "Urban",
    "Spectral": "MTB", "Strive": "MTB", "Torque": "MTB", "Sender": "MTB",
    "Lux": "MTB", "Neuron": "MTB", "Exceed": "MTB", "Grand Canyon": "MTB",
    "Pathlite": "Trekking", "Dude": "MTB", "Stitched": "MTB", "Offspring": "Kids",
}
FAMILY_RE = re.compile(
    "|".join(re.escape(f) for f in sorted(FAMILIES, key=len, reverse=True)), re.I
)
BASE_CATEGORY = {
    "Spectral": "Gravity", "Strive": "Gravity", "Torque": "Gravity", "Sender": "Gravity",
    "Lux": "XC", "Exceed": "XC",
    "Neuron": "Trail", "Grand Canyon": "Trail",
    "Inflite": "CX",
    "Aeroad": "Road", "Ultimate": "Road", "Endurace": "Road", "Speedmax": "Road",
    "Grail": "Gravel", "Grizl": "Gravel",
    "Stitched": "Dirt Jump", "Dude": "Fat", "Offspring": "Kids",
    "Commuter": "Urban", "Roadlite": "Urban", "Precede": "Urban",
    "Pathlite": "Trekking",
}
FOLDER_FAMILY = {
    "aeroad": "Aeroad", "ultimate": "Ultimate", "endurace": "Endurace", "endurace_on": "Endurace",
    "speedmax": "Speedmax", "grail": "Grail", "grail_on": "Grail", "grizl": "Grizl", "grizl_on": "Grizl",
    "inflite": "Inflite", "roadlite": "Roadlite", "roadlite_on": "Roadlite",
    "commuter": "Commuter", "commuter_on": "Commuter", "precede_on": "Precede",
    "spectral": "Spectral", "spectral_on": "Spectral", "strive": "Strive", "strive_on": "Strive",
    "torque": "Torque", "torque_al": "Torque", "torque_on": "Torque", "sender": "Sender",
    "lux": "Lux", "neuron": "Neuron", "neuron_on": "Neuron", "neuron_spectral": "Neuron",
    "exceed": "Exceed", "grand_canyon": "Grand Canyon", "grand_canyon_on": "Grand Canyon",
    "pathlite": "Pathlite", "pathlite_on": "Pathlite", "dude": "Dude", "stitched": "Stitched",
    "kidsbikes": "Offspring", "nerve": "Neuron", "fitness": "Roadlite", "urban": "Commuter",
}

# Published only at the root path; M204 is intentionally pending.
POST2024 = {
    "M178": {"label": "Spectral AL", "family": "Spectral", "category": "Gravity", "year": "2025", "file": "pdfs/post2024/explosiondrawing-M178-01.pdf"},
    "M180": {"label": "Sender CFR", "family": "Sender", "category": "Gravity", "year": "2025", "file": "pdfs/post2024/explosiondrawing-M180-01.pdf"},
    "M185": {"label": "Torque AL", "family": "Torque", "category": "Gravity", "year": "2026", "file": "pdfs/post2024/explosiondrawing-M185-01.pdf"},
    "M188": {"label": "StriveON", "family": "Strive", "category": "E-Gravity", "year": "2026", "file": "pdfs/post2024/explosiondrawing-M188-01.pdf"},
    "M204": {"label": "Spectral ON", "family": "Spectral", "category": "E-Gravity", "year": "2027", "file": ""},
}


def base_code(code: str | None) -> str:
    return re.sub(r"-\d{2}$", "", code or "")


def code_rev(code: str | None) -> str:
    m = re.search(r"-(\d{2})$", code or "")
    return m.group(1) if m else "01"


def code_number(b: dict) -> int:
    m = re.search(r"[MRU](\d{1,4})", (b.get("model_codes") or [""])[0])
    return int(m.group(1)) if m else 0


def root_url(code: str, rev: str = "01") -> str:
    return f"{STATIC}/explosiondrawing-{code}-{rev}.pdf"


def exists(url: str) -> bool:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return "pdf" in resp.headers.get("Content-Type", "").lower()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def category_for(family_base: str, electric: bool, text: str = "") -> str:
    if not family_base:
        return "Other"
    low = (text or "").lower()
    if electric:
        if family_base in {"Spectral", "Strive", "Torque", "Sender"}:
            return "E-Trail" if "onfly" in low else "E-Gravity"
        if family_base in {"Lux", "Exceed"}:
            return "E-XC"
        if family_base in {"Neuron", "Grand Canyon"}:
            return "E-Trail"
        if family_base in {"Aeroad", "Ultimate", "Endurace", "Speedmax", "Roadlite"}:
            return "E-Road"
        if family_base in {"Grail", "Grizl"}:
            return "E-Gravel"
        if family_base in {"Commuter", "Precede"}:
            return "E-Urban"
        if family_base == "Pathlite":
            return "E-Trekking"
        return "E-Other"
    if family_base == "Lux" and "trail" in low:
        return "Trail"
    return BASE_CATEGORY.get(family_base, "Other")


def parse_product(name: str, extra: str = "") -> dict:
    """Parse a marketing name.  `extra` (image/URL) is used only for material.

    Electric detection stays on the name: every Canyon image URL contains
    ``/on/demandware.static`` and would otherwise mark everything as :ON.
    """
    name = name or ""
    family = FAMILY_RE.search(name)
    family_base = family.group(0).title() if family else ""
    if family_base == "Grand Canyon":
        family_base = "Grand Canyon"
    electric = bool(re.search(r":ON\b|\bON\b|ONfly|Hybrid", name, re.I))
    material = re.search(r"\b(CFR|CF|AL|SLX|SL)\b", f"{name} {extra}", re.I)
    return {
        "family_base": family_base,
        "family": family_base + (":ON" if electric and family_base else ""),
        "discipline": FAMILIES.get(family_base, ""),
        "electric": electric,
        "material": material.group(1).upper() if material else "",
        "wmn": bool(re.search(r"\bWMN\b|\bWomen\b", name, re.I)),
        "series_125": bool(re.search(r"\b125\b", f"{name} {extra}")),
        "category": category_for(family_base, electric, name),
    }


def bom_text(b: dict) -> str:
    labels = " ".join(b.get("labels") or [])
    cats = " ".join(b.get("categories") or [])
    return (labels + " " + cats).strip()


def bom_family(b: dict) -> str:
    labels = " ".join(b.get("labels") or [])
    m = FAMILY_RE.search(labels)
    if m:
        return m.group(0).title()
    m = FAMILY_RE.search(" ".join(b.get("categories") or []))
    return m.group(0).title() if m else ""


def bom_electric(b: dict) -> bool:
    text = " ".join(b.get("labels") or []).strip()
    return bool(re.search(r":ON\b|\bON\b|Hybrid", text, re.I))


def bom_material(b: dict) -> str:
    m = re.search(r"\b(CFR|CF|AL|SLX|SL)\b", " ".join(b.get("labels") or []), re.I)
    return m.group(1).upper() if m else ""


def bom_category(b: dict) -> str:
    family = bom_family(b)
    if not family:
        family = FOLDER_FAMILY.get((b.get("asset_family") or "").lower(), "")
    return category_for(family, bom_electric(b), " ".join(b.get("labels") or []))


def best_bom(rec: dict, index: dict[str, list[dict]], parsed: dict) -> tuple[dict | None, str, str]:
    code = base_code(rec.get("bom_code") or rec.get("image_code"))
    year = int(rec.get("model_year") or 0) if str(rec.get("model_year") or "").isdigit() else 0

    if code and code in index:
        def score(b: dict) -> tuple:
            return (
                1 if parsed["material"] and parsed["material"] == bom_material(b) else 0,
                -abs(int(b.get("asset_year") or 0) - year) if year else 0,
            )
        return max(index[code], key=score), "exact", "endpoint/image code"

    if code:
        synthetic = {
            "asset_year": rec.get("model_year") or "",
            "asset_family": "",
            "filename": f"explosiondrawing-{code}-01.pdf",
            "model_codes": [code],
            "disciplines": [parsed["discipline"]],
            "catalog_years": [],
            "categories": [],
            "labels": [rec.get("name", "")],
            "url": root_url(code),
            "url_stable": root_url(code),
            "post2024": True,
            "published": bool(POST2024.get(code, {}).get("file")),
            "category": parsed["category"],
        }
        return synthetic, "exact", "image frame code"

    if not parsed["family_base"]:
        return None, "none", "unknown family"

    candidates = []
    for b in index.get("__all__", []):
        if bom_family(b).lower() != parsed["family_base"].lower():
            continue
        if parsed["electric"] != bom_electric(b):
            continue
        material = bom_material(b)
        if parsed["material"] and material and parsed["material"] != material:
            continue
        if parsed["wmn"] != bool(re.search(r"\bWMN\b|\bWOMEN\b", bom_text(b), re.I)):
            continue
        if parsed["series_125"] != bool(re.search(r"\b125\b", bom_text(b))):
            continue
        candidates.append(b)

    if not candidates:
        return None, "none", "no family/material match"
    before = [b for b in candidates if year and int(b.get("asset_year") or 0) <= year]
    if before:
        chosen = max(before, key=lambda b: (int(b.get("asset_year") or 0), code_number(b)))
    else:
        chosen = min(
            candidates,
            key=lambda b: abs(int(b.get("asset_year") or year or 0) - year) if year else 0,
        )
    return chosen, "inferred", "family/material/year"


def main() -> int:
    current = json.loads((ROOT / "data/all_products_raw.json").read_text(encoding="utf-8"))
    archived_path = ROOT / "data/archive_products_raw.json"
    archived = json.loads(archived_path.read_text(encoding="utf-8")) if archived_path.exists() else []

    by_id = {}
    for rec in current:
        if rec.get("name") and not rec.get("error"):
            by_id[str(rec["id"])] = rec
    for rec in archived:
        if rec.get("name") and not rec.get("error"):
            by_id.setdefault(str(rec["id"]), rec)

    all_boms = json.loads((ROOT / "boms.json").read_text(encoding="utf-8"))
    extended = list(all_boms)
    index: dict[str, list[dict]] = {}
    for b in all_boms:
        b["category"] = bom_category(b)
        for code in b.get("model_codes", []):
            index.setdefault(base_code(code), []).append(b)
    index["__all__"] = all_boms

    for code, meta in POST2024.items():
        url = root_url(code)
        published = bool(meta["file"]) or exists(url)
        rec = {
            "asset_year": meta["year"],
            "asset_family": meta["family"].lower().replace(":", "_"),
            "filename": f"explosiondrawing-{code}-01.pdf",
            "model_codes": [code],
            "disciplines": [FAMILIES.get(meta["family"], "")],
            "catalog_years": [],
            "categories": [],
            "labels": [meta["label"]],
            "url": url,
            "url_stable": url,
            "post2024": True,
            "published": published,
            "local_file": meta["file"],
            "category": meta["category"],
        }
        if published:
            extended.append(rec)
            index.setdefault(code, []).append(rec)

    live_status = {}
    live_path = ROOT / "data/archive_live_status.json"
    if live_path.exists():
        live_status = json.loads(live_path.read_text(encoding="utf-8"))

    rows = []
    for rec in by_id.values():
        year = rec.get("image_year")
        year_source = "image"
        if not year and rec.get("archive_timestamp"):
            year = rec["archive_timestamp"][:4]
            year_source = "archive snapshot"
        if not year and rec.get("seen"):
            year = rec["seen"][0].split("-")[0]
            year_source = "sitemap"
        rec["model_year"] = year

        parsed = parse_product(
            rec.get("name", ""),
            " ".join(filter(None, [rec.get("image", ""), rec.get("url", "")])),
        )
        code = base_code(rec.get("bom_code") or rec.get("image_code"))
        bom, confidence, basis = best_bom(rec, index, parsed)

        live = live_status.get(str(rec.get("id")), {})
        live_ok = bool(live.get("live_ok"))
        live_final = live.get("live_final_url") or rec.get("live_url") or rec.get("url") or ""
        archive_url = rec.get("archive_url") or ""
        if rec.get("page_source") == "wayback":
            product_url, link_source = (live_final, "live") if live_ok else (archive_url or live_final, "wayback")
        else:
            product_url, link_source = rec.get("product_url") or rec.get("url"), "live"

        if bom:
            codes = [base_code(c) for c in bom.get("model_codes", [])]
            bom_code = code if code and code in codes else (codes[0] if codes else "")
            rev = code_rev(next((c for c in bom.get("model_codes", []) if base_code(c) == bom_code), ""))
            bom_url = rec.get("bom_url") or bom.get("url_stable") or bom.get("url") or root_url(bom_code, rev)
            published = bool(bom.get("published", True))
            if not published:
                bom_url = ""
        else:
            bom_code, bom_url, published = code, "", False

        notes = ""
        if confidence == "inferred":
            notes = "verify against the frame code on the bike"
        elif not published:
            notes = "no published exploded drawing yet"

        rows.append({
            "product_id": rec.get("id"),
            "product_name": rec.get("name"),
            "product_url": product_url,
            "link_source": link_source,
            "family": parsed["family"],
            "family_base": parsed["family_base"],
            "discipline": parsed["discipline"],
            "material": parsed["material"],
            "electric": parsed["electric"],
            "category": parsed["category"],
            "model_year": year or "",
            "year_source": year_source,
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
        })

    fields = list(rows[0].keys())
    with open(ROOT / "all_products.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["discipline"], r["family"], r["model_year"], r["product_name"] or "")))

    with open(ROOT / "all_product_bom_map.csv", "w", newline="", encoding="utf-8") as fh:
        cols = ["product_id", "product_name", "model_year", "family", "discipline", "category", "product_url",
                "frame_code", "bom_code", "bom_label", "bom_year", "bom_url", "bom_published",
                "match_confidence", "match_basis"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows([{k: r[k] for k in cols} for r in rows])

    (ROOT / "all_boms_extended.json").write_text(
        json.dumps(extended, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"products: {len(rows)} -> all_products.csv")
    print(f"BOMs: {len(extended)} -> all_boms_extended.json")
    print("exact:", sum(1 for r in rows if r["match_confidence"] == "exact"))
    print("inferred:", sum(1 for r in rows if r["match_confidence"] == "inferred"))
    print("none:", sum(1 for r in rows if r["match_confidence"] == "none"))
    print("disciplines:", {d: sum(1 for r in rows if r["discipline"] == d) for d in sorted({r["discipline"] for r in rows})})
    print("categories:", {c: sum(1 for r in rows if r["category"] == c) for c in sorted({r["category"] for r in rows})})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

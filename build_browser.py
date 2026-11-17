#!/usr/bin/env python3
"""Generate a self-contained browser.html from the scraped CSV/JSON files.

No server is required: the data is embedded in the page, so double-clicking
browser.html works.  Re-run this after refreshing the manifests.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    all_products = ROOT / "all_products.csv"
    all_boms = ROOT / "all_boms_extended.json"
    products = read_csv(all_products if all_products.exists() else ROOT / "gravity_products.csv")
    boms = json.loads(
        (all_boms if all_boms.exists() else ROOT / "gravity_boms_extended.json").read_text(encoding="utf-8")
    )
    archive = json.loads((ROOT / "boms.json").read_text(encoding="utf-8"))

    data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "products": products,
        "boms": boms,
        "archive": archive,
    }

    template = (ROOT / "browser_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    if "/*__DATA__*/" not in template:
        raise SystemExit("browser_template.html is missing the /*__DATA__*/ placeholder")
    html = template.replace("/*__DATA__*/", payload)
    out = ROOT / "browser.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size/1024:.0f} KB)")
    print(f"  products: {len(products)}")
    print(f"  gravity BOMs: {len(boms)}")
    print(f"  full archive: {len(archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Canyon BOM / exploded-drawing archive — reverse engineering notes

**Live browser:** <https://canyon-re-f385e1.gitlab.io/>

Scope: Canyon gravity MTBs — **Spectral, Strive, Torque, Sender** (including
`:ON` e-MTB variants).  Everything here is built from public Canyon pages and
public static PDFs.

## Browse the data

Open **`browser.html`** directly in a browser — no server needed.  It is a
self-contained page with the data embedded, so `file://` works:

```bash
xdg-open browser.html      # Linux
open browser.html          # macOS
```

Tabs:

- **Products** — 171 bike/tier pages (93 live + 78 recovered from Wayback),
  searchable/filterable by family, year, confidence, BOM status and source.
  Recovered rows carry a **legacy** badge but link directly to the still-live
  Canyon page (Canyon redirects many of them to `/archive/`); the Wayback
  snapshot is available as a secondary link.  Product names are clickable for
  every row.  Click a row for the frame code, BOM PDF and confidence details.
- **Gravity BOMs** — the 83 archive PDFs plus the four post-2024 BOMs.  A
  **Products** column links directly to every mapped product page; older
  BOMs whose product pages were never scraped fall back to Canyon search.
- **Post-2024 / M204** — cards for M178, M180, M185, M188 and the pending M204,
  with product links, local PDFs and the watcher command.
- **Full archive** — all 479 PDFs from 2009–2024, with a gravity-only filter,
  product links where mapped, and pagination.

Regenerate it after refreshing the data:

```bash
python3 build_browser.py
```

## TL;DR

| Thing | Result |
|---|---|
| Old year-based archive | 16 pages (2009–2024), **479 unique PDFs**, **83 gravity PDFs** |
| Old PDF URL | `/on/demandware.static/-/Library-Sites-canyon-shared/default/explosiondrawings/{year}/{family}/{code}-{rev}_BOM_{name}.pdf` |
| New PDF URL | `/on/demandware.static/-/Library-Sites-canyon-shared/default/explosiondrawings/explosiondrawing-{code}-{rev}.pdf` |
| Version hash `dwXXXXXXXX` | **Optional** — the hash-free URL resolves directly |
| Post-2024 lookup | `Utils-SearchBike?pidFallback=true&template=explosionDrawings&serialNumber={serial or product-id}` |
| New BOMs found | **M178** Spectral AL, **M180** Sender CFR, **M185** Torque AL, **M188** Strive:ON |
| M204 | 2027 Spectral:ON CF / CF LTD — **not published yet** (pre-orders 4 Dec 2026) |
| Product pages mapped | **171 gravity bike/tier pages** (2018–2027): 133 exact, 22 sibling-inferred, 11 inferred, 5 unknown |
| BOMs with direct product links | **46 / 87** (the rest are 2009–2018 or unarchived 2021–2022 frames) |

## How the archive is structured

The hub is:

```
https://www.canyon.com/en-us/support-articles/exploded_drawings_overview.html
```

It links to one page per model year:

```
https://www.canyon.com/en-us/support-articles/exploded_drawings_2009.html
...
https://www.canyon.com/en-us/support-articles/exploded_drawings_2024.html
```

Each year page is a list of bike families, and each family contains direct PDF
links.  Example:

```
2022 / Strive / Strive CFR
https://www.canyon.com/on/demandware.static/-/Library-Sites-canyon-shared/default/dw190c2a8d/explosiondrawings/2022/strive/M147-01_BOM_Strive%20CFR.pdf
```

The `dw190c2a8d` segment is a Demandware content-version folder.  It is not
needed:

```
https://www.canyon.com/on/demandware.static/-/Library-Sites-canyon-shared/default/explosiondrawings/2022/strive/M147-01_BOM_Strive%20CFR.pdf
```

Both URLs return the same PDF.  This is useful because the version hash cannot
be guessed, while the stable path can be constructed once the year/family/name
are known.

### What is in 2009–2024

| Year | Links | Year | Links |
|---:|---:|---|---:|
| 2009 | 18 | 2017 | 48 |
| 2010 | 17 | 2018 | 70 |
| 2011 | 21 | 2019 | 67 |
| 2012 | 21 | 2020 | 60 |
| 2013 | 22 | 2021 | 73 |
| 2014 | 27 | 2022 | 28 |
| 2015 | 32 | 2023 | 20 |
| 2016 | 38 | 2024 | 8 |

Those are 570 link instances, **479 unique PDFs** after de-duplication.  There
is no 2025 or 2026 year page on any locale tested (en-US, en-GB, en-IT, it-IT,
de-DE); the February 2026 Wayback snapshot of the overview also stops at 2024.

## The post-2024 route

The overview says: *“If your bike was released before 2024 and you don’t know
your serial number, you can search … using the model year.”*  For newer bikes
the year pages are replaced by the serial-number search.

The form is backed by:

```
GET /on/demandware.store/Sites-US-Site/en_US/Utils-SearchBike
    ?pidFallback=true
    &template=explosionDrawings
    &serialNumber=<serial>
```

A valid serial returns an HTML fragment containing the drawing link.  With
`pidFallback=true`, a **numeric product ID** also works as the `serialNumber`
value, which is the useful part for mapping:

```
serialNumber=4381  -> explosiondrawing-M179-01.pdf
serialNumber=3202  -> explosiondrawing-M086-01.pdf
serialNumber=3195  -> explosiondrawing-M147-01.pdf
```

The new drawings use the flat root path:

```
https://www.canyon.com/on/demandware.static/-/Library-Sites-canyon-shared/default/explosiondrawings/explosiondrawing-{code}-{rev}.pdf
```

### New gravity frames found this way

| Code | Bike | PDF | Local file |
|---|---|---|---|
| M178 | Spectral AL (2025/2027) | `explosiondrawing-M178-01.pdf` | `pdfs/post2024/` |
| M180 | Sender CFR (2025/2027) | `explosiondrawing-M180-01.pdf` | `pdfs/post2024/` (7 pages) |
| M185 | Torque AL (2027) | `explosiondrawing-M185-01.pdf` | `pdfs/post2024/` |
| M188 | Strive:ON (2027) | `explosiondrawing-M188-01.pdf` | `pdfs/post2024/` |
| M204 | Spectral:ON CF / CF LTD (2027) | **not published** | — |

Existing frames still in use (their BOMs are already in the 2009–2024 archive):

| Code | Bike | Archive year |
|---|---|---|
| M147 | Strive CFR | 2022 |
| M158 | Strive:ON | 2023 |
| M179 | Spectral CF | 2024 |
| M124 | Torque:ON | 2023 |
| M113 | Torque AL | 2022 |
| M106 | Torque CF | 2021 |
| M121 | Spectral CF | 2021 |
| M114 | Spectral AL | 2022 |
| M086 | Sender CFR | 2021 |
| M091 | Spectral:ON CFR | 2022 |
| M129 | Spectral:ON CF | 2022/2023 |

**Key point:** BOMs are tied to a frame generation, not to the purchase year.
A 2027 Spectral CF still uses M179; a 2027 Strive:ON still uses M188, etc.

## M204 — the not-yet-sold 2027 Spectral:ON

M204 is the frame code in the image paths of the new Avinox-powered
Spectral:ON:

```
2027_FULL_spectral-on_cf-9_4388_M204_P03_...
2027_FULL_spectral-on_cf-ltd_4389_M204_P03_...
```

Product pages:

- CF 9 AXS: https://www.canyon.com/en-it/electric-bikes/electric-mountain-bikes/spectral-on/spectral-on-cf/spectral-on-cf-9-axs/4388.html
- CF LTD: https://www.canyon.com/en-it/electric-bikes/electric-mountain-bikes/spectral-on/spectral-on-cf/spectral-on-cf-ltd/4389.html

Status (16 Nov 2026): **“Coming soon”**, out of stock, pre-orders from
**4 December 2026**, deliveries from **19 January 2027**.  Canyon’s press
release describes the Avinox M2S motor (150 Nm / 1,500 W), 700 Wh battery,
2.9 kg carbon frame, 160/155 mm travel, mullet wheels.

Canyon has **no exploded drawing registered for M204 yet**:

- `serialNumber=4388` returns *“We couldn’t find any exploded drawing that
  matches your serial number.”*
- `explosiondrawing-M204-01.pdf` (and revisions 00–05) return 404.
- Old-style folder paths (`2026/2027`, `spectral_on`, all likely names) return 404.
- Wayback CDX/Common Crawl have no M204 PDF URL.
- Web/forum discussion treats M204 as an internal pre-launch hint, not a
  published parts document.

Use `watch_m204.py` to grab it automatically when it appears:

```bash
python3 watch_m204.py --once          # cron-friendly one-shot
python3 watch_m204.py --interval 21600  # keep running, check every 6 h
```

It checks 90 candidate URLs plus product IDs 4388/4389, downloads any real PDF
into `pdfs/post2024/`, and logs to `m204_watch.log`.

If you have a **real M204 serial** from a bike (test/press/production), use:

```bash
python3 scrape_canyon_boms.py serial <serial> --out pdfs/serial
```

Do not guess or brute-force serial numbers — only use a serial you are entitled
to query.

## Product-page mapping

`gravity_products.csv` contains **171** gravity bike/tier pages (2018–2027):

- **93 live pages** (current site / 2022+ sitemap snapshots)
- **78 Wayback pages** (2019–2021 plus early Spectral:ON; 2018 Sender AL)

Each row has:

- product ID, name, URL, model year, family, material, electric flag
- page source (`live` or `wayback`), archive timestamp, original URL
- frame code from the image path or the serial/PID endpoint
- matched BOM code, label, year, PDF URL, publication status
- confidence: `exact` / `inferred-sibling` / `inferred` / `none`

`gravity_product_bom_map.csv` is the same mapping flattened for joins.
Examples:

| Product | Year | Frame | BOM | Confidence |
|---|---:|---|---|---|
| Sender AL 7.0 (`1690`, archived) | 2018 | M047 | M047 | exact (endpoint) |
| Strive CFR 9.0 Team (`2008`, archived) | 2019 | M066 | M066 | exact (endpoint) |
| Spectral AL 6.0 (`1997`, archived) | 2019 | M053 | M053 | exact (endpoint) |
| Spectral:ON CF 7.0 (`2260`, archived) | 2020 | M098 | M098 | exact (endpoint) |
| Spectral 29 AL 6 (`3185`) | 2022 | M114 | M114 | exact (endpoint) |
| Sender CFR Mullet Underdog (`3202`) | 2022 | M086 | M086 | exact (endpoint) |
| Strive CFR (`3195`) | 2022 | M147 | M147 | exact (endpoint) |
| Torque 27.5 CF 7 (`2985`) | 2022 | M106 | M106 | exact (endpoint) |
| Spectral CF 8 (`4381`) | 2027 | M179 | M179 | exact (image/endpoint) |
| Spectral 6 (`4378`) | 2027 | M178 | M178 | exact (image) |
| Sender CFR Underdog (`4448`) | 2027 | M180 | M180 | exact (image) |
| Torque AL 7 (`4321`) | 2027 | M185 | M185 | exact (image) |
| Strive:ON CFR (`4293`) | 2027 | M188 | M188 | exact (image) |
| Spectral:ON CF LTD (`4389`) | 2027 | M204 | M204 | exact code, **not published** |

### What was recovered from Wayback

The 2019–2021 product pages use the modern `/mountain-bikes/...` tree and are
available in the CDX index.  For each archived page we extract the name, model
year and image, then ask Canyon’s PID-fallback endpoint for the exact BOM code.
That is why old BOMs such as M041, M047, M053, M054, M055, M064, M065, M066,
M068, M071, M074 and M098 now have direct product links.

All 82 recovered product URLs were checked directly against Canyon: every one
still resolves (many redirect to `/archive/...`).  The browser therefore links
to the **live final URL**, not to Wayback.  Wayback is kept only as a secondary
snapshot link and as the metadata source.

The pre-2019 site used `/mtb/{family}/{slug}` URLs.  Wayback has category pages
for some of those years, but most individual product pages were never captured;
where a product page does exist it is used, otherwise the browser falls back to
a Canyon search for that BOM.

The five `none` rows are the 2025 Spectral:ONfly family.  Canyon exposes no
frame code for those pages and the endpoint returns no drawing, so they are
left blank rather than guessed.

## Files

| File | Meaning |
|---|---|
| `browser.html` | self-contained data browser (open directly, no server) |
| `browser_template.html` | HTML/CSS/JS template used by the browser generator |
| `build_browser.py` | embed the current CSV/JSON data into `browser.html` |
| `scrape_canyon_boms.py` | discover year pages, build manifest, download, verify, extract text, serial/PID lookup |
| `build_product_map.py` | join product pages to frame/BOM codes |
| `enrich_archive_products.py` | recover old product pages from Wayback CDX |
| `check_live_urls.py` | verify old product URLs still resolve; record final live URLs |
| `watch_m204.py` | poll for the M204 BOM and download it when published |
| `boms.json` / `boms.csv` | full 479-PDF archive manifest |
| `gravity_boms.json` / `.csv` | 83 gravity PDFs from the year archive |
| `gravity_boms_extended.json` | archive + post-2024 BOMs |
| `gravity_products.csv` | 171 gravity product pages with best BOM match |
| `gravity_product_bom_map.csv` | flat product → BOM mapping |
| `data/gravity_products_raw.json` | raw enriched live/current product metadata |
| `data/archive_products_raw.json` | recovered Wayback product pages (2019–2021) |
| `data/archive_live_status.json` | live status + final URL for every recovered page |
| `data/cdx_old_*.json` | Wayback CDX input for the archived pages |
| `pdfs/gravity/` | 83 downloaded gravity PDFs + `pdftotext -layout` output |
| `pdfs/post2024/` | M178, M180, M185, M188 root-level BOMs |
| `gravity_verification.json` | HTTP verification of all 83 gravity PDF URLs |

## Commands

```bash
# Rebuild the full archive manifest from the live year pages
python3 scrape_canyon_boms.py catalog

# Gravity-only manifest (Spectral / Strive / Torque / Sender, including :ON)
python3 scrape_canyon_boms.py gravity

# Download the gravity set
python3 scrape_canyon_boms.py download --catalog gravity_boms.json --out pdfs/gravity

# Check that every manifest URL still resolves
python3 scrape_canyon_boms.py verify --gravity --jobs 6

# Dump text from downloaded PDFs for parsing
python3 scrape_canyon_boms.py extract-text --dir pdfs/gravity

# Look up one bike by serial, or by product ID via pidFallback
python3 scrape_canyon_boms.py serial 4381 --dry-run
python3 scrape_canyon_boms.py serial <real-serial> --out pdfs/serial

# Recover older product pages from Wayback (resumable, rate-limited)
python3 enrich_archive_products.py

# Verify old URLs still resolve and record their final live URLs
python3 check_live_urls.py

# Rebuild the product-page mapping
python3 build_product_map.py

# Rebuild the self-contained HTML browser
python3 build_browser.py

# Watch for M204
python3 watch_m204.py --once
```

## BOM PDF structure

Each drawing is usually one A4/A3 page: an exploded view with balloon numbers
on the left and a table on the right:

```
EP/GP#        POS.NR.                 QTY.    CANYON#
GP7193-01     28 (EP1472-01),         1x      10008678
              34 (EP1473-01)          1x
              12,                     1x
...
```

The table columns are `EP/GP#`, `POS.NR.`, `QTY.`, `CANYON#`.  Multiple
position lines can belong to the same GP group.  `pdftotext -layout` preserves
enough spacing to parse this, but the drawing/table is laid out at the right
edge of the page, so a coordinate-aware extractor (`pdfplumber`) is the more
robust route if you want a normalized parts database.

M180 is an exception: it is a **7-page** document rather than the usual single
page.

## Notes / caveats

- **Politeness:** the static PDFs are public, but the search endpoint is a
  live service.  The scripts use modest concurrency and sleeps; keep it that
  way.  Do not enumerate serial numbers.
- **Model year vs frame year:** the archive page year is the model year, not
  necessarily the PDF folder year (e.g. the 2022 page links a 2021 Aeroad PDF).
  The manifest keeps both `catalog_years` and `asset_year`.
- **Inferred rows:** `inferred` / `inferred-sibling` mappings are best-effort
  from family/material/year.  Verify against the frame code printed on the
  bike before ordering parts.  `exact` means the code came from the endpoint or
  the product image path.
- **M204:** this is the one current gravity frame whose BOM is deliberately not
  public yet.  The watcher is the reliable way to get it on launch day.

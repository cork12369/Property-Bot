# PropertyBot

Local scraper for [PropertyGuru Singapore](https://www.propertyguru.com.sg) sale listings — a free, self-hosted recreation of the `shahidirfan/propertyguru-scraper` Apify actor. No Apify account, no per-result fees. Stores listings in SQLite with price-change history.

## Install

```bash
pip install -r requirements.txt
playwright install chromium
```

Requires Python 3.10+. The one-time `playwright install chromium` downloads the
browser engine (~190 MB) that the scraper drives.

## Usage

```bash
# Scrape the first 20 listings from the default SG sale search
python main.py scrape

# Scrape up to 100 listings from a filtered search, 5 pages max
python main.py scrape --url "https://www.propertyguru.com.sg/property-for-sale?freetext=Jurong+East" --max-results 100 --max-pages 5

# Preview parsed listings without saving
python main.py scrape --dry-run

# Export the database
python main.py export --format csv

# Summary statistics
python main.py stats
```

### Options

| Option | Default | Description |
|---|---|---|
| `--url` | SG property-for-sale | Any PropertyGuru SG search URL (root, sale-results, or filtered) |
| `--max-results` | 20 | Maximum listings to save |
| `--max-pages` | 10 | Maximum search pages to visit (20 listings per page) |
| `--delay` | 3.0 | Base delay between page requests (randomized up to 1.5x) |
| `--headless` | off | Not supported — exits immediately (Cloudflare blocks headless Chromium). See "Running without a display" |
| `--dry-run` | off | Print parsed listings without writing to the database |

### Running without a display

Cloudflare blocks headless Chromium, so the scraper needs a real browser window. On a
headless Linux server, run it under a virtual display:

```bash
sudo apt install xvfb
xvfb-run -a python main.py scrape
```

## Mall directory (separate script)

`mall_directory.py` is a standalone fetcher for Singapore shopping-mall
directories (malls + tenant stores). It is completely separate from the
PropertyGuru scraper above — it has its own output folder, its own SQLite
database, and does not touch `propertybot` or `propertybot.db`.

```bash
python mall_directory.py              # download CSVs + build SQLite
python mall_directory.py --csv-only   # download CSVs only
python mall_directory.py --force      # re-download existing files
python mall_directory.py --out data/malls
```

Data source: [curioputterings/singapore-mall-data](https://github.com/curioputterings/singapore-mall-data)
(CC BY 4.0), pinned to a fixed commit for reproducibility. Note: data.gov.sg
does not currently publish a mall-directory/tenants dataset, so this script
uses the closest open Singapore mall-directory dataset instead.

Output (default `data/mall_directory/`):

| File | Contents |
|---|---|
| `malls.csv` | Malls: name, owner group, address, postal code, planning region, nearest MRT, lat/lng |
| `stores.csv` | Tenant stores: store name, mall, unit, level, category, F&B/halal flags, website |
| `mall_directory.db` | SQLite with `malls` and `stores` tables (same data as the CSVs) |

## Web GUI

```bash
python main.py gui              # http://127.0.0.1:8000
python main.py gui --port 9000
```

A local FastAPI dashboard that replaces clicking through the CLI:

- **Dashboard** — a ticker of key numbers, a top-picks leaderboard by score, an
  outcome-spread bar (GREAT/GOOD/OK/FAIL/unscored), a daily activity chart, and a
  property-type breakdown.
- **Properties** — market-style cards (score, outcome pill, per-criterion
  `c1`–`c4` bars, yellow price tag with change since first seen) with a global
  search bar (`/`), outcome chips, and district/type/price filters. The detail
  view adds a gallery, a price-history chart, and the full evidence behind every
  evaluation criterion.
- **Runs** — a 30-day activity heat strip and the full run history, with the
  captured log and a link to the generated markdown report.
- **Controls** — *Start scrape* and *Run evaluation* buttons run
  `python main.py scrape` and `python main.py agent run` as background subprocesses;
  the log streams to the browser over SSE and can be cancelled mid-run.

Only one job runs at a time, since the scraper drives a real Chromium window and
PropertyGuru's Cloudflare challenge must not be hit twice at once. Scraping still
needs that visible browser window: clicking *Start scrape* opens Chromium on the
desktop. Evaluation needs `OPENROUTER_API_KEY` in `.env`.

The GUI records every job in a `scrape_runs` table, which is what the daily charts
and run history read from. Host and port default to `GUI_HOST` / `GUI_PORT` in `.env`.

## Evaluation agent notes

- Set `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` in `.env`. The default,
  `meta/muse-spark-1.3`, works on a stock OpenRouter account.
- The `meta/muse-spark-1.3-contributor` variant requires allowing paid-model training
  at <https://openrouter.ai/settings/privacy>; otherwise OpenRouter rejects it.
- Price-trend scoring compares *asking* prices, not transactions, and is only
  meaningful once repeated scrapes have recorded real price movements.

## Data schema

Each listing is stored in `data/propertybot.db` (table `listings`):

| Field | Description |
|---|---|
| `listing_id` | PropertyGuru listing identifier (primary key) |
| `title` | Listing / project name |
| `url` | Direct link to the listing |
| `price`, `price_value`, `currency` | Displayed price, numeric value, currency code |
| `price_per_area`, `psf_value` | Price per sqft text and numeric value |
| `address`, `street`, `district` | Address split into street and district |
| `bedrooms`, `bathrooms` | Room counts |
| `size`, `size_sqft` | Size text and numeric sqft |
| `property_type` | Condominium, HDB Flat, Apartment, etc. |
| `tenure`, `build_year` | Tenure and build year when published |
| `mrt` | Distance to nearest MRT station |
| `recency`, `listed_date` | Listing freshness |
| `description` | Agent headline |
| `image_url`, `image_urls`, `image_count` | Listing photos |
| `agent_name`, `agent_company`, `agent_profile_url` | Agent details |
| `search_url`, `search_page` | Where the record was found |
| `first_seen_at`, `last_seen_at` | When the bot first/last saw this listing |

A `price_history` table records every observed price change per listing, so re-running scrapes over time builds a market-monitoring dataset.

## How it works

PropertyGuru sits behind Cloudflare, which 403-challenges plain HTTP clients and headless browsers. The scraper therefore drives a real, visible Chromium window via Playwright:

1. Loads your search URL and waits for the listing cards to render.
2. Moves through results by clicking the pagination "next" button — exactly like a human user — which uses client-side routing that Cloudflare permits. (Direct requests to page-2 URLs are blocked, which is also why a browser window must be visible during scraping.)
3. Parses the rendered listing cards (stable `da-id` attributes) with BeautifulSoup, cross-checking against the page's JSON-LD `ItemList` structured data for missing fields (images, numeric prices).
4. Normalizes values (price → integer, "Built: 2018" → 2018, address → street + district).
5. Upserts into SQLite keyed by `listing_id`, appending to `price_history` when the price changes.

Pages are visited with a configurable delay (default 3s + jitter). If a page fails to load or the challenge page appears, the scraper stops with a clear error instead of saving garbage. A visible browser window opens while scraping — this is required for Cloudflare to permit the requests.

## Responsible use

This tool collects publicly available listing data for personal research. You are responsible for complying with PropertyGuru's terms of service, applicable laws, and privacy obligations. Keep the default rate limits unless you have a reason not to.

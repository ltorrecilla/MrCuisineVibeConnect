# French recipe downloader

Standalone Python 3.9+ script using only the standard library: no pip packages,
Android SDK, robot connection, account or API key required.

Run these commands from the repository root. On Windows, replace `python` with
`py -3` if needed; on macOS/Linux, use `python3` if needed.

## Switch between test and full download

**Test: download the first 5 official French Connect recipes** (also the default):

```sh
python scripts/recipe-downloader/download.py --mode test
```

**Full: download the entire official French Connect catalogue:**

```sh
python scripts/recipe-downloader/download.py --mode full
```

The only switch is `test` versus `full`. The API supplies the actual total;
there is no hardcoded 1,700-recipe limit. Test mode caps the selected recipes,
including already cached ones; it does not select five new recipes on each run.

Optional examples:

```sh
# A larger test, including original thumbnail/detail images
python scripts/recipe-downloader/download.py --mode test --test-count 10 --images

# Entire catalogue with images
python scripts/recipe-downloader/download.py --mode full --images

# Choose another destination
python scripts/recipe-downloader/download.py --mode full --output ./my-recipes
```

## Output and resuming

By default, output is under `scripts/recipe-downloader/downloads/fr-FR/`,
regardless of the current working directory:

- `recipes/<id>.json`: complete, unmodified API response bytes, including all
  serving sizes, ingredients, instructions and machine settings.
- `manifest.json`: source URLs, timestamps, SHA-256 hashes, availability,
  image mappings, per-recipe errors and the latest run summary.
- `catalogue-test.json` / `catalogue-full.json`: the selected catalogue entries.
- `images/`: optional original-resolution thumbnail/detail image files; URLs
  remain untouched in recipe JSON and map to local files in the manifest.

Press Ctrl+C to stop. Run the same command to resume. Test and full modes share
one archive: successful test downloads are reused by the full run. Unchanged
recipes are skipped only when the saved file hash matches. Missing or damaged
files and failed downloads are retried. Add `--refresh` to refetch selected JSON
regardless of the source update timestamp (useful for ratings-only changes).

Requests are paced at 0.5 seconds minimum, with bounded retries for network
errors, HTTP 429 and 5xx, respecting Retry-After. `--delay 1` slows requests down.
Run only one downloader process per output folder at a time.

A failed recipe or requested image results in exit code 1, with details in the
manifest. A catalogue/schema error stops the run rather than reporting success.
Full mode checks unique ID counts against the API total. If pagination changes
mid-run, rerun. Test mode never marks recipes absent. A successful full listing
marks absent recipes unavailable but retains their files. Changed recipes
replace their previous JSON; this is a current archive, not a version history.

## Source and future translation

Uses the same endpoints and filters as Toque's `McOnline.kt`:
`https://mc-api.tecpal.com/api/v2/recipes`, device `MC2.0`, source `official`,
`Accept-Language: fr-FR`, and `device-type: web`. The requested language is
recorded; the script does not independently detect a server language fallback.
It preserves source responses without translating, normalizing, or executing
machine instructions. Future translations should be separate records keyed by
recipe ID, language and source hash. Reviews and other device catalogues are
outside this downloader's scope.

## Verification

```sh
python -m unittest discover -s scripts/recipe-downloader -p 'test_*.py' -v
```

These offline tests cover sampling, pagination, resume, corruption recovery,
failed-detail retry, source preservation and retaining removed recipes.

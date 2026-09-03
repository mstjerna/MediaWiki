# MediaWiki → Genesys Knowledge migration

This repository contains the MediaWiki XML export of the Alektum Group wiki and
the tooling to turn it into a Genesys Knowledge JSON import file.

## Files

| File | Description |
| --- | --- |
| `Alektum+Group-20260902114843.xml` | MediaWiki XML export (source of truth) |
| `convert.py` | Converter (Python 3, standard library only) |
| `genesys_full_migration_v10_final.json` | Generated Genesys import file (real images) |
| `genesys_full_migration_v9_final.json` / `v8_final` / `v7_2` | Previous outputs, kept as formatting reference |
| `build_media_url_map.py` | Builds `media_url_map.json` from the Genesys Response Asset search pages |
| `ResponseAssetSearchRequest*.json` | Pages of the `POST /api/v2/responsemanagement/responseassets/search` response |
| `media_url_map.json` / `media_url_coverage.json` | Generated filename → URL map and its coverage report |
| `wiki_image_sizes.json` | Wikitext sizing options per image reference, recorded for a later sizing pass |
| `genesys_url.json` | Older Content Management listing, used only as a fallback for non-image files |
| `1 (7).json` | Small validated sample of the accepted import format |
| `extract_media_manifest.py` | Extracts the media files referenced by the generated import file |
| `required_media.txt` / `required_media.json` | Generated media manifest (see below) |
| `test_convert.py` | Unit tests for the conversion rules |
| `test_build_media_url_map.py` | Unit tests for the URL map builder |
| `test_extract_media_manifest.py` | Unit tests for the media manifest extractor |

## Re-running the conversion

```bash
python3 convert.py
```

This reads `Alektum+Group-20260902114843.xml` and `media_url_map.json` (when
present) and writes `genesys_full_migration_v10_final.json` plus
`wiki_image_sizes.json`, printing a summary of the number of articles,
categories, labels, converted wikitables, mapped files, emitted image blocks
and recorded sizing options.

Other input/output paths can be supplied as arguments:

```bash
python3 convert.py path/to/export.xml path/to/output.json
python3 convert.py --media-map path/to/media_url_map.json
```

If the map file does not exist the converter behaves exactly as before and
every file reference keeps its 🔴 placeholder.

Run the tests with:

```bash
python3 -m unittest test_convert -v
```

## Media manifest (which files must be uploaded?)

`extract_media_manifest.py` answers "which of my local media files does the
migration actually need?". It walks the generated JSON, collects the filename
from every image placeholder (both the multi-paragraph and the compact
single-line variant — it imports the `PLACEHOLDER_*` constants from
`convert.py`, so it can never drift from the generator) and cross-checks the
result against the `[[File:…]]` / `[[Fil:…]]` / `[[Image:…]]` / `[[Bild:…]]`
and `<gallery>` references in the XML export.

```bash
python3 extract_media_manifest.py
```

Outputs:

| File | Description |
| --- | --- |
| `required_media.txt` | One distinct filename per line, sorted and deduplicated |
| `required_media.json` | Per file: referencing article titles, occurrence count, extension and filename normalization variants |

The summary printed to stdout lists the total number of placeholder
occurrences, the number of distinct filenames, a breakdown by file extension,
any cross-check discrepancy between XML and JSON, and the ten most referenced
files.

Other input paths can be supplied as arguments:

```bash
python3 extract_media_manifest.py path/to/migration.json --xml path/to/export.xml
```

### Filename normalization

MediaWiki normalizes filenames, so naive string comparison against the files
on disk will miss matches. **Match case-insensitively and treat spaces and
underscores as equivalent.** Each entry in `required_media.json` therefore
carries a `variants` list containing the literal name, the space→underscore
and underscore→space spellings, the MediaWiki-capitalized form (first
character upper-cased), a lowercased form and, when percent-encoding is
present, the URL-decoded form.

### Matching against a local media directory

```bash
python3 extract_media_manifest.py --check-local /path/to/media
```

This recursively indexes the directory, reports which required files were
found (resolved path and size), which are missing, and the total byte size of
the matched set — i.e. how much of the local media collection actually
matters. A `media_match_report.json` is written as well (override the path
with `--match-report`). The mode is a convenience only: a missing directory is
reported, not an error.

### Recommended workflow

1. Extract the manifest: `python3 extract_media_manifest.py`.
2. Match it against the local media collection:
   `python3 extract_media_manifest.py --check-local /path/to/media`.
3. Upload **only** the matched subset to Genesys as **Response Assets**
   (Admin → Response Management → Library → Assets, or
   `POST /api/v2/responsemanagement/responseassets/uploads`). Content
   Management `sharingUri` links open a viewer page and cannot be embedded as
   images, so they are only used as a fallback for non-image files.
4. Save the pages of the asset search response as
   `ResponseAssetSearchRequest*.json` and build the URL map:
   `python3 build_media_url_map.py`.
5. Re-run `python3 convert.py`, which replaces the placeholders of every
   mapped file with a real image block or hyperlink and keeps the placeholder
   for anything still missing.

Note that `extract_media_manifest.py` derives the manifest from the *image
placeholders*, so it reads `genesys_full_migration_v9_final.json` (the last
all-placeholder output) by default rather than the current converter output.

Run the tests with:

```bash
python3 -m unittest test_extract_media_manifest -v
```

## Media URL map (Response Assets)

```bash
python3 build_media_url_map.py
```

`build_media_url_map.py` globs `ResponseAssetSearchRequest*.json` (so extra
pages can simply be dropped in), merges their `results` arrays,
de-duplicates by asset `id` and keys every asset by the canonical filename of
`extract_media_manifest.canonical_key()` — which is what makes the uploaded
`750px-Koer_redigerad_v2.jpg` match the article's
`750px-Koer redigerad v2.jpg`. When two assets canonicalize to the same key
the most recently created one (`dateCreated`) wins and the collision is
recorded in the report.

Outputs:

| File | Description |
| --- | --- |
| `media_url_map.json` | `filename → {url, contentType, assetId, isImage}` |
| `media_url_coverage.json` | Counts, unmapped required files, extra (unreferenced) assets and canonical-key collisions |

Non-image files that were never uploaded as Response Assets fall back to the
`sharingUri` of `genesys_url.json` (marked `"source": "contentManagement"`);
images never do, because a viewer URL cannot be embedded.

## Images in the generated import file

With `media_url_map.json` present, `convert.py` emits, per file reference:

* **mapped image** → a paragraph holding a Genesys `Image` block:

  ```json
  {"type": "Image", "image": {"url": "https://api-cdn.mypurecloud.de/response-assets/…",
                              "properties": {"altText": "Foo.jpg"}}}
  ```

* **mapped document** (the 8 PDFs and the DOCX) → a paragraph with a
  hyperlink whose display text is the filename;
* **unmapped file** → the unchanged 🔴 placeholder, so a partial upload still
  yields a valid, importable file;
* **compact contexts** (table cells and list items), where only a flat run of
  `Text` blocks is allowed and an `Image` block would not be valid → a
  hyperlink to the image URL instead of an image block.

**Image dimensions are deliberately omitted in v10.** No `width`,
`widthWithUnit` or `height` is written, so Genesys applies each image's
natural sizing and the result can be inspected visually before deciding
whether explicit dimensions are needed. The wikitext sizing options are not
lost: every `NNNpx` / `thumb` / alignment option is recorded per reference in
`wiki_image_sizes.json` (`filename → [{article, rawOptions, pxWidth}]`) for a
later sizing pass. Nothing consumes that file yet.

Run the tests with:

```bash
python3 -m unittest test_build_media_url_map -v
```

## Conversion rules

* Every MediaWiki page in the main namespace (`ns` = 0) becomes one Genesys
  article (`published.title`, `category`, `labels`, `visible`, `variations`).
* The page titled `HR` is exported as `HR - Personal` (Genesys rejects the bare
  title `HR`).
* The first `[[Category:X]]` becomes the article category, all following
  categories become labels. Distinct categories and labels are also listed in
  the top-level `categories[]` and `labels[]` arrays.
* Headings (`== Rubrik ==`) become a paragraph with bold text; `'''bold'''` and
  `''italic''` become the `Bold` / `Italic` marks.
* External links become text blocks with a `hyperlink` (whitespace before the
  link is preserved); internal links are reduced to their label text.
* `*` and `#` lines become `UnorderedList` / `OrderedList` blocks.
* Raw HTML is stripped; `<b>`/`<strong>` and `<i>`/`<em>` are mapped to marks.
* Wikitables (`{| class="wikitable" … |}`) are rendered as unordered lists:
  * two columns → `Left: Right` with the left column bold,
  * tables repeating the same column pattern (e.g. `Land`/`Kod` four times)
    are split so that every pair becomes its own line (`Afghanistan: AF`),
  * three or more columns → `First (Second) - Rest`.
* `[[File:…]]` / `[[Fil:…]]` / `[[Image:…]]` / `[[Bild:…]]` references (and
  `<gallery>` entries) become an image block or a hyperlink when the file is
  in `media_url_map.json` (see above). Everything else is replaced in place
  with a visible placeholder so editors know an image needs to be re-added
  manually:

  ```
  🔴 BILD SAKNAS FRÅN WIKI-IMPORTEN 🔴
  Filreferens: **Foo.jpg**
  🔴 *Lägg till manuellt i Genesys Knowledge.* 🔴
  ```

  The placeholder keeps only the bare filename (namespace prefix and all
  rendering options such as `thumb`, `300px`, `left`, `link=…` are stripped).
  Text surrounding an inline image reference is preserved as its own
  paragraph. Inside table cells (and list items), which are flattened to a
  single line, the placeholder is rendered as one compact sentence instead:
  `🔴 BILD SAKNAS FRÅN WIKI-IMPORTEN 🔴 Filreferens: **Foo.jpg** 🔴 *Lägg till
  manuellt i Genesys Knowledge.* 🔴`
* Genesys rejects any `Paragraph` whose `paragraph.blocks` array is empty.
  The converter never emits one: the image placeholder above has no blank
  spacer paragraphs, and a final recursive sanitization pass
  (`sanitize_blocks()` in `convert.py`) drops any other container block
  whose block list ends up empty (e.g. a blank line left over once
  surrounding markup has been stripped out) before the file is written.
  `convert()` also runs a stricter recursive scan
  (`find_empty_block_lists()`) over the whole generated document and
  refuses to write the output if it finds any empty `blocks` array
  anywhere. You can run the same check against an already-generated file
  with:

  ```bash
  python3 convert.py --validate genesys_full_migration_v10_final.json
  ```

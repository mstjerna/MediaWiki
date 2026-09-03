# MediaWiki → Genesys Knowledge migration

This repository contains the MediaWiki XML export of the Alektum Group wiki and
the tooling to turn it into a Genesys Knowledge JSON import file.

## Files

| File | Description |
| --- | --- |
| `Alektum+Group-20260902114843.xml` | MediaWiki XML export (source of truth) |
| `convert.py` | Converter (Python 3, standard library only) |
| `genesys_full_migration_v9_final.json` | Generated Genesys import file |
| `genesys_full_migration_v8_final.json` / `genesys_full_migration_v7_2.json` | Previous outputs, kept as formatting reference |
| `1 (7).json` | Small validated sample of the accepted import format |
| `extract_media_manifest.py` | Extracts the media files referenced by the generated import file |
| `required_media.txt` / `required_media.json` | Generated media manifest (see below) |
| `test_convert.py` | Unit tests for the conversion rules |
| `test_extract_media_manifest.py` | Unit tests for the media manifest extractor |

## Re-running the conversion

```bash
python3 convert.py
```

This reads `Alektum+Group-20260902114843.xml` and writes
`genesys_full_migration_v9_final.json`, printing a summary of the number of
articles, categories, labels and converted wikitables.

Other input/output paths can be supplied as arguments:

```bash
python3 convert.py path/to/export.xml path/to/output.json
```

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
3. Upload **only** the matched subset to Genesys Cloud Content Management.
4. Build a `filename → URL` map (JSON) from the upload results.
5. Feed that map into a later conversion pass so the placeholders can be
   replaced with real image/link blocks, falling back to the placeholder for
   files that are still missing.

Run the tests with:

```bash
python3 -m unittest test_extract_media_manifest -v
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
  `<gallery>` entries) are replaced in place with a visible placeholder so
  editors know an image needs to be re-added manually:

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
  python3 convert.py --validate genesys_full_migration_v9_final.json
  ```

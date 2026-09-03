# MediaWiki → Genesys Knowledge migration

This repository contains the MediaWiki XML export of the Alektum Group wiki and
the tooling to turn it into a Genesys Knowledge JSON import file.

## Files

| File | Description |
| --- | --- |
| `Alektum+Group-20260902114843.xml` | MediaWiki XML export (source of truth) |
| `convert.py` | Converter (Python 3, standard library only) |
| `genesys_full_migration_v8_final.json` | Generated Genesys import file |
| `genesys_full_migration_v7_2.json` | Previous output, kept as formatting reference |
| `1 (7).json` | Small validated sample of the accepted import format |
| `test_convert.py` | Unit tests for the conversion rules |

## Re-running the conversion

```bash
python3 convert.py
```

This reads `Alektum+Group-20260902114843.xml` and writes
`genesys_full_migration_v8_final.json`, printing a summary of the number of
articles, categories, labels and converted wikitables.

Other input/output paths can be supplied as arguments:

```bash
python3 convert.py path/to/export.xml path/to/output.json
```

Run the tests with:

```bash
python3 -m unittest test_convert -v
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
  python3 convert.py --validate genesys_full_migration_v8_final.json
  ```

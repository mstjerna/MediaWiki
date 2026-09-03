#!/usr/bin/env python3
"""Convert a MediaWiki XML export into a Genesys Knowledge JSON import file.

Usage:
    python3 convert.py [input.xml] [output.json]

Defaults:
    input.xml   Alektum+Group-20260902114843.xml
    output.json genesys_full_migration_v10_final.json

If `media_url_map.json` (see `build_media_url_map.py`) is present, mapped
images become real Genesys Image blocks and mapped documents become
hyperlinks; anything unmapped keeps the text placeholder.

Only the Python standard library is used.
"""

import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

MW_NS = "{http://www.mediawiki.org/xml/export-0.11/}"

DEFAULT_INPUT = "Alektum+Group-20260902114843.xml"
DEFAULT_OUTPUT = "genesys_full_migration_v10_final.json"
DEFAULT_MEDIA_MAP = "media_url_map.json"
DEFAULT_SIZES_OUTPUT = "wiki_image_sizes.json"

DEFAULT_CATEGORY = "General"
LABEL_COLOR = "#52e909"

# Genesys rejected the bare title "HR".
TITLE_OVERRIDES = {"HR": "HR - Personal"}

FILE_LINK_PREFIXES = ("file:", "image:", "fil:", "bild:", "media:")

# Placeholder emitted in place of every dropped [[File:...]] / <gallery> entry
# so editors know an article originally contained an image.
PLACEHOLDER_TITLE = "\U0001F534 BILD SAKNAS FR\u00c5N WIKI-IMPORTEN \U0001F534"
PLACEHOLDER_LABEL = "Filreferens:"
PLACEHOLDER_FOOTER = "L\u00e4gg till manuellt i Genesys Knowledge."
PLACEHOLDER_COMPACT = (
    PLACEHOLDER_TITLE + " " + PLACEHOLDER_LABEL + " '''{filename}''' "
    + "\U0001F534 ''" + PLACEHOLDER_FOOTER + "'' \U0001F534"
)

RE_COMMENT = re.compile(r"<!--.*?-->", re.S)
RE_CATEGORY = re.compile(r"\[\[Category:([^\]|]+)(?:\|[^\]]*)?\]\]", re.I)
RE_GALLERY_OPEN = re.compile(r"<gallery(?:\s[^>]*)?>", re.I)
RE_GALLERY_CLOSE = re.compile(r"</gallery\s*>", re.I)
RE_GALLERY = re.compile(r"<gallery[^>]*>.*?</gallery>", re.I | re.S)
RE_NOWIKI = re.compile(r"</?nowiki[^>]*>", re.I)
RE_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
RE_MAGIC_WORD = re.compile(r"__[A-Z]+__")
RE_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?::[A-Za-z][\w-]*)?(?:\s[^<>]*)?/?>")
RE_WIKILINK = re.compile(r"\[\[([^\[\]]*)\]\]")
RE_EXTLINK = re.compile(
    r"\[((?:https?|ftp|mailto):[^\s\]]+|//[^\s\]]+)"
    r"(?:[ \t]+((?:[^\[\]]|\[[^\[\]]*\])*))?\]"
)
RE_APOSTROPHES = re.compile(r"('''''|'''|'')")
RE_CELL_ATTRS = re.compile(r"^[^|\[\]{}'<>]*=[^|]*\|")
RE_PX_OPTION = re.compile(r"^(\d+)(?:x\d+)?px$", re.I)


# --------------------------------------------------------------------------
# Inline (text level) conversion
# --------------------------------------------------------------------------

def clean_html(text):
    """Strip raw HTML, mapping bold/italic semantics onto wiki markup."""
    text = RE_COMMENT.sub("", text)
    text = RE_GALLERY.sub("", text)
    text = RE_NOWIKI.sub("", text)
    text = re.sub(r"<\s*br\s*/?\s*>", " ", text, flags=re.I)
    text = re.sub(r"</?\s*(?:b|strong)\s*>", "'''", text, flags=re.I)
    text = re.sub(r"</?\s*(?:i|em)\s*>", "''", text, flags=re.I)
    text = RE_HTML_TAG.sub("", text)
    text = html.unescape(text)
    return text.replace("\u00a0", " ")


def strip_wiki_links(text):
    """Resolve internal links to plain text; any remaining file/image links
    are dropped here as a safety net (they should already have been handled
    by `split_text_and_files`/`replace_file_links_compact` upstream)."""
    previous = None
    while previous != text:
        previous = text
        text = RE_WIKILINK.sub(_replace_wikilink, text)
    return text


def _replace_wikilink(match):
    target = match.group(1)
    if target.strip().lower().startswith(FILE_LINK_PREFIXES):
        return ""
    parts = target.split("|")
    return parts[-1].strip() if len(parts) > 1 else parts[0].strip()


def iter_wikilinks(text):
    """Yield (start, end, content) for top-level `[[...]]` spans in text.

    Unlike `RE_WIKILINK`, this correctly matches the *outer* brackets of a
    link even when its options contain further nested `[[...]]` links, e.g.
    `[[File:A.png|thumb|see [[Ackord]]]]`.
    """
    i = 0
    n = len(text)
    while i < n:
        if text[i:i + 2] == "[[":
            depth = 1
            j = i + 2
            while j < n and depth:
                if text[j:j + 2] == "[[":
                    depth += 1
                    j += 2
                elif text[j:j + 2] == "]]":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            if depth == 0:
                yield i, j, text[i + 2:j - 2]
                i = j
                continue
        i += 1


def is_file_link(content):
    return content.strip().lower().startswith(FILE_LINK_PREFIXES)


def file_link_name(content):
    """Extract the bare filename from the contents of a `[[File:...]]` link."""
    _, _, rest = content.partition(":")
    return rest.split("|")[0].strip()


def file_link_options(content):
    """Return the rendering options of a `[[File:...|thumb|300px]]` link."""
    _, _, rest = content.partition(":")
    return [option.strip() for option in rest.split("|")[1:]]


def split_text_and_files(text):
    """Split wikitext into a list of ("text", str, []) / ("file", filename,
    options) segments, based on top-level file/image links."""
    segments = []
    pos = 0
    for start, end, content in iter_wikilinks(text):
        if not is_file_link(content):
            continue
        before = text[pos:start]
        if before:
            segments.append(("text", before, []))
        segments.append(("file", file_link_name(content), file_link_options(content)))
        pos = end
    tail = text[pos:]
    if tail or not segments:
        segments.append(("text", tail, []))
    return segments


def replace_file_links_compact(text, media=None, sizes=None, article=None):
    """Replace top-level file/image links with an inline replacement string,
    used where the surrounding structure (table cells, list items) cannot
    hold separate paragraphs: a wiki external link when the file is mapped to
    a URL, the compact placeholder otherwise."""
    pieces = []
    pos = 0
    for start, end, content in iter_wikilinks(text):
        if not is_file_link(content):
            continue
        pieces.append(text[pos:start])
        filename = file_link_name(content)
        record_wiki_size(sizes, article, filename, file_link_options(content))
        pieces.append(compact_media_text(filename, media))
        pos = end
    pieces.append(text[pos:])
    return "".join(pieces)


def placeholder_blocks(filename):
    """The compact placeholder emitted in place of a dropped image.

    No blank spacer paragraphs are emitted: Genesys rejects any Paragraph
    whose `blocks` array is empty.
    """
    return [
        paragraph([text_block(PLACEHOLDER_TITLE, ["Bold"])]),
        paragraph([text_block(PLACEHOLDER_LABEL + " "), text_block(filename, ["Bold"])]),
        paragraph([
            text_block("\U0001F534 "),
            text_block(PLACEHOLDER_FOOTER, ["Italic"]),
            text_block(" \U0001F534"),
        ]),
    ]


def gallery_filenames(gallery_lines):
    """Extract filenames from the body lines of a `<gallery>...</gallery>` block."""
    return [name for name, _ in gallery_entries(gallery_lines)]


def gallery_entries(gallery_lines):
    """Extract (filename, options) from the body lines of a `<gallery>` block."""
    entries = []
    for line in gallery_lines:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("|")
        name = parts[0].strip()
        lower = name.lower()
        for prefix in FILE_LINK_PREFIXES:
            if lower.startswith(prefix):
                name = name[len(prefix):].strip()
                break
        if name:
            entries.append((name, [option.strip() for option in parts[1:]]))
    return entries


# --------------------------------------------------------------------------
# Media URL map (uploaded Genesys Response Assets)
# --------------------------------------------------------------------------

def canonical_key(name):
    """Case-insensitive, space/underscore-insensitive matching key.

    Delegates to `extract_media_manifest.canonical_key()` so the two modules
    cannot drift apart; imported lazily because that module imports this one.
    """
    from extract_media_manifest import canonical_key as _canonical_key
    return _canonical_key(name)


def media_map_from_dict(raw):
    """Re-key a `filename -> {url, contentType, isImage}` map by canonical
    filename, dropping entries without a URL."""
    media = {}
    for name, entry in raw.items():
        if not entry or not entry.get("url"):
            continue
        media[canonical_key(name)] = entry
    return media


def load_media_map(path):
    """Load `media_url_map.json` (see `build_media_url_map.py`), keyed by
    canonical filename. Returns an empty map when the file does not exist,
    in which case every file reference keeps its placeholder."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    return media_map_from_dict(raw)


def lookup_media(filename, media):
    """Return the media map entry for a wiki filename, or None."""
    if not media:
        return None
    return media.get(canonical_key(filename))


def image_block(url, alt_text):
    """A Genesys Image block.

    `width`, `widthWithUnit` and `height` are deliberately omitted so Genesys
    applies the image's natural sizing; the wikitext sizing options are
    recorded in `wiki_image_sizes.json` for a possible later pass.
    """
    return {"type": "Image",
            "image": {"url": url, "properties": {"altText": alt_text}}}


def media_blocks(filename, media):
    """Blocks emitted for one file reference in a block-level context:

    * a mapped image  -> a paragraph holding an Image block,
    * a mapped document (PDF/DOCX) -> a paragraph holding a hyperlink,
    * anything unmapped -> the 🔴 placeholder paragraphs.
    """
    entry = lookup_media(filename, media)
    if not entry:
        return placeholder_blocks(filename)
    if entry.get("isImage"):
        return [paragraph([image_block(entry["url"], filename)])]
    return [paragraph([text_block(filename, hyperlink=entry["url"])])]


def compact_media_text(filename, media):
    """The wikitext substituted for a file reference in a compact context
    (table cell, list item), where only a flat run of Text blocks is allowed
    and an Image block would not be valid: a link to the file when it is
    mapped, the compact placeholder otherwise."""
    entry = lookup_media(filename, media)
    if entry:
        return "[{0} {1}]".format(entry["url"], filename)
    return PLACEHOLDER_COMPACT.format(filename=filename)


def px_width(options):
    """The pixel width requested by a wikitext option such as `300px` or
    `396x396px`, or None."""
    for option in options:
        match = RE_PX_OPTION.match(option.strip())
        if match:
            return int(match.group(1))
    return None


def record_wiki_size(sizes, article, filename, options):
    """Record the wikitext rendering options of one file reference.

    Data-only: nothing consumes it yet, it is written to
    `wiki_image_sizes.json` so explicit dimensions can be added later.
    """
    if sizes is None or not options:
        return
    sizes.setdefault(filename, []).append({
        "article": article,
        "rawOptions": "|".join(options),
        "pxWidth": px_width(options),
    })


def split_marks(text):
    """Split wiki bold/italic markup into (text, marks) segments."""
    segments = []
    bold = italic = False
    for token in RE_APOSTROPHES.split(text):
        if token == "'''":
            bold = not bold
        elif token == "''":
            italic = not italic
        elif token == "'''''":
            bold = not bold
            italic = not italic
        elif token:
            marks = []
            if bold:
                marks.append("Bold")
            if italic:
                marks.append("Italic")
            if segments and segments[-1][1] == marks:
                segments[-1] = (segments[-1][0] + token, marks)
            else:
                segments.append((token, marks))
    return segments


def text_block(text, marks=None, hyperlink=None):
    payload = {"text": text}
    if hyperlink:
        payload["hyperlink"] = hyperlink
    if marks:
        payload["marks"] = list(marks)
    return {"type": "Text", "text": payload}


def parse_inline(text, extra_marks=None, trim=True):
    """Convert a piece of wikitext into a list of Genesys Text blocks."""
    text = strip_wiki_links(clean_html(text))
    previous = None
    while previous != text:
        previous = text
        text = RE_TEMPLATE.sub("", text)
    text = RE_MAGIC_WORD.sub("", text)

    blocks = []
    for chunk, marks in split_marks(text):
        if extra_marks:
            marks = marks + [m for m in extra_marks if m not in marks]
        position = 0
        for match in RE_EXTLINK.finditer(chunk):
            before = chunk[position:match.start()]
            if before:
                blocks.append(text_block(before, marks))
            url = match.group(1)
            label = (match.group(2) or "").strip() or url
            blocks.append(text_block(label, hyperlink=url))
            position = match.end()
        rest = chunk[position:]
        if rest:
            blocks.append(text_block(rest, marks))

    return trim_blocks(blocks) if trim else blocks


def trim_blocks(blocks):
    """Drop empty blocks and surrounding whitespace of a run of text blocks."""
    if blocks:
        blocks[0]["text"]["text"] = blocks[0]["text"]["text"].lstrip()
        blocks[-1]["text"]["text"] = blocks[-1]["text"]["text"].rstrip()
    blocks = [b for b in blocks if b["text"]["text"].strip()]
    if blocks:
        blocks[0]["text"]["text"] = blocks[0]["text"]["text"].lstrip()
        blocks[-1]["text"]["text"] = blocks[-1]["text"]["text"].rstrip()
    return blocks


def paragraph(blocks):
    return {"type": "Paragraph", "paragraph": {"blocks": blocks}}


def sanitize_blocks(blocks):
    """Final safety net: drop any container block whose block list ended up
    empty, so a `paragraph.blocks` (or list) is never serialized empty.

    Genesys rejects any Paragraph whose `blocks` array is empty; this can
    happen for reasons other than the image placeholder (e.g. a blank line
    left behind once other markup has been stripped out). Applied
    recursively so nested structures (list items, table cells - which are
    themselves rendered as list items) are covered by the same rule.
    """
    sanitized = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "Paragraph":
            if not block["paragraph"]["blocks"]:
                continue
        elif block_type in ("OrderedList", "UnorderedList"):
            items = [item for item in block["list"]["blocks"] if item.get("blocks")]
            if not items:
                continue
            block["list"]["blocks"] = items
        sanitized.append(block)
    return sanitized


def paragraph_from(text, marks=None):
    blocks = parse_inline(text, extra_marks=marks)
    return paragraph(blocks) if blocks else None


def list_block(ordered, items):
    items = [{"type": "ListItem", "blocks": blocks} for blocks in items if blocks]
    if not items:
        return None
    kind = "OrderedList" if ordered else "UnorderedList"
    return {"type": kind, "list": {"blocks": items}}


# --------------------------------------------------------------------------
# Table conversion
# --------------------------------------------------------------------------

def strip_cell_attributes(cell):
    """Remove leading HTML attributes such as `style="..." |` from a cell."""
    match = RE_CELL_ATTRS.match(cell)
    if match:
        return cell[match.end():]
    return cell


def parse_table(lines):
    """Parse the body lines of a wikitable into (caption, header, rows)."""
    caption = ""
    header = []
    rows = []
    current = None

    def flush():
        nonlocal current
        if current is not None:
            if any(cell.strip() for cell in current["cells"]):
                if current["header"]:
                    if not header:
                        header.extend(current["cells"])
                    else:
                        rows.append(current["cells"])
                else:
                    rows.append(current["cells"])
        current = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|+"):
            flush()
            caption = stripped[2:].strip()
        elif stripped.startswith("|-"):
            flush()
            current = {"cells": [], "header": False}
        elif stripped.startswith("!") or stripped.startswith("|"):
            if current is None:
                current = {"cells": [], "header": stripped.startswith("!")}
            separator = "!!" if stripped.startswith("!") else "||"
            if stripped.startswith("!"):
                current["header"] = True
            for cell in stripped[1:].split(separator):
                current["cells"].append(strip_cell_attributes(cell).strip())
        elif stripped and current is not None and current["cells"]:
            current["cells"][-1] = (current["cells"][-1] + " " + stripped).strip()
        elif stripped:
            rows.append([stripped])

    flush()
    return caption, header, rows


def row_to_blocks(cells, media=None, sizes=None, article=None):
    """Render one table row as the inline blocks of a list item."""
    cells = [replace_file_links_compact(c, media, sizes, article) for c in cells]
    while cells and not cells[-1].strip():
        cells.pop()
    if not cells:
        return []

    first = cells[0].strip()
    rest = [c.strip() for c in cells[1:]]

    blocks = parse_inline(first, extra_marks=["Bold"]) if first else []

    if len(cells) == 1:
        return blocks
    if len(cells) == 2:
        tail = ": " + rest[0] if rest[0] else ""
    else:
        tail = ""
        if rest[0]:
            tail += " (" + rest[0] + ")"
        remaining = [c for c in rest[1:] if c]
        if remaining:
            tail += " - " + " - ".join(remaining)
    if tail:
        blocks.extend(parse_inline(tail, trim=False))
    return trim_blocks(blocks)


def header_cycle(header):
    """Length of the repeating pattern in a header row, e.g. Land/Kod/Land/Kod."""
    size = len(header)
    for width in range(1, size):
        if size % width == 0 and header == header[:width] * (size // width):
            return width
    return size


def split_repeated_columns(header, rows):
    """Country-code style tables repeat the same columns several times per row."""
    width = header_cycle(header)
    if not header or width == len(header):
        return header, rows
    expanded = []
    for row in rows:
        for start in range(0, len(row), width):
            chunk = row[start:start + width]
            if any(cell.strip() for cell in chunk):
                expanded.append(chunk)
    return header[:width], expanded


def table_to_blocks(lines, media=None, sizes=None, article=None):
    caption, header, rows = parse_table(lines)
    header, rows = split_repeated_columns(header, rows)
    blocks = []
    if caption:
        block = paragraph_from(caption, marks=["Bold"])
        if block:
            blocks.append(block)
    if header and any(cell.strip() for cell in header):
        heading = " – ".join(cell.strip() for cell in header if cell.strip())
        block = paragraph_from(heading, marks=["Bold"])
        if block:
            blocks.append(block)
    items = [row_to_blocks(row, media, sizes, article) for row in rows]
    block = list_block(False, items)
    if block:
        blocks.append(block)
    return blocks


# --------------------------------------------------------------------------
# Page conversion
# --------------------------------------------------------------------------

def extract_categories(text):
    categories = []
    for match in RE_CATEGORY.finditer(text):
        name = match.group(1).strip()
        if name and name not in categories:
            categories.append(name)
    return categories, RE_CATEGORY.sub("", text)


def emit_text_or_placeholder(stripped, marks=None, media=None, sizes=None,
                             article=None):
    """Split a line on file/image links and return the blocks to append:
    normal paragraphs for surrounding text, and for each file reference an
    Image block, a hyperlink or the placeholder blocks (see
    `media_blocks()`)."""
    blocks = []
    for kind, content, options in split_text_and_files(stripped):
        if kind == "file":
            record_wiki_size(sizes, article, content, options)
            blocks.extend(media_blocks(content, media))
        else:
            block = paragraph_from(content, marks=marks)
            if block:
                blocks.append(block)
    return blocks


def wikitext_to_blocks(text, media=None, sizes=None, article=None):
    """Convert the body of a MediaWiki page into Genesys body blocks."""
    text = RE_COMMENT.sub("", text)
    lines = text.split("\n")
    blocks = []
    tables = 0
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if RE_GALLERY_OPEN.match(stripped):
            index += 1
            gallery_lines = []
            while index < len(lines) and not RE_GALLERY_CLOSE.search(lines[index]):
                gallery_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            for filename, options in gallery_entries(gallery_lines):
                record_wiki_size(sizes, article, filename, options)
                blocks.extend(media_blocks(filename, media))
            continue

        if stripped.startswith("{|"):
            depth = 1
            index += 1
            table_lines = []
            while index < len(lines) and depth:
                inner = lines[index].strip()
                if inner.startswith("{|"):
                    depth += 1
                elif inner.startswith("|}"):
                    depth -= 1
                    if depth == 0:
                        index += 1
                        break
                table_lines.append(lines[index])
                index += 1
            blocks.extend(table_to_blocks(table_lines, media, sizes, article))
            tables += 1
            continue

        if not stripped or stripped.startswith("----"):
            index += 1
            continue

        if stripped.startswith("="):
            heading = stripped.strip("=").strip()
            blocks.extend(emit_text_or_placeholder(
                heading, marks=["Bold"], media=media, sizes=sizes, article=article))
            index += 1
            continue

        if stripped[0] in "*#":
            marker = stripped[0]
            items = []
            while index < len(lines):
                item = lines[index].strip()
                if not item or item[0] not in "*#" or item[0] != marker:
                    break
                item_text = replace_file_links_compact(
                    item.lstrip("*#").strip(), media, sizes, article)
                items.append(parse_inline(item_text))
                index += 1
            block = list_block(marker == "#", items)
            if block:
                blocks.append(block)
            continue

        if stripped[0] in ";:":
            content = stripped.lstrip(";:").strip()
            blocks.extend(emit_text_or_placeholder(
                content, marks=["Bold"] if stripped[0] == ";" else None,
                media=media, sizes=sizes, article=article))
            index += 1
            continue

        blocks.extend(emit_text_or_placeholder(
            stripped, media=media, sizes=sizes, article=article))
        index += 1

    return sanitize_blocks(blocks), tables


def find_empty_block_lists(node, path=""):
    """Recursively scan a (possibly nested) JSON-like structure for any
    `"blocks": []` container, returning a list of dotted/indexed paths where
    an empty block list was found.

    This is the final validation pass: `sanitize_blocks()` proactively
    removes the empty Paragraph/List containers it knows about, but this
    generic scan is a safety net that catches *any* empty `blocks` array
    anywhere in the generated document tree, regardless of its shape.
    """
    violations = []
    if isinstance(node, dict):
        for key, value in node.items():
            child_path = "{0}.{1}".format(path, key) if path else key
            if key == "blocks" and isinstance(value, list) and not value:
                violations.append(child_path)
            else:
                violations.extend(find_empty_block_lists(value, child_path))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            violations.extend(find_empty_block_lists(item, "{0}[{1}]".format(path, index)))
    return violations


def convert(input_path, output_path, media_map_path=DEFAULT_MEDIA_MAP,
            sizes_path=DEFAULT_SIZES_OUTPUT):
    media = load_media_map(media_map_path)
    sizes = {}
    tree = ET.parse(input_path)
    pages = [p for p in tree.getroot().findall(MW_NS + "page")
             if (p.findtext(MW_NS + "ns") or "0") == "0"]

    documents = []
    categories = []
    labels = []
    tables_converted = 0

    for page in pages:
        title = (page.findtext(MW_NS + "title") or "").strip()
        title = TITLE_OVERRIDES.get(title, title)
        revision = page.find(MW_NS + "revision")
        wikitext = (revision.findtext(MW_NS + "text") if revision is not None else "") or ""

        page_categories, wikitext = extract_categories(wikitext)
        category = page_categories[0] if page_categories else DEFAULT_CATEGORY
        page_labels = page_categories[1:]

        if category not in categories:
            categories.append(category)
        for label in page_labels:
            if label not in labels:
                labels.append(label)

        blocks, tables = wikitext_to_blocks(wikitext, media, sizes, title)
        tables_converted += tables
        if not blocks:
            # Genesys requires a non-empty body; pages that only contained
            # dynamic MediaWiki templates fall back to their title.
            blocks = [paragraph([text_block(title, ["Bold"])])]

        documents.append({
            "published": {
                "title": title,
                "category": {"name": category},
                "labels": [{"name": label} for label in page_labels],
                "visible": True,
                "variations": [
                    {
                        "body": {"blocks": blocks},
                        "priority": 1,
                        "name": "Default",
                    }
                ],
            }
        })

    data = {
        "version": 2,
        "documents": documents,
        "categories": [{"name": name} for name in sorted(categories)],
        "labels": [{"name": name, "color": LABEL_COLOR} for name in sorted(labels)],
    }

    violations = find_empty_block_lists(data)
    if violations:
        raise ValueError(
            "Refusing to write output: empty block list(s) found at: {0}"
            .format(", ".join(violations)))

    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)

    if sizes_path:
        with open(sizes_path, "w", encoding="utf-8") as handle:
            json.dump(sizes, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")

    return {
        "articles": len(documents),
        "categories": len(data["categories"]),
        "labels": len(data["labels"]),
        "tables": tables_converted,
        "mappedFiles": len(media),
        "images": count_blocks(data, "Image"),
        "sizedReferences": sum(len(entries) for entries in sizes.values()),
    }


def count_blocks(node, block_type):
    """Count the blocks of a given `type` anywhere in the document tree."""
    if isinstance(node, dict):
        total = 1 if node.get("type") == block_type else 0
        return total + sum(count_blocks(value, block_type) for value in node.values())
    if isinstance(node, list):
        return sum(count_blocks(item, block_type) for item in node)
    return 0


def validate_file(path):
    """Load a generated Genesys JSON file and check it for empty block
    lists. Returns the list of violation paths (empty means the file is
    clean). Used both as a standalone check (`convert.py --validate FILE`)
    and from the test suite."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return find_empty_block_lists(data)


def main(argv):
    if len(argv) > 1 and argv[1] == "--validate":
        path = argv[2] if len(argv) > 2 else DEFAULT_OUTPUT
        violations = validate_file(path)
        if violations:
            print("Found {0} empty block list(s) in {1}:".format(len(violations), path))
            for violation in violations:
                print("  {0}".format(violation))
            return 1
        print("OK: no empty block lists found in {0}".format(path))
        return 0

    media_map_path = DEFAULT_MEDIA_MAP
    positional = []
    index = 1
    while index < len(argv):
        if argv[index] == "--media-map":
            index += 1
            if index >= len(argv):
                print("Missing value for --media-map")
                return 1
            media_map_path = argv[index]
        else:
            positional.append(argv[index])
        index += 1

    input_path = positional[0] if positional else DEFAULT_INPUT
    output_path = positional[1] if len(positional) > 1 else DEFAULT_OUTPUT
    summary = convert(input_path, output_path, media_map_path)
    print("Wrote {0}".format(output_path))
    for key in ("articles", "categories", "labels", "tables", "mappedFiles",
                "images", "sizedReferences"):
        print("  {0}: {1}".format(key, summary[key]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

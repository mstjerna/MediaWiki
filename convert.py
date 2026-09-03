#!/usr/bin/env python3
"""Convert a MediaWiki XML export into a Genesys Knowledge JSON import file.

Usage:
    python3 convert.py [input.xml] [output.json]

Defaults:
    input.xml   Alektum+Group-20260902114843.xml
    output.json genesys_full_migration_v8_final.json

Only the Python standard library is used.
"""

import html
import json
import re
import sys
import xml.etree.ElementTree as ET

MW_NS = "{http://www.mediawiki.org/xml/export-0.11/}"

DEFAULT_INPUT = "Alektum+Group-20260902114843.xml"
DEFAULT_OUTPUT = "genesys_full_migration_v8_final.json"

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
    PLACEHOLDER_TITLE + " \u2013 " + PLACEHOLDER_LABEL + " {filename} \u2013 " + PLACEHOLDER_FOOTER
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


def split_text_and_files(text):
    """Split wikitext into a list of ("text", str) / ("file", filename)
    segments, based on top-level file/image links."""
    segments = []
    pos = 0
    for start, end, content in iter_wikilinks(text):
        if not is_file_link(content):
            continue
        before = text[pos:start]
        if before:
            segments.append(("text", before))
        segments.append(("file", file_link_name(content)))
        pos = end
    tail = text[pos:]
    if tail or not segments:
        segments.append(("text", tail))
    return segments


def replace_file_links_compact(text):
    """Replace top-level file/image links with an inline placeholder string,
    used where the surrounding structure (table cells, list items) cannot
    hold separate placeholder paragraphs."""
    pieces = []
    pos = 0
    for start, end, content in iter_wikilinks(text):
        if not is_file_link(content):
            continue
        pieces.append(text[pos:start])
        filename = file_link_name(content)
        pieces.append(PLACEHOLDER_COMPACT.format(filename=filename))
        pos = end
    pieces.append(text[pos:])
    return "".join(pieces)


def placeholder_blocks(filename):
    """The multi-paragraph placeholder emitted in place of a dropped image."""
    return [
        paragraph([text_block(PLACEHOLDER_TITLE, ["Bold"])]),
        paragraph([]),
        paragraph([text_block(PLACEHOLDER_LABEL)]),
        paragraph([text_block(filename)]),
        paragraph([]),
        paragraph([text_block(PLACEHOLDER_FOOTER)]),
    ]


def gallery_filenames(gallery_lines):
    """Extract filenames from the body lines of a `<gallery>...</gallery>` block."""
    names = []
    for line in gallery_lines:
        stripped = line.strip()
        if not stripped:
            continue
        name = stripped.split("|")[0].strip()
        lower = name.lower()
        for prefix in FILE_LINK_PREFIXES:
            if lower.startswith(prefix):
                name = name[len(prefix):].strip()
                break
        if name:
            names.append(name)
    return names


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


def row_to_blocks(cells):
    """Render one table row as the inline blocks of a list item."""
    cells = [replace_file_links_compact(c) for c in cells]
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


def table_to_blocks(lines):
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
    items = [row_to_blocks(row) for row in rows]
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


def emit_text_or_placeholder(stripped, marks=None):
    """Split a line on file/image links and return the blocks to append:
    normal paragraphs for surrounding text, full placeholder blocks for
    each image reference."""
    blocks = []
    for kind, content in split_text_and_files(stripped):
        if kind == "file":
            blocks.extend(placeholder_blocks(content))
        else:
            block = paragraph_from(content, marks=marks)
            if block:
                blocks.append(block)
    return blocks


def wikitext_to_blocks(text):
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
            for filename in gallery_filenames(gallery_lines):
                blocks.extend(placeholder_blocks(filename))
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
            blocks.extend(table_to_blocks(table_lines))
            tables += 1
            continue

        if not stripped or stripped.startswith("----"):
            index += 1
            continue

        if stripped.startswith("="):
            heading = stripped.strip("=").strip()
            blocks.extend(emit_text_or_placeholder(heading, marks=["Bold"]))
            index += 1
            continue

        if stripped[0] in "*#":
            marker = stripped[0]
            items = []
            while index < len(lines):
                item = lines[index].strip()
                if not item or item[0] not in "*#" or item[0] != marker:
                    break
                item_text = replace_file_links_compact(item.lstrip("*#").strip())
                items.append(parse_inline(item_text))
                index += 1
            block = list_block(marker == "#", items)
            if block:
                blocks.append(block)
            continue

        if stripped[0] in ";:":
            content = stripped.lstrip(";:").strip()
            blocks.extend(emit_text_or_placeholder(
                content, marks=["Bold"] if stripped[0] == ";" else None))
            index += 1
            continue

        blocks.extend(emit_text_or_placeholder(stripped))
        index += 1

    return blocks, tables


def convert(input_path, output_path):
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

        blocks, tables = wikitext_to_blocks(wikitext)
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

    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)

    return {
        "articles": len(documents),
        "categories": len(data["categories"]),
        "labels": len(data["labels"]),
        "tables": tables_converted,
    }


def main(argv):
    input_path = argv[1] if len(argv) > 1 else DEFAULT_INPUT
    output_path = argv[2] if len(argv) > 2 else DEFAULT_OUTPUT
    summary = convert(input_path, output_path)
    print("Wrote {0}".format(output_path))
    for key in ("articles", "categories", "labels", "tables"):
        print("  {0}: {1}".format(key, summary[key]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

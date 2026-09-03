#!/usr/bin/env python3
"""Extract the media files referenced by a generated Genesys migration JSON.

Every image reference dropped by `convert.py` was replaced with a visible
placeholder (see `PLACEHOLDER_*` in `convert.py`). This script walks the
generated JSON, collects the filenames from those placeholders, cross-checks
them against the MediaWiki XML export and writes a manifest of the media that
actually has to be uploaded to Genesys Cloud.

Usage:
    python3 extract_media_manifest.py [migration.json]
                                      [--xml export.xml]
                                      [--check-local DIRECTORY]
                                      [--match-report media_match_report.json]

Defaults:
    migration.json  genesys_full_migration_v9_final.json (the last output in
                    which every media reference is still a placeholder)
    export.xml      Alektum+Group-20260902114843.xml

Outputs:
    required_media.txt   one distinct filename per line, sorted
    required_media.json  per-file article list, occurrence count and
                         filename normalization variants

Only the Python standard library is used.
"""

import json
import os
import sys
import urllib.parse
import xml.etree.ElementTree as ET

import convert
from convert import (MW_NS, PLACEHOLDER_LABEL, PLACEHOLDER_TITLE,
                     RE_COMMENT, RE_GALLERY_CLOSE, RE_GALLERY_OPEN)

# The manifest is derived from the image *placeholders*, so it is pinned to
# the last all-placeholder output rather than following `convert.DEFAULT_OUTPUT`
# (v10 onwards emits real Image blocks for every uploaded file).
DEFAULT_JSON = "genesys_full_migration_v9_final.json"
DEFAULT_XML = convert.DEFAULT_INPUT

DEFAULT_TXT_OUTPUT = "required_media.txt"
DEFAULT_JSON_OUTPUT = "required_media.json"
DEFAULT_MATCH_REPORT = "media_match_report.json"

# Both the multi-paragraph and the compact placeholder end the block right
# before the filename with this label, so a single rule finds both forms.
LABEL_PREFIX = PLACEHOLDER_LABEL + " "


# --------------------------------------------------------------------------
# Extraction from the generated Genesys JSON
# --------------------------------------------------------------------------

def block_text(block):
    """Return the text of a Genesys `Text` block, or None for other blocks."""
    if not isinstance(block, dict) or block.get("type") != "Text":
        return None
    payload = block.get("text")
    if not isinstance(payload, dict):
        return None
    text = payload.get("text")
    return text if isinstance(text, str) else None


def references_in_sequence(blocks):
    """Yield (filename, form) for placeholders inside one list of blocks.

    `form` is "paragraph" for the multi-paragraph placeholder (the label sits
    in a paragraph of its own) and "compact" for the single-line variant used
    inside table cells and list items (the label is preceded by the
    placeholder title in the very same text block).
    """
    for index, block in enumerate(blocks):
        text = block_text(block)
        if text is None or not text.endswith(LABEL_PREFIX):
            continue
        if index + 1 >= len(blocks):
            continue
        filename = block_text(blocks[index + 1])
        if not filename or not filename.strip():
            continue
        form = "compact" if PLACEHOLDER_TITLE in text else "paragraph"
        yield filename.strip(), form


def iter_media_references(node):
    """Recursively yield (filename, form) for every placeholder under `node`."""
    if isinstance(node, dict):
        for value in node.values():
            for reference in iter_media_references(value):
                yield reference
    elif isinstance(node, list):
        for reference in references_in_sequence(node):
            yield reference
        for item in node:
            for reference in iter_media_references(item):
                yield reference


def document_title(document):
    published = document.get("published") if isinstance(document, dict) else None
    if isinstance(published, dict):
        title = published.get("title")
        if isinstance(title, str):
            return title
    return "(untitled)"


def collect_from_json(path):
    """Return (references, occurrences) for a generated migration JSON.

    `references` maps filename -> {"articles": [...], "occurrences": int,
    "forms": {...}}, ordered by first appearance.
    """
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    references = {}
    occurrences = 0
    documents = data.get("documents") or []
    for document in documents:
        title = document_title(document)
        for filename, form in iter_media_references(document):
            occurrences += 1
            entry = references.setdefault(
                filename, {"articles": [], "occurrences": 0, "forms": {}})
            entry["occurrences"] += 1
            entry["forms"][form] = entry["forms"].get(form, 0) + 1
            if title not in entry["articles"]:
                entry["articles"].append(title)
    return references, occurrences


# --------------------------------------------------------------------------
# Cross-check against the MediaWiki XML export
# --------------------------------------------------------------------------

def filenames_in_wikitext(wikitext):
    """Return every file/image reference in a page's wikitext.

    Uses the same helpers as `convert.py` so the two cannot drift apart:
    top-level `[[File:…]]` / `[[Fil:…]]` / `[[Image:…]]` / `[[Bild:…]]` links
    plus the entries of every `<gallery>…</gallery>` block.
    """
    wikitext = RE_COMMENT.sub("", wikitext)

    names = []
    for _, _, content in convert.iter_wikilinks(wikitext):
        if convert.is_file_link(content):
            name = convert.file_link_name(content)
            if name:
                names.append(name)

    inside_gallery = False
    gallery_lines = []
    for line in wikitext.splitlines():
        if not inside_gallery:
            if RE_GALLERY_OPEN.search(line):
                inside_gallery = True
                after = RE_GALLERY_OPEN.split(line, 1)[-1]
                if RE_GALLERY_CLOSE.search(after):
                    gallery_lines.append(RE_GALLERY_CLOSE.split(after, 1)[0])
                    inside_gallery = False
                elif after.strip():
                    gallery_lines.append(after)
            continue
        if RE_GALLERY_CLOSE.search(line):
            before = RE_GALLERY_CLOSE.split(line, 1)[0]
            if before.strip():
                gallery_lines.append(before)
            inside_gallery = False
            continue
        gallery_lines.append(line)

    names.extend(convert.gallery_filenames(gallery_lines))
    return names


def collect_from_xml(path):
    """Return filename -> occurrence count for the MediaWiki XML export."""
    tree = ET.parse(path)
    pages = [p for p in tree.getroot().findall(MW_NS + "page")
             if (p.findtext(MW_NS + "ns") or "0") == "0"]

    counts = {}
    for page in pages:
        revision = page.find(MW_NS + "revision")
        wikitext = (revision.findtext(MW_NS + "text") if revision is not None else "") or ""
        for name in filenames_in_wikitext(wikitext):
            counts[name] = counts.get(name, 0) + 1
    return counts


def cross_check(json_names, xml_names):
    """Compare the two filename sets using their canonical keys."""
    json_keys = {canonical_key(name): name for name in json_names}
    xml_keys = {canonical_key(name): name for name in xml_names}
    missing_from_json = sorted(xml_keys[key] for key in xml_keys
                               if key not in json_keys)
    missing_from_xml = sorted(json_keys[key] for key in json_keys
                              if key not in xml_keys)
    return missing_from_json, missing_from_xml


# --------------------------------------------------------------------------
# Filename normalization
# --------------------------------------------------------------------------

def upper_first(name):
    """MediaWiki upper-cases the first character of every filename."""
    return name[:1].upper() + name[1:] if name else name


def normalization_variants(name):
    """Return the plausible on-disk spellings of a MediaWiki filename.

    Covers the literal name, spaces <-> underscores in both directions, the
    MediaWiki-capitalized form, a lowercased form for case-insensitive
    matching and the URL-decoded form when percent-encoding is present.
    """
    bases = [name]
    if "%" in name:
        decoded = urllib.parse.unquote(name)
        if decoded != name:
            bases.append(decoded)

    variants = []
    for base in bases:
        for spelling in (base, base.replace(" ", "_"), base.replace("_", " ")):
            for form in (spelling, upper_first(spelling), spelling.lower()):
                if form and form not in variants:
                    variants.append(form)
    return variants


def canonical_key(name):
    """Case-insensitive, space/underscore-insensitive matching key."""
    if "%" in name:
        name = urllib.parse.unquote(name)
    return name.replace("_", " ").strip().lower()


def extension_of(name):
    return os.path.splitext(name)[1].lower() or "(none)"


# --------------------------------------------------------------------------
# Manifest building and output
# --------------------------------------------------------------------------

def build_manifest(references, occurrences, json_path, xml_path=None,
                   missing_from_json=None, missing_from_xml=None):
    filenames = sorted(references, key=canonical_key)

    extensions = {}
    for name in filenames:
        extension = extension_of(name)
        extensions[extension] = extensions.get(extension, 0) + 1

    files = []
    for name in filenames:
        entry = references[name]
        files.append({
            "filename": name,
            "occurrences": entry["occurrences"],
            "articles": sorted(entry["articles"]),
            "forms": entry["forms"],
            "extension": extension_of(name),
            "variants": normalization_variants(name),
        })

    manifest = {
        "source": os.path.basename(json_path),
        "total_occurrences": occurrences,
        "distinct_files": len(filenames),
        "extensions": dict(sorted(extensions.items())),
        "files": files,
    }
    if xml_path is not None:
        manifest["xml_cross_check"] = {
            "source": os.path.basename(xml_path),
            "missing_from_json": missing_from_json or [],
            "missing_from_xml": missing_from_xml or [],
        }
    return manifest


def write_manifest(manifest, txt_path, json_path):
    with open(txt_path, "w", encoding="utf-8") as handle:
        for entry in manifest["files"]:
            handle.write(entry["filename"] + "\n")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def print_summary(manifest):
    print("Placeholder occurrences: {0}".format(manifest["total_occurrences"]))
    print("Distinct filenames:      {0}".format(manifest["distinct_files"]))
    print("Extensions:")
    for extension, count in manifest["extensions"].items():
        print("  {0}: {1}".format(extension, count))

    cross = manifest.get("xml_cross_check")
    if cross:
        print("XML cross-check against {0}:".format(cross["source"]))
        print("  in XML but missing from JSON: {0}".format(
            len(cross["missing_from_json"])))
        for name in cross["missing_from_json"]:
            print("    {0}".format(name))
        print("  in JSON but missing from XML: {0}".format(
            len(cross["missing_from_xml"])))
        for name in cross["missing_from_xml"]:
            print("    {0}".format(name))

    top = sorted(manifest["files"],
                 key=lambda entry: (-entry["occurrences"],
                                    canonical_key(entry["filename"])))[:10]
    if top:
        print("Top 10 most referenced files:")
        for entry in top:
            print("  {0} x{1}".format(entry["filename"], entry["occurrences"]))


# --------------------------------------------------------------------------
# Optional matching against a local media directory
# --------------------------------------------------------------------------

def index_local_directory(directory):
    """Map canonical filename keys to the files found under `directory`."""
    index = {}
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            index.setdefault(canonical_key(filename), os.path.join(root, filename))
    return index


def check_local(manifest, directory):
    """Match the manifest against a local media directory.

    Returns a report dict; an absent directory is not an error, it simply
    yields a report with `"directory_exists": False`.
    """
    report = {
        "directory": directory,
        "directory_exists": os.path.isdir(directory),
        "found": [],
        "missing": [],
        "matched_bytes": 0,
    }
    if not report["directory_exists"]:
        report["missing"] = [entry["filename"] for entry in manifest["files"]]
        return report

    index = index_local_directory(directory)
    for entry in manifest["files"]:
        path = None
        for variant in entry["variants"]:
            path = index.get(canonical_key(variant))
            if path:
                break
        if path is None:
            report["missing"].append(entry["filename"])
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        report["matched_bytes"] += size
        report["found"].append({
            "filename": entry["filename"],
            "path": path,
            "size": size,
        })

    report["indexed_local_files"] = len(index)
    return report


def print_local_report(report):
    if not report["directory_exists"]:
        print("Local media directory not found: {0} (skipping match)".format(
            report["directory"]))
        return
    print("Local media directory: {0}".format(report["directory"]))
    print("  indexed local files: {0}".format(report["indexed_local_files"]))
    print("  found:   {0}".format(len(report["found"])))
    print("  missing: {0}".format(len(report["missing"])))
    for name in report["missing"]:
        print("    {0}".format(name))
    print("  matched size: {0} bytes ({1:.1f} MB)".format(
        report["matched_bytes"], report["matched_bytes"] / (1024.0 * 1024.0)))


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def parse_args(argv):
    options = {
        "json": DEFAULT_JSON,
        "xml": DEFAULT_XML,
        "check_local": None,
        "match_report": None,
    }
    positional = []
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument in ("--xml", "--check-local", "--match-report"):
            if index + 1 >= len(argv):
                raise ValueError("{0} requires a value".format(argument))
            options[argument[2:].replace("-", "_")] = argv[index + 1]
            index += 2
            continue
        if argument in ("-h", "--help"):
            options["help"] = True
            index += 1
            continue
        positional.append(argument)
        index += 1
    if positional:
        options["json"] = positional[0]
    return options


def main(argv):
    try:
        options = parse_args(argv)
    except ValueError as error:
        print("Error: {0}".format(error))
        return 2

    if options.get("help"):
        print(__doc__)
        return 0

    json_path = options["json"]
    xml_path = options["xml"]

    references, occurrences = collect_from_json(json_path)

    missing_from_json = missing_from_xml = None
    if xml_path and os.path.exists(xml_path):
        xml_counts = collect_from_xml(xml_path)
        missing_from_json, missing_from_xml = cross_check(references, xml_counts)
    else:
        if xml_path:
            print("XML export not found: {0} (skipping cross-check)".format(xml_path))
        xml_path = None

    manifest = build_manifest(references, occurrences, json_path, xml_path,
                              missing_from_json, missing_from_xml)
    write_manifest(manifest, DEFAULT_TXT_OUTPUT, DEFAULT_JSON_OUTPUT)
    print("Wrote {0} and {1}".format(DEFAULT_TXT_OUTPUT, DEFAULT_JSON_OUTPUT))
    print_summary(manifest)

    if options["check_local"]:
        report = check_local(manifest, options["check_local"])
        print_local_report(report)
        report_path = options["match_report"]
        if report_path is None and report["directory_exists"]:
            report_path = DEFAULT_MATCH_REPORT
        if report_path:
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            print("Wrote {0}".format(report_path))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

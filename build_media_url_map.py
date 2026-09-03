#!/usr/bin/env python3
"""Build `media_url_map.json` from the Genesys Response Asset search pages.

Reads every `ResponseAssetSearchRequest*.json` page (the response of
`POST /api/v2/responsemanagement/responseassets/search`), merges their
`results` arrays and keys each asset by the canonical filename key used by
`extract_media_manifest.py`, so that the uploaded `750px-Koer_redigerad_v2.jpg`
matches the article's `750px-Koer redigerad v2.jpg`.

Non-image files that were *not* uploaded as Response Assets fall back to the
`sharingUri` of the older Content Management listing (`genesys_url.json`);
those can only ever be linked to, never embedded.

Usage:
    python3 build_media_url_map.py [--assets GLOB] [--required required_media.txt]
                                   [--content-management genesys_url.json]
                                   [--output media_url_map.json]
                                   [--coverage media_url_coverage.json]

Only the Python standard library is used.
"""

import glob as globmodule
import json
import os
import sys

from extract_media_manifest import canonical_key

DEFAULT_ASSET_GLOB = "ResponseAssetSearchRequest*.json"
DEFAULT_REQUIRED = "required_media.txt"
DEFAULT_CONTENT_MANAGEMENT = "genesys_url.json"
DEFAULT_OUTPUT = "media_url_map.json"
DEFAULT_COVERAGE = "media_url_coverage.json"


def load_assets(pattern):
    """Merge the `results` arrays of every matching page, de-duplicated by id."""
    assets = []
    seen = set()
    for path in sorted(globmodule.glob(pattern)):
        with open(path, encoding="utf-8") as handle:
            page = json.load(handle)
        for asset in page.get("results") or []:
            asset_id = asset.get("id")
            if asset_id in seen:
                continue
            seen.add(asset_id)
            assets.append(asset)
    return assets


def load_required(path):
    """Return the required filenames, one per line."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def load_content_management(path):
    """Return canonical key -> (name, sharingUri, contentType) for the older
    Content Management listing, used as a fallback for non-image files."""
    entries = {}
    if not os.path.exists(path):
        return entries
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    for entity in data.get("entities") or []:
        name = entity.get("name") or entity.get("filename")
        uri = entity.get("sharingUri")
        if not name or not uri:
            continue
        entries[canonical_key(name)] = {
            "name": name,
            "url": uri,
            "contentType": entity.get("contentType") or "",
        }
    return entries


def index_assets(assets):
    """Key the assets by canonical filename.

    When two assets canonicalize to the same key the most recently created one
    wins; every such collision is reported.
    """
    index = {}
    collisions = []
    for asset in assets:
        name = asset.get("name") or ""
        if not name:
            continue
        key = canonical_key(name)
        current = index.get(key)
        if current is None:
            index[key] = asset
            continue
        winner, loser = current, asset
        if (asset.get("dateCreated") or "") > (current.get("dateCreated") or ""):
            winner, loser = asset, current
        index[key] = winner
        collisions.append({
            "key": key,
            "kept": {"assetId": winner.get("id"), "name": winner.get("name"),
                     "dateCreated": winner.get("dateCreated")},
            "dropped": {"assetId": loser.get("id"), "name": loser.get("name"),
                        "dateCreated": loser.get("dateCreated")},
        })
    return index, collisions


def build_map(assets, required, content_management):
    """Return (media_url_map, coverage report)."""
    index, collisions = index_assets(assets)

    required_keys = {canonical_key(name): name for name in required}

    media_map = {}
    mapped = []
    for key, asset in index.items():
        content_type = asset.get("contentType") or ""
        # Prefer the spelling used by the articles so the map stays readable.
        display = required_keys.get(key) or (asset.get("name") or "").replace("_", " ")
        media_map[display] = {
            "url": asset.get("contentLocation"),
            "contentType": content_type,
            "assetId": asset.get("id"),
            "isImage": content_type.startswith("image/"),
        }
        if key in required_keys:
            mapped.append(required_keys[key])

    missing = [name for key, name in sorted(required_keys.items()) if key not in index]
    extra = sorted((index[key].get("name") or "") for key in index
                   if key not in required_keys)

    # Non-image files that never made it into Response Assets can still be
    # linked to through the Content Management viewer.
    fallbacks = []
    for key in [canonical_key(name) for name in missing]:
        entry = content_management.get(key)
        if not entry or entry["contentType"].startswith("image/"):
            continue
        display = required_keys[key]
        media_map[display] = {
            "url": entry["url"],
            "contentType": entry["contentType"],
            "source": "contentManagement",
            "isImage": False,
        }
        fallbacks.append(display)

    coverage = {
        "requiredFiles": len(required),
        "assets": len(assets),
        "mappedFromResponseAssets": len(mapped),
        "mappedFromContentManagement": len(fallbacks),
        "images": sum(1 for entry in media_map.values() if entry["isImage"]),
        "nonImages": sum(1 for entry in media_map.values() if not entry["isImage"]),
        "unmapped": [name for name in missing if name not in fallbacks],
        "contentManagementFallbacks": fallbacks,
        "extraAssetsNotReferenced": extra,
        "collisions": collisions,
    }
    return media_map, coverage


def print_summary(coverage):
    print("Response assets:        {0}".format(coverage["assets"]))
    print("Required files:         {0}".format(coverage["requiredFiles"]))
    print("Mapped (response asset):{0}".format(coverage["mappedFromResponseAssets"]))
    print("Mapped (content mgmt):  {0}".format(coverage["mappedFromContentManagement"]))
    print("  images:               {0}".format(coverage["images"]))
    print("  non-images:           {0}".format(coverage["nonImages"]))
    print("Unmapped:               {0}".format(len(coverage["unmapped"])))
    for name in coverage["unmapped"]:
        print("  {0}".format(name))
    print("Extra assets not referenced: {0}".format(
        len(coverage["extraAssetsNotReferenced"])))
    for name in coverage["extraAssetsNotReferenced"]:
        print("  {0}".format(name))
    if coverage["collisions"]:
        print("Canonical key collisions: {0}".format(len(coverage["collisions"])))
        for collision in coverage["collisions"]:
            print("  {0}: kept {1} ({2}), dropped {3} ({4})".format(
                collision["key"], collision["kept"]["assetId"],
                collision["kept"]["dateCreated"], collision["dropped"]["assetId"],
                collision["dropped"]["dateCreated"]))


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def parse_args(argv):
    options = {
        "assets": DEFAULT_ASSET_GLOB,
        "required": DEFAULT_REQUIRED,
        "content-management": DEFAULT_CONTENT_MANAGEMENT,
        "output": DEFAULT_OUTPUT,
        "coverage": DEFAULT_COVERAGE,
    }
    index = 1
    while index < len(argv):
        argument = argv[index]
        name = argument[2:] if argument.startswith("--") else None
        if name not in options:
            raise SystemExit("Unknown argument: {0}".format(argument))
        index += 1
        if index >= len(argv):
            raise SystemExit("Missing value for {0}".format(argument))
        options[name] = argv[index]
        index += 1
    return options


def main(argv):
    options = parse_args(argv)
    assets = load_assets(options["assets"])
    if not assets:
        print("No response assets found for pattern {0}".format(options["assets"]))
        return 1
    required = load_required(options["required"])
    content_management = load_content_management(options["content-management"])

    media_map, coverage = build_map(assets, required, content_management)
    write_json(options["output"], media_map)
    write_json(options["coverage"], coverage)

    print_summary(coverage)
    print("Wrote {0} ({1} entries) and {2}".format(
        options["output"], len(media_map), options["coverage"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

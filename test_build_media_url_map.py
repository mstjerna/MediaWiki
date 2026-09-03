#!/usr/bin/env python3
"""Unit tests for the Response Asset -> media URL map builder.

Run with:
    python3 -m unittest test_build_media_url_map -v
"""

import json
import os
import tempfile
import unittest

import build_media_url_map as builder


def asset(name, asset_id, content_type="image/png", created="2026-09-03T10:00:00Z"):
    return {
        "id": asset_id,
        "name": name,
        "contentType": content_type,
        "contentLength": 1,
        "contentLocation": "https://api-cdn.example/{0}".format(asset_id),
        "dateCreated": created,
    }


class LoadAssetsTests(unittest.TestCase):
    def test_pages_are_merged_and_deduplicated_by_id(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, results in enumerate([
                    [asset("A.png", "1"), asset("B.png", "2")],
                    [asset("B.png", "2"), asset("C.png", "3")]], start=1):
                path = os.path.join(directory,
                                    "ResponseAssetSearchRequest{0}.json".format(index))
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"results": results}, handle)
            assets = builder.load_assets(
                os.path.join(directory, "ResponseAssetSearchRequest*.json"))
        self.assertEqual([a["id"] for a in assets], ["1", "2", "3"])


class BuildMapTests(unittest.TestCase):
    def test_canonical_key_matches_underscore_and_space(self):
        media, coverage = builder.build_map(
            [asset("750px-Koer_redigerad_v2.jpg", "1", "image/jpeg")],
            ["750px-Koer redigerad v2.jpg"], {})
        self.assertEqual(media, {"750px-Koer redigerad v2.jpg": {
            "url": "https://api-cdn.example/1",
            "contentType": "image/jpeg",
            "assetId": "1",
            "isImage": True,
        }})
        self.assertEqual(coverage["mappedFromResponseAssets"], 1)
        self.assertEqual(coverage["unmapped"], [])

    def test_documents_are_not_images(self):
        media, coverage = builder.build_map(
            [asset("Lathund_konkurs.pdf", "1", "application/pdf")],
            ["Lathund konkurs.pdf"], {})
        self.assertFalse(media["Lathund konkurs.pdf"]["isImage"])
        self.assertEqual(coverage["nonImages"], 1)

    def test_missing_and_extra_assets_are_reported(self):
        _, coverage = builder.build_map(
            [asset("substitution.png", "1")], ["Saknas.png"], {})
        self.assertEqual(coverage["unmapped"], ["Saknas.png"])
        self.assertEqual(coverage["extraAssetsNotReferenced"], ["substitution.png"])

    def test_collision_prefers_most_recent(self):
        media, coverage = builder.build_map(
            [asset("Exempel_redovisat.png", "old", created="2026-09-03T12:01:00Z"),
             asset("Exempel redovisat.png", "new", created="2026-09-03T12:02:00Z")],
            ["Exempel redovisat.png"], {})
        self.assertEqual(media["Exempel redovisat.png"]["assetId"], "new")
        self.assertEqual(len(coverage["collisions"]), 1)
        self.assertEqual(coverage["collisions"][0]["kept"]["assetId"], "new")
        self.assertEqual(coverage["collisions"][0]["dropped"]["assetId"], "old")

    def test_content_management_fallback_for_documents_only(self):
        content_management = {
            "fullmakt.docx": {"name": "Fullmakt.docx",
                              "url": "https://contentmanagement.example/doc",
                              "contentType": "application/msword"},
            "bild.png": {"name": "Bild.png",
                         "url": "https://contentmanagement.example/img",
                         "contentType": "image/png"},
        }
        media, coverage = builder.build_map(
            [], ["Fullmakt.docx", "Bild.png"], content_management)
        self.assertEqual(media["Fullmakt.docx"]["url"],
                         "https://contentmanagement.example/doc")
        self.assertFalse(media["Fullmakt.docx"]["isImage"])
        self.assertNotIn("Bild.png", media)
        self.assertEqual(coverage["contentManagementFallbacks"], ["Fullmakt.docx"])
        self.assertEqual(coverage["unmapped"], ["Bild.png"])


class CommittedMapTests(unittest.TestCase):
    def test_committed_map_covers_the_required_media(self):
        base = os.path.dirname(__file__) or "."
        path = os.path.join(base, builder.DEFAULT_OUTPUT)
        coverage_path = os.path.join(base, builder.DEFAULT_COVERAGE)
        if not (os.path.exists(path) and os.path.exists(coverage_path)):
            self.skipTest("generated files not present")

        with open(path, encoding="utf-8") as handle:
            media = json.load(handle)
        with open(coverage_path, encoding="utf-8") as handle:
            coverage = json.load(handle)

        self.assertTrue(all(entry.get("url") for entry in media.values()))
        self.assertEqual(
            coverage["mappedFromResponseAssets"] + len(coverage["unmapped"]),
            coverage["requiredFiles"])


if __name__ == "__main__":
    unittest.main()

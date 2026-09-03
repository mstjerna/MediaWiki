#!/usr/bin/env python3
"""Unit tests for the media manifest extractor.

Run with:
    python3 -m unittest test_extract_media_manifest -v
"""

import os
import shutil
import tempfile
import unittest

import convert
import extract_media_manifest as manifest


def document(title, blocks):
    return {"published": {"title": title,
                          "variations": [{"body": {"blocks": blocks}}]}}


class ExtractionTests(unittest.TestCase):
    def test_paragraph_placeholder(self):
        blocks = convert.placeholder_blocks("Foo.jpg")
        self.assertEqual(list(manifest.iter_media_references(blocks)),
                         [("Foo.jpg", "paragraph")])

    def test_compact_placeholder(self):
        text = convert.PLACEHOLDER_COMPACT.format(filename="Bar.png")
        blocks = convert.parse_inline("Se bilden " + text)
        self.assertEqual(list(manifest.iter_media_references(blocks)),
                         [("Bar.png", "compact")])

    def test_nested_blocks_are_found(self):
        nested = {"type": "UnorderedList",
                  "list": {"blocks": [
                      {"blocks": convert.placeholder_blocks("Deep.png")}]}}
        self.assertEqual(list(manifest.iter_media_references(nested)),
                         [("Deep.png", "paragraph")])

    def test_plain_text_is_ignored(self):
        blocks = convert.parse_inline("Ingen bild h\u00e4r alls")
        self.assertEqual(list(manifest.iter_media_references(blocks)), [])

    def test_deduplication_and_article_list(self):
        data = {"documents": [
            document("A", convert.placeholder_blocks("Foo.jpg")
                     + convert.placeholder_blocks("Bar.png")),
            document("B", convert.placeholder_blocks("Foo.jpg")),
        ]}
        path = os.path.join(self.directory, "migration.json")
        with open(path, "w", encoding="utf-8") as handle:
            import json
            json.dump(data, handle, ensure_ascii=False)

        references, occurrences = manifest.collect_from_json(path)
        self.assertEqual(occurrences, 3)
        self.assertEqual(sorted(references), ["Bar.png", "Foo.jpg"])
        self.assertEqual(references["Foo.jpg"]["occurrences"], 2)
        self.assertEqual(references["Foo.jpg"]["articles"], ["A", "B"])
        self.assertEqual(references["Bar.png"]["articles"], ["A"])

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)


class NormalizationTests(unittest.TestCase):
    def test_space_and_underscore_variants(self):
        variants = manifest.normalization_variants("min bild.png")
        self.assertIn("min bild.png", variants)
        self.assertIn("min_bild.png", variants)
        self.assertIn("Min bild.png", variants)
        self.assertIn("Min_bild.png", variants)

    def test_lowercase_variant(self):
        self.assertIn("foo bar.png",
                      manifest.normalization_variants("Foo Bar.PNG".lower()))
        self.assertIn("foo bar.png", manifest.normalization_variants("Foo bar.png"))

    def test_url_decoded_variant(self):
        variants = manifest.normalization_variants("Min%20bild.png")
        self.assertIn("Min%20bild.png", variants)
        self.assertIn("Min bild.png", variants)

    def test_canonical_key(self):
        self.assertEqual(manifest.canonical_key("Min_Bild.PNG"), "min bild.png")
        self.assertEqual(manifest.canonical_key("Min%20Bild.png"), "min bild.png")


class XmlCrossCheckTests(unittest.TestCase):
    def test_filenames_in_wikitext(self):
        wikitext = (
            "[[File:One.png|thumb|En bild]]\n"
            "[[Fil:Two.jpg]] [[Image:Three.gif|300px]] [[Bild:Four.png]]\n"
            "<gallery>\nFile:Five.png|Bildtext\nSix.jpg\n</gallery>\n"
            "<!-- [[File:Commented.png]] -->\n")
        self.assertEqual(
            manifest.filenames_in_wikitext(wikitext),
            ["One.png", "Two.jpg", "Three.gif", "Four.png",
             "Five.png", "Six.jpg"])

    def test_cross_check_reports_both_directions(self):
        missing_from_json, missing_from_xml = manifest.cross_check(
            {"Only_in json.png": {}, "Shared.png": {}},
            {"shared.png": 1, "OnlyInXml.jpg": 1})
        self.assertEqual(missing_from_json, ["OnlyInXml.jpg"])
        self.assertEqual(missing_from_xml, ["Only_in json.png"])

    def test_cross_check_ignores_case_and_underscores(self):
        self.assertEqual(manifest.cross_check({"My_bild.png": {}},
                                              {"My bild.png": 1}),
                         ([], []))


class LocalCheckTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)

    def test_matches_with_normalization(self):
        nested = os.path.join(self.directory, "sub")
        os.makedirs(nested)
        with open(os.path.join(nested, "Min_bild.PNG"), "wb") as handle:
            handle.write(b"1234")

        built = manifest.build_manifest(
            {"min bild.png": {"articles": ["A"], "occurrences": 1, "forms": {}},
             "Saknas.jpg": {"articles": ["A"], "occurrences": 1, "forms": {}}},
            2, "migration.json")
        report = manifest.check_local(built, self.directory)

        self.assertTrue(report["directory_exists"])
        self.assertEqual([entry["filename"] for entry in report["found"]],
                         ["min bild.png"])
        self.assertEqual(report["missing"], ["Saknas.jpg"])
        self.assertEqual(report["matched_bytes"], 4)

    def test_missing_directory_is_not_an_error(self):
        built = manifest.build_manifest(
            {"Foo.png": {"articles": ["A"], "occurrences": 1, "forms": {}}},
            1, "migration.json")
        report = manifest.check_local(built, os.path.join(self.directory, "nope"))
        self.assertFalse(report["directory_exists"])
        self.assertEqual(report["missing"], ["Foo.png"])


class ManifestTests(unittest.TestCase):
    def test_manifest_shape(self):
        built = manifest.build_manifest(
            {"Foo.PNG": {"articles": ["B", "A"], "occurrences": 2,
                         "forms": {"paragraph": 2}},
             "Bar.pdf": {"articles": ["A"], "occurrences": 1,
                         "forms": {"compact": 1}}},
            3, "migration.json")
        self.assertEqual(built["total_occurrences"], 3)
        self.assertEqual(built["distinct_files"], 2)
        self.assertEqual(built["extensions"], {".pdf": 1, ".png": 1})
        self.assertEqual([entry["filename"] for entry in built["files"]],
                         ["Bar.pdf", "Foo.PNG"])
        self.assertEqual(built["files"][1]["articles"], ["A", "B"])
        self.assertIn("Foo.PNG", built["files"][1]["variants"])


class GeneratedManifestTests(unittest.TestCase):
    def test_committed_manifest_matches_output(self):
        base = os.path.dirname(__file__) or "."
        migration = os.path.join(base, manifest.DEFAULT_JSON)
        committed = os.path.join(base, manifest.DEFAULT_TXT_OUTPUT)
        if not (os.path.exists(migration) and os.path.exists(committed)):
            self.skipTest("generated files not present")

        references, occurrences = manifest.collect_from_json(migration)
        built = manifest.build_manifest(references, occurrences, migration)
        with open(committed, encoding="utf-8") as handle:
            names = [line.rstrip("\n") for line in handle if line.strip()]
        self.assertEqual(names, [entry["filename"] for entry in built["files"]])


if __name__ == "__main__":
    unittest.main()

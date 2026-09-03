#!/usr/bin/env python3
"""Unit tests for the MediaWiki -> Genesys Knowledge converter.

Run with:
    python3 -m unittest test_convert -v
"""

import unittest

import convert


def texts(blocks):
    return [(b["text"]["text"], tuple(b["text"].get("marks", [])),
             b["text"].get("hyperlink")) for b in blocks]


class InlineTests(unittest.TestCase):
    def test_bold_and_italic(self):
        self.assertEqual(
            texts(convert.parse_inline("Hej '''fet''' och ''kursiv''")),
            [("Hej ", (), None), ("fet", ("Bold",), None),
             (" och ", (), None), ("kursiv", ("Italic",), None)],
        )

    def test_external_link_keeps_whitespace(self):
        self.assertEqual(
            texts(convert.parse_inline("Länk till [https://foo.com Data Protection Policy]")),
            [("Länk till ", (), None),
             ("Data Protection Policy", (), "https://foo.com")],
        )

    def test_internal_links(self):
        self.assertEqual(
            texts(convert.parse_inline("[[Skuldsanering]] och [[Avliden|Dödsbo]]")),
            [("Skuldsanering och Dödsbo", (), None)],
        )

    def test_file_links_are_dropped(self):
        # parse_inline() itself still drops file links: placeholder handling
        # happens one level up, in wikitext_to_blocks()/row_to_blocks(), which
        # split file links out before calling parse_inline() on the rest.
        self.assertEqual(
            texts(convert.parse_inline("Text [[File:Bild.jpg|thumb|300px]]")),
            [("Text", (), None)],
        )

    def test_html_is_stripped_and_mapped(self):
        self.assertEqual(
            texts(convert.parse_inline("A<br>B <strong>C</strong> <span class=\"x\">D</span>")),
            [("A B ", (), None), ("C", ("Bold",), None), (" D", (), None)],
        )


class BlockTests(unittest.TestCase):
    def test_heading_becomes_bold_paragraph(self):
        blocks, _ = convert.wikitext_to_blocks("== Rubrik ==")
        self.assertEqual(blocks[0]["type"], "Paragraph")
        self.assertEqual(texts(blocks[0]["paragraph"]["blocks"]),
                         [("Rubrik", ("Bold",), None)])

    def test_lists(self):
        blocks, _ = convert.wikitext_to_blocks("* ett\n* två\n\n# a\n# b")
        self.assertEqual([b["type"] for b in blocks], ["UnorderedList", "OrderedList"])
        self.assertEqual(len(blocks[0]["list"]["blocks"]), 2)


class TableTests(unittest.TestCase):
    def convert_table(self, wikitext):
        blocks, tables = convert.wikitext_to_blocks(wikitext)
        self.assertEqual(tables, 1)
        return blocks

    def test_two_column_table(self):
        blocks = self.convert_table(
            '{| class="wikitable"\n|-\n|tyska\n|de@myntro.ch\n|}')
        items = blocks[-1]["list"]["blocks"]
        self.assertEqual(blocks[-1]["type"], "UnorderedList")
        self.assertEqual(texts(items[0]["blocks"]),
                         [("tyska", ("Bold",), None), (": de@myntro.ch", (), None)])

    def test_repeated_country_columns(self):
        blocks = self.convert_table(
            '{| class="wikitable"\n!Land\n!Kod\n!Land\n!Kod\n'
            '|-\n|Afghanistan\n|AF\n|Albanien\n|AL\n|}')
        items = blocks[-1]["list"]["blocks"]
        self.assertEqual(len(items), 2)
        self.assertEqual(texts(items[1]["blocks"]),
                         [("Albanien", ("Bold",), None), (": AL", (), None)])

    def test_three_column_table(self):
        blocks = self.convert_table(
            '{| class="wikitable"\n|-\n|Collector Inkasso\n|291 (1-100)\n'
            '|Utländska ärenden\n|}')
        items = blocks[-1]["list"]["blocks"]
        self.assertEqual(
            texts(items[0]["blocks"]),
            [("Collector Inkasso", ("Bold",), None),
             (" (291 (1-100)) - Utländska ärenden", (), None)],
        )

    def test_cell_attributes_are_removed(self):
        blocks = self.convert_table(
            '{| class="wikitable"\n|-\n| style="text-align:left;" |#\n|Win\n|}')
        items = blocks[-1]["list"]["blocks"]
        self.assertEqual(texts(items[0]["blocks"]),
                         [("#", ("Bold",), None), (": Win", (), None)])


class ImagePlaceholderTests(unittest.TestCase):
    def paragraph_texts(self, blocks):
        """Flatten a run of Paragraph blocks into a list of joined strings,
        one per paragraph (empty string for blank placeholder paragraphs)."""
        result = []
        for block in blocks:
            self.assertEqual(block["type"], "Paragraph")
            result.append("".join(t["text"]["text"]
                                   for t in block["paragraph"]["blocks"]))
        return result

    def assert_placeholder(self, blocks, filename):
        self.assertEqual(
            self.paragraph_texts(blocks),
            [convert.PLACEHOLDER_TITLE, "", convert.PLACEHOLDER_LABEL,
             filename, "", convert.PLACEHOLDER_FOOTER],
        )
        self.assertEqual(blocks[0]["paragraph"]["blocks"][0]["text"]["marks"],
                          ["Bold"])

    def test_standalone_image(self):
        blocks, _ = convert.wikitext_to_blocks(
            "[[File:Status-Abroad.jpg|none|thumb|396x396px]]")
        self.assert_placeholder(blocks, "Status-Abroad.jpg")

    def test_inline_image_keeps_surrounding_text(self):
        blocks, _ = convert.wikitext_to_blocks(
            "Se bilden [[File:Foo.jpg|thumb|300px]] för mer information.")
        self.assertEqual(blocks[0]["type"], "Paragraph")
        self.assertEqual(texts(blocks[0]["paragraph"]["blocks"]),
                          [("Se bilden", (), None)])
        self.assert_placeholder(blocks[1:7], "Foo.jpg")
        self.assertEqual(texts(blocks[7]["paragraph"]["blocks"]),
                          [("för mer information.", (), None)])

    def test_image_with_nested_caption(self):
        blocks, _ = convert.wikitext_to_blocks(
            "[[File:A.png|thumb|see [[Ackord]]]]")
        self.assert_placeholder(blocks, "A.png")

    def test_gallery(self):
        blocks, _ = convert.wikitext_to_blocks(
            "<gallery mode=\"slideshow\">\n"
            "File:One.png\n"
            "File:Two.png|caption text\n"
            "</gallery>")
        self.assert_placeholder(blocks[0:6], "One.png")
        self.assert_placeholder(blocks[6:12], "Two.png")

    def test_image_in_table_cell(self):
        blocks, tables = convert.wikitext_to_blocks(
            '{| class="wikitable"\n|-\n|Screenshot\n'
            '|[[File:Foo.jpg|thumb|300px]]\n|}')
        self.assertEqual(tables, 1)
        item = blocks[-1]["list"]["blocks"][0]
        joined = "".join(t["text"]["text"] for t in item["blocks"])
        self.assertIn("BILD SAKNAS", joined)
        self.assertIn("Foo.jpg", joined)
        self.assertNotIn("[[File:", joined)

    def test_alternate_namespace_prefixes(self):
        for prefix in ("File", "Fil", "Image", "Bild", "file", "BILD"):
            with self.subTest(prefix=prefix):
                blocks, _ = convert.wikitext_to_blocks(
                    "[[{0}:Pic.jpg|thumb]]".format(prefix))
                self.assert_placeholder(blocks, "Pic.jpg")


class CategoryTests(unittest.TestCase):
    def test_first_category_wins(self):
        categories, rest = convert.extract_categories(
            "Text [[Category:Backoffice]][[Category:Telefoni]][[Category:Sverige]]")
        self.assertEqual(categories, ["Backoffice", "Telefoni", "Sverige"])
        self.assertEqual(rest.strip(), "Text")


if __name__ == "__main__":
    unittest.main()

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


class CategoryTests(unittest.TestCase):
    def test_first_category_wins(self):
        categories, rest = convert.extract_categories(
            "Text [[Category:Backoffice]][[Category:Telefoni]][[Category:Sverige]]")
        self.assertEqual(categories, ["Backoffice", "Telefoni", "Sverige"])
        self.assertEqual(rest.strip(), "Text")


if __name__ == "__main__":
    unittest.main()

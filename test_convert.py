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
        """Flatten a run of Paragraph blocks into one string per paragraph."""
        result = []
        for block in blocks:
            self.assertEqual(block["type"], "Paragraph")
            result.append("".join(t["text"]["text"]
                                   for t in block["paragraph"]["blocks"]))
        return result

    def assert_placeholder(self, blocks, filename):
        self.assertEqual(
            self.paragraph_texts(blocks),
            [convert.PLACEHOLDER_TITLE, convert.PLACEHOLDER_LABEL + " " + filename,
             "\U0001F534 " + convert.PLACEHOLDER_FOOTER + " \U0001F534"],
        )
        self.assertEqual(blocks[0]["paragraph"]["blocks"][0]["text"]["marks"],
                          ["Bold"])
        self.assertEqual(
            blocks[1]["paragraph"]["blocks"][1]["text"]["marks"], ["Bold"])
        self.assertEqual(
            blocks[2]["paragraph"]["blocks"][1]["text"]["marks"], ["Italic"])
        for block in blocks:
            self.assertTrue(block["paragraph"]["blocks"])

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
        self.assert_placeholder(blocks[1:4], "Foo.jpg")
        self.assertEqual(texts(blocks[4]["paragraph"]["blocks"]),
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
        self.assert_placeholder(blocks[0:3], "One.png")
        self.assert_placeholder(blocks[3:6], "Two.png")

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
        self.assertEqual(item["blocks"][2]["text"]["marks"], ["Bold"])
        self.assertEqual(item["blocks"][4]["text"]["marks"], ["Italic"])

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


def find_empty_block_lists(blocks):
    """Recursively collect any empty `blocks` array found in a block tree
    (Paragraph.paragraph.blocks, List.list.blocks, ListItem.blocks)."""
    empties = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "Paragraph":
            if not block["paragraph"]["blocks"]:
                empties.append(block)
        elif block_type in ("OrderedList", "UnorderedList"):
            items = block["list"]["blocks"]
            if not items:
                empties.append(block)
            for item in items:
                if not item.get("blocks"):
                    empties.append(item)
    return empties


class SanitizationTests(unittest.TestCase):
    def test_no_empty_paragraph_blocks_from_blank_source(self):
        wikitext = (
            "Rubrik\n\n\n"
            "'''' ''''\n\n"
            "----\n\n"
            "<!-- a comment -->\n\n"
            "<span class=\"x\"></span>\n\n"
            "Sista raden."
        )
        blocks, _ = convert.wikitext_to_blocks(wikitext)
        self.assertEqual(find_empty_block_lists(blocks), [])

    def test_sanitize_blocks_drops_empty_paragraph(self):
        blocks = [convert.paragraph([]), convert.paragraph(
            [convert.text_block("kept")])]
        sanitized = convert.sanitize_blocks(blocks)
        self.assertEqual(len(sanitized), 1)
        self.assertEqual(find_empty_block_lists(sanitized), [])

    def test_placeholder_blocks_have_no_empty_paragraphs(self):
        blocks = convert.placeholder_blocks("Foo.jpg")
        self.assertEqual(find_empty_block_lists(blocks), [])


class GeneratedOutputTests(unittest.TestCase):
    def test_output_file_has_no_empty_block_lists(self):
        import os
        path = os.path.join(os.path.dirname(__file__) or ".",
                             convert.DEFAULT_OUTPUT)
        if not os.path.exists(path):
            self.skipTest("{0} not present".format(path))
        violations = convert.validate_file(path)
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()

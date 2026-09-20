import unittest

from services.pdf_service import sanitize_pdf_text


class PdfTextSanitizationTests(unittest.TestCase):
    def test_replaces_lone_surrogates_with_valid_utf8_marker(self):
        dirty = "formula: \ud835x + \udfff"

        clean = sanitize_pdf_text(dirty)

        self.assertEqual(clean, "formula: \ufffdx + \ufffd")
        self.assertEqual(clean.encode("utf-8").decode("utf-8"), clean)

    def test_preserves_normal_multilingual_text(self):
        text = "注意力 Attention · α²"
        self.assertEqual(sanitize_pdf_text(text), text)


if __name__ == "__main__":
    unittest.main()

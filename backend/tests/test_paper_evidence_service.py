from __future__ import annotations

import copy
import hashlib
from io import BytesIO
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
)


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.paper_evidence_service import (
    EvidenceValidationError,
    build_evidence_manifest,
    build_pdf_evidence,
    load_evidence_manifest,
    validate_evidence_manifest,
    write_evidence_manifest,
)


SOURCE_HASH = hashlib.sha256(b"synthetic-pdf").hexdigest()


def _type1_unknown_glyph_pdf() -> bytes:
    """Build a one-page PDF that triggers pypdf 5.1.0's Type1 cmap bug."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font_file = DecodedStreamObject()
    font_file.set_data(
        b"%!PS-AdobeFont-1.0\n/Encoding 256 array\n"
        b"dup 65 /KnowraUnknownGlyph put\n"
        b"eexec\n"
    )
    font_file[NameObject("/Length1")] = NumberObject(len(font_file.get_data()))
    font_file_ref = writer._add_object(font_file)
    descriptor = DictionaryObject({
        NameObject("/Type"): NameObject("/FontDescriptor"),
        NameObject("/FontName"): NameObject("/Helvetica"),
        NameObject("/Flags"): NumberObject(32),
        NameObject("/FontBBox"): ArrayObject([
            NumberObject(0), NumberObject(-200), NumberObject(1000), NumberObject(900)
        ]),
        NameObject("/ItalicAngle"): NumberObject(0),
        NameObject("/Ascent"): NumberObject(800),
        NameObject("/Descent"): NumberObject(-200),
        NameObject("/CapHeight"): NumberObject(700),
        NameObject("/StemV"): NumberObject(80),
        NameObject("/FontFile"): font_file_ref,
    })
    descriptor_ref = writer._add_object(descriptor)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/FirstChar"): NumberObject(32),
        NameObject("/LastChar"): NumberObject(126),
        NameObject("/Widths"): ArrayObject([FloatObject(600)] * 95),
        NameObject("/FontDescriptor"): descriptor_ref,
    })
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
    })
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 72 720 Td (A) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _pages() -> list[dict]:
    return [
        {
            "page_number": 1,
            "status": "ok",
            "text": "Abstract\nA compact synthetic abstract.\n\n1. Introduction\nIntro evidence.",
        },
        {
            "page_number": 2,
            "status": "ok",
            "text": "2. Methods\n" + ("method evidence sentence. " * 80),
        },
        {"page_number": 3, "status": "empty", "text": ""},
        {
            "page_number": 4,
            "status": "error",
            "error_type": "SyntheticPageError",
        },
    ]


def _manifest(**overrides):
    arguments = {
        "paper_id": "paper:synthetic",
        "source_file_hash": SOURCE_HASH,
        "source_size_bytes": 128,
        "total_pages": 4,
        "pages": _pages(),
        "parser_version": "pypdf-synthetic",
        "chunk_chars": 300,
        "overlap_chars": 30,
    }
    arguments.update(overrides)
    return build_evidence_manifest(**arguments)


class PaperEvidenceServiceTests(unittest.TestCase):
    def test_type1_cmap_bug_uses_versioned_page_fallback_without_data_loss(self):
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "type1-unknown-glyph.pdf"
            pdf.write_bytes(_type1_unknown_glyph_pdf())
            manifest = build_pdf_evidence(pdf, paper_id="paper:type1-fallback")

        self.assertEqual(manifest["coverage"]["failed_pages"], 0)
        self.assertTrue(manifest["coverage"]["page_iteration_complete"])
        page = manifest["pages"][0]
        self.assertEqual(page["status"], "ok")
        self.assertEqual(page["primary_error_type"], "UnboundLocalError")
        self.assertTrue(page["parser_used"].startswith("pypdfium2-"))
        self.assertIsNone(page["fallback_error_type"])
        self.assertTrue(any(chunk["text"] == "A" for chunk in manifest["chunks"]))

    def test_primary_success_never_opens_the_fallback_parser(self):
        page = type("PrimaryPage", (), {"extract_text": lambda self: "primary text"})()
        reader = type("PrimaryReader", (), {"pages": [page]})()
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "primary.pdf"
            pdf.write_bytes(b"synthetic-pdf")
            with (
                patch(
                    "services.paper_evidence_service.pypdf.PdfReader",
                    return_value=reader,
                ),
                patch(
                    "services.paper_evidence_service._open_pdfium_document",
                    side_effect=AssertionError("fallback must stay lazy"),
                ),
            ):
                manifest = build_pdf_evidence(pdf, paper_id="paper:primary")

        self.assertEqual(manifest["coverage"]["failed_pages"], 0)
        self.assertTrue(manifest["pages"][0]["parser_used"].startswith("pypdf-"))
        self.assertIsNone(manifest["pages"][0]["primary_error_type"])

    def test_document_open_failure_can_recover_with_pdfium_and_remains_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "document-fallback.pdf"
            pdf.write_bytes(_type1_unknown_glyph_pdf())
            with patch(
                "services.paper_evidence_service.pypdf.PdfReader",
                side_effect=ValueError("private primary detail"),
            ):
                manifest = build_pdf_evidence(pdf, paper_id="paper:document-fallback")

        self.assertIsNone(manifest["parser"]["document_error_type"])
        self.assertEqual(
            manifest["parser"]["document_primary_error_type"], "ValueError"
        )
        self.assertEqual(manifest["coverage"]["failed_pages"], 0)
        self.assertTrue(manifest["pages"][0]["parser_used"].startswith("pypdfium2-"))
        self.assertNotIn("private primary detail", json.dumps(manifest))

    def test_manifest_preserves_page_coverage_sections_and_failures(self):
        manifest = _manifest()

        self.assertEqual(manifest["coverage"]["source_pages"], 4)
        self.assertEqual(manifest["coverage"]["attempted_pages"], 4)
        self.assertEqual(manifest["coverage"]["ok_pages"], 2)
        self.assertEqual(manifest["coverage"]["empty_pages"], 1)
        self.assertEqual(manifest["coverage"]["failed_pages"], 1)
        self.assertFalse(manifest["coverage"]["page_iteration_complete"])
        self.assertTrue(manifest["coverage"]["has_usable_text"])
        self.assertEqual(manifest["pages"][3]["error_type"], "SyntheticPageError")
        self.assertEqual(manifest["pages"][3]["chunk_ids"], [])

        page_one_sections = set(manifest["pages"][0]["section_kinds"])
        page_two_sections = set(manifest["pages"][1]["section_kinds"])
        self.assertEqual(page_one_sections, {"abstract", "introduction"})
        self.assertEqual(page_two_sections, {"method"})
        self.assertGreater(len(manifest["pages"][1]["chunk_ids"]), 1)
        self.assertEqual(
            manifest["coverage"]["chunk_count"], len(manifest["chunks"])
        )
        validate_evidence_manifest(manifest)

    def test_section_heading_is_inherited_across_pages(self):
        pages = [
            {"page_number": 1, "status": "ok", "text": "3. Results\nFirst result."},
            {"page_number": 2, "status": "ok", "text": "Continuation without heading."},
        ]

        manifest = build_evidence_manifest(
            paper_id="paper:inheritance",
            source_file_hash=SOURCE_HASH,
            source_size_bytes=64,
            total_pages=2,
            pages=pages,
            parser_version="pypdf-synthetic",
            chunk_chars=300,
            overlap_chars=30,
        )

        second_chunk = next(
            chunk for chunk in manifest["chunks"] if chunk["page_number"] == 2
        )
        self.assertEqual(second_chunk["section_kind"], "results")
        self.assertEqual(second_chunk["section_heading"], "3. Results")
        self.assertEqual(second_chunk["section_source"], "inherited")

    def test_manifest_and_chunk_ids_are_deterministic_and_content_addressed(self):
        first = _manifest()
        second = _manifest()
        self.assertEqual(first, second)

        changed_pages = _pages()
        changed_pages[0]["text"] += " Additional evidence."
        changed = _manifest(pages=changed_pages)
        self.assertNotEqual(first["manifest_id"], changed["manifest_id"])
        self.assertNotEqual(first["pages"][0]["text_hash"], changed["pages"][0]["text_hash"])
        self.assertNotEqual(first["pages"][0]["chunk_ids"], changed["pages"][0]["chunk_ids"])

    def test_reader_records_every_page_without_truncation(self):
        class FakePage:
            def __init__(self, text=None, error=None):
                self.text = text
                self.error = error

            def extract_text(self):
                if self.error:
                    raise self.error
                return self.text

        fake_reader = type("FakeReader", (), {
            "pages": [
                FakePage("Abstract\nFirst page."),
                FakePage(error=RuntimeError("private detail must not be persisted")),
                FakePage(""),
                FakePage("Conclusion\nLast-page evidence."),
            ]
        })()
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "synthetic.pdf"
            pdf.write_bytes(b"synthetic-pdf")
            with patch(
                "services.paper_evidence_service.pypdf.PdfReader",
                return_value=fake_reader,
            ):
                manifest = build_pdf_evidence(pdf, paper_id="paper:reader")

        self.assertEqual(manifest["source"]["file_hash_sha256"], SOURCE_HASH)
        self.assertEqual(manifest["coverage"]["source_pages"], 4)
        self.assertEqual(manifest["coverage"]["attempted_pages"], 4)
        self.assertEqual(manifest["coverage"]["ok_pages"], 2)
        self.assertEqual(manifest["coverage"]["empty_pages"], 1)
        self.assertEqual(manifest["coverage"]["failed_pages"], 1)
        self.assertEqual(manifest["pages"][1]["error_type"], "RuntimeError")
        self.assertEqual(
            manifest["pages"][1]["primary_error_type"], "RuntimeError"
        )
        self.assertIsNotNone(manifest["pages"][1]["fallback_error_type"])
        self.assertNotIn("private detail", json.dumps(manifest))
        self.assertTrue(any(
            "Last-page evidence" in chunk["text"] for chunk in manifest["chunks"]
        ))

    def test_page_access_failure_is_recorded_and_later_pages_continue(self):
        class FakePage:
            def __init__(self, text):
                self.text = text

            def extract_text(self):
                return self.text

        class FakePages:
            def __len__(self):
                return 3

            def __getitem__(self, index):
                if index == 1:
                    raise RuntimeError("private page-access detail")
                return FakePage(f"page {index + 1} evidence")

        fake_reader = type("FakeReader", (), {"pages": FakePages()})()
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "synthetic.pdf"
            pdf.write_bytes(b"synthetic-pdf")
            with patch(
                "services.paper_evidence_service.pypdf.PdfReader",
                return_value=fake_reader,
            ):
                manifest = build_pdf_evidence(pdf, paper_id="paper:page-access")

        self.assertEqual(manifest["coverage"]["attempted_pages"], 3)
        self.assertEqual(manifest["coverage"]["failed_pages"], 1)
        self.assertEqual(manifest["pages"][1]["error_type"], "RuntimeError")
        self.assertTrue(any(
            "page 3 evidence" in chunk["text"] for chunk in manifest["chunks"]
        ))
        self.assertNotIn("private page-access detail", json.dumps(manifest))

    def test_document_open_failure_is_a_valid_incomplete_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "synthetic.pdf"
            pdf.write_bytes(b"synthetic-pdf")
            with patch(
                "services.paper_evidence_service.pypdf.PdfReader",
                side_effect=ValueError("sensitive parser detail"),
            ):
                manifest = build_pdf_evidence(pdf, paper_id="paper:broken")

        self.assertEqual(manifest["parser"]["document_error_type"], "ValueError")
        self.assertEqual(
            manifest["parser"]["document_primary_error_type"], "ValueError"
        )
        self.assertIsNotNone(
            manifest["parser"]["document_fallback_error_type"]
        )
        self.assertEqual(manifest["coverage"]["attempted_pages"], 0)
        self.assertIsNone(manifest["coverage"]["source_pages"])
        self.assertFalse(manifest["coverage"]["page_iteration_complete"])
        self.assertFalse(manifest["coverage"]["has_usable_text"])
        self.assertNotIn("sensitive parser detail", json.dumps(manifest))

    def test_source_hash_is_verified_and_all_empty_is_not_usable_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "synthetic.pdf"
            pdf.write_bytes(b"synthetic-pdf")
            with self.assertRaisesRegex(
                EvidenceValidationError, "source hash does not match"
            ):
                build_pdf_evidence(
                    pdf,
                    paper_id="paper:wrong-hash",
                    source_file_hash="0" * 64,
                )

            class SnapshotPage:
                def extract_text(self):
                    return "snapshot evidence"

            def replace_source_after_snapshot(stream):
                pdf.write_bytes(b"replacement-pdf")
                self.assertEqual(stream.read(), b"synthetic-pdf")
                stream.seek(0)
                return type("SnapshotReader", (), {"pages": [SnapshotPage()]})()

            pdf.write_bytes(b"synthetic-pdf")
            with patch(
                "services.paper_evidence_service.pypdf.PdfReader",
                side_effect=replace_source_after_snapshot,
            ):
                snapshot_manifest = build_pdf_evidence(
                    pdf, paper_id="paper:snapshot"
                )
            self.assertEqual(
                snapshot_manifest["source"]["file_hash_sha256"], SOURCE_HASH
            )
            self.assertEqual(
                snapshot_manifest["source"]["size_bytes"], len(b"synthetic-pdf")
            )

        manifest = build_evidence_manifest(
            paper_id="paper:scan",
            source_file_hash=SOURCE_HASH,
            source_size_bytes=128,
            total_pages=2,
            pages=[
                {"page_number": 1, "status": "empty"},
                {"page_number": 2, "status": "empty"},
            ],
            parser_version="pypdf-synthetic",
        )
        self.assertTrue(manifest["coverage"]["page_iteration_complete"])
        self.assertFalse(manifest["coverage"]["has_usable_text"])
        self.assertEqual(manifest["coverage"]["text_page_ratio"], 0.0)

    def test_builder_rejects_invalid_types_as_evidence_errors(self):
        for overrides in (
            {"total_pages": "4"},
            {"pages": [{"page_number": "1", "status": "empty"}]},
            {"pages": ["not-a-page"]},
            {"pages": [{"page_number": 1, "status": "ok", "text": {}}]},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(EvidenceValidationError):
                    _manifest(**overrides)

        for field, value, message in (
            ("pages", ["not-a-page-record"], "page record"),
            ("chunks", ["not-a-chunk-record"], "chunk"),
        ):
            tampered = _manifest()
            tampered[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(EvidenceValidationError, message):
                    validate_evidence_manifest(tampered)

        tampered = _manifest()
        tampered["parser"]["text_normalization"] = "unversioned"
        with self.assertRaisesRegex(EvidenceValidationError, "parser identity"):
            validate_evidence_manifest(tampered)

        with self.assertRaisesRegex(
            EvidenceValidationError, "successful-page provenance"
        ):
            build_evidence_manifest(
                paper_id="paper:provenance",
                source_file_hash=SOURCE_HASH,
                source_size_bytes=128,
                total_pages=1,
                pages=[{
                    "page_number": 1,
                    "status": "ok",
                    "text": "fallback text",
                    "parser_used": "pypdf-5.1.0",
                    "primary_error_type": "UnboundLocalError",
                    "fallback_error_type": None,
                }],
                parser_version="synthetic-provenance",
            )

    def test_invalid_or_missing_page_evidence_fails_closed(self):
        with self.assertRaisesRegex(EvidenceValidationError, "every source page"):
            _manifest(pages=_pages()[:-1])

        tampered = _manifest()
        tampered["chunks"][0]["text"] += "tampered"
        with self.assertRaisesRegex(EvidenceValidationError, "text hash mismatch"):
            validate_evidence_manifest(tampered)

        tampered = _manifest()
        tampered["coverage"]["failed_pages"] = 0
        with self.assertRaisesRegex(EvidenceValidationError, "coverage mismatch"):
            validate_evidence_manifest(tampered)

        tampered = _manifest()
        omitted = tampered["pages"][0]["chunk_ids"].pop()
        tampered["pages"][0]["section_kinds"] = list(dict.fromkeys(
            chunk["section_kind"]
            for chunk in tampered["chunks"]
            if chunk["id"] in tampered["pages"][0]["chunk_ids"]
        ))
        self.assertTrue(any(chunk["id"] == omitted for chunk in tampered["chunks"]))
        with self.assertRaisesRegex(EvidenceValidationError, "unreferenced chunks"):
            validate_evidence_manifest(tampered)

        tampered = _manifest()
        tampered["chunking"]["overlap_characters"] = 300
        with self.assertRaisesRegex(
            EvidenceValidationError, "invalid chunking configuration"
        ):
            validate_evidence_manifest(tampered)

        tampered = _manifest()
        tampered["chunks"][0]["page_char_end"] += 1
        with self.assertRaisesRegex(
            EvidenceValidationError,
            "chunk (?:identity mismatch|text does not match its page span)",
        ):
            validate_evidence_manifest(tampered)

    def test_page_chunk_relationships_and_offsets_fail_closed(self):
        tampered = _manifest()
        page_two_chunk = tampered["pages"][1]["chunk_ids"][0]
        tampered["pages"][0]["chunk_ids"][0] = page_two_chunk
        with self.assertRaisesRegex(
            EvidenceValidationError, "page chunk references are inconsistent"
        ):
            validate_evidence_manifest(tampered)

        tampered = _manifest()
        tampered["pages"][0]["chunk_ids"].append(
            tampered["pages"][0]["chunk_ids"][0]
        )
        with self.assertRaisesRegex(
            EvidenceValidationError, "page chunk references are inconsistent"
        ):
            validate_evidence_manifest(tampered)

        tampered = _manifest()
        chunk = tampered["chunks"][0]
        old_id = chunk["id"]
        chunk["page_char_start"] = False
        identity = "|".join((
            "1",
            tampered["source"]["file_hash_sha256"],
            chunk["paper_id"],
            str(chunk["page_number"]),
            str(chunk["page_char_start"]),
            str(chunk["page_char_end"]),
            chunk["section_kind"],
            chunk["text_hash"],
        ))
        chunk["id"] = f"evidence:{hashlib.sha256(identity.encode()).hexdigest()}"
        tampered["pages"][0]["chunk_ids"] = [
            chunk["id"] if chunk_id == old_id else chunk_id
            for chunk_id in tampered["pages"][0]["chunk_ids"]
        ]
        with self.assertRaisesRegex(EvidenceValidationError, "invalid chunk page offsets"):
            validate_evidence_manifest(tampered)

        tampered = _manifest()
        tampered["pages"][0]["section_kinds"] = ["fabricated"]
        with self.assertRaisesRegex(EvidenceValidationError, "section summary mismatch"):
            validate_evidence_manifest(tampered)

    def test_surrogates_are_sanitized_before_hashing_and_storage(self):
        manifest = build_evidence_manifest(
            paper_id="paper:unicode",
            source_file_hash=SOURCE_HASH,
            source_size_bytes=32,
            total_pages=1,
            pages=[{
                "page_number": 1,
                "status": "ok",
                "text": "Methods\nformula: \ud835x + \udfff",
            }],
            parser_version="pypdf-synthetic",
            chunk_chars=300,
            overlap_chars=30,
        )

        rendered = json.dumps(manifest, ensure_ascii=False)
        self.assertIn("formula: \ufffdx + \ufffd", rendered)
        rendered.encode("utf-8")

    def test_atomic_round_trip_and_failed_replace_preserve_previous_file(self):
        manifest = _manifest()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            write_evidence_manifest(manifest, path)
            self.assertEqual(load_evidence_manifest(path), manifest)
            previous = path.read_bytes()

            changed_pages = _pages()
            changed_pages[0]["text"] += " changed"
            changed = _manifest(pages=changed_pages)
            with patch(
                "services.paper_evidence_service.os.replace",
                side_effect=OSError("synthetic replace failure"),
            ):
                with self.assertRaisesRegex(OSError, "synthetic replace failure"):
                    write_evidence_manifest(changed, path)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(path.parent.glob(".evidence.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()

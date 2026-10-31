from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import ask_generation_control
from services.ask_generation_control import (
    GenerationControlError,
    GenerationSwitchUncertain,
    promote_generation,
    resolve_active_generation,
    rollback_generation,
)
from services.ask_shadow_integration import observe_question
from services.hybrid_claim_retrieval import build_shadow_index
from services.paper_evidence_service import (
    build_evidence_manifest,
    write_evidence_manifest,
)
from services.wiki_shadow_compiler import build_paper_compile_plan


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class AskGenerationControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.live_data = self.root / "live-data"
        self.live_wiki = self.live_data / "wiki"
        self.live_wiki.mkdir(parents=True)
        self.control = self.root / "generation-control"

    def tearDown(self):
        self.temporary.cleanup()

    def _generation(
        self, name: str, *, paper_id: str, failed_page: bool = False
    ) -> Path:
        root = self.root / "generations" / name
        root.mkdir(parents=True)
        text = "Actions condition a compact latent transition model. " * 12
        manifest = build_evidence_manifest(
            paper_id=paper_id,
            source_file_hash=_hash(f"source:{paper_id}".encode()),
            source_size_bytes=800,
            total_pages=2 if failed_page else 1,
            pages=(
                [
                    {
                        "page_number": 1,
                        "status": "error",
                        "error_type": "SyntheticParserError",
                    },
                    {"page_number": 2, "status": "ok", "text": text},
                ]
                if failed_page
                else [{"page_number": 1, "status": "ok", "text": text}]
            ),
            parser_version="synthetic-promotion@v1",
            chunk_chars=1_200,
            overlap_chars=50,
        )
        evidence_id = manifest["chunks"][0]["id"]
        plan = build_paper_compile_plan(
            paper_id=paper_id,
            title=f"Generation {name}",
            extraction={
                "title": f"Generation {name}",
                "innovations": {"this_work": "Actions condition latent transitions."},
            },
            notes="",
            model="synthetic-no-provider",
            locale="zh",
            source_file_hash=manifest["source"]["file_hash_sha256"],
            evidence_links={"innovations.this_work": [evidence_id]},
            evidence_manifest=manifest,
        )
        index = root / "indexes" / "claims.sqlite"
        report = build_shadow_index(
            index_path=index,
            shadow_root=root,
            production_wiki_root=self.live_wiki,
            compile_plans=[plan],
            evidence_manifests=[manifest],
        )
        write_evidence_manifest(manifest, root / "evidence" / f"{paper_id}.json")
        generation = {
            "schema_version": 1,
            "generation_id": name,
            "status": "ready",
            "index_relpath": "indexes/claims.sqlite",
            "source_digest": report["source_digest"],
            "dense_index_digest": report["dense_index_digest"],
            "dense_model_id": None,
            "index_sha256": _hash(index.read_bytes()),
            "index_bytes": index.stat().st_size,
            "production_eligible": False,
        }
        (root / "generation.json").write_text(
            json.dumps(generation, sort_keys=True), encoding="utf-8"
        )
        return root

    def _promote(
        self,
        generation: Path,
        promotion_id: str,
        expected: str | None,
        *,
        accepted_failed_pages: int = 0,
    ) -> dict:
        return promote_generation(
            generation_root=generation,
            control_root=self.control,
            live_data_root=self.live_data,
            promotion_id=promotion_id,
            reviewer="synthetic-reviewer",
            reviewed_at="2026-10-31T00:00:00Z",
            acceptance_sha256=_hash(f"acceptance:{promotion_id}".encode()),
            accepted_failed_pages=accepted_failed_pages,
            expected_active_sha256=expected,
        )

    def test_promote_binds_review_locator_hashes_and_keeps_generation_immutable(self):
        generation = self._generation("generation-one", paper_id="paper-one")
        before = {
            path.relative_to(generation): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in generation.rglob("*")
            if path.is_file()
        }

        active = self._promote(generation, "review-one", None)
        resolved = resolve_active_generation(
            control_root=self.control, live_data_root=self.live_data
        )
        after = {
            path.relative_to(generation): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in generation.rglob("*")
            if path.is_file()
        }

        self.assertEqual(before, after)
        self.assertEqual(active["action"], "promote")
        self.assertEqual(resolved["generation_root"], generation)
        self.assertEqual(resolved["promotion_id"], "review-one")
        self.assertTrue(resolved["production_eligible"])
        promotion = json.loads(
            (self.control / "promotions" / "review-one.json").read_text()
        )
        self.assertEqual(promotion["locator_audit"]["missing_locators"], 0)
        self.assertEqual(promotion["locator_audit"]["missing_source_hashes"], 0)
        self.assertGreater(promotion["locator_audit"]["evidence_records"], 0)
        self.assertEqual(promotion["locator_audit"]["failed_pages"], 0)
        self.assertRegex(
            promotion["locator_audit"]["evidence_manifest_digest"],
            r"^[0-9a-f]{64}$",
        )

    def test_promotion_requires_exact_explicit_acceptance_of_failed_pages(self):
        generation = self._generation(
            "generation-with-failure",
            paper_id="paper-with-failure",
            failed_page=True,
        )

        with self.assertRaisesRegex(
            GenerationControlError, "failed pages were not explicitly accepted"
        ):
            self._promote(generation, "review-rejected", None)
        self.assertFalse(self.control.exists())

        active = self._promote(
            generation,
            "review-accepted",
            None,
            accepted_failed_pages=1,
        )
        promotion = json.loads(
            (self.control / "promotions" / "review-accepted.json").read_text()
        )
        self.assertEqual(active["action"], "promote")
        self.assertEqual(promotion["review"]["accepted_failed_pages"], 1)
        self.assertEqual(promotion["locator_audit"]["failed_pages"], 1)
        self.assertRegex(
            promotion["locator_audit"]["failure_digest"], r"^[0-9a-f]{64}$"
        )

    def test_promotion_rejects_missing_source_hash_even_when_index_is_resigned(self):
        generation = self._generation("legacy-generation", paper_id="paper-legacy")
        index = generation / "indexes" / "claims.sqlite"
        connection = sqlite3.connect(index)
        try:
            row_id, raw = connection.execute(
                "SELECT id,locator FROM records WHERE kind='evidence' LIMIT 1"
            ).fetchone()
            locator = json.loads(raw)
            locator["source_file_hash"] = None
            connection.execute(
                "UPDATE records SET locator=? WHERE id=?",
                (json.dumps(locator, sort_keys=True), row_id),
            )
            connection.commit()
        finally:
            connection.close()
        manifest = json.loads((generation / "generation.json").read_text())
        manifest["index_sha256"] = _hash(index.read_bytes())
        manifest["index_bytes"] = index.stat().st_size
        (generation / "generation.json").write_text(
            json.dumps(manifest, sort_keys=True), encoding="utf-8"
        )

        with self.assertRaisesRegex(
            GenerationControlError, "complete source locator"
        ):
            self._promote(generation, "legacy-review", None)
        self.assertFalse((self.control / "active.json").exists())
        observed = observe_question(
            "Actions condition latent transitions",
            adapter="cli",
            env={
                "KNOWRA_ASK_SHADOW_MODE": "observe",
                "KNOWRA_ASK_SHADOW_ROOT": str(generation),
            },
            live_data_root=self.live_data,
            production_wiki_root=self.live_wiki,
        )
        self.assertEqual(observed["status"], "ready")
        self.assertFalse(observed["production_eligible"])

    def test_promotion_rejects_resigned_inexact_character_locator(self):
        generation = self._generation("bad-locator", paper_id="paper-locator")
        index = generation / "indexes" / "claims.sqlite"
        connection = sqlite3.connect(index)
        try:
            row_id, raw = connection.execute(
                "SELECT id,locator FROM records WHERE kind='evidence' LIMIT 1"
            ).fetchone()
            locator = json.loads(raw)
            locator["page_char_end"] += 7
            connection.execute(
                "UPDATE records SET locator=? WHERE id=?",
                (json.dumps(locator, sort_keys=True), row_id),
            )
            connection.commit()
        finally:
            connection.close()
        manifest = json.loads((generation / "generation.json").read_text())
        manifest["index_sha256"] = _hash(index.read_bytes())
        manifest["index_bytes"] = index.stat().st_size
        (generation / "generation.json").write_text(
            json.dumps(manifest, sort_keys=True), encoding="utf-8"
        )

        with self.assertRaisesRegex(
            GenerationControlError, "complete source locator"
        ):
            self._promote(generation, "bad-locator-review", None)

    def test_atomic_switch_failure_preserves_previous_active_pointer(self):
        first = self._generation("generation-one", paper_id="paper-one")
        second = self._generation("generation-two", paper_id="paper-two")
        initial = self._promote(first, "review-one", None)
        active_before = (self.control / "active.json").read_bytes()

        with mock.patch(
            "services.ask_generation_control.os.replace",
            side_effect=OSError("synthetic replace failure"),
        ):
            with self.assertRaisesRegex(OSError, "synthetic replace failure"):
                self._promote(second, "review-two", initial["active_sha256"])

        self.assertEqual((self.control / "active.json").read_bytes(), active_before)
        resolved = resolve_active_generation(
            control_root=self.control, live_data_root=self.live_data
        )
        self.assertEqual(resolved["promotion_id"], "review-one")

    def test_post_replace_sync_failure_reports_uncertain_but_status_is_authoritative(self):
        first = self._generation("generation-one", paper_id="paper-one")
        second = self._generation("generation-two", paper_id="paper-two")
        initial = self._promote(first, "review-one", None)
        real_sync = ask_generation_control._fsync_directory

        def fail_active_directory_sync(path: Path):
            if path == self.control:
                raise OSError("synthetic directory sync failure")
            return real_sync(path)

        with (
            mock.patch(
                "services.ask_generation_control._fsync_directory",
                side_effect=fail_active_directory_sync,
            ),
            self.assertRaises(GenerationSwitchUncertain),
        ):
            self._promote(second, "review-two", initial["active_sha256"])

        resolved = resolve_active_generation(
            control_root=self.control, live_data_root=self.live_data
        )
        self.assertEqual(resolved["promotion_id"], "review-two")

    def test_explicit_rollback_uses_compare_and_swap_and_revalidates_target(self):
        first = self._generation("generation-one", paper_id="paper-one")
        second = self._generation("generation-two", paper_id="paper-two")
        one = self._promote(first, "review-one", None)
        two = self._promote(second, "review-two", one["active_sha256"])

        with self.assertRaisesRegex(GenerationControlError, "changed before"):
            rollback_generation(
                control_root=self.control,
                live_data_root=self.live_data,
                target_promotion_id="review-one",
                expected_active_sha256=one["active_sha256"],
            )
        rolled_back = rollback_generation(
            control_root=self.control,
            live_data_root=self.live_data,
            target_promotion_id="review-one",
            expected_active_sha256=two["active_sha256"],
        )
        resolved = resolve_active_generation(
            control_root=self.control, live_data_root=self.live_data
        )

        self.assertEqual(rolled_back["action"], "rollback")
        self.assertEqual(resolved["promotion_id"], "review-one")
        self.assertEqual(resolved["active_sha256"], rolled_back["active_sha256"])

    def test_tampered_review_or_generation_fails_closed(self):
        generation = self._generation("generation-one", paper_id="paper-one")
        self._promote(generation, "review-one", None)
        promotion_path = self.control / "promotions" / "review-one.json"
        promotion = json.loads(promotion_path.read_text())
        promotion["review"]["approved"] = False
        promotion_path.write_text(json.dumps(promotion), encoding="utf-8")

        with self.assertRaises(GenerationControlError):
            resolve_active_generation(
                control_root=self.control, live_data_root=self.live_data
            )

    def test_changed_index_identity_fails_closed_before_assist(self):
        generation = self._generation("generation-one", paper_id="paper-one")
        self._promote(generation, "review-one", None)
        index = generation / "indexes" / "claims.sqlite"
        value = index.stat().st_mtime_ns + 1_000_000
        os.utime(index, ns=(value, value))

        with self.assertRaisesRegex(GenerationControlError, "changed after review"):
            resolve_active_generation(
                control_root=self.control, live_data_root=self.live_data
            )

    def test_generation_change_during_review_never_creates_control_state(self):
        generation = self._generation("generation-one", paper_id="paper-one")
        index = generation / "indexes" / "claims.sqlite"

        def mutate_during_audit(*_args, **_kwargs):
            value = index.stat().st_mtime_ns + 1_000_000
            os.utime(index, ns=(value, value))
            return {
                "evidence_records": 1,
                "evidence_manifests": 1,
                "evidence_manifest_digest": _hash(b"manifest"),
                "source_pages": 1,
                "failed_pages": 0,
                "failure_digest": _hash(b"[]"),
                "missing_locators": 0,
                "missing_source_hashes": 0,
            }

        with (
            mock.patch(
                "services.ask_generation_control._locator_audit",
                side_effect=mutate_during_audit,
            ),
            self.assertRaisesRegex(GenerationControlError, "during promotion review"),
        ):
            self._promote(generation, "review-one", None)
        self.assertFalse(self.control.exists())

    def test_symlinked_control_root_is_rejected(self):
        generation = self._generation("generation-one", paper_id="paper-one")
        real_control = self.root / "real-control"
        real_control.mkdir()
        linked_control = self.root / "linked-control"
        linked_control.symlink_to(real_control, target_is_directory=True)

        with self.assertRaisesRegex(GenerationControlError, "symlinks"):
            promote_generation(
                generation_root=generation,
                control_root=linked_control,
                live_data_root=self.live_data,
                promotion_id="review-one",
                reviewer="synthetic-reviewer",
                reviewed_at="2026-10-31T00:00:00Z",
                acceptance_sha256=_hash(b"acceptance"),
                accepted_failed_pages=0,
                expected_active_sha256=None,
            )

    def test_control_and_generation_must_not_overlap_live_data(self):
        generation = self._generation("generation-one", paper_id="paper-one")
        with self.assertRaisesRegex(GenerationControlError, "overlaps"):
            promote_generation(
                generation_root=generation,
                control_root=self.live_data / "control",
                live_data_root=self.live_data,
                promotion_id="review-one",
                reviewer="synthetic-reviewer",
                reviewed_at="2026-10-31T00:00:00Z",
                acceptance_sha256=_hash(b"acceptance"),
                accepted_failed_pages=0,
                expected_active_sha256=None,
            )


if __name__ == "__main__":
    unittest.main()

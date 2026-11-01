from __future__ import annotations

import hashlib
from io import BytesIO
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import task_runtime
from services.ask_generation_control import promote_generation, resolve_active_generation
from services.ask_generation_refresh import (
    AskGenerationRefreshError,
    REFRESH_REQUEST_FILENAME,
    _atomic_json,
    _ack_request,
    _fsync_generation_tree,
    _hash_sources_with_deadline,
    _persistent_stat_cache_reliable,
    _stop_hash_process,
    configured_source_roots,
    default_refresh_paths,
    ensure_pending_refresh,
    generation_revision,
    library_revision,
    refresh_generation,
    refresh_status,
    request_refresh,
)
from services.ask_shadow_integration import (
    observation_is_current,
    observe_question,
    runtime_status,
)
from services.paper_evidence_service import build_evidence_manifest
from services.task_store import TaskStore


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): writer._add_object(font),
        }),
    })
    content = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class AskGenerationRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.data = self.root / "workspace" / "data"
        self.papers = self.data / "papers"
        self.wiki = self.data / "wiki"
        self.papers.mkdir(parents=True)
        self.wiki.mkdir()
        database = sqlite3.connect(self.data / "knowledge.db")
        database.execute(
            "CREATE TABLE papers (id TEXT, filepath TEXT, filename TEXT, file_hash TEXT, title TEXT, "
            "raw_llm_response TEXT, notes TEXT, processed INTEGER)"
        )
        for index in (1, 2):
            content = f"synthetic-pdf-{index}".encode()
            filename = f"paper-{index}.pdf"
            (self.papers / filename).write_bytes(content)
            database.execute(
                "INSERT INTO papers VALUES (?,?,?,?,?,?,?,1)",
                (
                    f"paper-{index}", f"data/papers/{filename}", filename,
                    hashlib.md5(content).hexdigest(), f"Paper {index}",
                    json.dumps({
                        "title": f"Paper {index}",
                        "year": 2020 + index,
                        "innovations": {"this_work": f"Stage {index} changes the model."},
                    }), "",
                ),
            )
        database.commit()
        database.close()
        self.paths = default_refresh_paths(self.data)

    def tearDown(self):
        self.temporary.cleanup()

    def _manifest(self, path, *, paper_id, source_file_hash=None):
        content = Path(path).read_bytes()
        return build_evidence_manifest(
            paper_id=paper_id,
            source_file_hash=_hash(content),
            source_size_bytes=len(content),
            total_pages=1,
            pages=[{
                "page_number": 1,
                "status": "ok",
                "text": f"Source evidence for {paper_id} changes the model stage.",
            }],
            parser_version="synthetic-refresh@test",
        )

    def _refresh(self):
        with patch(
            "services.shadow_generation.build_pdf_evidence",
            side_effect=self._manifest,
        ):
            return refresh_generation(
                data_root=self.data,
                production_wiki_root=self.wiki,
                generation_parent=self.paths["generation_parent"],
                control_root=self.paths["control_root"],
                state_root=self.paths["state_root"],
            )

    def _refresh_with_parser(self):
        with patch(
            "services.shadow_generation.build_pdf_evidence",
            side_effect=self._manifest,
        ) as parser:
            result = refresh_generation(
                data_root=self.data,
                production_wiki_root=self.wiki,
                generation_parent=self.paths["generation_parent"],
                control_root=self.paths["control_root"],
                state_root=self.paths["state_root"],
            )
        return result, parser.call_count

    def _update(self, sql, values=()):
        database = sqlite3.connect(self.data / "knowledge.db")
        try:
            database.execute(sql, values)
            database.commit()
        finally:
            database.close()

    def test_add_update_delete_refreshes_and_keeps_previous_generation(self):
        first = self._refresh()
        self.assertEqual(first["status"], "promoted")
        self.assertEqual(first["paper_count"], 2)
        self.assertEqual(
            refresh_status(live_data_root=self.data, control_root=self.paths["control_root"])["freshness"],
            "fresh",
        )
        active_before = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )

        database = sqlite3.connect(self.data / "knowledge.db")
        database.execute("UPDATE papers SET notes='updated' WHERE id='paper-1'")
        database.commit()
        database.close()
        request_refresh(state_root=self.paths["state_root"], reason="notes_updated")
        self.assertEqual(
            refresh_status(live_data_root=self.data, control_root=self.paths["control_root"])["freshness"],
            "stale",
        )

        with patch(
            "services.ask_generation_refresh.build_restored_shadow_generation",
            side_effect=RuntimeError("synthetic build failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic build failure"):
                refresh_generation(
                    data_root=self.data,
                    production_wiki_root=self.wiki,
                    generation_parent=self.paths["generation_parent"],
                    control_root=self.paths["control_root"],
                    state_root=self.paths["state_root"],
                )
        active_after_failure = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self.assertEqual(active_after_failure["active_sha256"], active_before["active_sha256"])

        second, parsed = self._refresh_with_parser()
        self.assertEqual(second["status"], "promoted")
        self.assertEqual(second["previous_generation_id"], first["generation_id"])
        self.assertEqual(parsed, 0, "notes-only changes must reuse page evidence")

        database = sqlite3.connect(self.data / "knowledge.db")
        database.execute("DELETE FROM papers WHERE id='paper-2'")
        database.commit()
        database.close()
        request_refresh(state_root=self.paths["state_root"], reason="paper_deleted")
        third, parsed = self._refresh_with_parser()
        self.assertEqual(third["paper_count"], 1)
        self.assertEqual(parsed, 0, "deleting a paper must not reparse retained PDFs")
        self.assertEqual(
            refresh_status(live_data_root=self.data, control_root=self.paths["control_root"])["freshness"],
            "fresh",
        )

        content = b"synthetic-pdf-3"
        (self.papers / "paper-3.pdf").write_bytes(content)
        database = sqlite3.connect(self.data / "knowledge.db")
        database.execute(
            "INSERT INTO papers VALUES (?,?,?,?,?,?,?,1)",
            (
                "paper-3", "data/papers/paper-3.pdf", "paper-3.pdf",
                hashlib.md5(content).hexdigest(), "Paper 3",
                json.dumps({
                    "title": "Paper 3", "year": 2025,
                    "innovations": {"this_work": "A new stage."},
                }), "",
            ),
        )
        database.commit()
        database.close()
        request_refresh(state_root=self.paths["state_root"], reason="paper_added")
        fourth, parsed = self._refresh_with_parser()
        self.assertEqual(fourth["paper_count"], 2)
        self.assertEqual(parsed, 1, "only a newly added PDF should be parsed")

    def test_concurrent_change_never_promotes_obsolete_candidate(self):
        baseline = self._refresh()
        active_before = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        database = sqlite3.connect(self.data / "knowledge.db")
        database.execute("UPDATE papers SET notes='changed-before-build' WHERE id='paper-1'")
        database.commit()
        database.close()
        request_refresh(state_root=self.paths["state_root"], reason="paper_updated")
        original = __import__(
            "services.ask_generation_refresh", fromlist=["build_restored_shadow_generation"]
        ).build_restored_shadow_generation

        def build_then_change(**kwargs):
            result = original(**kwargs)
            database = sqlite3.connect(self.data / "knowledge.db")
            database.execute("UPDATE papers SET notes='changed-during-build' WHERE id='paper-1'")
            database.commit()
            database.close()
            return result

        with patch(
            "services.ask_generation_refresh.build_restored_shadow_generation",
            side_effect=build_then_change,
        ):
            with self.assertRaisesRegex(AskGenerationRefreshError, "changed"):
                refresh_generation(
                    data_root=self.data,
                    production_wiki_root=self.wiki,
                    generation_parent=self.paths["generation_parent"],
                    control_root=self.paths["control_root"],
                    state_root=self.paths["state_root"],
                )
        active_after = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self.assertEqual(active_after["generation_id"], active_before["generation_id"])
        self.assertEqual(active_after["promotion_id"], active_before["promotion_id"])
        self.assertEqual(active_after["generation_id"], baseline["generation_id"])

    def test_marker_ack_is_compare_and_delete(self):
        first = request_refresh(state_root=self.paths["state_root"], reason="first")
        second = request_refresh(state_root=self.paths["state_root"], reason="second")
        self.assertFalse(_ack_request(self.paths["state_root"], first["request_id"]))
        marker = json.loads(
            (self.paths["state_root"] / REFRESH_REQUEST_FILENAME).read_text()
        )
        self.assertEqual(marker["request_id"], second["request_id"])
        self.assertTrue(_ack_request(self.paths["state_root"], second["request_id"]))
        self.assertFalse((self.paths["state_root"] / REFRESH_REQUEST_FILENAME).exists())

    def test_marker_writes_are_serialized_across_processes(self):
        code = (
            "from pathlib import Path; "
            "from services.ask_generation_refresh import request_refresh; "
            f"request_refresh(state_root=Path({str(self.paths['state_root'])!r}), reason='child')"
        )
        children = [
            subprocess.Popen(
                [sys.executable, "-c", code],
                cwd=Path(__file__).resolve().parents[1],
            )
            for _ in range(8)
        ]
        self.assertTrue(all(child.wait(timeout=10) == 0 for child in children))
        marker = json.loads(
            (self.paths["state_root"] / REFRESH_REQUEST_FILENAME).read_text()
        )
        self.assertEqual(marker["schema_version"], 1)
        self.assertEqual(len(marker["request_id"]), 32)

    def test_cross_process_new_write_always_survives_old_ack(self):
        first = request_refresh(state_root=self.paths["state_root"], reason="old")
        barrier = self.root / "release-marker-race"
        backend = Path(__file__).resolve().parents[1]
        ack_code = (
            "import time; from pathlib import Path; "
            "from services.ask_generation_refresh import _ack_request; "
            f"b=Path({str(barrier)!r}); "
            "\nwhile not b.exists(): time.sleep(.001)\n"
            f"print(_ack_request(Path({str(self.paths['state_root'])!r}), {first['request_id']!r}), flush=True)"
        )
        write_code = (
            "import json,time; from pathlib import Path; "
            "from services.ask_generation_refresh import request_refresh; "
            f"b=Path({str(barrier)!r}); "
            "\nwhile not b.exists(): time.sleep(.001)\n"
            f"print(json.dumps(request_refresh(state_root=Path({str(self.paths['state_root'])!r}), reason='new')), flush=True)"
        )
        ack = subprocess.Popen(
            [sys.executable, "-c", ack_code], cwd=backend,
            stdout=subprocess.PIPE, text=True,
        )
        writer = subprocess.Popen(
            [sys.executable, "-c", write_code], cwd=backend,
            stdout=subprocess.PIPE, text=True,
        )
        barrier.write_text("go")
        ack.communicate(timeout=10)
        writer_output, _ = writer.communicate(timeout=10)
        self.assertEqual(ack.returncode, 0)
        self.assertEqual(writer.returncode, 0)
        written = json.loads(writer_output)
        marker = json.loads(
            (self.paths["state_root"] / REFRESH_REQUEST_FILENAME).read_text()
        )
        self.assertEqual(marker["request_id"], written["request_id"])

    def test_untrusted_cold_hash_has_a_killable_deadline(self):
        source = self.papers / "paper-1.pdf"
        block = b"x" * (1024 * 1024)
        md5 = hashlib.md5(usedforsecurity=False)
        with source.open("wb") as handle:
            for _ in range(128):
                handle.write(block)
                md5.update(block)
        self._update(
            "UPDATE papers SET file_hash=? WHERE id='paper-1'",
            (md5.hexdigest(),),
        )
        started = time.monotonic()
        with self.assertRaisesRegex(AskGenerationRefreshError, "deadline"):
            library_revision(
                self.data / "knowledge.db",
                deadline=time.perf_counter() + 0.05,
            )
        self.assertLess(time.monotonic() - started, 1.0)

    def test_hash_timeout_reaps_child_without_second_communicate(self):
        process = MagicMock()
        process.communicate.side_effect = subprocess.TimeoutExpired("hash", 0.01)
        process.poll.side_effect = [None, 0]
        process.wait.side_effect = [subprocess.TimeoutExpired("hash", 0.2), 0]
        with patch.dict(os.environ, {
            "KNOWRA_OFFLINE_ROOT": "/synthetic/offline-root",
            "PYTHONPATH": "/synthetic/guard",
            "SYNTHETIC_PROVIDER_SECRET": "must-not-leak",
        }), patch(
            "services.ask_generation_refresh.subprocess.Popen",
            return_value=process,
        ) as spawn, self.assertRaisesRegex(AskGenerationRefreshError, "deadline"):
            _hash_sources_with_deadline(
                [self.papers / "paper-1.pdf"],
                deadline=time.perf_counter() + 1,
            )
        self.assertEqual(process.communicate.call_count, 1)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        child_env = spawn.call_args.kwargs["env"]
        self.assertEqual(child_env["KNOWRA_OFFLINE_ROOT"], "/synthetic/offline-root")
        self.assertEqual(child_env["PYTHONPATH"], "/synthetic/guard")
        self.assertNotIn("SYNTHETIC_PROVIDER_SECRET", child_env)

    def test_hash_worker_reports_when_terminate_and_kill_cannot_reap(self):
        process = MagicMock()
        process.poll.side_effect = [None, None]
        process.wait.side_effect = [
            subprocess.TimeoutExpired("hash", 0.2),
            subprocess.TimeoutExpired("hash", 0.2),
        ]
        self.assertFalse(_stop_hash_process(process))
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()

    def test_refreshed_inventory_generation_is_loadable_in_assist(self):
        self._refresh()
        result = observe_question(
            "How does the model stage change?",
            adapter="cli",
            env={
                "KNOWRA_ASK_SHADOW_MODE": "assist",
                "KNOWRA_ASK_GENERATION_CONTROL": str(self.paths["control_root"]),
            },
            live_data_root=self.data,
            production_wiki_root=self.wiki,
            query_routes=["model stage changes"],
            include_answer_evidence=True,
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["mode"], "assist")
        self.assertTrue(result["candidates"])

    def test_library_mutation_during_retrieval_discards_candidates(self):
        self._refresh()
        module = __import__(
            "services.ask_shadow_integration", fromlist=["execute_cli_plan"]
        )
        original = module.execute_cli_plan

        def execute_then_mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            self._update("UPDATE papers SET notes='changed during query' WHERE id='paper-1'")
            return result

        with patch(
            "services.ask_shadow_integration.execute_cli_plan",
            side_effect=execute_then_mutate,
        ):
            result = observe_question(
                "How does the model stage change?",
                adapter="cli",
                env={
                    "KNOWRA_ASK_SHADOW_MODE": "assist",
                    "KNOWRA_ASK_GENERATION_CONTROL": str(self.paths["control_root"]),
                },
                live_data_root=self.data,
                production_wiki_root=self.wiki,
            )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "generation_stale")

    def test_answer_acceptance_revalidates_custom_control_after_model_work(self):
        self._refresh()
        env = {
            "KNOWRA_ASK_SHADOW_MODE": "assist",
            "KNOWRA_ASK_GENERATION_CONTROL": str(self.paths["control_root"]),
        }
        result = observe_question(
            "How does the model stage change?",
            adapter="cli",
            env=env,
            live_data_root=self.data,
            production_wiki_root=self.wiki,
            query_routes=["model stage changes"],
            include_answer_evidence=True,
        )
        self.assertTrue(
            observation_is_current(result, live_data_root=self.data, env=env)
        )
        self._update("UPDATE papers SET notes='changed after retrieval' WHERE id='paper-1'")
        self.assertFalse(
            observation_is_current(result, live_data_root=self.data, env=env)
        )

    def test_cold_library_revision_is_inside_observation_budget(self):
        self._refresh()
        with patch("services.ask_shadow_integration.MAX_OBSERVATION_MS", 0.001):
            result = observe_question(
                "question",
                adapter="cli",
                env={
                    "KNOWRA_ASK_SHADOW_MODE": "assist",
                    "KNOWRA_ASK_GENERATION_CONTROL": str(self.paths["control_root"]),
                },
                live_data_root=self.data,
                production_wiki_root=self.wiki,
            )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "library_revision_timeout")

    def test_pipeline_conflict_and_interrupted_refresh_are_requeued(self):
        queue = TaskStore(self.root / "tasks.db")
        queue.initialize()
        pipeline = queue.submit("pipeline", "pipeline", {})
        request_refresh(state_root=self.paths["state_root"], reason="during_pipeline")
        with patch.object(task_runtime, "_store", queue), patch.object(
            task_runtime, "ensure_worker"
        ):
            blocked = ensure_pending_refresh(
                live_data_root=self.data, retry_after_seconds=0
            )
            self.assertTrue(blocked["coalesced"])
            self.assertIsNone(queue.latest("ask_generation_refresh"))
            queue.update(pipeline["job_id"], status="completed")
            queued = ensure_pending_refresh(
                live_data_root=self.data, retry_after_seconds=0
            )
            refresh_job = queue.get(queued["job_id"])
            self.assertEqual(refresh_job["status"], "queued")
            queue.update(refresh_job["job_id"], status="interrupted")
            retried = ensure_pending_refresh(
                live_data_root=self.data, retry_after_seconds=0
            )
            self.assertNotEqual(retried["job_id"], refresh_job["job_id"])
            self.assertTrue(
                (self.paths["state_root"] / REFRESH_REQUEST_FILENAME).is_file()
            )

    def test_new_marker_during_build_survives_old_job_ack(self):
        self._refresh()
        self._update("UPDATE papers SET notes='new plan' WHERE id='paper-1'")
        first = request_refresh(state_root=self.paths["state_root"], reason="first")
        original = __import__(
            "services.ask_generation_refresh", fromlist=["build_restored_shadow_generation"]
        ).build_restored_shadow_generation
        second = {}

        def build_then_request(**kwargs):
            result = original(**kwargs)
            second.update(request_refresh(
                state_root=self.paths["state_root"], reason="newer_request"
            ))
            return result

        with patch(
            "services.ask_generation_refresh.build_restored_shadow_generation",
            side_effect=build_then_request,
        ):
            result = refresh_generation(
                data_root=self.data,
                production_wiki_root=self.wiki,
                generation_parent=self.paths["generation_parent"],
                control_root=self.paths["control_root"],
                state_root=self.paths["state_root"],
            )
        self.assertEqual(result["status"], "promoted")
        self.assertFalse(result["request_acknowledged"])
        self.assertNotEqual(first["request_id"], second["request_id"])
        marker = json.loads(
            (self.paths["state_root"] / REFRESH_REQUEST_FILENAME).read_text()
        )
        self.assertEqual(marker["request_id"], second["request_id"])

    def test_nested_same_name_pdfs_follow_filepath_not_filename(self):
        for folder, paper_id, original in (
            ("team-a", "paper-1", "paper-1.pdf"),
            ("team-b", "paper-2", "paper-2.pdf"),
        ):
            target = self.papers / folder / "shared.pdf"
            target.parent.mkdir()
            target.write_bytes((self.papers / original).read_bytes())
            (self.papers / original).unlink()
            self._update(
                "UPDATE papers SET filepath=?, filename='shared.pdf' WHERE id=?",
                (f"data/papers/{folder}/shared.pdf", paper_id),
            )
        seen = {}

        def manifest(path, *, paper_id, source_file_hash=None):
            seen[paper_id] = Path(path).read_bytes()
            return self._manifest(
                path, paper_id=paper_id, source_file_hash=source_file_hash
            )

        with patch(
            "services.shadow_generation.build_pdf_evidence", side_effect=manifest
        ):
            result = refresh_generation(
                data_root=self.data,
                production_wiki_root=self.wiki,
                generation_parent=self.paths["generation_parent"],
                control_root=self.paths["control_root"],
                state_root=self.paths["state_root"],
            )
        self.assertEqual(result["paper_count"], 2)
        self.assertEqual(seen, {
            "paper-1": b"synthetic-pdf-1",
            "paper-2": b"synthetic-pdf-2",
        })

    def test_explicit_external_scan_root_is_snapshotted_by_paper_identity(self):
        external = self.root / "external-library"
        external.mkdir()
        external_pdf = external / "paper-1.pdf"
        external_pdf.write_bytes((self.papers / "paper-1.pdf").read_bytes())
        (self.papers / "paper-1.pdf").unlink()
        self._update(
            "UPDATE papers SET filepath=? WHERE id='paper-1'",
            (str(external_pdf),),
        )
        seen = {}

        def manifest(path, *, paper_id, source_file_hash=None):
            resolved = Path(path).resolve()
            self.assertFalse(resolved.is_relative_to(external.resolve()))
            seen[paper_id] = resolved.read_bytes()
            return self._manifest(
                resolved, paper_id=paper_id, source_file_hash=source_file_hash
            )

        with patch(
            "services.shadow_generation.build_pdf_evidence", side_effect=manifest
        ):
            result = refresh_generation(
                data_root=self.data,
                production_wiki_root=self.wiki,
                generation_parent=self.paths["generation_parent"],
                control_root=self.paths["control_root"],
                state_root=self.paths["state_root"],
                source_roots=(self.papers, external),
            )
        self.assertEqual(result["status"], "promoted")
        self.assertEqual(seen["paper-1"], b"synthetic-pdf-1")

    def test_external_root_inventory_never_skips_bounded_source_hash(self):
        external = self.root / "external-library"
        external.mkdir()
        external_pdf = external / "paper-1.pdf"
        external_pdf.write_bytes((self.papers / "paper-1.pdf").read_bytes())
        (self.papers / "paper-1.pdf").unlink()
        self._update(
            "UPDATE papers SET filepath=? WHERE id='paper-1'",
            (str(external_pdf),),
        )
        roots = (self.papers, external)
        trusted = library_revision(
            self.data / "knowledge.db", source_roots=roots
        )
        cache = next(
            entry["source_cache"] for entry in trusted["entries"]
            if entry["paper_id"] == "paper-1"
        )
        self.assertFalse(cache["stat_reliable"])
        module = __import__(
            "services.ask_generation_refresh", fromlist=["_hash_sources_with_deadline"]
        )
        with patch(
            "services.ask_generation_refresh._hash_sources_with_deadline",
            wraps=module._hash_sources_with_deadline,
        ) as hasher:
            revised = library_revision(
                self.data / "knowledge.db",
                source_roots=roots,
                trusted_revision=trusted,
                deadline=time.perf_counter() + 2,
            )
        hasher.assert_called_once()
        self.assertEqual(revised["revision"], trusted["revision"])

    def test_coarse_managed_stat_is_not_a_persistent_cache_authority(self):
        stat_result = MagicMock(
            st_dev=1,
            st_ino=2,
            st_ctime_ns=3_010_000_000,
            st_mtime_ns=4_020_000_000,
            st_birthtime_ns=2_030_000_000,
        )
        self.assertFalse(_persistent_stat_cache_reliable(
            source_identity="root-0/paper.pdf", stat_result=stat_result
        ))

    def test_precise_birthtime_alone_cannot_authorize_persistent_cache(self):
        stat_result = MagicMock(
            st_dev=1,
            st_ino=2,
            st_ctime_ns=3_010_000_000,
            st_mtime_ns=4_020_000_000,
            st_birthtime_ns=2_030_000_001,
        )
        self.assertFalse(_persistent_stat_cache_reliable(
            source_identity="root-0/paper.pdf", stat_result=stat_result
        ))

    def test_precise_mtime_with_coarse_ctime_cannot_authorize_persistent_cache(self):
        stat_result = MagicMock(
            st_dev=1,
            st_ino=2,
            st_ctime_ns=3_010_000_000,
            st_mtime_ns=4_020_000_001,
            st_birthtime_ns=2_030_000_001,
        )
        self.assertFalse(_persistent_stat_cache_reliable(
            source_identity="root-0/paper.pdf", stat_result=stat_result
        ))

    def test_precise_ctime_can_authorize_managed_persistent_cache(self):
        stat_result = MagicMock(
            st_dev=1,
            st_ino=2,
            st_ctime_ns=3_010_000_001,
            st_mtime_ns=4_020_000_000,
            st_birthtime_ns=2_030_000_000,
        )
        self.assertTrue(_persistent_stat_cache_reliable(
            source_identity="root-0/paper.pdf", stat_result=stat_result
        ))

    def test_refresh_status_bounds_untrusted_hash_and_exposes_timeout(self):
        with patch(
            "services.ask_generation_refresh._hash_sources_with_deadline",
            side_effect=AskGenerationRefreshError(
                "library revision exceeded its deadline"
            ),
        ) as hasher:
            status = refresh_status(
                live_data_root=self.data,
                control_root=self.paths["control_root"],
                deadline_seconds=0.01,
            )
        self.assertEqual(status["freshness"], "unavailable")
        self.assertEqual(status["reason"], "library_revision_timeout")
        self.assertIsNone(status["library_revision"])
        self.assertIsInstance(hasher.call_args.kwargs["deadline"], float)

    def test_coarse_same_inode_in_place_replacement_is_stale_without_deadline(self):
        source = self.papers / "paper-1.pdf"
        fixed_stat = source.stat()
        real_stat = Path.stat
        self._update("UPDATE papers SET file_hash='' WHERE id='paper-1'")

        def fixed_source_stat(path, *args, **kwargs):
            if Path(path) == source:
                return fixed_stat
            return real_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", new=fixed_source_stat), patch(
            "services.ask_generation_refresh._persistent_stat_cache_reliable",
            return_value=False,
        ):
            first = self._refresh()
            active = resolve_active_generation(
                control_root=self.paths["control_root"], live_data_root=self.data
            )
            recorded = generation_revision(Path(active["generation_root"]))
            replacement = b"replacement-000"
            self.assertEqual(len(replacement), fixed_stat.st_size)
            source.write_bytes(replacement)
            status = refresh_status(
                live_data_root=self.data, control_root=self.paths["control_root"]
            )
            current = library_revision(self.data / "knowledge.db")

        self.assertEqual(first["status"], "promoted")
        self.assertEqual(status["freshness"], "stale")
        self.assertNotEqual(current["revision"], recorded["revision"])
        current_cache = next(
            entry["source_cache"] for entry in current["entries"]
            if entry["paper_id"] == "paper-1"
        )
        self.assertEqual(current_cache["source_sha256"], _hash(replacement))

    def test_configured_external_scan_root_is_the_only_external_allowlist(self):
        external = self.root / "configured-external"
        external.mkdir()
        with patch("path_utils.DATA_DIR", self.data), patch(
            "config.load_config", return_value={"scan_directory": str(external)}
        ):
            roots = configured_source_roots(self.data)
        self.assertEqual(roots, (self.papers.resolve(), external.resolve()))

        outside = self.root / "not-configured" / "paper.pdf"
        outside.parent.mkdir()
        outside.write_bytes((self.papers / "paper-1.pdf").read_bytes())
        self._update(
            "UPDATE papers SET filepath=? WHERE id='paper-1'", (str(outside),)
        )
        status = refresh_status(
            live_data_root=self.data,
            control_root=self.paths["control_root"],
            source_roots=roots,
        )
        self.assertEqual(status["freshness"], "unavailable")

    def test_pdf_replacement_is_sha_bound_and_only_reparses_changed_source(self):
        self._refresh()
        replacement = b"replacement-pdf-one"
        (self.papers / "paper-1.pdf").write_bytes(replacement)
        self._update(
            "UPDATE papers SET file_hash=? WHERE id='paper-1'",
            (hashlib.md5(replacement).hexdigest(),),
        )
        self.assertEqual(
            refresh_status(
                live_data_root=self.data, control_root=self.paths["control_root"]
            )["freshness"],
            "stale",
        )
        request_refresh(state_root=self.paths["state_root"], reason="pdf_replaced")
        result, parsed = self._refresh_with_parser()
        self.assertEqual(parsed, 1)
        active = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        recorded = generation_revision(Path(active["generation_root"]))
        self.assertEqual(recorded["schema_version"], 2)
        paper = next(item for item in recorded["entries"] if item["paper_id"] == "paper-1")
        self.assertEqual(len(paper["evidence_digest"]), 64)
        manifest = json.loads(
            (Path(active["generation_root"]) / "evidence" / "paper-1.json").read_text()
        )
        self.assertEqual(manifest["source"]["file_hash_sha256"], _hash(replacement))

    def test_same_size_mtime_preserving_replacement_invalidates_hash_cache(self):
        self._refresh()
        source = self.papers / "paper-1.pdf"
        original = source.stat()
        replacement = b"replacement-000"
        self.assertEqual(len(replacement), original.st_size)
        source.write_bytes(replacement)
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
        self._update(
            "UPDATE papers SET file_hash=? WHERE id='paper-1'",
            (hashlib.md5(replacement).hexdigest(),),
        )
        self.assertNotEqual(source.stat().st_ctime_ns, original.st_ctime_ns)
        status = refresh_status(
            live_data_root=self.data, control_root=self.paths["control_root"]
        )
        self.assertEqual(status["freshness"], "stale")

    def test_same_stat_replacement_with_different_inode_cannot_reuse_persistent_hash(self):
        self._refresh()
        active = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        trusted = generation_revision(Path(active["generation_root"]))
        source = self.papers / "paper-1.pdf"
        old_cache = next(
            entry["source_cache"] for entry in trusted["entries"]
            if entry["paper_id"] == "paper-1"
        )
        replacement = self.papers / ".paper-1.replacement"
        replacement_bytes = b"replacement-000"
        self.assertEqual(len(replacement_bytes), source.stat().st_size)
        replacement.write_bytes(replacement_bytes)
        os.utime(replacement, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns))
        os.replace(replacement, source)
        current = source.stat()
        self.assertNotEqual(old_cache["inode"], current.st_ino)
        self._update(
            "UPDATE papers SET file_hash=? WHERE id='paper-1'",
            (hashlib.md5(replacement_bytes).hexdigest(),),
        )

        forged = json.loads(json.dumps(trusted))
        forged_cache = next(
            entry["source_cache"] for entry in forged["entries"]
            if entry["paper_id"] == "paper-1"
        )
        # Match every persisted stat field except inode. If inode were omitted,
        # this deliberately stale source hash would be accepted.
        forged_cache.update({
            "recorded_file_hash": hashlib.md5(replacement_bytes).hexdigest(),
            "size_bytes": current.st_size,
            "mtime_ns": current.st_mtime_ns,
            "ctime_ns": current.st_ctime_ns,
            "device": current.st_dev,
            "birthtime_ns": getattr(
                current, "st_birthtime_ns",
                int(getattr(current, "st_birthtime", 0) * 1_000_000_000),
            ),
            "stat_reliable": True,
        })
        revised = library_revision(
            self.data / "knowledge.db",
            trusted_revision=forged,
            deadline=time.perf_counter() + 2,
        )
        revised_cache = next(
            entry["source_cache"] for entry in revised["entries"]
            if entry["paper_id"] == "paper-1"
        )
        self.assertEqual(revised_cache["inode"], current.st_ino)
        self.assertEqual(revised_cache["source_sha256"], _hash(replacement_bytes))
        self.assertNotEqual(revised_cache["source_sha256"], old_cache["source_sha256"])

    def test_real_pdf_parser_replacement_updates_page_evidence(self):
        for paper_id, text in (("paper-1", "First version"), ("paper-2", "Second paper")):
            payload = _text_pdf(text)
            (self.papers / f"{paper_id}.pdf").write_bytes(payload)
            self._update(
                "UPDATE papers SET file_hash=? WHERE id=?",
                (hashlib.md5(payload).hexdigest(), paper_id),
            )
        first = refresh_generation(
            data_root=self.data,
            production_wiki_root=self.wiki,
            generation_parent=self.paths["generation_parent"],
            control_root=self.paths["control_root"],
            state_root=self.paths["state_root"],
        )
        first_active = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        first_manifest = json.loads(
            (Path(first_active["generation_root"]) / "evidence" / "paper-1.json").read_text()
        )
        self.assertTrue(any("First version" in item["text"] for item in first_manifest["chunks"]))

        replacement = _text_pdf("Replacement version")
        (self.papers / "paper-1.pdf").write_bytes(replacement)
        self._update(
            "UPDATE papers SET file_hash=? WHERE id='paper-1'",
            (hashlib.md5(replacement).hexdigest(),),
        )
        request_refresh(state_root=self.paths["state_root"], reason="real_pdf_replaced")
        second = refresh_generation(
            data_root=self.data,
            production_wiki_root=self.wiki,
            generation_parent=self.paths["generation_parent"],
            control_root=self.paths["control_root"],
            state_root=self.paths["state_root"],
        )
        self.assertNotEqual(first["generation_id"], second["generation_id"])
        second_active = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        second_manifest = json.loads(
            (Path(second_active["generation_root"]) / "evidence" / "paper-1.json").read_text()
        )
        self.assertTrue(any("Replacement version" in item["text"] for item in second_manifest["chunks"]))
        self.assertFalse(any("First version" in item["text"] for item in second_manifest["chunks"]))

    def test_recorded_md5_mismatch_fails_without_switching_active(self):
        self._refresh()
        active_before = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        (self.papers / "paper-1.pdf").write_bytes(b"tampered-without-db-update")
        request_refresh(state_root=self.paths["state_root"], reason="tampered")
        with self.assertRaisesRegex(AskGenerationRefreshError, "hash does not match"):
            self._refresh()
        active_after = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self.assertEqual(active_after["generation_id"], active_before["generation_id"])
        self.assertEqual(active_after["promotion_id"], active_before["promotion_id"])

    def test_deleted_source_and_legacy_inventory_fail_closed_in_ask(self):
        self._refresh()
        self._update("DELETE FROM papers WHERE id='paper-1'")
        stale = observe_question(
            "Stage 1 changes the model",
            adapter="cli",
            env={"KNOWRA_ASK_SHADOW_MODE": "assist", "KNOWRA_ASK_GENERATION_CONTROL": str(self.paths["control_root"])},
            live_data_root=self.data,
            production_wiki_root=self.wiki,
        )
        self.assertEqual(stale["status"], "unavailable")
        self.assertEqual(stale["reason"], "generation_stale")
        with patch(
            "services.ask_shadow_integration.generation_revision", return_value=None
        ), patch(
            "services.ask_generation_refresh.generation_revision", return_value=None
        ):
            legacy_status = runtime_status(
                {"KNOWRA_ASK_SHADOW_MODE": "assist", "KNOWRA_ASK_GENERATION_CONTROL": str(self.paths["control_root"])},
                live_data_root=self.data,
            )
            legacy_query = observe_question(
                "anything",
                adapter="cli",
                env={"KNOWRA_ASK_SHADOW_MODE": "assist", "KNOWRA_ASK_GENERATION_CONTROL": str(self.paths["control_root"])},
                live_data_root=self.data,
                production_wiki_root=self.wiki,
            )
        self.assertFalse(legacy_status["ready"])
        self.assertEqual(legacy_status["reason"], "generation_revision_missing")
        self.assertEqual(legacy_query["reason"], "generation_revision_missing")

    def test_legacy_generation_is_rebuilt_and_promoted_with_inventory(self):
        current = self._refresh()
        active = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        legacy_root = self.root / "legacy-generations" / "legacy-generation"
        shutil.copytree(Path(active["generation_root"]), legacy_root)
        manifest_path = legacy_root / "generation.json"
        manifest = json.loads(manifest_path.read_text())
        for key in (
            "library_revision",
            "library_paper_count",
            "paper_ids_digest",
            "library_inventory_relpath",
            "library_inventory_sha256",
        ):
            manifest.pop(key, None)
        manifest["generation_id"] = "legacy-generation"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True))
        inventory = legacy_root / "inventory" / "library-inputs.json"
        if inventory.exists():
            inventory.unlink()
        legacy_control = self.root / "legacy-control"
        promote_generation(
            generation_root=legacy_root,
            control_root=legacy_control,
            live_data_root=self.data,
            promotion_id="legacy-promotion",
            reviewer="synthetic-legacy-review",
            reviewed_at="2026-10-31T00:00:00Z",
            acceptance_sha256="a" * 64,
            accepted_failed_pages=0,
            expected_active_sha256=None,
        )
        self.assertIsNone(generation_revision(legacy_root))

        with patch(
            "services.shadow_generation.build_pdf_evidence",
            side_effect=self._manifest,
        ):
            upgraded = refresh_generation(
                data_root=self.data,
                production_wiki_root=self.wiki,
                generation_parent=self.root / "legacy-upgraded-generations",
                control_root=legacy_control,
                state_root=self.root / "legacy-refresh-state",
            )
        self.assertEqual(upgraded["status"], "promoted")
        self.assertEqual(upgraded["previous_generation_id"], "legacy-generation")
        upgraded_active = resolve_active_generation(
            control_root=legacy_control, live_data_root=self.data
        )
        self.assertIsNotNone(generation_revision(Path(upgraded_active["generation_root"])))
        self.assertNotEqual(upgraded["generation_id"], current["generation_id"])

    def test_v1_source_cache_inventory_is_upgraded_not_used(self):
        self._refresh()
        active = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        legacy_root = self.root / "old-schema-generations" / "old-schema"
        shutil.copytree(Path(active["generation_root"]), legacy_root)
        inventory_path = legacy_root / "library-inventory.json"
        inventory = json.loads(inventory_path.read_text())
        inventory["schema_version"] = 1
        for entry in inventory["entries"]:
            for field in ("device", "inode", "birthtime_ns", "stat_reliable"):
                entry["source_cache"].pop(field)
        encoded_entries = json.dumps(
            inventory["entries"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode()
        inventory["revision"] = hashlib.sha256(encoded_entries).hexdigest()
        inventory_path.write_text(json.dumps(inventory, sort_keys=True))
        manifest_path = legacy_root / "generation.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["generation_id"] = "old-schema"
        manifest["library_revision"] = inventory["revision"]
        manifest["library_inventory_sha256"] = _hash(inventory_path.read_bytes())
        manifest_path.write_text(json.dumps(manifest, sort_keys=True))
        legacy_control = self.root / "old-schema-control"
        promote_generation(
            generation_root=legacy_root,
            control_root=legacy_control,
            live_data_root=self.data,
            promotion_id="old-schema-promotion",
            reviewer="synthetic-old-schema-review",
            reviewed_at="2026-10-31T00:00:00Z",
            acceptance_sha256="b" * 64,
            accepted_failed_pages=0,
            expected_active_sha256=None,
        )
        with self.assertRaisesRegex(AskGenerationRefreshError, "inventory"):
            generation_revision(legacy_root)
        with patch(
            "services.shadow_generation.build_pdf_evidence",
            side_effect=self._manifest,
        ):
            upgraded = refresh_generation(
                data_root=self.data,
                production_wiki_root=self.wiki,
                generation_parent=self.root / "old-schema-upgraded-generations",
                control_root=legacy_control,
                state_root=self.root / "old-schema-refresh-state",
            )
        self.assertEqual(upgraded["status"], "promoted")
        self.assertEqual(upgraded["previous_generation_id"], "old-schema")
        upgraded_active = resolve_active_generation(
            control_root=legacy_control, live_data_root=self.data
        )
        self.assertEqual(
            generation_revision(Path(upgraded_active["generation_root"]))["schema_version"],
            2,
        )

    def test_initial_promotion_change_reports_no_previous_active(self):
        request_refresh(state_root=self.paths["state_root"], reason="initial")
        module = __import__(
            "services.ask_generation_refresh", fromlist=["promote_generation"]
        )
        original = module.promote_generation

        def promote_then_change(**kwargs):
            result = original(**kwargs)
            self._update("UPDATE papers SET notes='changed after initial switch' WHERE id='paper-1'")
            return result

        with patch(
            "services.ask_generation_refresh.promote_generation",
            side_effect=promote_then_change,
        ), self.assertRaisesRegex(
            AskGenerationRefreshError, "initial generation promotion.*no previous active"
        ):
            self._refresh()
        active = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self.assertIsNotNone(active)
        self.assertEqual(
            refresh_status(
                live_data_root=self.data, control_root=self.paths["control_root"]
            )["freshness"],
            "stale",
        )

    def test_initial_promotion_change_and_marker_fsync_failure_are_both_reported(self):
        module = __import__(
            "services.ask_generation_refresh", fromlist=["promote_generation"]
        )
        original = module.promote_generation

        def promote_then_change(**kwargs):
            result = original(**kwargs)
            self._update("UPDATE papers SET notes='changed without old active' WHERE id='paper-1'")
            return result

        with patch(
            "services.ask_generation_refresh.promote_generation",
            side_effect=promote_then_change,
        ), patch(
            "services.ask_generation_refresh.request_refresh",
            side_effect=OSError("synthetic marker fsync failure"),
        ), self.assertRaisesRegex(
            AskGenerationRefreshError,
            "initial generation promotion.*no previous active.*marker failed",
        ) as raised:
            self._refresh()
        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertFalse(
            (self.paths["state_root"] / REFRESH_REQUEST_FILENAME).exists()
        )
        active = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self.assertIsNotNone(active)
        self.assertEqual(
            refresh_status(
                live_data_root=self.data, control_root=self.paths["control_root"]
            )["freshness"],
            "stale",
        )

    def test_atomic_marker_file_fsync_failure_never_publishes_marker(self):
        marker = self.paths["state_root"] / "fsync-file.json"
        with patch(
            "services.ask_generation_refresh.os.fsync",
            side_effect=OSError("synthetic file fsync failure"),
        ), self.assertRaisesRegex(OSError, "file fsync failure"):
            _atomic_json(marker, {"value": 1})
        self.assertFalse(marker.exists())

    def test_atomic_marker_directory_fsync_failure_leaves_valid_recoverable_marker(self):
        marker = self.paths["state_root"] / "fsync-directory.json"
        with patch(
            "services.ask_generation_refresh.os.fsync",
            side_effect=[None, OSError("synthetic directory fsync failure")],
        ), self.assertRaisesRegex(OSError, "directory fsync failure"):
            _atomic_json(marker, {"value": 1})
        self.assertEqual(json.loads(marker.read_text()), {"value": 1})

    def test_generation_tree_fsync_distinguishes_file_directory_and_parent_stages(self):
        tree = self.root / "fsync-tree"
        tree.mkdir()
        (tree / "artifact").write_text("content")
        for effects, message in (
            ([OSError("file stage")], "file stage"),
            ([None, OSError("directory stage")], "directory stage"),
            ([None, None, OSError("parent stage")], "parent stage"),
        ):
            with self.subTest(stage=message), patch(
                "services.ask_generation_refresh.os.fsync", side_effect=effects
            ), self.assertRaisesRegex(OSError, message):
                _fsync_generation_tree(tree)

    def test_revision_failure_is_unavailable_and_never_raises_from_ask(self):
        self._refresh()
        with patch(
            "services.ask_shadow_integration.library_revision",
            side_effect=AskGenerationRefreshError("synthetic read failure"),
        ):
            result = observe_question(
                "question",
                adapter="cli",
                env={"KNOWRA_ASK_SHADOW_MODE": "assist", "KNOWRA_ASK_GENERATION_CONTROL": str(self.paths["control_root"])},
                live_data_root=self.data,
                production_wiki_root=self.wiki,
            )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "library_revision_unavailable")

    def test_post_promotion_change_is_detected_and_remains_requested(self):
        self._refresh()
        active_before = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self._update("UPDATE papers SET notes='before' WHERE id='paper-1'")
        request_refresh(state_root=self.paths["state_root"], reason="before")
        module = __import__(
            "services.ask_generation_refresh", fromlist=["promote_generation"]
        )
        original = module.promote_generation

        def promote_then_change(**kwargs):
            result = original(**kwargs)
            self._update("UPDATE papers SET notes='after' WHERE id='paper-1'")
            return result

        with patch(
            "services.ask_generation_refresh.promote_generation",
            side_effect=promote_then_change,
        ), self.assertRaisesRegex(AskGenerationRefreshError, "during generation promotion"):
            self._refresh()
        self.assertTrue(
            (self.paths["state_root"] / REFRESH_REQUEST_FILENAME).is_file()
        )
        active_after = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self.assertEqual(active_after["generation_id"], active_before["generation_id"])
        self.assertEqual(active_after["promotion_id"], active_before["promotion_id"])
        self.assertEqual(
            refresh_status(
                live_data_root=self.data, control_root=self.paths["control_root"]
            )["freshness"],
            "stale",
        )

    def test_marker_write_failure_cannot_prevent_post_promotion_rollback(self):
        self._refresh()
        active_before = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self._update("UPDATE papers SET notes='before marker failure' WHERE id='paper-1'")
        request_refresh(state_root=self.paths["state_root"], reason="before")
        module = __import__(
            "services.ask_generation_refresh", fromlist=["promote_generation"]
        )
        original = module.promote_generation

        def promote_then_change(**kwargs):
            result = original(**kwargs)
            self._update("UPDATE papers SET notes='after marker failure' WHERE id='paper-1'")
            return result

        with patch(
            "services.ask_generation_refresh.promote_generation",
            side_effect=promote_then_change,
        ), patch(
            "services.ask_generation_refresh.request_refresh",
            side_effect=OSError("synthetic marker fsync failure"),
        ), self.assertRaisesRegex(AskGenerationRefreshError, "rolled back.*marker failed"):
            self._refresh()
        active_after = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self.assertEqual(active_after["generation_id"], active_before["generation_id"])
        self.assertEqual(active_after["promotion_id"], active_before["promotion_id"])

    def test_generation_tree_fsync_failure_never_switches_active(self):
        self._refresh()
        active_before = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self._update("UPDATE papers SET notes='fsync' WHERE id='paper-1'")
        request_refresh(state_root=self.paths["state_root"], reason="fsync")
        with patch(
            "services.ask_generation_refresh._fsync_generation_tree",
            side_effect=OSError("synthetic generation fsync failure"),
        ), self.assertRaisesRegex(OSError, "generation fsync failure"):
            self._refresh()
        active_after = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self.assertEqual(active_after["active_sha256"], active_before["active_sha256"])
        self.assertTrue(
            (self.paths["state_root"] / REFRESH_REQUEST_FILENAME).is_file()
        )

    def test_promotion_disk_failure_keeps_old_active_and_candidate_for_forensics(self):
        self._refresh()
        active_before = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self._update("UPDATE papers SET notes='disk failure case' WHERE id='paper-1'")
        request_refresh(state_root=self.paths["state_root"], reason="disk_failure")
        before = set(self.paths["generation_parent"].iterdir())
        with patch(
            "services.ask_generation_refresh.promote_generation",
            side_effect=OSError("synthetic fsync failure"),
        ), self.assertRaisesRegex(OSError, "fsync failure"):
            self._refresh()
        active_after = resolve_active_generation(
            control_root=self.paths["control_root"], live_data_root=self.data
        )
        self.assertEqual(active_after["active_sha256"], active_before["active_sha256"])
        candidates = set(self.paths["generation_parent"].iterdir()) - before
        self.assertEqual(len(candidates), 1)
        self.assertTrue(next(iter(candidates)).is_dir())
        self.assertTrue(
            (self.paths["state_root"] / REFRESH_REQUEST_FILENAME).is_file()
        )


if __name__ == "__main__":
    unittest.main()

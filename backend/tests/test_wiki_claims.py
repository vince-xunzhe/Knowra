from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.paper_evidence_service import build_evidence_manifest
from services.wiki_claims import (
    CONCEPT_CLAIM_ROOTS,
    ClaimPackageError,
    build_claim_catalog,
    render_claim_blocks,
    validate_claim_catalog,
)
from services.wiki_shadow_compiler import (
    ShadowCompileError,
    build_concept_compile_plan,
    build_paper_compile_plan,
    validate_synthesis,
    write_shadow_artifacts,
)
from services.wiki_compiler import _paper_user_prompt


SOURCE_HASH = hashlib.sha256(b"synthetic-pdf").hexdigest()


def _extraction(**overrides):
    extraction = {
        "title": "Synthetic Evidence Model",
        "abstract_summary": "A" * 12_000,
        "core_contribution": "A compact contribution.",
        "problem": "How should prediction incorporate action?",
        "innovations": {
            "previous_work": "Earlier systems separated prediction and action.",
            "this_work": "The new system couples action-conditioned dynamics.",
            "why_better": "The coupling supports controllable rollouts.",
        },
        "experimental_gains": "SyntheticSet score improves from 10 to 14.",
        "historical_position": {
            "builds_on": "It builds on latent dynamics.",
            "inspired": "It informs later interactive agents.",
            "overall": "A transition toward action-conditioned models.",
        },
        "limitations": "The evaluation uses only a synthetic environment.",
        "techniques": [{"name": "Action conditioning", "role": "dynamics input"}],
        "datasets": [{"name": "SyntheticSet", "purpose": "evaluation"}],
        "baselines": ["Separated predictor"],
        "key_findings": [
            {"short": "Coupling helps", "detail": "Ablation loses four points."}
        ],
        "pytorch_snippet": {
            "module_name": "SyntheticModule",
            "code": "private code " * 2_000,
            "notes": "Code is illustrative only.",
        },
    }
    extraction.update(overrides)
    return extraction


def _paper_plan(**overrides):
    arguments = {
        "paper_id": "paper-synthetic",
        "title": "Synthetic Evidence Model",
        "extraction": _extraction(),
        "notes": "A private synthetic note.",
        "model": "synthetic-model",
        "locale": "zh",
        "source_file_hash": SOURCE_HASH,
    }
    arguments.update(overrides)
    return build_paper_compile_plan(**arguments)


class WikiClaimPackageTests(unittest.TestCase):
    def test_shadow_path_leaves_v1_prompt_contract_unchanged(self):
        paper = type("Paper", (), {
            "id": "paper-synthetic",
            "filename": "synthetic.pdf",
            "title": "Synthetic Evidence Model",
            "authors": ["Ada Example"],
            "notes": "",
        })()
        prompt = _paper_user_prompt(paper, _extraction())
        self.assertIn("## 抽取 JSON", prompt)
        self.assertNotIn("STRUCTURED EXTRACTION CLAIMS", prompt)

    def test_field_budgets_preserve_late_research_fields(self):
        catalog = build_claim_catalog(
            paper_id="paper-synthetic", extraction=_extraction()
        )
        paths = {claim["field_path"]: claim for claim in catalog["claims"]}
        self.assertTrue(paths["abstract_summary"]["truncated"])
        for expected in (
            "innovations.this_work",
            "experimental_gains",
            "historical_position.overall",
            "limitations",
            "techniques[0].name",
            "datasets[0].name",
            "baselines[0]",
        ):
            self.assertIn(expected, paths)
        self.assertNotIn("pytorch_snippet.code", paths)
        self.assertGreater(
            catalog["retention"]["source_characters"],
            catalog["retention"]["retained_characters"],
        )
        validate_claim_catalog(catalog)

    def test_candidate_evidence_never_becomes_verified_support(self):
        manifest = build_evidence_manifest(
            paper_id="paper-synthetic",
            source_file_hash=SOURCE_HASH,
            source_size_bytes=128,
            total_pages=1,
            pages=[{
                "page_number": 1,
                "status": "ok",
                "text": "Introduction\nSynthetic source evidence.",
            }],
            parser_version="pypdf-synthetic",
        )
        evidence_id = manifest["chunks"][0]["id"]
        catalog = build_claim_catalog(
            paper_id="paper-synthetic",
            extraction=_extraction(),
            evidence_links={"innovations.this_work": [evidence_id]},
            evidence_manifest=manifest,
        )
        claim = next(
            item for item in catalog["claims"]
            if item["field_path"] == "innovations.this_work"
        )
        self.assertEqual(claim["support_level"], "extraction_attribution")
        self.assertEqual(claim["evidence_status"], "candidate_evidence")
        self.assertEqual(claim["candidate_evidence_ids"], [evidence_id])
        unlinked = next(
            item for item in catalog["claims"] if item["field_path"] == "limitations"
        )
        self.assertEqual(unlinked["evidence_status"], "unavailable")
        with self.assertRaisesRegex(ClaimPackageError, "unknown evidence id"):
            build_claim_catalog(
                paper_id="paper-synthetic",
                extraction=_extraction(),
                evidence_links={"limitations": [f"evidence:{'0' * 64}"]},
                evidence_manifest=manifest,
            )
        other_manifest = build_evidence_manifest(
            paper_id="paper-other",
            source_file_hash=SOURCE_HASH,
            source_size_bytes=128,
            total_pages=1,
            pages=[{"page_number": 1, "status": "ok", "text": "other evidence"}],
            parser_version="pypdf-synthetic",
        )
        with self.assertRaisesRegex(ClaimPackageError, "another paper"):
            build_claim_catalog(
                paper_id="paper-synthetic",
                extraction=_extraction(),
                evidence_links={
                    "limitations": [other_manifest["chunks"][0]["id"]]
                },
                evidence_manifest=other_manifest,
            )

    def test_links_without_manifest_fail_closed(self):
        with self.assertRaisesRegex(ClaimPackageError, "require a validated manifest"):
            build_claim_catalog(
                paper_id="paper-synthetic",
                extraction=_extraction(),
                evidence_links={"limitations": [f"evidence:{'0' * 64}"]},
            )

        invalid_manifest = build_evidence_manifest(
            paper_id="paper-synthetic",
            source_file_hash=SOURCE_HASH,
            source_size_bytes=128,
            total_pages=1,
            pages=[{"page_number": 1, "status": "ok", "text": "evidence"}],
            parser_version="pypdf-synthetic",
        )
        invalid_manifest["coverage"]["ok_pages"] = 0
        with self.assertRaisesRegex(ClaimPackageError, "failed validation"):
            build_claim_catalog(
                paper_id="paper-synthetic",
                extraction=_extraction(),
                evidence_links={},
                evidence_manifest=invalid_manifest,
            )

    def test_catalog_and_rendering_fail_closed_without_cutting_claims(self):
        catalog = build_claim_catalog(
            paper_id="paper-synthetic", extraction=_extraction()
        )
        rendered, included, omitted = render_claim_blocks(
            catalog,
            roots=CONCEPT_CLAIM_ROOTS,
            max_characters=900,
        )
        self.assertTrue(included)
        self.assertTrue(omitted)
        self.assertNotIn("A" * 500, rendered)
        for claim_id in included:
            self.assertIn(claim_id, rendered)
        tampered = copy.deepcopy(catalog)
        tampered["claims"][0]["text"] += "tampered"
        with self.assertRaisesRegex(ClaimPackageError, "retained hash mismatch"):
            validate_claim_catalog(tampered)

    def test_paper_plan_binds_payload_template_and_generation_config(self):
        plan = _paper_plan()
        repeated = _paper_plan()
        changed = _paper_plan(generation_config={"temperature": 0.1})
        self.assertEqual(plan, repeated)
        self.assertNotEqual(plan["source_signature_v2"], changed["source_signature_v2"])
        for expected in (
            "Earlier systems separated prediction and action.",
            "The new system couples action-conditioned dynamics.",
            "SyntheticSet score improves from 10 to 14.",
            "A transition toward action-conditioned models.",
            "The evaluation uses only a synthetic environment.",
        ):
            self.assertIn(expected, plan["user_prompt"])
        self.assertNotIn("private code", plan["user_prompt"])
        expected_hash = hashlib.sha256(json.dumps(
            plan["bounded_payload"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode()).hexdigest()
        self.assertEqual(
            plan["signature_inputs"]["bounded_payload_hash"], expected_hash
        )

    def test_concept_plan_allocates_every_paper_a_fair_capsule(self):
        sources = [{
            "paper_id": f"paper-{index:02d}",
            "title": f"Paper {index:02d}",
            "extraction": _extraction(
                title=f"Paper {index:02d}",
                core_contribution="C" * 8_000,
                problem="P" * 8_000,
                motivation="M" * 8_000,
                innovations={"this_work": f"innovation sentinel {index:02d}"},
                experimental_gains=f"experiment sentinel {index:02d}",
                historical_position={"overall": f"history sentinel {index:02d}"},
                limitations=f"limitation sentinel {index:02d}",
            ),
        } for index in range(20)]
        arguments = {
            "concept_id": "concept-synthetic",
            "title": "Action-conditioned dynamics",
            "description": "Synthetic cross-paper concept.",
            "source_papers": sources,
            "model": "synthetic-model",
            "locale": "zh",
        }
        plan = build_concept_compile_plan(**arguments)
        self.assertEqual(plan["coverage"]["source_paper_count"], 20)
        self.assertEqual(len(plan["coverage"]["papers"]), 20)
        self.assertTrue(all(
            item["included_claim_count"] > 0 for item in plan["coverage"]["papers"]
        ))
        for index in range(20):
            self.assertIn(f"PAPER paper-{index:02d}", plan["user_prompt"])
            for category in ("innovation", "experiment", "history", "limitation"):
                self.assertIn(f"{category} sentinel {index:02d}", plan["user_prompt"])
        required_roots = {
            "innovations", "experimental_gains", "historical_position", "limitations"
        }
        self.assertTrue(all(
            required_roots.issubset(set(item["included_roots"]))
            for item in plan["coverage"]["papers"]
        ))
        changed_sources = copy.deepcopy(sources)
        changed_sources[-1]["extraction"]["limitations"] = "changed tail paper"
        changed = build_concept_compile_plan(
            **{**arguments, "source_papers": changed_sources}
        )
        self.assertNotEqual(plan["source_signature_v2"], changed["source_signature_v2"])

    def test_synthesis_rejects_unknown_or_unattributed_claims(self):
        plan = _paper_plan()
        valid_id = next(
            source["source_id"] for source in plan["sources"]
            if source["origin"] == "structured_extraction"
        )
        valid = {"paragraphs": [{
            "section": "建模变化",
            "text": "动作条件进入动力学建模。",
            "source_claim_ids": [valid_id],
        }]}
        self.assertEqual(len(validate_synthesis(plan, valid)), 1)
        invalid_cases = (
            {"paragraphs": [{
                "section": "建模变化", "text": "无归因段落", "source_claim_ids": [],
            }]},
            {"paragraphs": [{
                "section": "建模变化", "text": "未知归因段落",
                "source_claim_ids": [f"extraction-claim:{'0' * 64}"],
            }]},
        )
        for invalid in invalid_cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ShadowCompileError):
                    validate_synthesis(plan, invalid)

        concept = build_concept_compile_plan(
            concept_id="concept-tight-budget",
            title="Tight budget",
            description="",
            source_papers=[{
                "paper_id": f"p{index}",
                "title": f"Paper {index}",
                "extraction": _extraction(),
            } for index in range(100)],
            model="synthetic-model",
            locale="zh",
        )
        omitted_id = next(
            claim_id
            for capsule in concept["bounded_payload"]["source_capsules"]
            for claim_id in capsule["omitted_claim_ids"]
        )
        with self.assertRaisesRegex(ShadowCompileError, "unknown source claim"):
            validate_synthesis(concept, {"paragraphs": [{
                "section": "invalid",
                "text": "References a claim the model never received.",
                "source_claim_ids": [omitted_id],
            }]})

    def test_shadow_write_is_explicit_attributed_and_non_overlapping(self):
        plan = _paper_plan()
        claim_id = next(
            source["source_id"] for source in plan["sources"]
            if source["origin"] == "structured_extraction"
        )
        synthesis = {"paragraphs": [{
            "section": "核心贡献",
            "text": "合成段落。",
            "source_claim_ids": [claim_id],
        }]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "shadow"
            production = root / "production-wiki"
            production.mkdir()
            sentinel = production / "sentinel.md"
            sentinel.write_text("unchanged", encoding="utf-8")
            artifacts = write_shadow_artifacts(
                plan=plan, synthesis=synthesis, output_root=output,
                production_wiki_root=production,
            )
            markdown = artifacts["markdown"].read_text(encoding="utf-8")
            sidecar = json.loads(artifacts["claims"].read_text(encoding="utf-8"))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

            outside = root / "outside.json"
            tampered_signature = copy.deepcopy(plan)
            tampered_signature["source_signature_v2"] = "../../outside"
            with self.assertRaisesRegex(ShadowCompileError, "signature"):
                write_shadow_artifacts(
                    plan=tampered_signature,
                    synthesis=synthesis,
                    output_root=output,
                    production_wiki_root=production,
                )
            self.assertFalse(outside.exists())

            for field in ("bounded_payload", "sources", "coverage"):
                tampered_plan = copy.deepcopy(plan)
                if field == "sources":
                    tampered_plan[field].append({
                        "source_id": f"extraction-claim:{'0' * 64}",
                        "origin": "structured_extraction",
                    })
                else:
                    tampered_plan[field]["tampered"] = True
                with self.subTest(tampered_field=field):
                    with self.assertRaisesRegex(ShadowCompileError, "altered"):
                        write_shadow_artifacts(
                            plan=tampered_plan,
                            synthesis=synthesis,
                            output_root=output,
                            production_wiki_root=production,
                        )
            self.assertIn(f"source_claim_ids:{claim_id}", markdown)
            self.assertEqual(sidecar["support_level"], "extraction_attribution")
            self.assertEqual(sidecar["evidence_status"], "unavailable_or_candidate_only")
            self.assertEqual(
                sidecar["paragraphs"][0]["sources"][0]["evidence_status"],
                "unavailable",
            )
            for unsafe in (production, production / "child", root):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaisesRegex(ShadowCompileError, "overlap"):
                        write_shadow_artifacts(
                            plan=plan, synthesis=synthesis, output_root=unsafe,
                            production_wiki_root=production,
                        )

            nested_output = root / "nested-shadow"
            nested_output.mkdir()
            os.symlink(production, nested_output / "papers")
            with self.assertRaisesRegex(ShadowCompileError, "symlink"):
                write_shadow_artifacts(
                    plan=plan,
                    synthesis=synthesis,
                    output_root=nested_output,
                    production_wiki_root=production,
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")


if __name__ == "__main__":
    unittest.main()

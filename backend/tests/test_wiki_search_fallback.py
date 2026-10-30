import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import ask_agent, wiki_search
from services.wiki_compiler import _render_frontmatter


QUESTION = "世界模型与动作模型的结合，在建模上经历了哪些变化？"


def _write_page(path: Path, *, title: str, aliases: list[str], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "title": title,
        "aliases": aliases,
        "slug": path.stem.split("-", 1)[-1],
        "compiled_at": "2026-10-29T00:00:00+00:00",
    }
    path.write_text(
        _render_frontmatter(meta) + f"\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )


class WikiSearchFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.wiki_dir = root / "wiki"
        self.papers_dir = self.wiki_dir / "papers"
        self.concepts_dir = self.wiki_dir / "concepts"
        self.db_path = root / "wiki_search.sqlite"
        _write_page(
            self.papers_dir / "0001-world-models.md",
            title="World Models",
            aliases=["世界模型", "WM"],
            body="World Models predicts environment dynamics for planning.",
        )
        _write_page(
            self.papers_dir / "0002-action-models.md",
            title="Action Models",
            aliases=["动作模型", "AM"],
            body="Action Models represents controllable actions.",
        )
        _write_page(
            self.concepts_dir / "0003-world-action-integration.md",
            title="World-Action Integration",
            aliases=["联合世界动作建模"],
            body="World Models and Action Models are combined for imagined control.",
        )
        _write_page(
            self.concepts_dir / "0004-unrelated.md",
            title="Unrelated Baseline",
            aliases=["无关基线"],
            body="A deliberately unrelated document.",
        )
        self.db_patch = patch.object(wiki_search, "DB_PATH", self.db_path)
        self.wiki_patch = patch.object(wiki_search, "WIKI_DIR", self.wiki_dir)
        self.db_patch.start()
        self.wiki_patch.start()
        wiki_search._clear_entity_cache()
        wiki_search.rebuild_index()

    def tearDown(self):
        wiki_search._clear_entity_cache()
        self.wiki_patch.stop()
        self.db_patch.stop()
        self.temporary.cleanup()

    def test_long_chinese_question_uses_entity_and_then_or(self):
        self.assertEqual(wiki_search.search(QUESTION), [])

        result = wiki_search.search_with_fallback(QUESTION, limit=12)

        self.assertEqual(result["mode"], "entity_and_or")
        self.assertEqual(result["terms"], ["World Models", "Action Models"])
        self.assertEqual(
            [attempt["mode"] for attempt in result["attempts"]],
            ["strict", "entity_and", "entity_or"],
        )
        filenames = [hit["filename"] for hit in result["hits"]]
        self.assertEqual(filenames[0], "0003-world-action-integration.md")
        self.assertIn("0001-world-models.md", filenames)
        self.assertIn("0002-action-models.md", filenames)
        self.assertEqual(len(filenames), len(set(filenames)))

    def test_exact_title_keeps_strict_order_and_scores(self):
        strict_hits = wiki_search.search("World Models", limit=12)

        result = wiki_search.search_with_fallback("World Models", limit=12)

        self.assertEqual(result["mode"], "strict")
        self.assertEqual(result["hits"], strict_hits)
        self.assertEqual(len(result["attempts"]), 1)

    def test_english_alias_resolves_to_canonical_title(self):
        result = wiki_search.search_with_fallback("How does WM plan?", limit=12)

        self.assertEqual(result["mode"], "entity")
        self.assertEqual(result["terms"], ["World Models"])
        self.assertEqual(
            [attempt["mode"] for attempt in result["attempts"]],
            ["strict", "entity"],
        )
        self.assertEqual(result["hits"][0]["filename"], "0001-world-models.md")

    def test_multiple_aliases_prioritize_and_then_deduplicated_or_hits(self):
        result = wiki_search.search_with_fallback("Compare WM with AM", limit=12)

        filenames = [hit["filename"] for hit in result["hits"]]
        self.assertEqual(result["terms"], ["World Models", "Action Models"])
        self.assertEqual(filenames[0], "0003-world-action-integration.md")
        self.assertEqual(len(filenames), len(set(filenames)))

    def test_unknown_question_stops_after_strict_search(self):
        result = wiki_search.search_with_fallback("完全未知且无实体的问题", limit=12)

        self.assertEqual(result["mode"], "strict_no_match")
        self.assertEqual(result["terms"], [])
        self.assertEqual(result["hits"], [])
        self.assertEqual([item["mode"] for item in result["attempts"]], ["strict"])

    def test_short_ascii_alias_does_not_match_inside_another_word(self):
        result = wiki_search.search_with_fallback("Compare example quality", limit=12)

        self.assertEqual(result["mode"], "strict_no_match")
        self.assertEqual(result["terms"], [])
        self.assertEqual(result["hits"], [])

    def test_rebuild_invalidates_alias_cache_as_one_snapshot(self):
        first = wiki_search.search_with_fallback("How does WM plan?", limit=12)
        self.assertEqual(first["terms"], ["World Models"])

        _write_page(
            self.papers_dir / "0001-world-models.md",
            title="World Models",
            aliases=["世界模型", "WMA"],
            body="World Models predicts environment dynamics for planning.",
        )
        wiki_search.rebuild_index()

        old_alias = wiki_search.search_with_fallback("How does WM plan?", limit=12)
        new_alias = wiki_search.search_with_fallback("How does WMA plan?", limit=12)
        self.assertEqual(old_alias["mode"], "strict_no_match")
        self.assertEqual(new_alias["terms"], ["World Models"])
        self.assertGreater(len(new_alias["hits"]), 0)

    def test_punctuation_only_query_does_not_build_invalid_fts(self):
        result = wiki_search.search_with_fallback("？！——", limit=12)

        self.assertEqual(result["mode"], "strict_no_match")
        self.assertEqual(result["attempts"][0]["query"], "")
        self.assertEqual(result["hits"], [])

    def test_codex_cli_reads_real_fallback_hits_and_exposes_trace_mode(self):
        with patch.object(ask_agent, "WIKI_PAPERS_DIR", self.papers_dir), patch.object(
            ask_agent, "WIKI_CONCEPTS_DIR", self.concepts_dir
        ), patch("services.ask_agent._tool_list_wiki_index", return_value="# Synthetic index"), patch(
            "services.ask_agent._provider_type_for_model", return_value="codex_cli"
        ), patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent.call_text_model", return_value="## Synthetic answer"
        ) as model_call:
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question=QUESTION,
                api_key="",
                model="codex-cli/gpt-test",
            )

        self.assertEqual(result.trace[1].tool, "search_wiki")
        self.assertIn('"mode": "entity_and_or"', result.trace[1].result_summary)
        self.assertIn('"executed_queries"', result.trace[1].result_summary)
        self.assertGreaterEqual(
            len([step for step in result.trace if step.tool == "read_wiki"]),
            2,
        )
        self.assertEqual(result.trace[-1].tool, "evidence_gate")
        self.assertIn('"status":"supported"', result.trace[-1].result_summary)
        self.assertGreaterEqual(len(result.citations), 2)
        self.assertIn("[read_wiki 材料]", model_call.call_args.kwargs["user"])

    def test_api_tool_loop_receives_structured_fallback_result(self):
        first_response = SimpleNamespace(
            id="resp_search",
            output=[SimpleNamespace(
                type="function_call",
                name="search_wiki",
                arguments=json.dumps({"q": QUESTION}, ensure_ascii=False),
                call_id="call_search",
            )],
            output_text="",
        )
        second_response = SimpleNamespace(
            id="resp_done",
            output=[],
            output_text="## Synthetic API answer",
        )
        client = MagicMock()
        client.responses.create.side_effect = [first_response, second_response]

        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent._provider_type_for_model", return_value="openai"
        ), patch(
            "services.ask_agent.create_openai_client_for_model",
            return_value=(client, "gpt-test", {"id": "openai"}, {}),
        ), patch("services.ask_agent.model_uses_responses_api", return_value=True):
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question=QUESTION,
                api_key="test",
                model="gpt-test",
            )

        tool_output = client.responses.create.call_args_list[1].kwargs["input"][0]["output"]
        payload = json.loads(tool_output)
        self.assertEqual(payload["mode"], "entity_and_or")
        self.assertGreater(len(payload["hits"]), 0)
        self.assertEqual(result.trace[0].tool, "search_wiki")


if __name__ == "__main__":
    unittest.main()

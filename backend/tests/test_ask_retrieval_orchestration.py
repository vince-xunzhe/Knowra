import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import ask_agent
from services.ask_retrieval_budget import RetrievalBudget


def _response_call(name: str, args: dict, call_id: str):
    return SimpleNamespace(
        id=f"resp_{call_id}",
        output=[SimpleNamespace(
            type="function_call",
            name=name,
            arguments=json.dumps(args, ensure_ascii=False),
            call_id=call_id,
        )],
        output_text="",
    )


def _final_response(text: str = "## Synthetic answer\n\nDone."):
    return SimpleNamespace(id="resp_done", output=[], output_text=text)


def _search_payload(*filenames: str, terms=None) -> str:
    return json.dumps({
        "mode": "synthetic",
        "terms": list(terms or []),
        "attempts": [],
        "hits": [
            {
                "kind": "paper",
                "filename": filename,
                "title": filename,
                "snippet": "synthetic",
            }
            for filename in filenames
        ],
    }, ensure_ascii=False)


def _budget_snapshot(result) -> dict:
    step = next(item for item in result.trace if item.tool == "retrieval_budget")
    return json.loads(step.raw_result)


class AskRetrievalOrchestrationTests(unittest.TestCase):
    def test_safe_read_honors_the_total_character_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "page.md"
            path.write_text("x" * 200, encoding="utf-8")

            result = ask_agent._safe_read(path, max_chars=24)

        self.assertLessEqual(len(result), 24)

    def test_read_character_and_time_budgets_block_before_dispatch(self):
        now = [0.0]
        budget = RetrievalBudget(
            max_read_calls=2,
            max_read_chars=10,
            max_elapsed_ms=20,
            clock=lambda: now[0],
        )

        def dispatch(_db, _name, _args, *, read_max_chars=0):
            return "x" * read_max_chars

        with patch("services.ask_agent._dispatch_tool", side_effect=dispatch) as mocked:
            first, _ = ask_agent._execute_tool_once(
                MagicMock(),
                "read_wiki",
                {"filename": "0001-alpha.md", "kind": "papers"},
                set(),
                budget,
            )
            second, _ = ask_agent._execute_tool_once(
                MagicMock(),
                "read_wiki",
                {"filename": "0002-beta.md", "kind": "papers"},
                set(),
                budget,
            )

        self.assertEqual(len(first), 10)
        self.assertIn("read_chars 10/10", second)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(budget.read_calls, 1)
        self.assertEqual(budget.read_chars, 10)

        now[0] = 0.021
        with patch("services.ask_agent._dispatch_tool") as late_dispatch:
            late, _ = ask_agent._execute_tool_once(
                MagicMock(), "search_wiki", {"q": "Alpha"}, set(), budget,
            )
        self.assertIn("elapsed_ms", late)
        late_dispatch.assert_not_called()

    def test_model_call_budget_is_hard_and_measured(self):
        budget = RetrievalBudget(max_model_calls=1)
        call = MagicMock(return_value="first")

        first, first_reason = ask_agent._run_budgeted_model_call(budget, call)
        second, second_reason = ask_agent._run_budgeted_model_call(budget, call)

        self.assertEqual(first, "first")
        self.assertIsNone(first_reason)
        self.assertIsNone(second)
        self.assertIn("model_calls 1/1", second_reason)
        call.assert_called_once()
        self.assertEqual(budget.model_calls, 1)
        self.assertEqual(budget.blocked_calls, 1)

    def test_remaining_timeout_and_compact_summary_expose_the_full_budget(self):
        now = [10.0]
        budget = RetrievalBudget(max_elapsed_ms=20_000, clock=lambda: now[0])
        now[0] = 17.0
        budget.record_block("duplicate_tool_call")
        budget.record_block("search_calls 4/4")

        self.assertEqual(budget.remaining_timeout_s(), 13)
        summary = budget.summary_text()
        self.assertLessEqual(len(summary), ask_agent.SUMMARY_MAX_CHARS)
        for field in (
            "calls=m0/9,t0/16,s0/4,r0/5,g0/6",
            "chars=w0/60000,i0/16000",
            "elapsed_ms=7000/20000",
            "duration_ms=m0,t0",
            "blocked=2",
            "reasons=duplicate,search_calls",
        ):
            self.assertIn(field, summary)

        maximum = RetrievalBudget(clock=lambda: 0.0)
        maximum.model_calls = maximum.max_model_calls
        maximum.tool_calls = maximum.max_tool_calls
        maximum.search_calls = maximum.max_search_calls
        maximum.read_calls = maximum.max_read_calls
        maximum.graph_calls = maximum.max_graph_calls
        maximum.read_chars = maximum.max_read_chars
        maximum.index_chars = maximum.max_index_chars
        maximum.model_duration_ms = maximum.max_model_calls * maximum.max_elapsed_ms
        maximum.tool_duration_ms = maximum.max_tool_calls * maximum.max_elapsed_ms
        for reason in (
            "duplicate_tool_call",
            "elapsed_ms 180000/180000",
            "model_calls 9/9",
            "tool_calls 16/16",
            "search_calls 4/4",
            "read_calls 5/5",
            "read_chars 60000/60000",
            "graph_calls 6/6",
        ):
            maximum.record_block(reason)
        maximum_summary = maximum.summary_text()
        self.assertLessEqual(len(maximum_summary), ask_agent.SUMMARY_MAX_CHARS)
        self.assertIn(
            "reasons=duplicate,elapsed_ms,model_calls,tool_calls,search_calls,"
            "read_calls,read_chars,graph_calls",
            maximum_summary,
        )

    def test_index_is_truncated_and_measured_before_entering_model_context(self):
        budget = RetrievalBudget()
        with patch(
            "services.ask_agent.wiki_index.read_index",
            return_value="x" * (ask_agent.MAX_INDEX_CHARS + 500),
        ):
            result, _ = ask_agent._execute_tool_once(
                MagicMock(), "list_wiki_index", {}, set(), budget,
            )

        self.assertEqual(len(result), ask_agent.MAX_INDEX_CHARS)
        self.assertIn("索引已截断", result)
        self.assertEqual(budget.index_chars, ask_agent.MAX_INDEX_CHARS)
        self.assertEqual(budget.tool_calls, 1)

    def test_normalized_tool_calls_cannot_bypass_duplicate_protection(self):
        budget = RetrievalBudget()
        seen: set[str] = set()
        with patch(
            "services.ask_agent._dispatch_tool", return_value=_search_payload()
        ) as dispatch:
            first, _ = ask_agent._execute_tool_once(
                MagicMock(),
                "search_wiki",
                {"q": "  Alpha   Beta  ", "route": "primary"},
                seen,
                budget,
            )
            duplicate, _ = ask_agent._execute_tool_once(
                MagicMock(),
                "search_wiki",
                {"q": "alpha beta", "route": "entity:Alpha"},
                seen,
                budget,
            )

        self.assertIn('"hits": []', first)
        self.assertIn("duplicate tool call blocked", duplicate)
        dispatch.assert_called_once()

        with patch(
            "services.ask_agent._dispatch_tool", return_value="# Synthetic evidence"
        ) as dispatch:
            first, _ = ask_agent._execute_tool_once(
                MagicMock(),
                "read_wiki",
                {"filename": "0001-alpha.md", "kind": "paper"},
                seen,
                budget,
            )
            duplicate, _ = ask_agent._execute_tool_once(
                MagicMock(),
                "read_wiki",
                {
                    "filename": "0001-alpha.md",
                    "kind": "papers",
                    "route": "entity:Alpha",
                },
                seen,
                budget,
            )

        self.assertEqual(first, "# Synthetic evidence")
        self.assertIn("duplicate tool call blocked", duplicate)
        dispatch.assert_called_once()
        self.assertEqual(budget.search_calls, 1)
        self.assertEqual(budget.read_calls, 1)
        self.assertEqual(budget.blocked_calls, 2)

        with patch(
            "services.ask_agent._dispatch_tool", return_value='{"nodes":[]}'
        ) as dispatch:
            first, _ = ask_agent._execute_tool_once(
                MagicMock(), "find_nodes", {"query": "Alpha"}, seen, budget,
            )
            duplicate, _ = ask_agent._execute_tool_once(
                MagicMock(),
                "find_nodes",
                {"query": " alpha ", "limit": 20, "node_type": None},
                seen,
                budget,
            )

        self.assertEqual(first, '{"nodes":[]}')
        self.assertIn("duplicate tool call blocked", duplicate)
        dispatch.assert_called_once()
        self.assertEqual(budget.graph_calls, 1)

        self.assertEqual(
            ask_agent._tool_call_fingerprint(
                "find_shared_neighbors", {"node_ids": ["b", "a"]},
            ),
            ask_agent._tool_call_fingerprint(
                "find_shared_neighbors", {"node_ids": ["a", "b"]},
            ),
        )
        self.assertNotEqual(
            ask_agent._tool_call_fingerprint(
                "find_shared_neighbors", {"node_ids": ["a", "a"]},
            ),
            ask_agent._tool_call_fingerprint(
                "find_shared_neighbors", {"node_ids": ["a"]},
            ),
        )

    def test_graph_call_budget_blocks_before_the_second_dispatch(self):
        budget = RetrievalBudget(max_graph_calls=1)
        with patch(
            "services.ask_agent._dispatch_tool", return_value='{"nodes":[],"count":0}'
        ) as dispatch:
            first, _ = ask_agent._execute_tool_once(
                MagicMock(), "find_nodes", {"query": "Alpha"}, set(), budget,
            )
            second, _ = ask_agent._execute_tool_once(
                MagicMock(), "find_nodes", {"query": "Beta"}, set(), budget,
            )

        self.assertIn('"count":0', first)
        self.assertIn("graph_calls 1/1", second)
        dispatch.assert_called_once()
        self.assertEqual(budget.graph_calls, 1)

    def test_cli_uses_bounded_entity_routes_balanced_reads_and_one_synthesis(self):
        primary = _search_payload(
            "0001-combined.md", "0002-alpha.md",
            terms=["Alpha", "Beta"],
        )
        searches = {
            "比较 Alpha 与 Beta": primary,
            "Alpha": _search_payload("0002-alpha.md", "0004-alpha-detail.md"),
            "Beta": _search_payload("0003-beta.md", "0005-beta-detail.md"),
        }

        def search(q: str) -> str:
            return searches[q]

        def read(filename: str, kind: str, *, max_chars: int):
            return f"# {filename}\n\nSynthetic {kind} evidence."[:max_chars]

        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent._provider_type_for_model", return_value="codex_cli"
        ), patch(
            "services.ask_agent._tool_list_wiki_index", return_value="# Synthetic index"
        ), patch(
            "services.ask_agent._tool_search_wiki", side_effect=search
        ), patch(
            "services.ask_agent._tool_read_wiki", side_effect=read
        ) as read_wiki, patch(
            "services.ask_agent.call_text_model", return_value="## Synthesis\n\nDone."
        ) as answer_model:
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question="比较 Alpha 与 Beta",
                api_key="",
                model="codex-cli/gpt-test",
            )

        search_steps = [step for step in result.trace if step.tool == "search_wiki"]
        self.assertEqual(
            [step.args["route"] for step in search_steps],
            ["primary", "entity:Alpha", "entity:Beta"],
        )
        self.assertEqual(
            [call.args[0] for call in read_wiki.call_args_list],
            [
                "0001-combined.md",
                "0002-alpha.md",
                "0003-beta.md",
                "0004-alpha-detail.md",
            ],
        )
        answer_model.assert_called_once()
        self.assertIn("[search_wiki 多路结果]", answer_model.call_args.kwargs["user"])
        self.assertGreater(answer_model.call_args.kwargs["timeout_s"], 0)
        self.assertLessEqual(answer_model.call_args.kwargs["timeout_s"], 180)
        self.assertEqual(result.trace[-2].tool, "retrieval_budget")
        self.assertEqual(result.trace[-1].tool, "evidence_gate")
        snapshot = _budget_snapshot(result)
        self.assertEqual(snapshot["used"]["model_calls"], 1)
        self.assertEqual(snapshot["used"]["search_calls"], 3)
        self.assertEqual(snapshot["used"]["read_calls"], 4)
        self.assertEqual(snapshot["used"]["tool_calls"], 8)
        self.assertIn('"status":"supported"', result.trace[-1].result_summary)

    def test_cli_stops_entity_expansion_at_the_search_budget(self):
        terms = [f"Entity {index}" for index in range(6)]

        def search(q: str) -> str:
            if q == "synthetic question":
                return _search_payload("0001-primary.md", terms=terms)
            return _search_payload(f"000{terms.index(q) + 2}-{terms.index(q)}.md")

        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent._provider_type_for_model", return_value="codex_cli"
        ), patch(
            "services.ask_agent._tool_list_wiki_index", return_value="# Synthetic index"
        ), patch(
            "services.ask_agent._tool_search_wiki", side_effect=search
        ) as search_wiki, patch(
            "services.ask_agent._tool_read_wiki", return_value="# Evidence"
        ), patch(
            "services.ask_agent.call_text_model", return_value="## Synthesis\n\nDone."
        ):
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question="synthetic question",
                api_key="",
                model="codex-cli/gpt-test",
            )

        self.assertEqual(search_wiki.call_count, 4)
        snapshot = _budget_snapshot(result)
        self.assertEqual(snapshot["used"]["search_calls"], 4)
        self.assertEqual(snapshot["used"]["blocked_calls"], 1)
        self.assertIn("search_calls 4/4", snapshot["blocked_reasons"])

    def test_cli_tries_the_next_balanced_candidate_after_a_failed_read(self):
        search_result = _search_payload(
            "0001-missing.md",
            "0002-alpha.md",
            "0003-beta.md",
            "0004-gamma.md",
            "0005-delta.md",
        )

        def read(filename: str, _kind: str, *, max_chars: int):
            if filename == "0001-missing.md":
                return "[file not found: papers/0001-missing.md]"
            return f"# {filename}\n\nEvidence."[:max_chars]

        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent._provider_type_for_model", return_value="codex_cli"
        ), patch(
            "services.ask_agent._tool_list_wiki_index", return_value="# Synthetic index"
        ), patch(
            "services.ask_agent._tool_search_wiki", return_value=search_result
        ), patch(
            "services.ask_agent._tool_read_wiki", side_effect=read
        ) as read_wiki, patch(
            "services.ask_agent.call_text_model", return_value="## Synthesis\n\nDone."
        ):
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question="Synthetic definition",
                api_key="",
                model="codex-cli/gpt-test",
            )

        self.assertEqual(read_wiki.call_count, 5)
        self.assertEqual(len(result.cited_files), 4)
        self.assertNotIn("0001-missing.md", result.cited_files)
        snapshot = _budget_snapshot(result)
        self.assertEqual(snapshot["used"]["read_calls"], 5)

    def test_responses_api_refines_after_zero_hits_then_reads(self):
        client = MagicMock()
        client.responses.create.side_effect = [
            _response_call("search_wiki", {"q": "broad question"}, "search_broad"),
            _response_call("search_wiki", {"q": "Alpha"}, "search_alpha"),
            _response_call(
                "read_wiki",
                {"filename": "0001-alpha.md", "kind": "paper"},
                "read_alpha",
            ),
            _final_response(),
        ]

        def dispatch(_db, name, args, *, read_max_chars=ask_agent.READ_MAX_CHARS):
            if name == "search_wiki" and args["q"] == "broad question":
                return _search_payload()
            if name == "search_wiki":
                return _search_payload("0001-alpha.md")
            if name == "read_wiki":
                return "# Alpha\n\nSynthetic evidence."[:read_max_chars]
            raise AssertionError(name)

        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent.create_openai_client_for_model",
            return_value=(client, "gpt-test", {"id": "openai"}, {}),
        ), patch("services.ask_agent.model_uses_responses_api", return_value=True), patch(
            "services.ask_agent._dispatch_tool", side_effect=dispatch
        ):
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question="Alpha 是什么？",
                api_key="test",
                model="gpt-test",
            )

        self.assertEqual(
            [step.tool for step in result.trace],
            [
                "search_wiki",
                "search_wiki",
                "read_wiki",
                "retrieval_budget",
                "evidence_gate",
            ],
        )
        self.assertEqual(result.cited_files, ["data/wiki/papers/0001-alpha.md"])
        snapshot = _budget_snapshot(result)
        self.assertEqual(snapshot["used"]["model_calls"], 4)
        self.assertEqual(snapshot["used"]["search_calls"], 2)
        self.assertEqual(snapshot["used"]["read_calls"], 1)
        for call in client.responses.create.call_args_list:
            self.assertGreater(call.kwargs["timeout"], 0)
            self.assertLessEqual(call.kwargs["timeout"], 180)

    def test_responses_api_blocks_searches_beyond_the_budget(self):
        client = MagicMock()
        client.responses.create.side_effect = [
            *[
                _response_call("search_wiki", {"q": f"query {index}"}, f"search_{index}")
                for index in range(6)
            ],
            _final_response("## Unsupported\n\nShould be discarded."),
        ]

        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent.create_openai_client_for_model",
            return_value=(client, "gpt-test", {"id": "openai"}, {}),
        ), patch("services.ask_agent.model_uses_responses_api", return_value=True), patch(
            "services.ask_agent._dispatch_tool", return_value=_search_payload()
        ) as dispatch:
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question="Unknown content question",
                api_key="test",
                model="gpt-test",
            )

        self.assertEqual(dispatch.call_count, 4)
        blocked = [
            step for step in result.trace
            if "retrieval budget reached" in step.raw_result
        ]
        self.assertEqual(len(blocked), 2)
        snapshot = _budget_snapshot(result)
        self.assertEqual(snapshot["used"]["model_calls"], 7)
        self.assertEqual(snapshot["used"]["search_calls"], 4)
        self.assertEqual(snapshot["used"]["blocked_calls"], 2)
        self.assertIn("## 证据不足", result.answer)
        self.assertNotIn("Should be discarded", result.answer)


if __name__ == "__main__":
    unittest.main()

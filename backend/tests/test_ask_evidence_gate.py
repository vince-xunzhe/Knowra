import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import ask_agent


def _search_hits(*filenames: str) -> str:
    return json.dumps([
        {
            "kind": "paper",
            "filename": filename,
            "title": filename,
            "snippet": "synthetic match",
        }
        for filename in filenames
    ])


class AskEvidenceGateTests(unittest.TestCase):
    def test_tool_control_sentinels_never_count_as_read_evidence(self):
        for result in (
            "[duplicate tool call blocked; use the existing result]",
            "[tool call limit reached: 16]",
            "[unknown tool: read_wiki]",
        ):
            self.assertFalse(ask_agent._successful_wiki_read(result))

    def _run_local(
        self,
        *,
        question: str,
        search_result: str,
        read_results=None,
        model_answer: str = "## 合成回答\n\nSynthetic answer.",
    ):
        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent._provider_type_for_model", return_value="codex_cli"
        ), patch(
            "services.ask_agent._tool_list_wiki_index", return_value="# Synthetic index"
        ), patch(
            "services.ask_agent._tool_search_wiki", return_value=search_result
        ), patch(
            "services.ask_agent._tool_read_wiki",
            side_effect=read_results,
        ) as read_wiki, patch(
            "services.ask_agent.call_text_model", return_value=model_answer
        ) as answer_model:
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question=question,
                api_key="",
                model="codex-cli/gpt-test",
            )
        return result, answer_model, read_wiki

    def test_local_zero_hits_refuses_without_answer_model(self):
        result, answer_model, read_wiki = self._run_local(
            question="Alpha 的核心结论是什么？",
            search_result=_search_hits(),
            read_results=[],
        )

        self.assertIn("## 证据不足", result.answer)
        self.assertIn("没有成功读取", result.answer)
        self.assertEqual(result.cited_files, [])
        self.assertEqual(result.citations, [])
        self.assertEqual(result.trace[-1].tool, "evidence_gate")
        self.assertIn('"status":"insufficient"', result.trace[-1].result_summary)
        answer_model.assert_not_called()
        read_wiki.assert_not_called()

    def test_cross_source_question_with_one_read_refuses_before_model(self):
        result, answer_model, _ = self._run_local(
            question="比较 Alpha 与 Beta 的变化",
            search_result=_search_hits("0001-alpha.md"),
            read_results=["# Alpha\n\nSynthetic evidence."],
        )

        self.assertIn("需要至少 2 份", result.answer)
        self.assertEqual(result.citations, [])
        self.assertIn('"required_content_sources":2', result.trace[-1].result_summary)
        answer_model.assert_not_called()

    def test_multiple_search_entities_require_two_sources_without_broad_change_keyword(self):
        search_result = json.dumps({
            "mode": "entity_and_or",
            "terms": ["Alpha", "Beta"],
            "hits": json.loads(_search_hits("0001-alpha.md")),
        })
        result, answer_model, _ = self._run_local(
            question="Alpha 与 Beta 在建模上经历了哪些变化？",
            search_result=search_result,
            read_results=["# Alpha\n\nSynthetic evidence."],
        )

        self.assertIn("需要至少 2 份", result.answer)
        self.assertIn('"search_entity_terms":2', result.trace[-1].result_summary)
        answer_model.assert_not_called()

    def test_single_integration_question_is_not_automatically_multi_source(self):
        self.assertFalse(ask_agent._question_needs_multiple_sources(
            "How does the integration change the latent state?"
        ))

    def test_successful_read_is_the_only_trusted_model_citation(self):
        result, answer_model, _ = self._run_local(
            question="Alpha 的定义是什么？",
            search_result=_search_hits("0001-alpha.md"),
            read_results=["# Alpha\n\nSynthetic evidence."],
            model_answer=(
                "## 回答\n\nAlpha 的合成定义。[[paper:999]]\n\n"
                "## 📚 引用来源\n\n- `data/wiki/papers/9999-fake.md`"
            ),
        )

        answer_model.assert_called_once()
        self.assertEqual(result.cited_files, ["data/wiki/papers/0001-alpha.md"])
        self.assertEqual([item["paper_id"] for item in result.citations], [1])
        self.assertNotIn("paper:999", result.answer)
        self.assertNotIn("9999-fake", result.answer)
        self.assertIn("data/wiki/papers/0001-alpha.md", result.answer)
        self.assertIn("当前只成功读取 1 份", result.answer)

    def test_two_successful_reads_meet_cross_source_gate(self):
        result, answer_model, _ = self._run_local(
            question="比较 Alpha 与 Beta 的变化",
            search_result=_search_hits("0001-alpha.md", "0002-beta.md"),
            read_results=["# Alpha\n\nEvidence A.", "# Beta\n\nEvidence B."],
        )

        answer_model.assert_called_once()
        self.assertEqual(len(result.citations), 2)
        self.assertNotIn("证据边界", result.answer)
        self.assertIn('"status":"supported"', result.trace[-1].result_summary)

    def test_responses_answer_without_tools_is_replaced(self):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(
            id="resp_done",
            output=[],
            output_text="## Unsupported\n\nInvented claim. [[paper:999]]",
        )
        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent.create_openai_client_for_model",
            return_value=(client, "gpt-test", {"id": "openai"}, {}),
        ), patch("services.ask_agent.model_uses_responses_api", return_value=True):
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question="Alpha 的核心结论是什么？",
                api_key="test",
                model="gpt-test",
            )

        self.assertIn("## 证据不足", result.answer)
        self.assertNotIn("Invented claim", result.answer)
        self.assertEqual(result.citations, [])
        self.assertEqual(
            [step.tool for step in result.trace],
            ["retrieval_budget", "evidence_gate"],
        )

    def test_failed_read_does_not_become_a_citation(self):
        first = SimpleNamespace(
            id="resp_read",
            output=[SimpleNamespace(
                type="function_call",
                name="read_wiki",
                arguments='{"filename":"0001-missing.md","kind":"papers"}',
                call_id="call_read",
            )],
            output_text="",
        )
        second = SimpleNamespace(
            id="resp_done", output=[], output_text="## Unsupported\n\nInvented claim.",
        )
        client = MagicMock()
        client.responses.create.side_effect = [first, second]
        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent.create_openai_client_for_model",
            return_value=(client, "gpt-test", {"id": "openai"}, {}),
        ), patch("services.ask_agent.model_uses_responses_api", return_value=True), patch(
            "services.ask_agent._dispatch_tool",
            return_value="[file not found: papers/0001-missing.md]",
        ):
            result = ask_agent.run_ask_agent(
                MagicMock(), question="Alpha 是什么？", api_key="test", model="gpt-test",
            )

        self.assertIn("## 证据不足", result.answer)
        self.assertEqual(result.citations, [])
        self.assertEqual(result.trace[-1].tool, "evidence_gate")

    def test_responses_singular_kind_is_normalized_after_a_successful_read(self):
        first = SimpleNamespace(
            id="resp_read",
            output=[SimpleNamespace(
                type="function_call",
                name="read_wiki",
                arguments='{"filename":"0003-alpha.md","kind":"paper"}',
                call_id="call_read",
            )],
            output_text="",
        )
        second = SimpleNamespace(
            id="resp_done", output=[], output_text="## Answer\n\nSynthetic answer.",
        )
        client = MagicMock()
        client.responses.create.side_effect = [first, second]
        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent.create_openai_client_for_model",
            return_value=(client, "gpt-test", {"id": "openai"}, {}),
        ), patch("services.ask_agent.model_uses_responses_api", return_value=True), patch(
            "services.ask_agent._dispatch_tool", return_value="# Alpha\n\nSynthetic evidence."
        ):
            result = ask_agent.run_ask_agent(
                MagicMock(), question="Alpha 是什么？", api_key="test", model="gpt-test",
            )

        self.assertIn("## Answer", result.answer)
        self.assertEqual(result.cited_files, ["data/wiki/papers/0003-alpha.md"])
        self.assertEqual(result.citations[0]["paper_id"], 3)

    def test_generic_indexing_question_cannot_use_the_catalog_exception(self):
        self.assertFalse(ask_agent._question_is_catalog_request(
            "How does the indexing method work?"
        ))
        self.assertFalse(ask_agent._question_is_catalog_request(
            "有哪些论文支持 Alpha 的结论？"
        ))
        self.assertTrue(ask_agent._question_is_catalog_request(
            "Show the library index"
        ))
        self.assertTrue(ask_agent._question_is_catalog_request(
            "知识库里有哪些论文？"
        ))

        first = SimpleNamespace(
            id="resp_index",
            output=[SimpleNamespace(
                type="function_call", name="list_wiki_index", arguments="{}",
                call_id="call_index",
            )],
            output_text="",
        )
        second = SimpleNamespace(
            id="resp_done", output=[], output_text="## Unsupported\n\nUnsupported claim.",
        )
        client = MagicMock()
        client.responses.create.side_effect = [first, second]
        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent.create_openai_client_for_model",
            return_value=(client, "gpt-test", {"id": "openai"}, {}),
        ), patch("services.ask_agent.model_uses_responses_api", return_value=True), patch(
            "services.ask_agent._dispatch_tool", return_value="# Synthetic index"
        ):
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question="有哪些论文支持 Alpha 的结论？",
                api_key="test",
                model="gpt-test",
            )

        self.assertIn("## 证据不足", result.answer)
        self.assertNotIn("Unsupported claim", result.answer)
        self.assertEqual(result.citations, [])

    def test_chat_api_uses_the_same_verified_read_contract(self):
        tool_call = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(
                name="read_wiki",
                arguments='{"filename":"0007-alpha.md","kind":"papers"}',
            ),
        )
        first_message = SimpleNamespace(content="", tool_calls=[tool_call])
        second_message = SimpleNamespace(
            content=(
                "## Chat answer\n\nSupported by the supplied page. [[paper:888]]\n\n"
                "## Sources\n\n- fake.md"
            ),
            tool_calls=[],
        )
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            SimpleNamespace(choices=[SimpleNamespace(message=first_message)]),
            SimpleNamespace(choices=[SimpleNamespace(message=second_message)]),
        ]
        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent.create_openai_client_for_model",
            return_value=(client, "chat-test", {"id": "openai"}, {}),
        ), patch("services.ask_agent.model_uses_responses_api", return_value=False), patch(
            "services.ask_agent._dispatch_tool", return_value="# Alpha\n\nSynthetic evidence."
        ):
            result = ask_agent.run_ask_agent(
                MagicMock(), question="Alpha 是什么？", api_key="test", model="chat-test",
            )

        self.assertIn("## Chat answer", result.answer)
        self.assertNotIn("paper:888", result.answer)
        self.assertNotIn("fake.md", result.answer)
        self.assertEqual(result.cited_files, ["data/wiki/papers/0007-alpha.md"])
        self.assertEqual(result.citations[0]["paper_id"], 7)
        budget_step = next(
            step for step in result.trace if step.tool == "retrieval_budget"
        )
        budget = json.loads(budget_step.raw_result)
        self.assertEqual(budget["used"]["model_calls"], 2)
        self.assertEqual(budget["used"]["read_calls"], 1)
        for call in client.chat.completions.create.call_args_list:
            self.assertGreater(call.kwargs["timeout"], 0)
            self.assertLessEqual(call.kwargs["timeout"], 180)

    def test_verified_graph_path_allows_only_a_structural_answer(self):
        first = SimpleNamespace(
            id="resp_graph",
            output=[SimpleNamespace(
                type="function_call",
                name="shortest_path",
                arguments='{"source_id":"paper:a","target_id":"paper:b"}',
                call_id="call_graph",
            )],
            output_text="",
        )
        second = SimpleNamespace(
            id="resp_done", output=[], output_text="## Structure\n\nA verified path exists.",
        )
        client = MagicMock()
        client.responses.create.side_effect = [first, second]
        graph_result = json.dumps({
            "found": True,
            "nodes": [{"id": "paper:a"}, {"id": "paper:b"}],
            "edges": [{"id": "edge:ab"}],
        })
        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent.create_openai_client_for_model",
            return_value=(client, "gpt-test", {"id": "openai"}, {}),
        ), patch("services.ask_agent.model_uses_responses_api", return_value=True), patch(
            "services.ask_agent._dispatch_tool", return_value=graph_result
        ):
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question="How are Alpha and Beta connected?",
                api_key="test",
                model="gpt-test",
            )

        self.assertIn("## Structure", result.answer)
        self.assertIn("只能回答工具返回的图结构关系", result.answer)
        self.assertEqual(
            [item["ref"] for item in result.citations],
            ["graph-node:paper:a", "graph-node:paper:b", "graph-edge:edge:ab"],
        )
        self.assertIn('"status":"structure_only"', result.trace[-1].result_summary)

    def test_explain_edge_includes_the_single_verified_edge_citation(self):
        first = SimpleNamespace(
            id="resp_edge",
            output=[SimpleNamespace(
                type="function_call",
                name="explain_edge",
                arguments='{"edge_id":"edge:ab"}',
                call_id="call_edge",
            )],
            output_text="",
        )
        second = SimpleNamespace(
            id="resp_done", output=[], output_text="## Structure\n\nVerified edge.",
        )
        client = MagicMock()
        client.responses.create.side_effect = [first, second]
        edge_result = json.dumps({
            "edge": {"id": "edge:ab"},
            "source": {"id": "paper:a"},
            "target": {"id": "paper:b"},
        })
        with patch("services.ask_agent.load_config", return_value={}), patch(
            "services.ask_agent.create_openai_client_for_model",
            return_value=(client, "gpt-test", {"id": "openai"}, {}),
        ), patch("services.ask_agent.model_uses_responses_api", return_value=True), patch(
            "services.ask_agent._dispatch_tool", return_value=edge_result
        ):
            result = ask_agent.run_ask_agent(
                MagicMock(),
                question="How are Alpha and Beta related?",
                api_key="test",
                model="gpt-test",
            )

        self.assertIn("graph-edge:edge:ab", [item["ref"] for item in result.citations])


if __name__ == "__main__":
    unittest.main()

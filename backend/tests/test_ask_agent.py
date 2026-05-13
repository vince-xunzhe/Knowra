import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ask_agent import run_ask_agent


class AskAgentResponsesTests(unittest.TestCase):
    @patch("services.ask_agent._dispatch_tool", return_value="INDEX CONTENT")
    @patch("services.ask_agent.create_openai_client_for_model")
    def test_responses_models_can_complete_tool_loop(
        self,
        mock_create_client,
        mock_dispatch_tool,
    ):
        tool_call = SimpleNamespace(
            type="function_call",
            name="list_wiki_index",
            arguments="{}",
            call_id="call_123",
        )
        first_response = SimpleNamespace(
            id="resp_1",
            output=[tool_call],
            output_text="",
        )
        second_response = SimpleNamespace(
            id="resp_2",
            output=[],
            output_text="## Answer\n\nDone.",
        )

        client = MagicMock()
        client.responses.create.side_effect = [first_response, second_response]
        mock_create_client.return_value = (
            client,
            "gpt-5.4",
            {"id": "openai", "provider_type": "openai"},
            {"id": "openai/gpt-5.4", "upstream_model": "gpt-5.4"},
        )

        result = run_ask_agent(
            MagicMock(),
            question="知识库里有什么？",
            history=[{"role": "user", "content": "先看看总览"}],
            api_key="test-key",
            model="gpt-5.4",
        )

        self.assertEqual(result.answer, "## Answer\n\nDone.")
        self.assertEqual(result.model, "gpt-5.4")
        self.assertEqual(result.steps, 1)
        self.assertEqual(len(result.trace), 1)
        self.assertEqual(result.trace[0].tool, "list_wiki_index")
        self.assertEqual(result.trace[0].args, {})
        mock_dispatch_tool.assert_called_once_with("list_wiki_index", {})

        first_call = client.responses.create.call_args_list[0].kwargs
        self.assertEqual(first_call["model"], "gpt-5.4")
        self.assertEqual(
            first_call["input"],
            [
                {"role": "user", "content": "先看看总览"},
                {"role": "user", "content": "知识库里有什么？"},
            ],
        )

        second_call = client.responses.create.call_args_list[1].kwargs
        self.assertEqual(second_call["previous_response_id"], "resp_1")
        self.assertEqual(
            second_call["input"],
            [
                {
                    "type": "function_call_output",
                    "call_id": "call_123",
                    "output": "INDEX CONTENT",
                }
            ],
        )

    @patch("services.ask_agent.call_text_model", return_value="## Answer\n\nLocal Codex.")
    @patch("services.ask_agent._tool_read_wiki", return_value="# Paper\n\n内容")
    @patch(
        "services.ask_agent._tool_search_wiki",
        return_value='[{"kind":"paper","filename":"0001-test.md","title":"Test","snippet":"match"}]',
    )
    @patch("services.ask_agent._tool_list_wiki_index", return_value="# Index\n\n- test")
    @patch("services.ask_agent._provider_type_for_model", return_value="codex_cli")
    @patch("services.ask_agent.load_config", return_value={})
    def test_codex_cli_can_answer_via_local_retrieval(
        self,
        mock_load_config,
        mock_provider_type,
        mock_list_index,
        mock_search_wiki,
        mock_read_wiki,
        mock_call_text_model,
    ):
        result = run_ask_agent(
            MagicMock(),
            question="测试问题",
            history=[{"role": "user", "content": "先看知识库"}],
            api_key="",
            model="codex-cli/gpt-5.4",
            reasoning_effort="high",
        )

        self.assertEqual(result.answer, "## Answer\n\nLocal Codex.")
        self.assertEqual(result.model, "codex-cli/gpt-5.4")
        self.assertEqual(result.steps, 3)
        self.assertEqual([step.tool for step in result.trace], ["list_wiki_index", "search_wiki", "read_wiki"])
        self.assertIn("data/wiki/papers/0001-test.md", result.cited_files)
        self.assertEqual(mock_call_text_model.call_args.kwargs["reasoning_effort"], "high")
        self.assertIn("[index.md]", mock_call_text_model.call_args.kwargs["user"])
        self.assertIn("[read_wiki 材料]", mock_call_text_model.call_args.kwargs["user"])
        mock_read_wiki.assert_called_once_with("0001-test.md", "papers")


if __name__ == "__main__":
    unittest.main()

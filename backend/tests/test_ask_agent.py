import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ask_agent import run_ask_agent


class AskAgentResponsesTests(unittest.TestCase):
    @patch("services.ask_agent._dispatch_tool", return_value="INDEX CONTENT")
    @patch("services.ask_agent.OpenAI")
    def test_responses_models_can_complete_tool_loop(
        self,
        mock_openai,
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
        mock_openai.return_value = client

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


if __name__ == "__main__":
    unittest.main()

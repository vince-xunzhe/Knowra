import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers.ask import AskRequest, ask
from services.ask_agent import AskResult


class AskRouterTitleTests(unittest.TestCase):
    @patch("routers.ask.call_text_model", return_value="标题： 因果注意力")
    @patch(
        "routers.ask.ask_agent.run_ask_agent",
        return_value=AskResult(
            answer="## 回答\n\n它负责在注意力里建模因果约束。",
            cited_files=["data/wiki/papers/0001-causal-transformer.md"],
            citations=[
                {
                    "kind": "paper",
                    "ref": "data/wiki/papers/0001-causal-transformer.md",
                    "path": "data/wiki/papers/0001-causal-transformer.md",
                    "filename": "0001-causal-transformer.md",
                    "paper_id": 1,
                }
            ],
            trace=[],
            model="codex-cli/gpt-5.5",
            duration_ms=123,
            steps=2,
        ),
    )
    @patch("routers.ask.task_reasoning_effort", return_value="high")
    @patch("routers.ask.task_model_name", return_value="codex-cli/gpt-5.5")
    @patch("routers.ask.load_config", return_value={})
    def test_ask_response_includes_model_generated_session_title(
        self,
        mock_load_config,
        mock_task_model_name,
        mock_task_reasoning_effort,
        mock_run_ask_agent,
        mock_call_text_model,
    ):
        response = ask(
            AskRequest(
                question="因果注意力在模型构建里扮演什么角色？",
                history=[],
                session_id="ask-session-001",
            ),
            db=MagicMock(),
        )

        self.assertEqual(response.session_title, "因果注意力")
        self.assertEqual(response.session_id, "ask-session-001")
        self.assertEqual(response.model, "codex-cli/gpt-5.5")
        self.assertEqual(response.answer, "## 回答\n\n它负责在注意力里建模因果约束。")
        self.assertEqual(response.citations[0].paper_id, 1)
        self.assertEqual(mock_call_text_model.call_args.kwargs["reasoning_effort"], "low")


if __name__ == "__main__":
    unittest.main()

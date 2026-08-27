import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import _upgrade_extraction_prompt
from prompts import DEFAULT_PAPER_PROMPT
from services.vlm_service import RUNTIME_CATEGORY_MARKER, _with_runtime_paper_categories


LEGACY_PROMPT = """你扮演一位资深的人工智能研究员，正在给初学者讲解这篇论文。我已将 PDF 作为附件上传，请先用 file_search 工具通读全文（正文、图表、公式、参考文献），再按下方 JSON schema 返回抽取结果。语言通俗易懂、多用类比、少堆术语；同时所有图谱所需的"关键字段"都必须完整填写，不得省略。
2. JSON 所有 value 用简体中文书写，面向初学者。
5. 缺失信息时：字符串字段返回 ""，数组字段返回 []，数字字段返回 0；但 **关键字段**（见下方列表）必须尽力填写，不得留空。
6. pytorch_snippet.code 必须是**单个字符串**，内部用真实换行符；严禁使用数组，严禁外包 ```python 围栏。
  title / authors / venue / year / problem_area / tech_stack_position / keywords
图谱结构（决定知识图谱连边与相似度，不能为空）：
  "year": <number|string>,
  "problem_area": <string>,
- year: 公开年份（数字优先）
- principle.key_formulas: **至少列 2-4 条**论文最关键的公式；每条 {name: "式(3) 自注意力" 之类, formula: "公式正文，可用 LaTeX 或论文里的标准写法", plain: "白话解释这条公式在做什么"}；`formula` 必须填写真正公式内容，不能为空，`plain` 不要粘 LaTeX
═══════════ 输出前的自检清单（默念一遍再输出） ═══════════
[ ] principle.key_formulas 至少 2 条，且每条都有非空 formula，且每条都有非空 formula，且每条都有非空 formula
"""


class ExtractionPromptTests(unittest.TestCase):
    def test_legacy_upgrade_is_idempotent_and_repairs_accumulated_suffixes(self):
        upgraded = _upgrade_extraction_prompt(LEGACY_PROMPT)

        self.assertEqual(upgraded, _upgrade_extraction_prompt(upgraded))
        self.assertNotIn("且每条都有非空 formula", upgraded)
        self.assertEqual(upgraded.count('"paper_category": <string>'), 1)
        self.assertEqual(upgraded.count("- paper_category:"), 1)
        self.assertEqual(upgraded.count("═══════════ 证据与保真规则"), 1)
        self.assertIn("file_search，或本地解析的分页正文", upgraded)
        self.assertIn("论文没有关键公式时为 []", upgraded)

    def test_default_prompt_keeps_schema_and_evidence_contract(self):
        required_fields = (
            '"paper_category": <string>',
            '"principle": {',
            '"key_formulas": [',
            '"pytorch_snippet": {',
            '"techniques": [',
            '"datasets": [',
            '"baselines": <string[]>',
            '"contributions": <string[]>',
            '"key_findings": [',
        )

        self.assertEqual(DEFAULT_PAPER_PROMPT, _upgrade_extraction_prompt(DEFAULT_PAPER_PROMPT))
        for field in required_fields:
            self.assertIn(field, DEFAULT_PAPER_PROMPT)
        self.assertIn("证据与保真规则", DEFAULT_PAPER_PROMPT)
        self.assertIn("严禁为了“非空”而编造", DEFAULT_PAPER_PROMPT)
        self.assertIn("换行写成 `\\n`", DEFAULT_PAPER_PROMPT)

    @patch(
        "services.vlm_service.get_active_categories",
        return_value=["VLM", "蒸馏", "其他"],
    )
    def test_runtime_categories_are_current_and_injected_once(self, _mock_categories):
        rendered = _with_runtime_paper_categories("BASE PROMPT")
        rendered_twice = _with_runtime_paper_categories(rendered)

        self.assertEqual(rendered, rendered_twice)
        self.assertEqual(rendered.count(RUNTIME_CATEGORY_MARKER), 1)
        self.assertIn("`VLM` / `蒸馏` / `其他`", rendered)
        self.assertIn("根据论文的主贡献而不是单个关键词选择", rendered)


if __name__ == "__main__":
    unittest.main()

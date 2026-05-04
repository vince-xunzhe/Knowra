"""Default prompt templates for paper extraction."""

DEFAULT_PAPER_PROMPT = """你扮演一位资深的人工智能研究员，正在给初学者讲解这篇论文。我已将 PDF 作为附件上传，请先用 file_search 工具通读全文（正文、图表、公式、参考文献），再按下方 JSON schema 返回抽取结果。语言通俗易懂、多用类比、少堆术语；同时所有图谱所需的"关键字段"都必须完整填写，不得省略。

═══════════ 硬性规则（违反任意一条视为无效） ═══════════
1. 所有 JSON key 必须严格使用下方 schema 给出的英文 snake_case，严禁翻译成中文，严禁新增未定义 key。
   ✗ 错误示例：  "核心贡献" / "原理解析" / "PyTorch代码" / "报告" / "论文身份卡" / "principle_explanation" / "background_status" / "pytorch_code" / "torch_code" / "pitfalls"
   ✓ 正确：     所有 key 原封不动抄自下方 schema。
2. JSON 所有 value 用简体中文书写，面向初学者。
3. 只返回一个顶层 JSON 对象，不要包 "报告"/"result"/"output" 之类的外层 wrapper。
4. 不要任何 markdown 代码围栏（不要 ```json 也不要 ```），不要 file_search 的【...†source】引用标记，不要任何解释性文字。
5. 缺失信息时：字符串字段返回 ""，数组字段返回 []，数字字段返回 0；但 **关键字段**（见下方列表）必须尽力填写，不得留空。
6. pytorch_snippet.code 必须是**单个字符串**，内部用真实换行符；严禁使用数组，严禁外包 ```python 围栏。

═══════════ 关键字段清单（必须每一项都填写） ═══════════
身份与分类（图谱节点合并依赖这些字段，命名要规范）：
  title / authors / venue / year / paper_category / problem_area / tech_stack_position / keywords
图谱结构（决定知识图谱连边与相似度，不能为空）：
  techniques / datasets / baselines / contributions / key_findings
叙事分析（深度解读，每段都要有实质内容，避免敷衍一句话）：
  core_contribution / abstract_summary / problem / motivation
  principle.analogy / principle.architecture_flow / principle.key_formulas
  innovations.previous_work / innovations.this_work / innovations.why_better
  experimental_gains
  historical_position.builds_on / historical_position.inspired / historical_position.overall
  limitations
代码示例：
  pytorch_snippet.module_name / pytorch_snippet.code / pytorch_snippet.notes

═══════════ JSON Schema（key 即契约，一字不改） ═══════════
{
  "title": <string>,
  "authors": <string[]>,
  "venue": <string>,
  "year": <number|string>,
  "paper_category": <string>,
  "problem_area": <string>,
  "tech_stack_position": <string>,
  "keywords": <string[]>,

  "core_contribution": <string>,
  "abstract_summary": <string>,
  "problem": <string>,
  "motivation": <string>,

  "principle": {
    "analogy": <string>,
    "architecture_flow": <string>,
    "key_formulas": [
      {"name": <string>, "formula": <string>, "plain": <string>}
    ]
  },

  "innovations": {
    "previous_work": <string>,
    "this_work": <string>,
    "why_better": <string>
  },

  "experimental_gains": <string>,

  "historical_position": {
    "builds_on": <string>,
    "inspired": <string>,
    "overall": <string>
  },

  "limitations": <string>,

  "pytorch_snippet": {
    "module_name": <string>,
    "code": <string>,
    "notes": <string>
  },

  "techniques": [
    {"name": <string>, "aliases": <string[]>, "role": <string>, "builds_on": <string[]>}
  ],
  "datasets": [
    {"name": <string>, "purpose": <string>}
  ],
  "baselines": <string[]>,
  "contributions": <string[]>,
  "key_findings": [
    {"short": <string>, "detail": <string>}
  ]
}

═══════════ 各字段写作要求（按字段写好 value） ═══════════

身份与分类
- title: 论文原题（英文原题即可）
- authors: 作者列表
- venue: 会议/期刊（含年份前缀），如 "NeurIPS 2024"、"arXiv preprint"
- year: 公开年份（数字优先）
- paper_category: 请严格从以下分类中选择一个：`LLM` / `VLM` / `VLA` / `三维重建-静态` / `三维重建-动态` / `世界模型`；如果都不属于，填 `其他`
- problem_area: 研究领域规范名，如 "NLP"、"CV"、"多模态"、"强化学习"、"图神经网络"
- tech_stack_position: 在大模型技术栈里的定位，如 "基座模型"、"参数高效微调"、"推理优化"、"多模态对齐"、"表征学习"
- keywords: **至少 5-10 个**最具代表性的术语，用于跨论文相似度匹配

叙事分析（每段都要有深度，字数到位）
- core_contribution: 一句话（30-60 字），初学者一读就懂论文解决了什么核心痛点
- abstract_summary: 200 字以内摘要，用自己的话重述，突出读者要带走的关键信息
- problem: 研究问题 30 字内
- motivation: 为什么值得做 50 字内
- principle.analogy: 用日常生活比喻把核心机制讲清楚（120-200 字），不要公式，突出直觉
- principle.architecture_flow: 文字描述数据从输入到输出依次流经哪些模块、每一步发生了什么（120-250 字）；如果论文有架构图，把图用文字读出来
- principle.key_formulas: **至少列 2-4 条**论文最关键的公式；每条 {name: "式(3) 自注意力" 之类, formula: "公式正文，可用 LaTeX 或论文里的标准写法", plain: "白话解释这条公式在做什么"}；`formula` 必须填写真正公式内容，不能为空，`plain` 不要粘 LaTeX
- innovations.previous_work: 在这篇论文之前，同类问题主流是怎么做的？卡在哪里？（80-150 字）
- innovations.this_work: 这篇论文关键改动是什么？（80-150 字）
- innovations.why_better: 为什么新做法更好？从效率 / 效果 / 扩展性 / 简洁性给出理由（80-150 字）
- experimental_gains: 实验比前人好在哪？给具体数字与对比对象（如 "ImageNet top-1 从 76.5 → 80.1"），指出最有说服力的实验（120-200 字）
- historical_position.builds_on: 直接站在哪些前作肩膀上，注明继承关系
- historical_position.inspired: 启发了哪些后续方向或代表工作（如已知）
- historical_position.overall: 在 LLM / VLM / CV / RL 发展史上的地位评价（奠基 / 集大成 / 工程化 / 范式转移）
- limitations: 作者通常不会明说、但实际存在的缺点：假设前提、适用边界、计算代价、数据依赖、复现难度等（120-200 字）

代码示例
- pytorch_snippet.module_name: 要实现的核心模块名，如 "Multi-head Attention"、"LoRA Layer"、"RoPE 位置编码"
- pytorch_snippet.code: 最简 PyTorch 实现。要求：
    · 聚焦核心模块，跳过工程细节
    · 每个关键行末用 " # 中文注释" 说明对应论文哪一个公式或步骤
    · 末尾构造示例输入并 print 输入 / 输出张量 shape
    · 整段写成单个字符串，行间用真实换行符，不要字符串数组，不要 markdown 围栏
- pytorch_snippet.notes: 2-3 句补充：做了哪些简化、与原论文出入在哪、重点看哪几行

图谱字段（决定知识图谱节点合并，这些字段绝不能为空）
- techniques: **至少列 3-8 条**本论文涉及的技术
    · name: 2-10 字技术规范名（如 "Attention"、"LoRA"、"对比学习"），不要写成"论文中提出的 XX 机制"
    · aliases: 别名/全称/缩写
    · role: 在本论文中的作用（20 字内，如 "主干网络"、"baseline"、"优化目标"）
    · builds_on: **必须引用 techniques 数组内其他 name**，形成技术路径
- datasets: **至少列出论文使用的所有数据集**；name 保持论文原名（如 "ImageNet"、"MS-COCO"）；purpose 如 "训练" / "评测" / "预训练"
- baselines: 对比 baseline 方法规范名字符串数组（至少 1-3 个）
- contributions: 贡献点短句数组（至少 2-4 条），每条 15 字内
- key_findings: **至少列 2-4 条**关键结论，每条 {short: 短结论 15 字内, detail: 详细结论 + 数据}

═══════════ 输出前的自检清单（默念一遍再输出） ═══════════
[ ] 顶层所有 key 都是 schema 里的英文 snake_case，没有出现任何中文 key 或半吊子英文变体
[ ] 没有外层 wrapper（"报告" / "result" / "output" / "paper_identity_card"）
[ ] 关键字段全部填写：title / authors / paper_category / keywords / techniques / datasets / baselines / contributions / key_findings 都不为空
[ ] techniques 至少 3 条，datasets 至少 1 条，keywords 至少 5 个，key_findings 至少 2 条
[ ] principle / innovations / historical_position / pytorch_snippet 都是对象结构，子 key 齐全
[ ] principle.key_formulas 至少 2 条，且每条都有非空 formula
[ ] pytorch_snippet.code 是单个字符串，不是数组，也没被 ```python 包着
[ ] 所有叙事字段字数到位，没有一句话敷衍
[ ] 输出前后没有任何多余文字、围栏、引用标记

自检通过后，直接输出 JSON。"""

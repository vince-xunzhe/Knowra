"""Default prompt templates for paper extraction."""

DEFAULT_PAPER_PROMPT = """你是一位学术论文分析助手。我已将一篇论文的 PDF 作为附件上传，请使用 file_search 工具阅读全文（包括正文、图表、公式、参考文献），然后抽取结构化知识并返回 JSON：

{
  "title": "论文标题",
  "authors": ["作者1", "作者2"],
  "venue": "会议/期刊（如 NeurIPS 2024, arXiv）",
  "year": 2024,
  "abstract_summary": "200字以内的简洁摘要，用自己的话重述",
  "problem": "论文要解决的核心问题（30字以内）",
  "motivation": "为什么这个问题重要（50字以内）",
  "techniques": [
    {
      "name": "技术规范名（精简，2-10字，如 'Transformer'、'LoRA'、'对比学习'）",
      "aliases": ["别名/全称/缩写"],
      "role": "在本论文中的作用（20字内，如 '主干网络'、'baseline'、'优化目标'）",
      "builds_on": ["依赖的已有技术规范名"]
    }
  ],
  "datasets": [
    {"name": "数据集名", "purpose": "用途（训练/评测/预训练）"}
  ],
  "baselines": ["对比的 baseline 方法规范名"],
  "contributions": ["贡献点1（15字内）", "贡献点2"],
  "key_findings": [
    {"short": "短结论（15字内）", "detail": "详细结论+数据"}
  ],
  "keywords": ["关键词1", "关键词2", "关键词3"],
  "problem_area": "研究领域规范名（如 'NLP'、'CV'、'强化学习'、'图神经网络'）"
}

关键要求：
1. **techniques.name 必须是规范化的技术名词**，用于图谱节点合并。例如 "Attention" 而不是 "论文中提出的Attention机制"
2. **builds_on 必须引用其他 techniques 里出现过的 name**，形成技术路径图
3. **keywords** 要选最具代表性的 5-10 个术语，作为跨论文相似度匹配依据
4. **datasets/baselines** 保持原名（如 ImageNet、MS-COCO），用于跨论文建立连接
5. 若某些页面 OCR 不清晰或 file_search 检索不到完整信息，基于可读到的部分尽力输出，缺失字段留空数组或空字符串
6. **只返回 JSON**，不要任何解释文字、markdown 代码块围栏、file_search 引用标记"""

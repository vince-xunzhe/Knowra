import os
import json
import re
from pathlib import Path
from prompts import DEFAULT_PAPER_PROMPT, DEFAULT_PROMOTION_PROMPT
from path_utils import PAPERS_DIR, portable_data_path, resolve_papers_directory
from path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from model_gateway import (
    BUILTIN_MODEL_REGISTRY,
    DEFAULT_PROVIDER_REGISTRY,
    TASK_BINDING_DEFAULTS,
    ensure_model_gateway_config,
    get_bound_model_id,
    get_task_binding,
)

CONFIG_FILE = Path(__file__).parent.parent / "data" / "config.json"
DB_PATH = Path(__file__).parent.parent / "data" / "knowledge.db"

# ── Deploy mode ─────────────────────────────────────────────────────────
# Controls whether this FastAPI process runs as:
#   "local"  → desktop sidecar mode: SQLite, no auth, single user, Codex CLI on
#   "cloud"  → multi-tenant cloud mode: Postgres (Supabase), JWT auth, no Codex
# See docs/ARCHITECTURE-CLOUD.md §4.3.
#
# Default is "local" so an unset env var keeps current desktop behavior.
DEPLOY_MODE = os.environ.get("KNOWRA_DEPLOY_MODE", "local").lower().strip()
if DEPLOY_MODE not in {"local", "cloud"}:
    raise RuntimeError(
        f"KNOWRA_DEPLOY_MODE must be 'local' or 'cloud', got: {DEPLOY_MODE!r}"
    )


def is_cloud_mode() -> bool:
    """Convenience predicate used by routers that gate behavior on deploy mode."""
    return DEPLOY_MODE == "cloud"

# Exposed OpenAI models for the frontend dropdown.
# Only file_search-compatible models. Reasoning models (o1/o3) removed because
# the Assistants API + file_search pipeline does not support them.
AVAILABLE_MODELS = [
    {"id": "gpt-5.5", "label": "GPT-5.5", "supports_vision": True, "desc": "最强主力，复杂推理与专业工作"},
    {"id": "gpt-5.4", "label": "GPT-5.4", "supports_vision": True, "desc": "高性能主力，适合深度论文抽取"},
    {"id": "gpt-5.4-mini", "label": "GPT-5.4-mini", "supports_vision": True, "desc": "更快更省，仍支持 file_search"},
    {"id": "gpt-4o", "label": "GPT-4o", "supports_vision": True, "desc": "推荐，视觉+文本"},
    {"id": "gpt-4o-mini", "label": "GPT-4o-mini", "supports_vision": True, "desc": "快，便宜"},
    {"id": "gpt-4-turbo", "label": "GPT-4 Turbo", "supports_vision": True, "desc": "长上下文"},
    {"id": "gpt-4.1", "label": "GPT-4.1", "supports_vision": True, "desc": "最新主力"},
    {"id": "gpt-4.1-mini", "label": "GPT-4.1-mini", "supports_vision": True, "desc": "性价比"},
]

AVAILABLE_EMBEDDING_MODELS = [
    {"id": "text-embedding-3-large", "label": "text-embedding-3-large", "desc": "精度更高，适合更细的语义连接"},
    {"id": "text-embedding-3-small", "label": "text-embedding-3-small", "desc": "默认推荐，成本和效果更均衡"},
]

# Wiki compile is a pure summarization task — no file_search, no PDF —
# so any chat-completions OR responses-capable model works. Defaults below
# are sorted cheap → strong; gpt-4o-mini is the fast/cheap recommendation.
AVAILABLE_WIKI_COMPILE_MODELS = [
    {"id": "gpt-4o-mini", "label": "GPT-4o-mini", "desc": "默认推荐，便宜快速"},
    {"id": "gpt-4.1-mini", "label": "GPT-4.1-mini", "desc": "性价比"},
    {"id": "gpt-4o", "label": "GPT-4o", "desc": "更稳，写作质量更高"},
    {"id": "gpt-4.1", "label": "GPT-4.1", "desc": "主力，更稳"},
    {"id": "gpt-5.4-mini", "label": "GPT-5.4-mini", "desc": "Responses API 通道"},
    {"id": "gpt-5.4", "label": "GPT-5.4", "desc": "Responses API 通道，写作更细"},
    {"id": "gpt-5.5", "label": "GPT-5.5", "desc": "Responses API 通道，最强"},
]

MODEL_GATEWAY_MODEL_OPTIONS = [
    {
        "id": model["id"],
        "label": model["label"],
        "desc": ", ".join(model.get("supported_tasks") or []),
        "supports_vision": bool(model.get("supports_vision", False)),
        "provider_id": model.get("provider_id"),
        "upstream_model": model.get("upstream_model"),
        "model_kind": model.get("model_kind"),
        "supported_tasks": list(model.get("supported_tasks") or []),
        "builtin": bool(model.get("builtin", False)),
    }
    for model in BUILTIN_MODEL_REGISTRY
]


def _upgrade_extraction_prompt(prompt: str) -> str:
    if not prompt:
        return DEFAULT_PAPER_PROMPT

    upgraded = prompt

    def replace_line(old: str, new: str) -> None:
        nonlocal upgraded
        upgraded = re.sub(
            rf"(?m)^{re.escape(old)}$",
            lambda _match: new,
            upgraded,
        )

    # Keep user-edited prompts intact while migrating known legacy wording.
    # Whole-line replacements make this migration safe to run on every load.
    replace_line(
        "你扮演一位资深的人工智能研究员，正在给初学者讲解这篇论文。我已将 PDF 作为附件上传，请先用 file_search 工具通读全文（正文、图表、公式、参考文献），再按下方 JSON schema 返回抽取结果。语言通俗易懂、多用类比、少堆术语；同时所有图谱所需的\"关键字段\"都必须完整填写，不得省略。",
        "你扮演一位资深的人工智能研究员，正在给初学者讲解这篇论文。系统会通过 file_search，或本地解析的分页正文、关键页图像与分段笔记提供论文材料；请使用当前通道实际提供的材料通读正文、图表、公式和参考文献，再按下方 JSON schema 返回抽取结果。语言通俗易懂、多用类比、少堆术语；同时所有图谱所需的\"关键字段\"都必须保留，不得省略对象结构。",
    )
    replace_line(
        "2. JSON 所有 value 用简体中文书写，面向初学者。",
        "2. JSON 中的解释性 value 用简体中文书写，面向初学者；论文原题、作者、公式、代码、方法名、数据集名、指标名和专有名词保留原文，不要强行翻译。",
    )
    replace_line(
        "5. 缺失信息时：字符串字段返回 \"\"，数组字段返回 []，数字字段返回 0；但 **关键字段**（见下方列表）必须尽力填写，不得留空。",
        "5. 缺失信息时：字符串字段返回 \"\"，数组字段返回 []，数字字段返回 0。**关键字段**必须先检索全文、图表和附录并尽力填写；材料确实没有时允许使用对应空值，严禁为了“非空”而编造。",
    )
    replace_line(
        "6. pytorch_snippet.code 必须是**单个字符串**，内部用真实换行符；严禁使用数组，严禁外包 ```python 围栏。",
        "6. pytorch_snippet.code 必须是**单个合法 JSON 字符串**，序列化输出中的换行写成 `\\n`（JSON 解析后成为真实换行）；严禁使用数组，严禁外包 ```python 围栏。",
    )
    replace_line(
        "  title / authors / venue / year / problem_area / tech_stack_position / keywords",
        "  title / authors / venue / year / paper_category / problem_area / tech_stack_position / keywords",
    )
    replace_line(
        "图谱结构（决定知识图谱连边与相似度，不能为空）：",
        "图谱结构（决定知识图谱连边与相似度；对象结构必须存在，内容以论文证据为准）：",
    )
    upgraded = upgraded.replace(
        '{"name": <string>, "plain": <string>}',
        '{"name": <string>, "formula": <string>, "plain": <string>}',
    )
    upgraded = upgraded.replace(
        '  "year": <number|string>,\n  "problem_area": <string>,',
        '  "year": <number|string>,\n  "paper_category": <string>,\n  "problem_area": <string>,',
    )
    if "- paper_category:" not in upgraded:
        replace_line(
            "- year: 公开年份（数字优先）",
            "- year: 公开年份（数字优先）\n- paper_category: 必须从系统在本 Prompt 末尾追加的“运行时可选论文大类”中选择一个最贴近论文主贡献的分类；无法归入时填 `其他`",
        )
    replace_line(
        "- abstract_summary: 200 字以内摘要，用自己的话重述，突出读者要带走的关键信息",
        "- abstract_summary: 200 字以内摘要，用自己的话按“任务 → 方法 → 关键结果 → 意义”重述，突出读者要带走的信息",
    )
    replace_line(
        "- keywords: **至少 5-10 个**最具代表性的术语，用于跨论文相似度匹配",
        "- keywords: 通常列 5-10 个有论文依据、最具代表性的术语，用于跨论文相似度匹配；术语较少时按实际返回，不为凑数量添加泛词",
    )
    replace_line(
        "- principle.architecture_flow: 文字描述数据从输入到输出依次流经哪些模块、每一步发生了什么（120-250 字）；如果论文有架构图，把图用文字读出来",
        "- principle.architecture_flow: 用“输入 → 表征/编码 → 核心模块 → 训练目标或推理步骤 → 输出”的顺序描述数据流（120-250 字），说明张量/信息在每一步如何变化；如果论文有架构图，把图用文字读出来",
    )
    replace_line(
        '- principle.key_formulas: **至少列 2-4 条**论文最关键的公式；每条 {name: "式(3) 自注意力" 之类, plain: "白话解释这条公式在做什么"}；plain 不要粘 LaTeX',
        '- principle.key_formulas: 优先列 2-4 条论文真正出现且最关键的公式；每条 {name: "式(3) 自注意力" 之类, formula: "公式正文，可用 LaTeX 或论文里的标准写法", plain: "解释变量、输入输出以及这条公式解决什么问题"}。不得把普通描述伪造成公式；论文少于 2 条关键公式时按实际数量返回，没有公式则返回 []',
    )
    replace_line(
        '- principle.key_formulas: **至少列 2-4 条**论文最关键的公式；每条 {name: "式(3) 自注意力" 之类, formula: "公式正文，可用 LaTeX 或论文里的标准写法", plain: "白话解释这条公式在做什么"}；`formula` 必须填写真正公式内容，不能为空，`plain` 不要粘 LaTeX',
        '- principle.key_formulas: 优先列 2-4 条论文真正出现且最关键的公式；每条 {name: "式(3) 自注意力" 之类, formula: "公式正文，可用 LaTeX 或论文里的标准写法", plain: "解释变量、输入输出以及这条公式解决什么问题"}。不得把普通描述伪造成公式；论文少于 2 条关键公式时按实际数量返回，没有公式则返回 []',
    )
    replace_line(
        '- experimental_gains: 实验比前人好在哪？给具体数字与对比对象（如 "ImageNet top-1 从 76.5 → 80.1"），指出最有说服力的实验（120-200 字）',
        '- experimental_gains: 说明“数据集 + 指标 + 对比方法 + 本文结果 + 差值”，给出论文中的具体数字，并补充最关键的消融或效率结果；材料没有数字时明确写定性结论，不得猜测（120-200 字）',
    )
    replace_line(
        "- historical_position.inspired: 启发了哪些后续方向或代表工作（如已知）",
        "- historical_position.inspired: 只写论文材料或可靠时间关系能够支持的后续方向/代表工作；无法确认具体后续论文时，说明“材料未提供”，再概括它可能推动的研究方向，不虚构论文名",
    )
    replace_line(
        "- limitations: 作者通常不会明说、但实际存在的缺点：假设前提、适用边界、计算代价、数据依赖、复现难度等（120-200 字）",
        "- limitations: 先概括作者明确承认的限制，再给出有依据的研究者判断；覆盖假设前提、适用边界、计算代价、数据依赖、复现难度等，并区分事实与推断（120-200 字）",
    )
    replace_line(
        "    · 整段写成单个字符串，行间用真实换行符，不要字符串数组，不要 markdown 围栏",
        "    · 代码应可独立运行，整段写成单个 JSON 字符串，换行使用 `\\n` 转义，不要字符串数组，不要 markdown 围栏",
    )
    replace_line(
        "图谱字段（决定知识图谱节点合并，这些字段绝不能为空）",
        "图谱字段（决定知识图谱节点合并；结构必须保留，内容以论文证据为准）",
    )
    replace_line(
        "- techniques: **至少列 3-8 条**本论文涉及的技术",
        "- techniques: 通常列 3-8 条在论文中承担明确作用的技术；不足 3 条时按实际返回，不为凑数量拆分同一机制",
    )
    replace_line(
        "    · builds_on: **必须引用 techniques 数组内其他 name**，形成技术路径",
        "    · builds_on: 只能引用 techniques 数组内其他 name，禁止引用自己；没有前置依赖的根技术填 []",
    )
    replace_line(
        '- datasets: **至少列出论文使用的所有数据集**；name 保持论文原名（如 "ImageNet"、"MS-COCO"）；purpose 如 "训练" / "评测" / "预训练"',
        '- datasets: 列出论文明确使用的所有数据集；name 保持论文原名（如 "ImageNet"、"MS-COCO"）；purpose 说明 "训练" / "评测" / "预训练" / "消融"。纯理论论文未使用数据集时返回 []，不要虚构',
    )
    replace_line(
        "- baselines: 对比 baseline 方法规范名字符串数组（至少 1-3 个）",
        "- baselines: 对比 baseline 方法的规范名字符串数组；论文有对比实验时尽量列 1-3 个，未设置 baseline 时返回 []",
    )
    replace_line(
        "- contributions: 贡献点短句数组（至少 2-4 条），每条 15 字内",
        "- contributions: 优先提炼 2-4 条相互不重复的贡献点短句，每条 15 字内",
    )
    replace_line(
        "- key_findings: **至少列 2-4 条**关键结论，每条 {short: 短结论 15 字内, detail: 详细结论 + 数据}",
        "- key_findings: 优先提炼 2-4 条有证据的关键结论，每条 {short: 短结论 15 字内, detail: 详细结论 + 数据或定性证据}",
    )
    replace_line(
        "[ ] 关键字段全部填写：title / authors / keywords / techniques / datasets / baselines / contributions / key_findings 都不为空",
        "[ ] 所有关键字段和对象结构都存在；有论文证据时，title / authors / paper_category / keywords / techniques / datasets / baselines / contributions / key_findings 已尽量填写",
    )
    replace_line(
        "[ ] techniques 至少 3 条，datasets 至少 1 条，keywords 至少 5 个，key_findings 至少 2 条",
        "[ ] 通常应有 techniques 3-8 条、keywords 5-10 个、key_findings 2-4 条；论文确实没有 datasets / baselines / formulas 时使用 []，没有为凑数量而编造",
    )

    # The old replacement matched its own output, so each config load appended
    # another copy of the formula suffix. Collapse any accumulated copies once.
    upgraded = re.sub(
        r"(?m)^\[ \] principle\.key_formulas 至少 2 条"
        r"(?:，且每条都有非空 formula)*$",
        "[ ] principle.key_formulas 中每一条都有论文依据和非空 formula；论文没有关键公式时为 []",
        upgraded,
    )

    evidence_rules = """═══════════ 证据与保真规则（丰富内容，但不编造） ═══════════
- 论文事实只能来自当前提供的正文、图表、公式、附录和参考文献；不要把常识、同名方法或其他论文的结果混入本文。
- 对实验数字做“对象绑定”：每个数字都要能对应数据集、指标、对比方法或消融设置，避免只堆孤立数字。
- 对方法关系做“证据分层”：论文明确陈述的内容直接写；根据结构合理推断的内容使用“可理解为/可能”表述；无法确认则明确材料不足。
- 图谱实体采用跨论文可复用粒度：技术名用公认名称，避免论文私有长句、超参数和泛化领域词；别名去重，builds_on 不自环。
- 输出信息要密集但不重复：core_contribution 负责一句话定位，abstract_summary 负责全貌，principle 负责机制，innovations 负责相对前作变化，避免同一句话复制到多个字段。
"""
    checklist_marker = "═══════════ 输出前的自检清单（默念一遍再输出） ═══════════"
    if "═══════════ 证据与保真规则" not in upgraded and checklist_marker in upgraded:
        upgraded = upgraded.replace(
            checklist_marker,
            f"{evidence_rules}\n{checklist_marker}",
            1,
        )
    return upgraded


def load_config() -> dict:
    defaults = {
        "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
        "scan_directory": str(PAPERS_DIR),
        "vlm_model": "gpt-4o",
        "embedding_model": "text-embedding-3-small",
        # Used by services.wiki_compiler. Decoupled from vlm_model because
        # compile is plain summarization and doesn't need the strongest model.
        # Env var WIKI_COMPILE_MODEL overrides only at first-run / unset state;
        # once saved into config.json the user's choice wins.
        "wiki_compile_model": os.environ.get("WIKI_COMPILE_MODEL", "gpt-4o-mini"),
        "similarity_threshold": 0.6,
        "use_first_page_image": True,   # kept for backwards-compat; ignored by the Assistants pipeline
        # Extraction chain resilience knobs.
        "paper_process_max_retries": 3,
        "paper_process_backoff_base_seconds": 1.5,
        "paper_process_backoff_max_seconds": 20.0,
        "extraction_prompt": DEFAULT_PAPER_PROMPT,
        # User-editable system prompt for the concept-promotion LLM stage.
        # Default is the built-in template so first-time users get the
        # heuristic + Agent pipeline out of the box. Setting it to ""
        # via the editor is the explicit "skip Agent, only run heuristic"
        # opt-out — preserved for back-compat and power users.
        "promotion_prompt": DEFAULT_PROMOTION_PROMPT,
        "openai_assistant_id": None,    # cached Assistants API assistant id, reused across runs
        "model_gateway": {
            "providers": DEFAULT_PROVIDER_REGISTRY,
            "models": BUILTIN_MODEL_REGISTRY,
            "task_bindings": TASK_BINDING_DEFAULTS,
        },
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            defaults.update(saved)
        except Exception:
            pass
    defaults["extraction_prompt"] = _upgrade_extraction_prompt(
        defaults.get("extraction_prompt", DEFAULT_PAPER_PROMPT)
    )
    if defaults.get("scan_directory"):
        defaults["scan_directory"] = str(
            resolve_papers_directory(defaults["scan_directory"])
        )
    defaults = ensure_model_gateway_config(defaults)
    return defaults


def save_config(updates: dict) -> dict:
    if "scan_directory" in updates:
        updates = {
            **updates,
            "scan_directory": portable_data_path(updates["scan_directory"]),
        }
    current = load_config()
    current.update(updates)
    if "extraction_prompt" in current:
        current["extraction_prompt"] = _upgrade_extraction_prompt(current["extraction_prompt"])
    if current.get("scan_directory"):
        current["scan_directory"] = portable_data_path(current["scan_directory"])
    current = ensure_model_gateway_config(current)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    return current


def task_model_id(cfg: dict, task_id: str) -> str:
    return get_bound_model_id(cfg, task_id)


def task_model_name(cfg: dict, task_id: str) -> str:
    model_id = get_bound_model_id(cfg, task_id)
    if model_id.startswith("openai/"):
        return model_id.split("/", 1)[1]
    return model_id


def task_reasoning_effort(cfg: dict, task_id: str) -> str:
    return get_task_binding(cfg, task_id).get("reasoning_effort") or "medium"

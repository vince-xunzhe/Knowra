"""Synthetic fixture only, copied into the disposable acceptance API snapshot.

No main lifespan, provider configuration, external model, or queue worker runs.
The production domain routes and generation function remain unmodified; only
the queue submission boundary and existing model gateway callable are replaced.
"""
from copy import deepcopy
import json

from database import Base, DB_PATH, SessionLocal, engine
from fastapi import FastAPI, HTTPException
from models import DomainAgentRun, DomainWorkspace, Paper, RoadmapPaper, RoadmapRun, RoadmapWorkspace
from routers import domain_agent, domains
from services import domain_agent as generation
from services.paper_category_service import set_active_categories

QUOTE = "We improve the baseline attention operator by replacing dense scores with a sparse routing step."


def node(key, paper=None, *, kind="paper", x=0, y=0, text="", role=None, locked=False):
    return dict(id=key, kind=kind, paperId=paper, x=x, y=y, width=280, height=160,
                groupId=None, text=text, locked=locked, roadmapRole=role)


def board(key, category, nodes=(), edges=()):
    return dict(id=key, name=category, category=category, nodes=list(nodes), edges=list(edges),
                researchKeywords=["注意力效率"], viewport=dict(x=100, y=90, zoom=.8))


def seed():
    Base.metadata.create_all(bind=engine)
    set_active_categories(["LLM", "视觉", "空领域", "其他"])
    with SessionLocal() as db:
        domain = board("domain-llm", "LLM", [
            node("a", "paper-a", x=50, y=80, text="人工笔记：保留原假设", locked=True),
            node("b", "paper-b", x=450, y=80),
            node("c", "paper-c", x=450, y=400),
            node("d", "paper-d", x=850, y=400),
            node("note", kind="text", x=850, y=80, text="人工整理\n论文仍是画布的主体"),
        ], [dict(id="manual", source="a", target="b", label="人工对照", locked=False),
            dict(id="locked", source="b", target="d", label="人工锁定", locked=True)])
        other = board("domain-vision", "视觉", [node("v-a", "paper-a"), node("v-b", "paper-b", x=400)])
        empty = board("domain-empty", "空领域")
        ds = domains.WorkspaceState.model_validate(dict(activeId="domain-llm", boards=[domain, other, empty, board("domain-other", "其他")])).model_dump()
        db.add(DomainWorkspace(id=1, revision=1, state=ds, admitted={n["id"]:n["paperId"] for b in ds["boards"] for n in b["nodes"] if n["paperId"]}))
        framework = board("roadmap-llm", "LLM", [
            node("topic", kind="text", text="研究问题：如何减少注意力计算成本？", role="topic"),
            node("branch", kind="text", text="稀疏注意力分支", role="branch", x=400),
            node("milestone", "roadmap-paper", role="milestone", x=400, y=300),
        ], [dict(id="structure", source="topic", target="branch", label="组织归属", roadmapStructure=True, locked=False),
            dict(id="framework-evolution", source="branch", target="milestone", label="框架发展线（不证明论文关系）", roadmapStructure=True, locked=False)])
        visual = board("roadmap-vision", "视觉", [node("vision-topic", kind="text", text="视觉定位与重建", role="topic")])
        rs = domains.WorkspaceState.model_validate(dict(activeId="roadmap-llm", boards=[framework, visual, board("roadmap-empty", "空领域"), board("roadmap-other", "其他")])).model_dump()
        db.add(RoadmapWorkspace(id=1, revision=1, state=rs))
        titles = {"a":"合成论文 A：基础注意力", "b":"合成论文 B：人工对照", "c":"合成论文 C：稀疏路由", "d":"合成论文 D：未归类的检索方法"}
        for index, (key,title) in enumerate(titles.items()):
            text = QUOTE if key == "c" else "Synthetic independent method description without evidence of inheritance."
            db.add(Paper(id="paper-"+key, filepath=str(DB_PATH.parent/"papers"/(key+".pdf")),
                filename=key+".pdf", file_hash="synthetic-"+key, title=title, processed=True,
                paper_category_override="LLM", extracted_text=text,
                raw_llm_response=json.dumps(dict(title=title, year=2020+index, core_contribution=text,
                    abstract_summary="隔离验收合成摘要", problem="注意力成本", motivation="可验证的论文关系"), ensure_ascii=False)))
        db.add(RoadmapPaper(id="roadmap-paper", data=dict(id="roadmap-paper", title="合成里程碑：稀疏注意力", year="2021",
            branch="稀疏注意力分支", reason="阶段一：稀疏化", sourceUrl="https://example.invalid/framework-source", doi=None, arxivId=None)))
        db.add(RoadmapRun(id="applied-framework", board_id="roadmap-llm", status="applied", snapshot={"topic":"注意力效率"},
            proposal={"report":"# 合成研究框架\n\n**研究问题**：如何减少注意力计算成本？\n**范围**：注意力效率与稀疏路由\n## 5. 发展历史与范式里程碑\n阶段一：稀疏化；阶段二：检索。\n## 6. 当前研究分支\n稀疏注意力分支。\n[来源](https://example.invalid/framework-source)\n仅为组织依据，不能证明论文继承。"}))
        db.add(RoadmapRun(id="preview-framework", board_id="roadmap-llm", status="preview", snapshot={},
            proposal={"report":"未应用预览：秘密的新方向不应进入框架上下文"}))
        db.commit()
        (DB_PATH.parent / "synthetic-roadmap-fixture.json").write_text(json.dumps(rs, ensure_ascii=False))


class Queue:
    def __init__(self):
        self.jobs = {}

    def submit(self, kind, key, payload, job_id=None):
        key_id = "fixture-" + payload["run_id"]
        self.jobs[key_id] = dict(job_id=key_id, kind=kind, key=key, payload=payload, status="queued", progress={})
        return self.jobs[key_id]

    def get(self, key):
        return self.jobs.get(key)

    def latest(self, key):
        return next((job for job in reversed(list(self.jobs.values())) if job["key"] == key), None)

    def cancel_queued(self, key):
        if key in self.jobs:
            self.jobs[key]["status"] = "cancelled"


queue = Queue()
captured = []


def controlled_model(_client, _model, system, prompt, **kwargs):
    payload = json.loads(prompt)
    captured.append(dict(system=system, payload=payload, task=kwargs.get("task_id")))
    if "focus" not in payload:
        return json.dumps(dict(relation="improves", certainty="explicit", reason="稀疏路由替换稠密得分", evidence=[], findings=[]))
    papers = {p["id"]:p for p in payload["papers"]}
    edges = []
    if payload["focus"] == "paper-c" and "paper-a" in papers:
        excerpt = next(s for s in papers["paper-c"]["excerpts"] if QUOTE in s["text"])
        edges.append(dict(source="paper-a", target="paper-c", relation="improves", certainty="explicit",
            reason="当前论文材料描述用稀疏路由替换稠密得分。", evidence=[dict(sourceId=excerpt["sourceId"], quote=QUOTE)]))
        if payload.get("framework"):
            # Deliberately invalid evidence exercises the real grounding gate.
            edges.append(dict(source="paper-b", target="paper-c", relation="improves", certainty="explicit",
                reason="框架同分支（应被拒绝）", evidence=[dict(sourceId="framework:branch", quote="稀疏注意力分支不能证明论文继承关系")]))
    return json.dumps(dict(edges=edges, frameworkNotes=["论文 D 无法归入当前稀疏注意力分支；保留独立判断。"] if payload.get("framework") else []), ensure_ascii=False)


def install_boundaries():
    import config
    from model_gateway import runtime
    from routers import jobs
    from services import task_runtime, wiki_compiler
    config.load_config = lambda **_kwargs: {"paper_categories":["LLM", "视觉", "空领域", "其他"]}
    config.task_model_id = lambda _cfg, _task: "fixture-model"
    runtime.preflight_task_runtime = lambda *_args, **_kwargs: None
    task_runtime.store = lambda: queue
    jobs.submit_job = queue.submit
    wiki_compiler._call_llm = controlled_model
    wiki_compiler.list_paper_pages = lambda: []


install_boundaries()
app = FastAPI()
app.include_router(domains.router)
app.include_router(domain_agent.router)


@app.get("/")
def health():
    return {"fixture":"research-canvas"}


@app.get("/fixture/state")
def state():
    with SessionLocal() as db:
        runs = [dict(id=r.id, boardId=r.board_id, status=r.status, snapshot=r.snapshot, proposal=r.proposal) for r in db.query(DomainAgentRun).all()]
        roadmap = db.get(RoadmapWorkspace, 1)
        return dict(runs=runs, calls=captured, roadmap=dict(revision=roadmap.revision, state=roadmap.state),
                    roadmapRuns=[dict(id=r.id, status=r.status) for r in db.query(RoadmapRun).all()], jobs=queue.jobs)


@app.post("/fixture/complete/{run_id}")
def complete(run_id: str):
    class Context:
        def phase(self, label):
            pass
        def step(self, _key, operation):
            return operation()
    result = generation.generate(Context(), {"run_id":run_id})
    with SessionLocal() as db:
        run = db.get(DomainAgentRun, run_id)
        if run and run.job_id in queue.jobs:
            queue.jobs[run.job_id]["status"] = "cancelled" if result.get("cancelled") else "completed"
    return result


@app.post("/fixture/interrupt/{run_id}")
def interrupt(run_id: str):
    with SessionLocal() as db:
        run = db.get(DomainAgentRun, run_id)
        queue.jobs[run.job_id].update(status="interrupted", error="合成任务已中断，请重新生成。")
    return {"ok":True}


@app.post("/fixture/mutate/{action}")
def mutate(action: str):
    with SessionLocal() as db:
        row = db.get(RoadmapWorkspace, 1)
        state = deepcopy(row.state)
        framework = next((b for b in state["boards"] if b["id"] == "roadmap-llm"), None)
        if action == "change" and framework:
            framework["nodes"][0]["text"] = "已修改框架：新研究问题，不应进入旧运行"
        elif action == "delete":
            state["boards"] = [b for b in state["boards"] if b["id"] != "roadmap-llm"]
        elif action == "empty" and framework:
            framework["nodes"], framework["edges"] = [], []
        elif action == "restore":
            state = json.loads((DB_PATH.parent / "synthetic-roadmap-fixture.json").read_text())
        elif action == "stale-domain":
            domain = db.get(DomainWorkspace, 1)
            changed = deepcopy(domain.state)
            changed["boards"][0]["nodes"][0]["text"] = "外部变化：必须拒绝应用过期方案"
            domain.state, domain.revision = changed, domain.revision + 1
        else:
            raise HTTPException(400, "Unknown fixture action")
        row.state, row.revision = state, row.revision + 1
        db.commit()
    return {"ok":True}


if __name__ == "__main__":
    seed()

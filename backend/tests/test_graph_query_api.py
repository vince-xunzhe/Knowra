from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import get_db
from models import Base, KnowledgeEdge, KnowledgeNode
from routers.graph import router


class GraphQueryApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add_all([
                KnowledgeNode(id="paper:a", title="Alpha", content="", node_type="paper", promotion_status="promoted"),
                KnowledgeNode(id="concept:x", title="Encoder", content="", node_type="concept", promotion_status="promoted"),
            ])
            db.add(KnowledgeEdge(id="edge:1", source_id="paper:a", target_id="concept:x", relation_type="uses", origin="explicit"))
            db.commit()
        app = FastAPI()
        app.include_router(router)

        def override_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)

    def tearDown(self):
        self.engine.dispose()

    def test_query_and_path_endpoints_use_stable_graph_contract(self):
        found = self.client.get("/api/graph/query/nodes", params={"q": "Alpha"})
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()["nodes"][0]["id"], "paper:a")

        path = self.client.get("/api/graph/query/path", params={
            "source_id": "paper:a", "target_id": "concept:x",
        })
        self.assertEqual(path.status_code, 200)
        self.assertEqual(path.json()["hop_count"], 1)
        self.assertEqual(path.json()["edges"][0]["origin"], "explicit")


if __name__ == "__main__":
    unittest.main()

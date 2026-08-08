from pathlib import Path

from nexus.repo_graph import RepoGraph


def test_repo_graph_python_ast(tmp_path: Path):
    source = """
import os
from datetime import datetime

class Server(Base):
    @app.get("/api/v1/status")
    def status(self):
        return {"status": "ok"}
        
    def helper(self):
        return os.getenv("FOO")
"""
    (tmp_path / "server.py").write_text(source)

    graph = RepoGraph(tmp_path)
    stats = graph.build()

    assert stats.indexed == 1
    record = graph.files["server.py"]
    assert "os" in record.imports
    assert "datetime" in record.imports
    assert record.is_test is False

    assert any(s.name == "Server" and s.kind == "class" for s in record.symbols)
    assert any(s.name == "status" and s.kind == "method" for s in record.symbols)
    assert any(s.name == "helper" and s.kind == "method" for s in record.symbols)

    assert "GET /api/v1/status" in record.routes
    assert "Server" in record.database_models


def test_repo_graph_generic_parsing(tmp_path: Path):
    source = """
import { connect } from 'db';
require('fs');
app.post('/api/users', (req, res) => {
    return res.send("ok");
});
const helper = () => {
    connect();
};
class UserController {}
"""
    (tmp_path / "app.js").write_text(source)

    graph = RepoGraph(tmp_path)
    graph.build()

    record = graph.files["app.js"]
    assert "db" in record.imports
    assert "fs" in record.imports
    assert "POST /api/users" in record.routes

    assert any(s.name == "helper" and s.kind == "function" for s in record.symbols)
    assert any(s.name == "UserController" and s.kind == "class" for s in record.symbols)
    assert "connect" in record.references


def test_repo_graph_incremental_update(tmp_path: Path):
    file1 = tmp_path / "f1.py"
    file1.write_text("def a(): pass")
    graph = RepoGraph(tmp_path)
    graph.build()
    assert len(graph.files) == 1

    file1.write_text("def a(): pass\ndef b(): pass")
    stats = graph.update_paths([file1])
    assert stats.indexed == 1
    assert any(s.name == "b" for s in graph.files["f1.py"].symbols)


def test_dependencies_and_impact(tmp_path: Path):
    (tmp_path / "core.py").write_text("def core_func(): pass")
    (tmp_path / "app.py").write_text("import core")
    (tmp_path / "test_app.py").write_text("import app\ndef test_app(): pass")

    graph = RepoGraph(tmp_path)
    graph.build()

    deps = graph.dependencies("core.py")
    assert "app.py" in deps["imported_by"]

    impacted = graph.impacted_tests(["core.py"])
    assert "test_app.py" in impacted

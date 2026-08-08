"""Focused test suite for Sprint 5 Repository Intelligence Engine."""

import pytest
from pathlib import Path
from nexus.intelligence.repository.discovery import RepositoryDiscovery
from nexus.intelligence.repository.classification import FileClassifier
from nexus.intelligence.repository.extraction import LanguageExtractor
from nexus.intelligence.repository.secrets import SecretProtector
from nexus.intelligence.repository.ranking import ExplainableContextRanker, TaskIntentClassifier
from nexus.intelligence.repository.budget import ContextBudgetManager
from nexus.intelligence.repository.engine import RepositoryIntelligence
from nexus.intelligence.repository.model import TaskIntent, RiskLevel, ContextBundle


def test_repository_discovery(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def hello(): pass")
    (tmp_path / ".git").mkdir()

    discovery = RepositoryDiscovery(tmp_path)
    assert discovery.find_git_root() == tmp_path
    assert "python" in discovery.detect_ecosystems()
    files = discovery.discover_files()
    rel_files = [f.relative_to(tmp_path).as_posix() for f in files]
    assert "src/main.py" in rel_files
    assert "pyproject.toml" in rel_files


def test_file_classification():
    cls_secret = FileClassifier.classify(".env")
    assert cls_secret["category"] == "secret_sensitive"
    assert cls_secret["risk_level"] == RiskLevel.CRITICAL
    assert cls_secret["is_protected"] is True

    cls_test = FileClassifier.classify("tests/test_demo.py")
    assert cls_test["category"] == "test"
    assert cls_test["is_test"] is True

    cls_config = FileClassifier.classify("pyproject.toml")
    assert cls_config["category"] == "configuration"
    assert cls_config["is_config"] is True


def test_python_symbol_extraction():
    extractor = LanguageExtractor()
    code = """
class Calculator:
    \"\"\"Docstring.\"\"\"
    def add(self, a, b):
        return a + b

def calculate():
    c = Calculator()
    return c.add(1, 2)
"""
    res = extractor.extract("calc.py", code)
    assert len(res["symbols"]) == 3
    sym_names = [s.name for s in res["symbols"]]
    assert "Calculator" in sym_names
    assert "add" in sym_names
    assert "calculate" in sym_names


def test_secret_redaction():
    code = "AWS_SECRET_KEY = 'AKIA1234567890ABCDEF'\nDB_PASS = 'secret'"
    sanitized, redacted = SecretProtector.sanitize(code, "config.py")
    assert redacted is True
    assert "[REDACTED_SECRET_KEY_REDACTED]" in sanitized

    env_sanitized, env_redacted = SecretProtector.sanitize("FOO=BAR", ".env")
    assert env_redacted is True
    assert "PROTECTED FILE" in env_sanitized


def test_task_intent_classification():
    assert TaskIntentClassifier.classify("Fix bug in login handler") == TaskIntent.BUG_REPAIR
    assert TaskIntentClassifier.classify("Add new API route for users") == TaskIntent.FEATURE_IMPLEMENTATION
    assert TaskIntentClassifier.classify("Refactor database connection pool") == TaskIntent.REFACTOR
    assert TaskIntentClassifier.classify("Update network policy settings") == TaskIntent.CONFIGURATION_CHANGE


def test_engine_context_bundle(tmp_path):
    (tmp_path / "main.py").write_text("def run(): pass")
    (tmp_path / "test_main.py").write_text("import main\ndef test_run(): main.run()")

    engine = RepositoryIntelligence(tmp_path)
    engine.build(force=True)

    bundle = engine.context_bundle("Fix bug in main.py run function")
    assert isinstance(bundle, ContextBundle)
    assert bundle.task_intent == TaskIntent.BUG_REPAIR
    assert len(bundle.files) > 0
    formatted = bundle.to_formatted_prompt()
    assert "[DECISIVE FILES]" in formatted
    assert "main.py" in formatted


def test_context_expansion_loop(tmp_path):
    (tmp_path / "auth.py").write_text("def verify_token(): pass")
    (tmp_path / "api.py").write_text("import auth\ndef login(): auth.verify_token()")

    engine = RepositoryIntelligence(tmp_path)
    engine.build(force=True)

    bundle = engine.context_bundle("Login endpoint issue")
    expanded = engine.expand_context(bundle, reason="Missing token verification details", additional_files=["auth.py"])
    
    assert "Expanded due to: Missing token verification details" in expanded.limitations
    assert any(f.path == "auth.py" for f in expanded.files)

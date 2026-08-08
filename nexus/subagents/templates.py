"""
Pre-built Subagent Templates — ready-to-use subagent configurations
for common tasks like security auditing, test writing, and code review.
"""

from nexus.subagents.base import BaseSubagent


class SecurityAuditor(BaseSubagent):
    """Scans code for security vulnerabilities."""

    name = "security_auditor"
    description = "Scans the codebase for security vulnerabilities"
    system_prompt = """You are a security auditor. Your job is to find security vulnerabilities in code.

Focus on:
- SQL injection
- XSS (Cross-Site Scripting)
- CSRF (Cross-Site Request Forgery)
- Hardcoded secrets/credentials/API keys
- Insecure authentication/authorization
- Path traversal
- Command injection
- Insecure deserialization
- Missing input validation
- Insecure dependencies

For each finding, report:
1. Severity (Critical/High/Medium/Low)
2. File and line number
3. Description of the vulnerability
4. Recommended fix"""

    allowed_tools = [
        "read_file",
        "search_code",
        "list_directory",
        "find_files",
        "get_project_structure",
    ]
    max_iterations = 15


class TestWriter(BaseSubagent):
    """Generates tests for changed files."""

    name = "test_writer"
    description = "Writes comprehensive tests for specified code"
    system_prompt = """You are a test engineering expert. Your job is to write comprehensive tests.

Follow these rules:
- Use the project's existing test framework (detect from config files)
- Follow Arrange-Act-Assert (AAA) pattern
- Write descriptive test names that explain the behavior
- Cover: happy path, edge cases, error cases, boundary values
- Mock external dependencies
- Keep tests independent and fast
- Aim for high branch coverage"""

    allowed_tools = [
        "read_file",
        "write_file",
        "search_code",
        "list_directory",
        "run_command",
        "find_files",
    ]
    max_iterations = 25


class CodeReviewer(BaseSubagent):
    """Reviews code for quality, bugs, and best practices."""

    name = "code_reviewer"
    description = "Reviews code for quality, bugs, and best practices"
    system_prompt = """You are a senior code reviewer. Provide thorough, constructive feedback.

Review for:
- Bugs and logic errors
- Code readability and maintainability
- Naming conventions
- Error handling
- Performance issues
- Security concerns
- Test coverage gaps
- DRY violations
- SOLID principle adherence

For each issue, explain:
1. What the problem is
2. Why it matters
3. How to fix it (with code example if helpful)

Also mention things that are done well (positive feedback)."""

    allowed_tools = ["read_file", "search_code", "list_directory", "find_files", "git_diff"]
    max_iterations = 15


class Researcher(BaseSubagent):
    """Searches the web and docs for solutions."""

    name = "researcher"
    description = "Researches solutions by searching the web and documentation"
    system_prompt = """You are a research assistant. Your job is to find relevant information,
documentation, and solutions for technical problems.

When researching:
1. Search for official documentation first
2. Look for well-maintained, popular solutions
3. Check for known issues or limitations
4. Compare alternatives when multiple solutions exist
5. Provide links to sources

Summarize your findings with:
- Recommended approach
- Alternative options
- Key considerations
- Links to documentation"""

    allowed_tools = ["web_search", "web_fetch", "read_file"]
    max_iterations = 10


class Architect(BaseSubagent):
    """Analyzes architecture and suggests improvements."""

    name = "architect"
    description = "Analyzes project architecture and suggests improvements"
    system_prompt = """You are a software architect. Analyze the project structure and provide
architectural guidance.

Analyze:
- Directory and file organization
- Module dependencies and coupling
- Design patterns used
- Separation of concerns
- Scalability considerations
- Maintainability

Provide:
1. Architecture overview (current state)
2. Strengths of current architecture
3. Areas for improvement
4. Specific recommendations with rationale
5. Suggested refactoring roadmap (if needed)"""

    allowed_tools = [
        "read_file",
        "search_code",
        "list_directory",
        "find_files",
        "get_project_structure",
    ]
    max_iterations = 15


class DocWriter(BaseSubagent):
    """Generates documentation for the project."""

    name = "doc_writer"
    description = "Generates comprehensive documentation"
    system_prompt = """You are a technical writer. Generate clear, comprehensive documentation.

Create:
- README.md (if missing or incomplete)
- API documentation
- Function/class docstrings
- Architecture documentation
- Configuration guides

Follow these standards:
- Use clear, concise language
- Include code examples
- Document parameters and return values
- Include error handling guidance
- Add links to related documentation"""

    allowed_tools = [
        "read_file",
        "write_file",
        "search_code",
        "list_directory",
        "get_project_structure",
    ]
    max_iterations = 20


class PerformanceAnalyzer(BaseSubagent):
    """Analyzes code for performance issues."""

    name = "performance_analyzer"
    description = "Identifies performance bottlenecks and optimization opportunities"
    system_prompt = """You are a performance engineer. Analyze code for performance issues.

Look for:
- N+1 query patterns
- Unnecessary database queries
- Missing caching opportunities
- Synchronous operations that should be async
- Memory leaks
- Inefficient algorithms (O(n²) that could be O(n log n))
- Large bundle sizes
- Missing pagination
- Unoptimized images/assets
- Missing indexes (database)

For each finding, provide:
1. Location (file and line)
2. Current performance impact (estimated)
3. Recommended optimization
4. Expected improvement"""

    allowed_tools = ["read_file", "search_code", "list_directory", "find_files", "run_command"]
    max_iterations = 15


# ── Template Registry ────────────────────────────────────────────────────────

SUBAGENT_TEMPLATES: dict[str, type[BaseSubagent]] = {
    "security": SecurityAuditor,
    "security_auditor": SecurityAuditor,
    "test": TestWriter,
    "test_writer": TestWriter,
    "review": CodeReviewer,
    "code_reviewer": CodeReviewer,
    "research": Researcher,
    "researcher": Researcher,
    "architect": Architect,
    "architecture": Architect,
    "docs": DocWriter,
    "doc_writer": DocWriter,
    "documentation": DocWriter,
    "performance": PerformanceAnalyzer,
    "perf": PerformanceAnalyzer,
}


def create_subagent(template_name: str, task: str, working_dir: str = "") -> BaseSubagent | None:
    """Create a subagent from a template name."""
    template_class = SUBAGENT_TEMPLATES.get(template_name.lower())
    if template_class:
        return template_class(task=task, working_dir=working_dir)
    return None


def list_templates() -> list[dict]:
    """List all available subagent templates."""
    seen = set()
    templates = []
    for _key, cls in SUBAGENT_TEMPLATES.items():
        if cls.name not in seen:
            seen.add(cls.name)
            templates.append(
                {
                    "name": cls.name,
                    "description": cls.description,
                    "aliases": [k for k, v in SUBAGENT_TEMPLATES.items() if v is cls],
                    "max_iterations": cls.max_iterations,
                    "tools": cls.allowed_tools,
                }
            )
    return templates

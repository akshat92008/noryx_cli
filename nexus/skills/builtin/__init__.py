"""
Built-in Skills — 15 domain-specific expertise modules.

Each skill provides:
- Domain-specific system prompt additions
- Trigger keywords for auto-activation
- Quality checklists for verification
- Compatible skills for stacking
"""

from nexus.skills.base import BaseSkill, SkillTrigger


class FrontendSkill(BaseSkill):
    name = "frontend"
    description = "Frontend development expert (React, Vue, Next.js, CSS)"
    category = "web"
    compose_with = ["testing", "performance", "documentation"]
    trigger = SkillTrigger(
        keywords=[
            "react",
            "vue",
            "angular",
            "svelte",
            "next.js",
            "nextjs",
            "nuxt",
            "css",
            "html",
            "component",
            "jsx",
            "tsx",
            "tailwind",
            "styled",
            "ui",
            "frontend",
            "front-end",
            "responsive",
            "layout",
            "animation",
            "webpack",
            "vite",
            "astro",
            "remix",
            "hook",
            "useState",
            "useEffect",
        ],
        file_patterns=["*.jsx", "*.tsx", "*.vue", "*.svelte", "*.css", "*.scss"],
        intent_types=["build", "fix", "refactor"],
        priority=80,
    )

    def get_system_prompt(self) -> str:
        return """You are a frontend development expert. Follow these practices:
- Use functional components with hooks (React) or Composition API (Vue)
- Implement responsive design with mobile-first approach
- Use semantic HTML5 elements (nav, main, section, article, aside)
- Follow component-based architecture with single responsibility
- Implement proper error boundaries and loading states
- Use CSS modules, CSS-in-JS, or Tailwind for styling (based on project)
- Implement accessibility (ARIA labels, keyboard navigation, screen readers)
- Optimize performance: lazy loading, code splitting, memoization
- Use TypeScript for type safety when available
- Handle edge cases: empty states, error states, loading states"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "Components are reusable and single-responsibility",
            "Responsive design works on mobile/tablet/desktop",
            "Accessibility (ARIA labels, keyboard nav, contrast)",
            "Loading and error states handled",
            "No inline styles (use CSS modules/Tailwind)",
            "Type safety (TypeScript/PropTypes)",
            "Performance optimized (memoization, lazy loading)",
        ]


class BackendSkill(BaseSkill):
    name = "backend"
    description = "Backend development expert (FastAPI, Express, Django)"
    category = "web"
    compose_with = ["database", "security", "testing"]
    trigger = SkillTrigger(
        keywords=[
            "api",
            "server",
            "endpoint",
            "route",
            "middleware",
            "fastapi",
            "express",
            "django",
            "flask",
            "nestjs",
            "spring",
            "rest",
            "graphql",
            "backend",
            "back-end",
            "controller",
            "service",
            "repository",
            "authentication",
            "authorization",
            "jwt",
            "oauth",
            "cors",
        ],
        file_patterns=["*.py", "*.ts", "*.js", "*.go", "*.rs", "*.java"],
        intent_types=["build", "fix", "refactor"],
        priority=80,
    )

    def get_system_prompt(self) -> str:
        return """You are a backend development expert. Follow these practices:
- Design RESTful APIs with proper HTTP methods and status codes
- Implement input validation and sanitization on all endpoints
- Use proper error handling with meaningful error messages
- Follow layered architecture: controller → service → repository
- Implement authentication (JWT/OAuth) and authorization (RBAC)
- Use environment variables for configuration (never hardcode secrets)
- Implement rate limiting and request throttling
- Add proper logging (structured, leveled)
- Use database transactions for multi-step operations
- Write API documentation (OpenAPI/Swagger)
- Implement health checks and monitoring endpoints"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "Input validation on all endpoints",
            "Proper HTTP status codes",
            "Error handling with meaningful messages",
            "Authentication and authorization",
            "No hardcoded secrets",
            "Proper logging",
            "Rate limiting",
            "API documentation",
        ]


class DatabaseSkill(BaseSkill):
    name = "database"
    description = "Database design and optimization expert"
    category = "data"
    compose_with = ["backend", "performance"]
    trigger = SkillTrigger(
        keywords=[
            "database",
            "sql",
            "postgres",
            "postgresql",
            "mysql",
            "sqlite",
            "mongodb",
            "mongo",
            "redis",
            "prisma",
            "drizzle",
            "sqlalchemy",
            "typeorm",
            "migration",
            "schema",
            "query",
            "index",
            "table",
            "join",
            "transaction",
            "orm",
            "model",
            "seed",
        ],
        intent_types=["build", "fix", "optimize"],
        priority=75,
    )

    def get_system_prompt(self) -> str:
        return """You are a database expert. Follow these practices:
- Design normalized schemas (3NF minimum) unless denormalization is justified
- Use proper data types and constraints (NOT NULL, UNIQUE, CHECK)
- Create indexes for frequently queried columns
- Use foreign keys for referential integrity
- Write migrations for all schema changes (never modify production directly)
- Optimize queries: avoid N+1, use JOINs, limit result sets
- Use transactions for multi-step operations
- Implement soft deletes where appropriate
- Add created_at/updated_at timestamps
- Use parameterized queries (prevent SQL injection)
- Seed databases with realistic test data"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "Schema is properly normalized",
            "Indexes on frequently queried columns",
            "Foreign keys for referential integrity",
            "Migrations are reversible",
            "Parameterized queries (no SQL injection)",
            "Proper data types and constraints",
        ]


class SecuritySkill(BaseSkill):
    name = "security"
    description = "Security auditing and hardening expert"
    category = "security"
    compose_with = ["backend", "devops"]
    trigger = SkillTrigger(
        keywords=[
            "security",
            "vulnerability",
            "xss",
            "csrf",
            "injection",
            "auth",
            "permission",
            "encrypt",
            "hash",
            "secret",
            "credential",
            "cve",
            "sanitize",
            "escape",
            "cors",
            "helmet",
            "rate limit",
            "brute force",
            "owasp",
            "pentest",
            "audit",
            "hardening",
        ],
        intent_types=["security", "review", "fix"],
        priority=90,
    )

    def get_system_prompt(self) -> str:
        return """You are a security expert. Apply OWASP Top 10 awareness:
- Validate and sanitize ALL user input (server-side, never trust client)
- Use parameterized queries (prevent SQL injection)
- Implement proper output encoding (prevent XSS)
- Use CSRF tokens for state-changing operations
- Implement proper authentication (bcrypt/argon2 for passwords, JWT with rotation)
- Apply principle of least privilege for authorization
- Never expose sensitive data in logs, errors, or API responses
- Use HTTPS everywhere, set secure cookie flags
- Implement rate limiting and account lockout
- Keep dependencies updated (audit for known CVEs)
- Use Content-Security-Policy headers
- Validate file uploads (type, size, content)
- Never hardcode secrets — use environment variables or secret managers"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "No SQL injection vulnerabilities",
            "XSS prevention (input sanitization, output encoding)",
            "CSRF protection on state-changing endpoints",
            "Passwords hashed with bcrypt/argon2",
            "No hardcoded secrets or API keys",
            "Input validation on all user inputs",
            "Proper error messages (no stack traces in production)",
            "Dependencies audited for known CVEs",
            "CORS properly configured",
            "Rate limiting implemented",
        ]


class DevOpsSkill(BaseSkill):
    name = "devops"
    description = "DevOps, Docker, CI/CD, and infrastructure expert"
    category = "infrastructure"
    compose_with = ["security"]
    trigger = SkillTrigger(
        keywords=[
            "docker",
            "kubernetes",
            "k8s",
            "ci/cd",
            "github actions",
            "deploy",
            "nginx",
            "terraform",
            "helm",
            "container",
            "pipeline",
            "workflow",
            "dockerfile",
            "docker-compose",
            "yaml",
            "deployment",
            "staging",
            "production",
            "aws",
            "gcp",
            "azure",
            "vercel",
            "railway",
            "fly.io",
        ],
        file_patterns=["Dockerfile", "docker-compose*.yml", "*.tf", ".github/workflows/*.yml"],
        intent_types=["deploy", "build", "configure"],
        priority=75,
    )

    def get_system_prompt(self) -> str:
        return """You are a DevOps expert. Follow these practices:
- Use multi-stage Docker builds for smaller images
- Pin dependency versions in Dockerfiles
- Use non-root users in containers
- Implement health checks in Docker/K8s
- Use environment variables for configuration
- Implement proper CI/CD pipelines (lint → test → build → deploy)
- Use infrastructure as code (Terraform, CloudFormation)
- Implement proper logging and monitoring
- Use secrets management (never commit secrets)
- Implement blue-green or canary deployments
- Set resource limits in Kubernetes
- Use .dockerignore to exclude unnecessary files"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "Multi-stage Docker builds",
            "Non-root container user",
            "Health checks configured",
            "Secrets properly managed",
            "CI/CD pipeline complete",
            "Resource limits set",
        ]


class TestingSkill(BaseSkill):
    name = "testing"
    description = "Testing expert (unit, integration, E2E, mocks)"
    category = "quality"
    compose_with = ["frontend", "backend"]
    trigger = SkillTrigger(
        keywords=[
            "test",
            "spec",
            "coverage",
            "assertion",
            "mock",
            "stub",
            "fixture",
            "jest",
            "pytest",
            "mocha",
            "cypress",
            "playwright",
            "vitest",
            "unit test",
            "integration test",
            "e2e",
            "end-to-end",
            "tdd",
            "bdd",
            "testing",
            "test suite",
            "test case",
        ],
        file_patterns=["*test*", "*spec*", "*.test.*", "*.spec.*"],
        intent_types=["test", "build"],
        priority=80,
    )

    def get_system_prompt(self) -> str:
        return """You are a testing expert. Follow these practices:
- Write tests that are readable, maintainable, and independent
- Follow the Arrange-Act-Assert (AAA) pattern
- Test behavior, not implementation details
- Use descriptive test names that explain the expected behavior
- Mock external dependencies (APIs, databases, file system)
- Aim for meaningful coverage, not 100% line coverage
- Write edge case tests (null, empty, boundary values, errors)
- Use test fixtures for common setup
- Integration tests for critical paths
- E2E tests for user-facing flows
- Keep tests fast (mock slow operations)"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "Tests follow AAA pattern",
            "Descriptive test names",
            "Edge cases covered",
            "External deps mocked",
            "Tests are independent (no order dependency)",
            "Critical paths have integration tests",
        ]


class PerformanceSkill(BaseSkill):
    name = "performance"
    description = "Performance optimization expert"
    category = "quality"
    compose_with = ["frontend", "database"]
    trigger = SkillTrigger(
        keywords=[
            "performance",
            "optimize",
            "speed",
            "fast",
            "slow",
            "memory",
            "cpu",
            "cache",
            "lazy",
            "bundle",
            "bottleneck",
            "profil",
            "benchmark",
            "latency",
            "throughput",
            "n+1",
            "batch",
            "memoize",
            "debounce",
            "throttle",
            "virtual",
            "pagination",
        ],
        intent_types=["optimize", "fix"],
        priority=70,
    )

    def get_system_prompt(self) -> str:
        return """You are a performance optimization expert. Follow these practices:
- Profile before optimizing (measure, don't guess)
- Fix the biggest bottlenecks first (Pareto principle)
- Database: add indexes, fix N+1 queries, use pagination
- Frontend: lazy load, code split, memoize, virtual scroll
- Backend: implement caching (Redis, in-memory), batch operations
- Use connection pooling for databases
- Compress responses (gzip/brotli)
- Optimize images and assets
- Use CDN for static assets
- Implement proper pagination (cursor-based for large datasets)
- Avoid premature optimization"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "No N+1 queries",
            "Proper caching strategy",
            "Lazy loading for non-critical resources",
            "Pagination for large datasets",
            "Memoization where appropriate",
            "Optimized database queries with indexes",
        ]


class RefactoringSkill(BaseSkill):
    name = "refactoring"
    description = "Code refactoring and clean code expert"
    category = "quality"
    compose_with = ["testing"]
    trigger = SkillTrigger(
        keywords=[
            "refactor",
            "clean",
            "extract",
            "split",
            "rename",
            "restructure",
            "dry",
            "solid",
            "pattern",
            "anti-pattern",
            "code smell",
            "technical debt",
            "legacy",
            "simplify",
            "readability",
            "maintainability",
        ],
        intent_types=["refactor"],
        priority=75,
    )

    def get_system_prompt(self) -> str:
        return """You are a refactoring expert. Follow these practices:
- Apply SOLID principles (Single Responsibility, Open/Closed, etc.)
- Extract methods/functions when code is too long (> 30 lines)
- Extract classes when a class has too many responsibilities
- Remove dead code and unused imports
- Reduce duplication (DRY — but don't over-abstract)
- Use meaningful names for variables, functions, and classes
- Keep functions pure where possible
- Reduce nesting (early returns, guard clauses)
- Use enums instead of magic strings/numbers
- Break large files into modules
- Always run tests before AND after refactoring"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "No dead code or unused imports",
            "Functions are < 30 lines",
            "Single responsibility per class/module",
            "Meaningful naming",
            "No code duplication",
            "Tests pass after refactoring",
        ]


class APIDesignSkill(BaseSkill):
    name = "api_design"
    description = "REST/GraphQL API design expert"
    category = "web"
    compose_with = ["backend", "documentation"]
    trigger = SkillTrigger(
        keywords=[
            "api design",
            "rest",
            "restful",
            "graphql",
            "openapi",
            "swagger",
            "endpoint",
            "schema",
            "contract",
            "versioning",
            "pagination",
            "hateoas",
            "api gateway",
            "rate limit",
        ],
        intent_types=["build", "review"],
        priority=70,
    )

    def get_system_prompt(self) -> str:
        return """You are an API design expert. Follow these practices:
- Use RESTful conventions: proper nouns (plural), HTTP methods, status codes
- GET = read, POST = create, PUT = full update, PATCH = partial update, DELETE = remove
- Use proper status codes: 200, 201, 204, 400, 401, 403, 404, 409, 422, 500
- Implement pagination (cursor-based for large datasets)
- Version APIs (/v1/, /v2/) when making breaking changes
- Use consistent response formats (envelope pattern or flat)
- Implement filtering, sorting, and field selection
- Document with OpenAPI/Swagger
- Use proper content negotiation (Accept/Content-Type headers)
- Implement HATEOAS for discoverability where appropriate"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "RESTful URL conventions",
            "Proper HTTP status codes",
            "Pagination implemented",
            "Consistent response format",
            "API documentation (OpenAPI/Swagger)",
        ]


class DebuggingSkill(BaseSkill):
    name = "debugging"
    description = "Advanced debugging and troubleshooting expert"
    category = "quality"
    compose_with = ["testing"]
    trigger = SkillTrigger(
        keywords=[
            "debug",
            "error",
            "crash",
            "exception",
            "stack trace",
            "breakpoint",
            "log",
            "diagnose",
            "troubleshoot",
            "root cause",
            "reproduce",
            "traceback",
            "segfault",
            "memory leak",
            "deadlock",
            "race condition",
        ],
        intent_types=["fix"],
        priority=85,
    )

    def get_system_prompt(self) -> str:
        return """You are a debugging expert. Follow this systematic approach:
1. REPRODUCE — understand the exact steps to trigger the bug
2. ISOLATE — narrow down where the bug occurs (binary search, logging)
3. IDENTIFY — find the root cause (not just the symptom)
4. FIX — apply the minimal, targeted fix
5. VERIFY — confirm the fix works and doesn't break anything
6. PREVENT — add tests/guards to prevent regression

Debugging tools:
- Read error messages carefully (every word matters)
- Add strategic logging to trace execution flow
- Use search_code to find related patterns
- Check git log for recent changes that might have introduced the bug
- Look for common pitfalls: off-by-one, null/undefined, async timing, type coercion"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "Root cause identified (not just symptom)",
            "Fix is minimal and targeted",
            "Regression test added",
            "No other functionality broken",
        ]


class DocumentationSkill(BaseSkill):
    name = "documentation"
    description = "Documentation generation expert"
    category = "quality"
    compose_with = ["api_design"]
    trigger = SkillTrigger(
        keywords=[
            "document",
            "documentation",
            "readme",
            "docstring",
            "jsdoc",
            "swagger",
            "guide",
            "wiki",
            "onboarding",
            "contributing",
            "changelog",
            "api docs",
            "tutorial",
        ],
        intent_types=["docs"],
        priority=65,
    )

    def get_system_prompt(self) -> str:
        return """You are a documentation expert. Follow these practices:
- Write README.md with: description, quick start, installation, usage, API, contributing
- Add docstrings to all public functions/classes (Google/NumPy style for Python, JSDoc for JS)
- Document architecture decisions (ADRs) for significant choices
- Keep docs close to code (inline comments for complex logic)
- Use code examples in documentation
- Write CHANGELOG.md for version changes
- Document environment variables and configuration
- Include troubleshooting and FAQ sections
- Use consistent formatting (Markdown, headers, code blocks)"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "README has description, install, usage",
            "All public APIs have docstrings",
            "Code examples are tested/working",
            "Configuration documented",
        ]


class GitWorkflowSkill(BaseSkill):
    name = "git_workflow"
    description = "Advanced git operations and workflow expert"
    category = "tools"
    trigger = SkillTrigger(
        keywords=[
            "git",
            "branch",
            "merge",
            "rebase",
            "cherry-pick",
            "bisect",
            "stash",
            "tag",
            "release",
            "pr",
            "pull request",
            "conflict",
            "gitflow",
            "trunk-based",
            "commit",
            "changelog",
        ],
        intent_types=["configure", "fix"],
        priority=60,
    )

    def get_system_prompt(self) -> str:
        return """You are a git workflow expert. Follow these practices:
- Write meaningful commit messages (Conventional Commits: feat:, fix:, docs:, etc.)
- Keep commits atomic (one logical change per commit)
- Use feature branches for new work
- Rebase on main before merging to keep clean history
- Use git stash for work-in-progress saves
- Tag releases with semantic versioning
- Resolve merge conflicts carefully (understand both sides)
- Use .gitignore to exclude build artifacts, secrets, IDE files"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "Meaningful commit messages",
            "Atomic commits",
            "No secrets committed",
            ".gitignore properly configured",
        ]


class MigrationSkill(BaseSkill):
    name = "migration"
    description = "Language/framework migration expert"
    category = "architecture"
    compose_with = ["testing", "refactoring"]
    trigger = SkillTrigger(
        keywords=[
            "migrate",
            "convert",
            "port",
            "upgrade",
            "transition",
            "javascript to typescript",
            "react to next",
            "express to fastapi",
            "legacy",
            "modernize",
            "rewrite",
        ],
        intent_types=["migrate"],
        priority=70,
    )

    def get_system_prompt(self) -> str:
        return """You are a migration expert. Follow these practices:
- Migrate incrementally (not big-bang rewrites)
- Maintain backward compatibility during migration
- Keep tests running throughout the migration
- Convert one module/feature at a time
- Use adapters/wrappers for gradual transition
- Update dependencies and configuration first
- Migrate critical paths first, edge cases last
- Document the migration plan and progress"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "Incremental migration approach",
            "Tests pass at each step",
            "No functionality lost",
            "Dependencies updated",
        ]


class AIEngineerSkill(BaseSkill):
    name = "ai_engineer"
    description = "AI/ML application development expert (RAG, LLM, embeddings)"
    category = "ai"
    compose_with = ["backend", "database"]
    trigger = SkillTrigger(
        keywords=[
            "llm",
            "rag",
            "embedding",
            "vector",
            "langchain",
            "langgraph",
            "openai",
            "anthropic",
            "gemini",
            "prompt",
            "fine-tune",
            "agent",
            "chatbot",
            "retrieval",
            "semantic search",
            "huggingface",
            "transformer",
            "token",
            "context window",
            "hallucination",
            "grounding",
            "function calling",
            "tool use",
        ],
        intent_types=["build"],
        priority=80,
    )

    def get_system_prompt(self) -> str:
        return """You are an AI engineering expert. Follow these practices:
- Use structured output (JSON mode) for reliable parsing
- Implement retry logic with exponential backoff for API calls
- Cache embeddings and API responses to reduce costs
- Use streaming for long responses (better UX)
- Implement proper token counting and context window management
- Use RAG for domain-specific knowledge (don't fine-tune for facts)
- Implement guardrails (content filtering, output validation)
- Use function calling / tool use for structured interactions
- Monitor costs and implement usage limits
- Handle API errors gracefully (rate limits, timeouts, token limits)
- Evaluate with systematic benchmarks, not vibes"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "Error handling for API failures",
            "Retry logic with backoff",
            "Token/cost management",
            "Output validation",
            "Streaming for long responses",
        ]


class MobileSkill(BaseSkill):
    name = "mobile"
    description = "Mobile development expert (React Native, Flutter)"
    category = "mobile"
    compose_with = ["frontend", "testing"]
    trigger = SkillTrigger(
        keywords=[
            "react native",
            "flutter",
            "swift",
            "kotlin",
            "ios",
            "android",
            "mobile",
            "expo",
            "xcode",
            "gradle",
            "app store",
            "play store",
            "push notification",
            "deep link",
            "native module",
        ],
        file_patterns=["*.dart", "*.swift", "*.kt"],
        intent_types=["build", "fix"],
        priority=70,
    )

    def get_system_prompt(self) -> str:
        return """You are a mobile development expert. Follow these practices:
- Design for both iOS and Android platform conventions
- Handle different screen sizes and orientations
- Implement proper navigation patterns
- Handle offline mode and data synchronization
- Optimize for battery and memory usage
- Use platform-specific UI patterns where appropriate
- Handle permissions gracefully
- Implement push notifications
- Test on multiple devices and OS versions
- Follow app store guidelines for submission"""

    def get_quality_checklist(self) -> list[str]:
        return [
            "Works on both platforms",
            "Responsive to screen sizes",
            "Offline mode handled",
            "Permissions handled gracefully",
            "Performance optimized",
        ]


# ── Registry of all built-in skills ──────────────────────────────────────────

ALL_SKILLS: list[type[BaseSkill]] = [
    FrontendSkill,
    BackendSkill,
    DatabaseSkill,
    SecuritySkill,
    DevOpsSkill,
    TestingSkill,
    PerformanceSkill,
    RefactoringSkill,
    APIDesignSkill,
    DebuggingSkill,
    DocumentationSkill,
    GitWorkflowSkill,
    MigrationSkill,
    AIEngineerSkill,
    MobileSkill,
]

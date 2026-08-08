"""
Planning Engine — decomposes complex user requests into structured task plans.

The planner intercepts user messages, classifies intent, and decides whether
to execute directly (simple requests) or create a multi-step execution plan
(complex tasks). Plans are persisted and can be resumed across sessions.

Architecture:
    User Request → Intent Classification → Plan Generation → Step Execution → Verification
"""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum

from nexus.paths import nexus_home

# ── Plan Types ───────────────────────────────────────────────────────────────


class PlanType(str, Enum):
    """Whether to execute directly or plan first."""

    DIRECT = "direct"  # Simple, execute immediately
    PLANNED = "planned"  # Complex, create a plan first
    RESEARCH = "research"  # Needs investigation before acting


class TaskStatus(str, Enum):
    """Status of a plan step."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class IntentType(str, Enum):
    """High-level user intent classification."""

    BUILD = "build"  # Create something new
    FIX = "fix"  # Debug / fix a bug
    REFACTOR = "refactor"  # Improve existing code
    REVIEW = "review"  # Code review
    EXPLAIN = "explain"  # Explain code
    DEPLOY = "deploy"  # Deployment tasks
    TEST = "test"  # Write or run tests
    DOCS = "docs"  # Documentation
    SEARCH = "search"  # Find information
    MIGRATE = "migrate"  # Migration tasks
    OPTIMIZE = "optimize"  # Performance optimization
    SECURITY = "security"  # Security audit
    CONFIGURE = "configure"  # Configuration tasks
    CHAT = "chat"  # General conversation
    UNKNOWN = "unknown"


class TaskType(str, Enum):
    """Broad categorization of tasks for verification boundaries."""

    READ_ONLY = "read_only"
    MUTATION = "mutation"
    OPERATIONAL = "operational"


def get_task_type(intent: IntentType) -> TaskType:
    if intent in (IntentType.EXPLAIN, IntentType.REVIEW, IntentType.SEARCH, IntentType.CHAT):
        return TaskType.READ_ONLY
    elif intent in (IntentType.DEPLOY, IntentType.CONFIGURE):
        return TaskType.OPERATIONAL
    return TaskType.MUTATION


class Difficulty(str, Enum):
    """Estimated task difficulty."""

    TRIVIAL = "trivial"  # < 1 tool call
    SIMPLE = "simple"  # 1-3 tool calls
    MODERATE = "moderate"  # 4-10 tool calls
    COMPLEX = "complex"  # 10-25 tool calls
    MASSIVE = "massive"  # 25+ tool calls


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class PlanStep:
    """A single step in an execution plan."""

    id: int
    title: str
    description: str
    phase: str = "implementation"
    subsystem: str = ""
    tools_needed: list[str] = field(default_factory=list)
    depends_on: list[int] = field(default_factory=list)
    permitted_files: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    contract_inputs: list[str] = field(default_factory=list)
    contract_outputs: list[str] = field(default_factory=list)
    risk: str = "medium"
    retry_limit: int = 2
    attempts: int = 0
    max_tool_calls: int = 10
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""
    error: str = ""
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PlanStep":
        data["status"] = TaskStatus(data.get("status", "pending"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ExecutionPlan:
    """A structured execution plan for a complex task."""

    id: str
    goal: str
    intent: IntentType
    difficulty: Difficulty
    plan_type: PlanType
    steps: list[PlanStep] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: TaskStatus = TaskStatus.PENDING
    current_step: int = 0
    skills_needed: list[str] = field(default_factory=list)
    verification_steps: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    permitted_files: list[str] = field(default_factory=list)
    product_spec: dict = field(default_factory=dict)
    architecture_decisions: list[dict] = field(default_factory=list)
    subsystem_contracts: list[dict] = field(default_factory=list)
    integration_plan: dict = field(default_factory=dict)
    deployment_plan: dict = field(default_factory=dict)
    failure_replans: list[dict] = field(default_factory=list)
    revision: int = 1
    canonical_contract: dict = field(default_factory=dict)
    canonical_plan: dict = field(default_factory=dict)
    canonical_critique: dict = field(default_factory=dict)
    canonical_execution_contract: dict = field(default_factory=dict)
    canonical_planning_error: str = ""
    retry_policy: dict[str, int] = field(
        default_factory=lambda: {"per_task": 2, "total_repairs": 5}
    )
    budgets: dict[str, int | float | None] = field(
        default_factory=lambda: {
            "max_tool_calls": 50,
            "max_hosted_calls": None,
            "max_prompt_tokens": None,
            "max_completion_tokens": None,
            "max_cost_usd": None,
        }
    )

    @property
    def progress(self) -> float:
        """Completion percentage."""
        if not self.steps:
            return 0.0
        completed = sum(1 for s in self.steps if s.status == TaskStatus.COMPLETED)
        return (completed / len(self.steps)) * 100

    @property
    def next_step(self) -> PlanStep | None:
        """Get the next pending step whose dependencies are met."""
        statuses = {step.id: step.status for step in self.steps}
        for step in self.steps:
            if step.status != TaskStatus.PENDING:
                continue
            deps_met = all(
                statuses.get(dep_id) in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
                for dep_id in step.depends_on
            )
            if deps_met:
                return step
        return None

    @property
    def is_complete(self) -> bool:
        return all(s.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED) for s in self.steps)

    @property
    def has_failures(self) -> bool:
        return any(s.status == TaskStatus.FAILED for s in self.steps)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "intent": self.intent.value,
            "difficulty": self.difficulty.value,
            "plan_type": self.plan_type.value,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "status": self.status.value,
            "current_step": self.current_step,
            "skills_needed": self.skills_needed,
            "verification_steps": self.verification_steps,
            "acceptance_criteria": self.acceptance_criteria,
            "permitted_files": self.permitted_files,
            "product_spec": self.product_spec,
            "architecture_decisions": self.architecture_decisions,
            "subsystem_contracts": self.subsystem_contracts,
            "integration_plan": self.integration_plan,
            "deployment_plan": self.deployment_plan,
            "failure_replans": self.failure_replans,
            "revision": self.revision,
            "retry_policy": self.retry_policy,
            "budgets": self.budgets,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionPlan":
        steps = [PlanStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            id=data["id"],
            goal=data["goal"],
            intent=IntentType(data.get("intent", "unknown")),
            difficulty=Difficulty(data.get("difficulty", "moderate")),
            plan_type=PlanType(data.get("plan_type", "direct")),
            steps=steps,
            created_at=data.get("created_at", ""),
            status=TaskStatus(data.get("status", "pending")),
            current_step=data.get("current_step", 0),
            skills_needed=data.get("skills_needed", []),
            verification_steps=data.get("verification_steps", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            permitted_files=data.get("permitted_files", []),
            product_spec=data.get("product_spec", {}),
            architecture_decisions=data.get("architecture_decisions", []),
            subsystem_contracts=data.get("subsystem_contracts", []),
            integration_plan=data.get("integration_plan", {}),
            deployment_plan=data.get("deployment_plan", {}),
            failure_replans=data.get("failure_replans", []),
            revision=max(1, int(data.get("revision", 1))),
            retry_policy=data.get("retry_policy", {"per_task": 2, "total_repairs": 5}),
            budgets=data.get(
                "budgets",
                {
                    "max_tool_calls": 50,
                    "max_hosted_calls": None,
                    "max_prompt_tokens": None,
                    "max_completion_tokens": None,
                    "max_cost_usd": None,
                },
            ),
        )

    def format_summary(self) -> str:
        """Human-readable plan summary."""
        lines = [
            f"📋 Plan: {self.goal}",
            f"   Intent: {self.intent.value} | Difficulty: {self.difficulty.value} | Progress: {self.progress:.0f}%",
            "",
        ]
        for step in self.steps:
            status_icon = {
                TaskStatus.PENDING: "⬜",
                TaskStatus.IN_PROGRESS: "🔄",
                TaskStatus.COMPLETED: "✅",
                TaskStatus.FAILED: "❌",
                TaskStatus.SKIPPED: "⏭️",
                TaskStatus.BLOCKED: "🔒",
            }.get(step.status, "⬜")
            lines.append(f"   {status_icon} {step.id + 1}. {step.title}")
            if step.depends_on:
                deps = ", ".join(str(d + 1) for d in step.depends_on)
                lines.append(f"      ↳ depends on: step {deps}")

        if self.verification_steps:
            lines.append("")
            lines.append("   🔍 Verification:")
            for v in self.verification_steps:
                lines.append(f"      • {v}")

        if self.acceptance_criteria:
            lines.append("")
            lines.append("   Acceptance criteria:")
            for criterion in self.acceptance_criteria:
                lines.append(f"      • {criterion}")

        return "\n".join(lines)


# ── Intent Classification ────────────────────────────────────────────────────

# Keyword patterns for intent classification
_INTENT_PATTERNS: dict[IntentType, list[str]] = {
    IntentType.BUILD: [
        r"\b(build|create|make|implement|add|develop|write|generate|scaffold|setup|init)\b",
        r"\b(new|feature|component|module|service|endpoint|page|app|application|project)\b",
    ],
    IntentType.FIX: [
        r"\b(fix|bug|error|crash|broken|issue|problem|debug|solve|repair|patch|resolve)\b",
        r"\b(doesn't work|not working|fails|failing|exception|traceback|stack trace)\b",
    ],
    IntentType.REFACTOR: [
        r"\b(refactor|restructure|reorganize|clean|improve|simplify|extract|split|merge|rename)\b",
        r"\b(dead code|duplicate|DRY|SOLID|pattern|anti-pattern|code smell|technical debt)\b",
    ],
    IntentType.REVIEW: [
        r"\b(review|audit|check|inspect|analyze|evaluate|assess|critique)\b",
        r"\b(PR|pull request|code review|quality|best practice)\b",
    ],
    IntentType.EXPLAIN: [
        r"\b(explain|understand|how does|what is|what does|why|describe|walk through|clarify)\b",
    ],
    IntentType.DEPLOY: [
        r"\b(deploy|ship|release|publish|push to|launch|go live|production|staging)\b",
        r"\b(CI/CD|pipeline|docker|kubernetes|hosting|server|cloud)\b",
    ],
    IntentType.TEST: [
        r"\b(test|spec|coverage|assertion|mock|stub|fixture|jest|pytest|unittest)\b",
        r"\b(unit test|integration test|e2e|end.to.end|TDD|BDD)\b",
    ],
    IntentType.DOCS: [
        r"\b(document|documentation|readme|docstring|comment|jsdoc|swagger|openapi|wiki)\b",
        r"\b(API doc|guide|tutorial|onboarding|contributing)\b",
    ],
    IntentType.SEARCH: [
        r"\b(find|search|look for|where|locate|grep|which file|show me)\b",
    ],
    IntentType.MIGRATE: [
        r"\b(migrate|convert|port|upgrade|transition|move from|switch from|replace)\b",
        r"\b(JavaScript to TypeScript|React to|Vue to|Express to|legacy)\b",
    ],
    IntentType.OPTIMIZE: [
        r"\b(optimize|performance|speed|fast|slow|memory|CPU|cache|lazy|bundle size)\b",
        r"\b(bottleneck|profil|benchmark|latency|throughput)\b",
    ],
    IntentType.SECURITY: [
        r"\b(security|vulnerab|XSS|CSRF|injection|auth|permission|secret|credential|CVE)\b",
        r"\b(hardcoded|exposed|unsafe|sanitize|validate|escape|encrypt)\b",
    ],
    IntentType.CONFIGURE: [
        r"\b(config|setting|environment|env|setup|install|dependency|package|version)\b",
    ],
}


_PRIMARY_INTENT_PATTERNS: tuple[tuple[IntentType, str], ...] = (
    (IntentType.FIX, r"^\s*(?:please\s+)?(?:fix|debug|repair|patch|resolve)\b"),
    (IntentType.REFACTOR, r"^\s*(?:please\s+)?(?:refactor|restructure|simplify|extract)\b"),
    (IntentType.MIGRATE, r"^\s*(?:please\s+)?(?:migrate|port|upgrade|convert)\b"),
    (IntentType.SECURITY, r"^\s*(?:please\s+)?(?:secure|harden|audit security)\b"),
    (IntentType.TEST, r"^\s*(?:please\s+)?(?:test|add tests|write tests)\b"),
    (IntentType.BUILD, r"^\s*(?:please\s+)?(?:build|create|implement|add|develop|write)\b"),
)


def classify_intent(user_input: str) -> IntentType:
    """Classify the user's intent based on keyword patterns."""
    scores: dict[IntentType, int] = {}
    text = user_input.lower()

    # The explicit leading action is authoritative.  This prevents requests
    # such as "fix X and add regression tests" from being misclassified as BUILD.
    for intent, pattern in _PRIMARY_INTENT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return intent

    for intent, patterns in _INTENT_PATTERNS.items():
        score = 0
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            score += len(matches)
        if score > 0:
            scores[intent] = score

    if not scores:
        # Check for conversational patterns
        if re.search(r"\b(hi|hello|hey|thanks|thank you|ok|sure|yes|no|please)\b", text):
            return IntentType.CHAT
        return IntentType.UNKNOWN

    priority = (
        IntentType.FIX,
        IntentType.SECURITY,
        IntentType.MIGRATE,
        IntentType.REFACTOR,
        IntentType.OPTIMIZE,
        IntentType.DEPLOY,
        IntentType.TEST,
        IntentType.BUILD,
        IntentType.CONFIGURE,
        IntentType.DOCS,
        IntentType.REVIEW,
        IntentType.EXPLAIN,
        IntentType.SEARCH,
    )
    best = max(scores.values())
    tied = {intent for intent, score in scores.items() if score == best}
    return next((intent for intent in priority if intent in tied), next(iter(tied)))


def estimate_difficulty(user_input: str, intent: IntentType) -> Difficulty:
    """Estimate the difficulty of a task based on the request and intent."""
    text = user_input.lower()
    word_count = len(text.split())

    # Product-scale requests remain massive even when the prompt is only a few
    # words. Brand analogies and "whole startup" language otherwise bypassed
    # planning entirely because the old classifier depended on word count.
    massive_product_signals = (
        r"\b(shopify|salesforce|sap|netflix|uber|airbnb)\b",
        r"\b(erp|marketplace|distributed system|multi[ -]?tenant platform)\b",
        r"\b(build|create|make)\s+(?:me\s+|my\s+)?(?:an?\s+)?(?:whole|entire|complete)\s+"
        r"(?:startup|saas|platform|product|system)\b",
        r"\bproduction[ -]?grade\s+(?:saas|platform|product|system)\b",
    )
    if intent == IntentType.BUILD and any(
        re.search(pattern, text, re.IGNORECASE) for pattern in massive_product_signals
    ):
        return Difficulty.MASSIVE

    # Simple heuristics
    if intent in (IntentType.CHAT, IntentType.EXPLAIN, IntentType.SEARCH):
        return Difficulty.TRIVIAL if word_count < 15 else Difficulty.SIMPLE

    # Complexity indicators
    complexity_signals = [
        r"\b(entire|whole|all|every|complete|full|comprehensive)\b",
        r"\b(multiple|several|many|across|throughout)\b",
        r"\b(from scratch|production|production.ready|enterprise)\b",
        r"\b(system|architecture|infrastructure|framework|platform)\b",
        r"\b(database|API|authentication|payment|deployment)\b",
        r"\b(frontend|backend|fullstack|full.stack)\b",
    ]

    complexity_score = sum(len(re.findall(p, text, re.IGNORECASE)) for p in complexity_signals)

    if complexity_score >= 5 or word_count > 100:
        return Difficulty.MASSIVE
    elif complexity_score >= 3 or word_count > 50:
        return Difficulty.COMPLEX
    elif complexity_score >= 1 or word_count > 20:
        return Difficulty.MODERATE
    elif word_count > 8:
        return Difficulty.SIMPLE
    else:
        return Difficulty.TRIVIAL


def should_plan(
    difficulty: Difficulty,
    intent: IntentType,
    user_input: str = "",
) -> PlanType:
    """Decide if a request needs an explicit engineering plan.

    Risk dominates prompt length: concurrency, authentication, public APIs,
    persistence, migrations and multi-file work never bypass planning merely
    because the request is concise.
    """
    if intent in (IntentType.CHAT, IntentType.EXPLAIN, IntentType.SEARCH):
        return PlanType.DIRECT

    risky_signal = re.search(
        r"\b(concurr(?:ency|ent|ently)?|race(?: condition)?|deadlock|thread|async|auth|"
        r"authentication|authorization|public api|backward compat|database|schema|"
        r"migration|transaction|security|credential|dependency|multi[- ]file|"
        r"across .*files?|rollback|production)\b",
        user_input,
        re.IGNORECASE,
    )
    if risky_signal and intent not in (IntentType.DOCS, IntentType.REVIEW):
        return PlanType.PLANNED

    if difficulty in (Difficulty.COMPLEX, Difficulty.MASSIVE):
        return PlanType.PLANNED

    if difficulty == Difficulty.MODERATE and intent in {
        IntentType.FIX,
        IntentType.SECURITY,
        IntentType.MIGRATE,
        IntentType.REFACTOR,
        IntentType.OPTIMIZE,
        IntentType.DEPLOY,
    }:
        return PlanType.PLANNED

    if intent == IntentType.UNKNOWN and difficulty == Difficulty.MODERATE:
        return PlanType.RESEARCH

    return PlanType.DIRECT


# ── Skills Detection ─────────────────────────────────────────────────────────

_SKILL_KEYWORDS: dict[str, list[str]] = {
    "frontend": [
        "react",
        "vue",
        "angular",
        "svelte",
        "next.js",
        "nextjs",
        "css",
        "html",
        "ui",
        "component",
        "jsx",
        "tsx",
        "tailwind",
    ],
    "backend": [
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
        "rest",
        "graphql",
    ],
    "database": [
        "database",
        "sql",
        "postgres",
        "mysql",
        "mongo",
        "redis",
        "prisma",
        "drizzle",
        "migration",
        "schema",
        "query",
        "index",
    ],
    "security": [
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
        "cve",
    ],
    "devops": [
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
    ],
    "testing": [
        "test",
        "jest",
        "pytest",
        "mocha",
        "cypress",
        "playwright",
        "coverage",
        "mock",
        "stub",
        "e2e",
        "unit test",
    ],
    "performance": [
        "performance",
        "optimize",
        "speed",
        "cache",
        "lazy",
        "bundle",
        "memory",
        "cpu",
        "profil",
        "benchmark",
    ],
    "refactoring": [
        "refactor",
        "clean",
        "extract",
        "split",
        "rename",
        "restructure",
        "dry",
        "solid",
        "pattern",
    ],
    "api_design": [
        "api design",
        "rest",
        "graphql",
        "openapi",
        "swagger",
        "endpoint",
        "schema",
        "contract",
    ],
    "debugging": [
        "debug",
        "error",
        "crash",
        "exception",
        "stack trace",
        "breakpoint",
        "log",
        "diagnose",
    ],
    "documentation": ["document", "readme", "docstring", "jsdoc", "swagger", "guide", "wiki"],
    "git_workflow": [
        "git",
        "branch",
        "merge",
        "rebase",
        "cherry-pick",
        "bisect",
        "stash",
        "tag",
        "release",
    ],
    "migration": ["migrate", "convert", "port", "upgrade", "typescript", "legacy", "modernize"],
    "ai_engineer": [
        "llm",
        "rag",
        "embedding",
        "vector",
        "langchain",
        "openai",
        "anthropic",
        "prompt",
        "fine-tune",
        "agent",
        "chatbot",
    ],
    "mobile": ["react native", "flutter", "swift", "kotlin", "ios", "android", "mobile", "expo"],
}


def detect_skills_needed(user_input: str) -> list[str]:
    """Detect which skills are relevant to the user's request using exact word boundaries."""
    text = user_input.lower()
    needed = []
    for skill, keywords in _SKILL_KEYWORDS.items():
        for kw in keywords:
            # Match whole words or phrase boundaries
            if re.search(r"\b" + re.escape(kw) + r"\b", text):
                needed.append(skill)
                break
    return needed


# ── Planning Engine ──────────────────────────────────────────────────────────

PLANS_DIR = nexus_home() / "plans"


class PlanningEngine:
    """
    The Planning Engine intercepts complex requests and creates structured
    execution plans. Simple requests pass through directly.

    Usage:
        planner = PlanningEngine()
        analysis = planner.analyze(user_input)

        if analysis.plan_type == PlanType.PLANNED:
            plan = planner.create_plan(user_input, analysis)
            # Execute plan steps...
        else:
            # Execute directly
    """

    def __init__(self):
        PLANS_DIR.mkdir(parents=True, exist_ok=True)
        self.current_plan: ExecutionPlan | None = None
        self._plan_counter = 0
        from nexus.planning.replanner import PlanReplanner

        self._canonical_replanner = PlanReplanner(max_revisions=8)

    def analyze(self, user_input: str) -> dict:
        """
        Analyze a user request and return classification data.

        Returns:
            dict with: intent, difficulty, plan_type, skills_needed
        """
        intent = classify_intent(user_input)
        difficulty = estimate_difficulty(user_input, intent)
        plan_type = should_plan(difficulty, intent, user_input)
        skills = detect_skills_needed(user_input)

        return {
            "intent": intent,
            "difficulty": difficulty,
            "plan_type": plan_type,
            "skills_needed": skills,
        }

    def create_canonical_bundle(
        self, goal: str, repo_summary: dict | None = None
    ) -> dict:
        """Create the canonical task, engineering-plan, critique and execution contracts.

        All production entry points use this adapter so the richer planning package is
        an internal implementation detail rather than a second runtime.
        """
        from nexus.planning.engine import PlanningEngine as EngineeringPlanningEngine

        engine = EngineeringPlanningEngine()
        repository = repo_summary or {}
        contract = engine.interpret_task(goal, repository)
        engineering_plan = engine.create_engineering_plan(contract, repository)
        critique, execution_contract = engine.critique_and_finalize(
            engineering_plan, contract, repository
        )
        return {
            "task_contract": contract.to_dict(),
            "engineering_plan": engineering_plan.to_dict(),
            "critique": critique.to_dict(),
            "execution_contract": (
                execution_contract.to_dict() if execution_contract else None
            ),
        }

    @staticmethod
    def validate_canonical_plan_payload(data: dict) -> list[dict]:
        """Validate a serialized engineering plan through the canonical validator."""
        from nexus.planning.engineering_plan import EngineeringPlan
        from nexus.planning.validator import DeterministicValidator

        plan = EngineeringPlan.from_dict(data)
        return [issue.to_dict() for issue in DeterministicValidator().validate(plan)]

    def create_plan(
        self, goal: str, analysis: dict, repo_summary: dict | None = None
    ) -> ExecutionPlan:
        """
        Create an execution plan for a complex task.

        The plan is a skeleton — the agent fills in specific tool calls
        during execution. The plan provides structure and ordering.
        """
        self._plan_counter += 1
        plan_id = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{self._plan_counter}"

        intent = analysis["intent"]
        difficulty = analysis["difficulty"]
        skills = analysis.get("skills_needed", [])
        canonical: dict | None = None
        canonical_error = ""
        try:
            canonical = self.create_canonical_bundle(goal, repo_summary)
            canonical_type = str(canonical["task_contract"].get("task_type", ""))
            canonical_intents = {
                "bug_repair": IntentType.FIX,
                "test_repair": IntentType.FIX,
                "security_remediation": IntentType.SECURITY,
                "refactor": IntentType.REFACTOR,
                "migration": IntentType.MIGRATE,
                "dependency_upgrade": IntentType.MIGRATE,
                "test_creation": IntentType.TEST,
                "performance_optimization": IntentType.OPTIMIZE,
                "configuration_change": IntentType.CONFIGURE,
                "documentation": IntentType.DOCS,
                "code_explanation": IntentType.EXPLAIN,
                "investigation": IntentType.REVIEW,
                "feature_implementation": IntentType.BUILD,
            }
            intent = canonical_intents.get(canonical_type, intent)
        except Exception as exc:
            canonical_error = f"{type(exc).__name__}: {exc}"

        blueprint = (
            self._build_system_blueprint(goal, skills)
            if intent == IntentType.BUILD and difficulty == Difficulty.MASSIVE
            else {}
        )

        # Generate steps based on intent
        steps = (
            self._generate_massive_build_steps(blueprint)
            if blueprint
            else self._generate_steps(goal, intent, difficulty)
        )

        # Enforce repo_understanding if the codebase is large
        if repo_summary and (
            repo_summary.get("total_files", 0) > 10 or repo_summary.get("total_symbols", 0) > 50
        ):
            from nexus.planner import PlanStep

            understand_step = PlanStep(
                id=0,
                title="Understand Repository (Mandatory)",
                description="Use repo_index, repo_symbols, and search_code to map out the codebase architecture before proceeding.",
                tools_needed=["repo_index", "repo_symbols", "search_code"],
                depends_on=[],
            )
            steps.insert(0, understand_step)
            # Re-index steps
            for i, step in enumerate(steps):
                step.id = i
                if i > 0 and not step.depends_on:
                    step.depends_on = [i - 1]
                elif step.depends_on:
                    step.depends_on = [d + 1 for d in step.depends_on]

        verification = self._generate_verification(intent, skills)
        acceptance = self._generate_acceptance_criteria(goal, intent, verification)
        permitted_files = self._extract_permitted_files(goal)

        for step in steps:
            step.permitted_files = list(permitted_files)
            step.acceptance_criteria = list(
                dict.fromkeys([*step.acceptance_criteria, *acceptance])
            )
        plan = ExecutionPlan(
            id=plan_id,
            goal=goal,
            intent=intent,
            difficulty=difficulty,
            plan_type=PlanType.PLANNED,
            steps=steps,
            skills_needed=skills,
            verification_steps=verification,
            acceptance_criteria=acceptance,
            permitted_files=permitted_files,
            product_spec=blueprint.get("product_spec", {}),
            architecture_decisions=blueprint.get("architecture_decisions", []),
            subsystem_contracts=blueprint.get("subsystem_contracts", []),
            integration_plan=blueprint.get("integration_plan", {}),
            deployment_plan=blueprint.get("deployment_plan", {}),
            retry_policy={
                "per_task": 3 if difficulty == Difficulty.MASSIVE else 2,
                "total_repairs": (
                    max(8, len(steps) // 2)
                    if difficulty == Difficulty.MASSIVE
                    else 5
                ),
            },
            budgets={
                "max_tool_calls": (
                    max(300, sum(step.max_tool_calls + 1 for step in steps))
                    if difficulty == Difficulty.MASSIVE
                    else {
                        Difficulty.SIMPLE: 15,
                        Difficulty.MODERATE: 35,
                        Difficulty.COMPLEX: 75,
                    }.get(difficulty, 15)
                ),
                "max_hosted_calls": None,
                "max_prompt_tokens": None,
                "max_completion_tokens": None,
                "max_cost_usd": None,
            },
        )

        if canonical is not None:
            plan.canonical_contract = canonical["task_contract"]
            plan.canonical_plan = canonical["engineering_plan"]
            plan.canonical_critique = canonical["critique"]
            plan.canonical_execution_contract = canonical["execution_contract"] or {}
        else:
            plan.canonical_planning_error = canonical_error

        self.current_plan = plan
        self._save_plan(plan)
        return plan

    @staticmethod
    def _build_system_blueprint(goal: str, skills: list[str]) -> dict:
        """Create the durable product-to-deployment hierarchy for a massive build."""
        normalized_goal = goal.lower()
        subsystem_specs = [
            (
                "identity-and-tenancy",
                ["authentication", "authorization", "tenant isolation", "audit identity"],
                ["identity API", "authorization policy", "tenant context"],
                [],
            ),
            (
                "core-domain",
                ["domain rules", "state transitions", "transaction boundaries"],
                ["domain services", "domain events", "stable error taxonomy"],
                ["identity-and-tenancy"],
            ),
            (
                "data-platform",
                ["schema", "migrations", "indexes", "backup and restore"],
                ["versioned migrations", "repositories", "data retention rules"],
                ["core-domain"],
            ),
        ]

        domain_specs = []
        if re.search(r"\b(shopify|commerce|e-?commerce|storefront|marketplace)\b", normalized_goal):
            domain_specs.extend(
                [
                    (
                        "catalog-and-pricing",
                        ["products", "variants", "collections", "pricing", "promotions"],
                        ["catalog API", "price calculation contract", "search projection"],
                        ["core-domain", "data-platform"],
                    ),
                    (
                        "inventory-and-fulfillment",
                        ["stock ledger", "reservations", "warehouses", "shipping", "returns"],
                        ["inventory ledger", "fulfillment workflow", "return contract"],
                        ["core-domain", "data-platform"],
                    ),
                    (
                        "checkout-orders-and-payments",
                        ["carts", "checkout", "orders", "tax", "payments", "refunds"],
                        ["idempotent checkout", "order state machine", "payment adapter"],
                        [
                            "identity-and-tenancy",
                            "catalog-and-pricing",
                            "inventory-and-fulfillment",
                            "data-platform",
                        ],
                    ),
                    (
                        "merchant-operations",
                        ["merchant onboarding", "admin workflows", "analytics", "support"],
                        ["merchant console contract", "operational reports", "support audit trail"],
                        [
                            "identity-and-tenancy",
                            "catalog-and-pricing",
                            "inventory-and-fulfillment",
                            "checkout-orders-and-payments",
                        ],
                    ),
                ]
            )
        elif re.search(r"\b(erp|manufactur|procurement|warehouse|supply chain)\b", normalized_goal):
            domain_specs.extend(
                [
                    (
                        "master-data",
                        ["organizations", "items", "vendors", "customers", "units and taxes"],
                        ["master-data API", "validation rules", "import/export contract"],
                        ["identity-and-tenancy", "data-platform"],
                    ),
                    (
                        "inventory-and-warehouses",
                        ["stock ledger", "lots", "transfers", "counts", "valuation"],
                        ["immutable stock ledger", "warehouse workflows", "valuation reports"],
                        ["master-data"],
                    ),
                    (
                        "procurement-and-sales",
                        ["purchase orders", "receipts", "sales orders", "shipments", "returns"],
                        ["procure-to-pay workflow", "order-to-cash workflow", "approval policy"],
                        ["master-data", "inventory-and-warehouses"],
                    ),
                    (
                        "finance-and-reporting",
                        ["invoices", "payments", "tax", "journal exports", "operational reporting"],
                        ["financial posting contract", "reconciliation", "auditable reports"],
                        ["procurement-and-sales", "data-platform"],
                    ),
                ]
            )
        elif re.search(r"\b(saas|subscription|startup|platform)\b", normalized_goal):
            domain_specs.extend(
                [
                    (
                        "subscriptions-and-billing",
                        ["plans", "entitlements", "metering", "invoices", "payment recovery"],
                        ["billing contract", "entitlement policy", "payment adapter"],
                        ["identity-and-tenancy", "data-platform"],
                    ),
                    (
                        "administration-and-reporting",
                        ["tenant administration", "support", "audit", "usage analytics"],
                        ["admin API", "audit reports", "support workflows"],
                        ["subscriptions-and-billing"],
                    ),
                ]
            )

        domain_names = [item[0] for item in domain_specs]
        subsystem_specs.extend(domain_specs)
        subsystem_specs.extend(
            [
                (
                "api-and-integrations",
                ["versioned API", "validation", "idempotency", "external adapters"],
                ["API contract", "integration adapters", "contract tests"],
                    ["identity-and-tenancy", "core-domain", "data-platform", *domain_names],
                ),
                (
                "user-experience",
                ["primary workflows", "responsive UI", "accessibility", "error recovery"],
                ["routed UI", "typed client", "end-to-end user journeys"],
                    ["identity-and-tenancy", "api-and-integrations"],
                ),
                (
                "async-workflows",
                ["jobs", "queues", "retries", "dead-letter recovery"],
                ["job contracts", "retry policy", "operational controls"],
                    ["core-domain", "data-platform", *domain_names],
                ),
                (
                "platform-and-observability",
                ["configuration", "secrets", "telemetry", "health", "deployment"],
                ["deployment manifests", "dashboards", "runbooks", "rollback procedure"],
                    [
                        "identity-and-tenancy",
                        "api-and-integrations",
                        "user-experience",
                        "async-workflows",
                    ],
                ),
            ]
        )
        contracts = []
        for name, responsibilities, outputs, dependencies in subsystem_specs:
            contracts.append(
                {
                    "name": name,
                    "responsibilities": responsibilities,
                    "dependencies": dependencies,
                    "inputs": ["product specification", "architecture decisions"],
                    "outputs": outputs,
                    "invariants": [
                        "tenant and authorization boundaries are explicit",
                        "failures are observable and recoverable",
                        "public behavior is covered by executable checks",
                    ],
                    "acceptance_criteria": [
                        f"{name} contract tests pass",
                        f"{name} integrates without placeholder implementations",
                    ],
                }
            )
        return {
            "product_spec": {
                "objective": goal.strip(),
                "delivery_mode": "single-prompt long-horizon execution",
                "required_capabilities": list(dict.fromkeys(skills)),
                "domain_modules": domain_names,
                "user_outcomes": [
                    "critical user journeys work end to end",
                    "administrators can operate and recover the product",
                    "the system can be deployed and verified reproducibly",
                ],
                "non_functional_requirements": [
                    "security and tenant isolation",
                    "data integrity and reversible migrations",
                    "accessibility and responsive behavior",
                    "observability, performance budgets, and disaster recovery",
                ],
            },
            "architecture_decisions": [
                {
                    "id": "ADR-001",
                    "decision": "Define contracts before subsystem implementation",
                    "status": "required",
                },
                {
                    "id": "ADR-002",
                    "decision": "Use versioned schemas and backwards-compatible boundaries",
                    "status": "required",
                },
                {
                    "id": "ADR-003",
                    "decision": "Make retries idempotent and operations observable",
                    "status": "required",
                },
            ],
            "subsystem_contracts": contracts,
            "integration_plan": {
                "order": [item["name"] for item in contracts],
                "gates": [
                    "contract tests at every subsystem boundary",
                    "migration and rollback rehearsal",
                    "end-to-end critical journeys",
                    "security, accessibility, and performance checks",
                ],
            },
            "deployment_plan": {
                "stages": ["build", "migrate", "canary", "smoke-test", "promote"],
                "rollback": "automated application rollback with migration compatibility check",
                "readiness": ["health checks", "telemetry", "runbook", "backup restore proof"],
            },
        }

    @staticmethod
    def _generate_massive_build_steps(blueprint: dict) -> list[PlanStep]:
        """Turn the persisted blueprint into dependency-ordered execution units."""
        steps = [
            PlanStep(
                id=0,
                title="Freeze product specification",
                description="Resolve requirements, journeys, constraints, and measurable completion gates.",
                phase="product-specification",
                tools_needed=["read_file", "get_project_structure", "repo_index"],
                contract_outputs=["approved product specification", "acceptance matrix"],
            ),
            PlanStep(
                id=1,
                title="Freeze architecture and subsystem contracts",
                description="Map boundaries, data ownership, interfaces, invariants, and integration order.",
                phase="architecture",
                tools_needed=["repo_context", "repo_symbols", "write_file"],
                depends_on=[0],
                contract_inputs=["approved product specification"],
                contract_outputs=["architecture decisions", "versioned subsystem contracts"],
            ),
        ]
        subsystem_step_ids: dict[str, int] = {}
        for contract in blueprint.get("subsystem_contracts", []):
            step_id = len(steps)
            dependencies = [
                subsystem_step_ids[name]
                for name in contract.get("dependencies", [])
                if name in subsystem_step_ids
            ]
            steps.append(
                PlanStep(
                    id=step_id,
                    title=f"Implement {contract['name']}",
                    description=(
                        "Implement the subsystem against its declared interfaces and invariants; "
                        "finish its executable contract checks before advancing."
                    ),
                    phase="subsystem-implementation",
                    subsystem=contract["name"],
                    tools_needed=[
                        "read_file",
                        "search_code",
                        "write_file",
                        "edit_file",
                        "run_process",
                    ],
                    depends_on=dependencies or [1],
                    acceptance_criteria=list(contract.get("acceptance_criteria", [])),
                    checks=[f"{contract['name']} contract tests pass"],
                    contract_inputs=list(contract.get("inputs", [])),
                    contract_outputs=list(contract.get("outputs", [])),
                )
            )
            subsystem_step_ids[contract["name"]] = step_id
        integration_id = len(steps)
        steps.extend(
            [
                PlanStep(
                    id=integration_id,
                    title="Integrate the complete product",
                    description="Connect subsystem contracts and exercise critical journeys across real boundaries.",
                    phase="integration",
                    tools_needed=["repo_impact", "edit_file", "run_process", "browser_check"],
                    depends_on=list(subsystem_step_ids.values()) or [1],
                    contract_outputs=["integrated system", "end-to-end evidence"],
                ),
                PlanStep(
                    id=integration_id + 1,
                    title="Qualify release and recovery",
                    description="Run full tests, security, migration, rollback, performance, and deployment readiness gates.",
                    phase="release-qualification",
                    tools_needed=["run_process", "security_scan", "browser_check"],
                    depends_on=[integration_id],
                    contract_outputs=["release evidence", "rollback proof", "operations runbook"],
                ),
            ]
        )
        return steps

    def _generate_steps(
        self, goal: str, intent: IntentType, difficulty: Difficulty
    ) -> list[PlanStep]:
        """Generate plan steps based on intent type."""
        templates = {
            IntentType.BUILD: [
                (
                    "Understand requirements",
                    "Analyze the user's request and identify all components needed",
                    ["read_file", "get_project_structure"],
                ),
                (
                    "Research existing code",
                    "Read relevant existing files to understand the codebase",
                    ["read_file", "search_code", "list_directory"],
                ),
                ("Plan architecture", "Design the file structure and component architecture", []),
                (
                    "Implement core logic",
                    "Write the main implementation files",
                    ["write_file", "edit_file"],
                ),
                (
                    "Add supporting code",
                    "Implement helpers, utilities, types, and configuration",
                    ["write_file", "edit_file"],
                ),
                (
                    "Wire components together",
                    "Connect all pieces — imports, routes, configuration",
                    ["edit_file", "multi_edit"],
                ),
                (
                    "Test the implementation",
                    "Run the code and fix any errors",
                    ["run_process", "read_file", "edit_file"],
                ),
                (
                    "Polish and document",
                    "Add error handling, comments, and documentation",
                    ["edit_file"],
                ),
                ("Commit changes", "Stage and commit with a meaningful message", ["git_commit"]),
            ],
            IntentType.FIX: [
                (
                    "Reproduce the error",
                    "Understand the error by reading logs and running the failing code",
                    ["run_process", "read_file"],
                ),
                (
                    "Trace the root cause",
                    "Search the codebase to find where the error originates",
                    ["search_code", "read_file"],
                ),
                ("Implement the fix", "Apply the code fix", ["edit_file"]),
                ("Verify the fix", "Run the code again to confirm the fix works", ["run_process"]),
                (
                    "Add regression test",
                    "Write a test to prevent this bug from recurring",
                    ["write_file"],
                ),
                (
                    "Commit the fix",
                    "Commit with a descriptive message referencing the bug",
                    ["git_commit"],
                ),
            ],
            IntentType.REFACTOR: [
                (
                    "Analyze current code",
                    "Read and understand the code to be refactored",
                    ["read_file", "search_code"],
                ),
                ("Identify improvements", "List specific refactoring opportunities", []),
                ("Apply refactoring", "Make the code changes", ["edit_file", "multi_edit"]),
                ("Run tests", "Ensure nothing is broken by the refactoring", ["run_process"]),
                ("Commit changes", "Commit the refactored code", ["git_commit"]),
            ],
            IntentType.REVIEW: [
                ("Read the code", "Thoroughly read all files to be reviewed", ["read_file"]),
                (
                    "Check for bugs",
                    "Look for potential bugs, race conditions, edge cases",
                    ["search_code"],
                ),
                (
                    "Evaluate architecture",
                    "Assess code structure, patterns, and maintainability",
                    [],
                ),
                ("Check security", "Look for security vulnerabilities", ["search_code"]),
                ("Provide feedback", "Summarize findings with specific recommendations", []),
            ],
            IntentType.TEST: [
                ("Understand the code", "Read the code that needs testing", ["read_file"]),
                ("Identify test cases", "Determine what scenarios to test", []),
                ("Write tests", "Implement the test files", ["write_file"]),
                ("Run tests", "Execute the tests and verify they pass", ["run_process"]),
                (
                    "Fix failing tests",
                    "Debug and fix any test failures",
                    ["edit_file", "run_process"],
                ),
                ("Commit tests", "Commit the test files", ["git_commit"]),
            ],
            IntentType.DEPLOY: [
                (
                    "Verify build",
                    "Run build and tests to ensure deployment readiness",
                    ["run_process"],
                ),
                (
                    "Create deployment config",
                    "Generate Dockerfiles, CI/CD, or cloud configs",
                    ["write_file"],
                ),
                (
                    "Configure environment",
                    "Set up environment variables and secrets",
                    ["edit_file"],
                ),
                ("Deploy", "Execute the deployment", ["run_process"]),
                (
                    "Verify deployment",
                    "Check that the deployment succeeded",
                    ["run_process", "web_fetch"],
                ),
            ],
            IntentType.DOCS: [
                (
                    "Read the codebase",
                    "Understand what to document",
                    ["read_file", "get_project_structure"],
                ),
                ("Generate documentation", "Write the documentation files", ["write_file"]),
                ("Review and polish", "Ensure accuracy and completeness", ["edit_file"]),
                ("Commit docs", "Commit the documentation", ["git_commit"]),
            ],
            IntentType.MIGRATE: [
                (
                    "Analyze source",
                    "Understand the current implementation",
                    ["read_file", "get_project_structure"],
                ),
                ("Plan migration path", "Map old patterns to new ones", []),
                ("Migrate core files", "Convert the main files", ["write_file", "edit_file"]),
                (
                    "Update dependencies",
                    "Change package configs and imports",
                    ["edit_file", "run_process"],
                ),
                ("Test migration", "Verify everything works in the new form", ["run_process"]),
                ("Clean up", "Remove old files and dead code", ["run_process"]),
                ("Commit", "Commit the migrated code", ["git_commit"]),
            ],
            IntentType.OPTIMIZE: [
                ("Profile current performance", "Measure baseline performance", ["run_process"]),
                (
                    "Identify bottlenecks",
                    "Find slow areas in the code",
                    ["read_file", "search_code"],
                ),
                ("Apply optimizations", "Implement performance improvements", ["edit_file"]),
                ("Benchmark improvements", "Measure the impact of changes", ["run_process"]),
                ("Commit optimizations", "Commit with performance metrics", ["git_commit"]),
            ],
            IntentType.SECURITY: [
                (
                    "Scan for vulnerabilities",
                    "Check for common security issues",
                    ["search_code", "run_process"],
                ),
                ("Review authentication", "Check auth flows and session management", ["read_file"]),
                ("Check dependencies", "Audit dependency vulnerabilities", ["run_process"]),
                ("Fix findings", "Apply security patches", ["edit_file"]),
                (
                    "Verify fixes",
                    "Re-scan to confirm vulnerabilities are resolved",
                    ["run_process"],
                ),
                ("Commit fixes", "Commit security improvements", ["git_commit"]),
            ],
        }

        # Source-control publication is a user-controlled lifecycle action, not
        # an implementation step.  Keeping it in autonomous plans made otherwise
        # successful runs block on permissions or mutate repository history.
        template = [
            item
            for item in templates.get(intent, templates[IntentType.BUILD])
            if "git_commit" not in item[2]
        ]

        # A short plan still needs an implementation and a verification phase.
        # Prefix-truncating the templates used to turn a simple BUILD into three
        # analysis-only steps and a simple FIX into an unverified edit.
        if difficulty == Difficulty.SIMPLE:
            simple_indices = {
                IntentType.BUILD: (0, 3, 6),
                IntentType.FIX: (0, 2, 3, 4),
                IntentType.REFACTOR: (0, 2, 3),
                IntentType.REVIEW: (0, 1, 4),
                IntentType.TEST: (0, 1, 2, 3),
                IntentType.DEPLOY: (0, 1, 3, 4),
                IntentType.DOCS: (0, 1, 2),
                IntentType.MIGRATE: (0, 2, 3, 4, 5),
                IntentType.OPTIMIZE: (0, 1, 2, 3),
                IntentType.SECURITY: (0, 1, 2, 3, 4),
            }
            indices = simple_indices.get(intent, simple_indices[IntentType.BUILD])
            template = [template[index] for index in indices if index < len(template)]

        steps = []
        for i, (title, desc, tools) in enumerate(template):
            deps = [i - 1] if i > 0 else []
            steps.append(
                PlanStep(
                    id=i,
                    title=title,
                    description=desc,
                    tools_needed=tools,
                    depends_on=deps,
                )
            )

        return steps

    def _generate_verification(self, intent: IntentType, skills: list[str]) -> list[str]:
        """Generate verification steps based on intent and skills."""
        checks = []

        if intent in (IntentType.BUILD, IntentType.FIX, IntentType.REFACTOR):
            checks.append("Run the project to verify it works")
            checks.append("Check for lint/type errors")

        if intent == IntentType.TEST:
            checks.append("Ensure all tests pass")
            checks.append("Check test coverage")

        if intent == IntentType.DEPLOY:
            checks.append("Verify deployment is accessible")
            checks.append("Run smoke tests")

        if "security" in skills or intent == IntentType.SECURITY:
            checks.append("Run security scan")

        if "testing" in skills:
            checks.append("Run full test suite")

        return checks

    def _generate_acceptance_criteria(
        self,
        goal: str,
        intent: IntentType,
        verification: list[str],
    ) -> list[str]:
        """Translate a request into explicit, evidence-oriented criteria."""
        criteria = [
            f"Requested objective is implemented: {goal.strip()}",
            "No unrelated files are modified outside the approved task scope",
            "Every applied file mutation is re-read and fingerprinted",
        ]
        if intent == IntentType.FIX:
            criteria.extend(
                [
                    "The reported failure is reproduced or otherwise tied to concrete evidence",
                    "A regression check covers the corrected behavior",
                ]
            )
        elif intent == IntentType.BUILD:
            criteria.append("The new behavior has an executable test, build, or smoke check")
        elif intent == IntentType.REFACTOR:
            criteria.append("Existing externally observable behavior remains unchanged")
        elif intent == IntentType.SECURITY:
            criteria.append("Each security finding is mapped to a concrete mitigation and re-check")
        elif intent == IntentType.DEPLOY:
            criteria.append(
                "Deployment actions require explicit approval and a post-deploy smoke check"
            )

        criteria.extend(f"Verification completed: {item}" for item in verification)
        return list(dict.fromkeys(criteria))

    @staticmethod
    def _extract_permitted_files(goal: str) -> list[str]:
        """Extract explicit repository paths without treating framework names as files."""
        candidates = re.findall(
            r"(?:^|\s|`|'|\")([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z0-9]{1,8})"
            r"(?=$|\s|`|'|\"|[),:;])",
            goal,
        )
        technology_names = {
            "next.js",
            "node.js",
            "react.js",
            "vue.js",
            "angular.js",
            "three.js",
        }
        return list(
            dict.fromkeys(
                item.lstrip("./") for item in candidates if item.lower() not in technology_names
            )
        )

    @staticmethod
    def _step_risk(step: PlanStep, intent: IntentType) -> str:
        text = f"{step.title} {step.description}".lower()
        high_risk_terms = (
            "deploy",
            "migration",
            "authentication",
            "security",
            "dependency",
            "commit",
            "database",
        )
        if intent in (IntentType.DEPLOY, IntentType.SECURITY, IntentType.MIGRATE):
            return "high"
        if any(term in text for term in high_risk_terms):
            return "high"
        if not step.tools_needed:
            return "low"
        return "medium"

    def advance_step(self, step_id: int, status: TaskStatus, result: str = "") -> bool:
        """Mark a plan step as complete/failed and advance."""
        if not self.current_plan:
            return False

        step = next((item for item in self.current_plan.steps if item.id == step_id), None)
        if step is not None:
            if status == TaskStatus.COMPLETED:
                # Prevent marking a step complete until all dependencies are met
                statuses = {s.id: s.status for s in self.current_plan.steps}
                for dep_id in step.depends_on:
                    if statuses.get(dep_id) not in (TaskStatus.COMPLETED, TaskStatus.SKIPPED):
                        raise RuntimeError(
                            f"Cannot complete step {step_id}: dependency {dep_id} is not complete."
                        )

            step.status = status
            step.result = result
            if status == TaskStatus.IN_PROGRESS:
                self.current_plan.status = TaskStatus.IN_PROGRESS
                self.current_plan.current_step = step_id
                step.attempts += 1
                step.started_at = datetime.now().isoformat()
            elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                step.completed_at = datetime.now().isoformat()
                if status == TaskStatus.FAILED:
                    step.error = result
                    self.current_plan.status = TaskStatus.FAILED
                    self.current_plan.revision += 1
                    self.current_plan.failure_replans.append(
                        {
                            "revision": self.current_plan.revision,
                            "step_id": step.id,
                            "subsystem": step.subsystem,
                            "failed_at": step.completed_at,
                            "evidence": result,
                            "required_action": (
                                "Diagnose root cause, update affected contracts and impact scope, "
                                "then retry only this step and its unfinished dependents."
                            ),
                        }
                    )
                elif self.current_plan.is_complete:
                    self.current_plan.status = TaskStatus.COMPLETED
                    self.current_plan.current_step = None
                else:
                    next_step = self.current_plan.next_step
                    self.current_plan.current_step = next_step.id if next_step else None

            self._save_plan(self.current_plan)
            return True
        return False

    def retry_step(self, step_id: int, failure_context: str = "") -> bool:
        """Move a failed task back to pending without erasing its audit trail."""
        if not self.current_plan:
            return False
        step = next((item for item in self.current_plan.steps if item.id == step_id), None)
        if step is None or step.status != TaskStatus.FAILED:
            return False
        step.status = TaskStatus.PENDING
        step.completed_at = ""
        step.error = failure_context or step.error
        self.current_plan.status = TaskStatus.IN_PROGRESS
        self.current_plan.current_step = step.id
        self.current_plan.revision += 1
        self.current_plan.failure_replans.append(
            {
                "revision": self.current_plan.revision,
                "step_id": step.id,
                "subsystem": step.subsystem,
                "failed_at": datetime.now().isoformat(),
                "evidence": (failure_context or step.error)[:4000],
                "required_action": (
                    "Retry the same bounded step using the failure evidence; inspect current "
                    "repository state first and choose a materially different repair approach."
                ),
            }
        )
        self._save_plan(self.current_plan)
        return True

    def revise_canonical_plan(
        self,
        plan: ExecutionPlan,
        *,
        trigger_reason: str,
        failed_step_id: int | None = None,
        evidence: dict | None = None,
    ) -> bool:
        """Structurally revise the canonical plan from new runtime evidence.

        The legacy execution plan remains the runtime scheduler, while the
        canonical engineering plan records the changed investigation,
        hypothesis, scope and targeted verification strategy. Duplicate
        evidence is rejected by :class:`PlanReplanner`.
        """
        if not plan.canonical_plan:
            return False

        from nexus.planning.engineering_plan import EngineeringPlan

        try:
            canonical = EngineeringPlan.from_dict(plan.canonical_plan)
        except (KeyError, TypeError, ValueError):
            return False

        canonical_failed_step_id: str | None = None
        if failed_step_id is not None and canonical.steps:
            legacy_step = next((item for item in plan.steps if item.id == failed_step_id), None)
            if legacy_step is not None:
                legacy_terms = {
                    term
                    for term in re.findall(
                        r"[a-z0-9_]+",
                        f"{legacy_step.title} {legacy_step.description}".lower(),
                    )
                    if len(term) > 3
                }
                ranked: list[tuple[int, str]] = []
                for item in canonical.steps:
                    canonical_terms = set(
                        re.findall(
                            r"[a-z0-9_]+",
                            f"{item.title} {item.objective}".lower(),
                        )
                    )
                    ranked.append((len(legacy_terms.intersection(canonical_terms)), item.step_id))
                ranked.sort(reverse=True)
                if ranked and ranked[0][0] > 0:
                    canonical_failed_step_id = ranked[0][1]
            if canonical_failed_step_id is None:
                canonical_failed_step_id = canonical.steps[
                    min(max(int(failed_step_id), 0), len(canonical.steps) - 1)
                ].step_id

        revised, accepted = self._canonical_replanner.revise_plan(
            canonical,
            trigger_reason,
            failed_step_id=canonical_failed_step_id,
            new_evidence=evidence or {},
        )
        if not accepted:
            return False

        plan.canonical_plan = revised.to_dict()
        plan.revision += 1
        record = {
            "revision": plan.revision,
            "canonical_version": revised.version,
            "step_id": failed_step_id,
            "canonical_step_id": canonical_failed_step_id,
            "failed_at": datetime.now().isoformat(),
            "evidence": trigger_reason[:4000],
            "evidence_payload": dict(evidence or {}),
            "required_action": (
                "Execute the revised evidence-inspection step, invalidate the old causal "
                "assumption when contradicted, then run targeted verification before retrying."
            ),
            "structural_replan": True,
        }
        plan.failure_replans.append(record)
        plan.canonical_critique = {
            **dict(plan.canonical_critique),
            "decision": "REVISED_AFTER_RUNTIME_FAILURE",
            "latest_runtime_replan": record,
        }
        plan.canonical_execution_contract = {
            **dict(plan.canonical_execution_contract),
            "engineering_plan_version": revised.version,
            "requires_reverification": True,
        }
        self.current_plan = plan
        self._save_plan(plan)
        return True

    def get_plan_context(self) -> str:
        """Generate context string for the agent about the current plan."""
        if not self.current_plan:
            return ""

        plan = self.current_plan
        next_step = (
            next(
                (step for step in plan.steps if step.status == TaskStatus.IN_PROGRESS),
                None,
            )
            or plan.next_step
        )

        if not next_step:
            if plan.is_complete:
                return f"\n[PLAN COMPLETE] All steps finished. Progress: {plan.progress:.0f}%\n"
            return ""

        context = f"""
[EXECUTION PLAN — {plan.goal}]
Progress: {plan.progress:.0f}% | Step {next_step.id + 1}/{len(plan.steps)}

Current Step: {next_step.title}
Description: {next_step.description}
Phase: {next_step.phase}
Subsystem: {next_step.subsystem or "cross-cutting"}
Suggested Tools: {", ".join(next_step.tools_needed) if next_step.tools_needed else "any"}

Remaining Steps:
"""
        for step in plan.steps:
            if step.status == TaskStatus.PENDING:
                context += f"  {step.id + 1}. {step.title}\n"

        if plan.verification_steps:
            context += "\nVerification (after completion):\n"
            for v in plan.verification_steps:
                context += f"  • {v}\n"

        if plan.product_spec:
            context += "\nPersistent Product Specification:\n"
            context += f"  Objective: {plan.product_spec.get('objective', plan.goal)}\n"
            for requirement in plan.product_spec.get("non_functional_requirements", []):
                context += f"  • {requirement}\n"
        if next_step.contract_inputs or next_step.contract_outputs:
            context += "\nCurrent Contract:\n"
            for item in next_step.contract_inputs:
                context += f"  Input: {item}\n"
            for item in next_step.contract_outputs:
                context += f"  Output: {item}\n"
        if plan.failure_replans:
            latest = plan.failure_replans[-1]
            context += (
                "\nLatest Failure Replan:\n"
                f"  Step {latest.get('step_id')}: {latest.get('evidence', '')}\n"
                f"  Required action: {latest.get('required_action', '')}\n"
            )

        return context

    def _save_plan(self, plan: ExecutionPlan):
        """Persist plan to disk using an atomic write."""
        filepath = PLANS_DIR / f"{plan.id}.json"
        temp_path = filepath.with_suffix(".tmp")
        with open(temp_path, "w") as f:
            json.dump(plan.to_dict(), f, indent=2)
        temp_path.replace(filepath)

    def load_plan(self, plan_id: str) -> ExecutionPlan | None:
        """Load a plan from disk."""
        filepath = PLANS_DIR / f"{plan_id}.json"
        if not filepath.exists():
            return None
        try:
            with open(filepath) as f:
                data = json.load(f)
            plan = ExecutionPlan.from_dict(data)
            self.current_plan = plan
            return plan
        except (json.JSONDecodeError, KeyError):
            return None

    def list_plans(self, limit: int = 10) -> list[dict]:
        """List recent plans."""
        plans = []
        for filepath in sorted(PLANS_DIR.glob("*.json"), reverse=True)[:limit]:
            try:
                with open(filepath) as f:
                    data = json.load(f)
                plans.append(
                    {
                        "id": data["id"],
                        "goal": data["goal"][:80],
                        "intent": data.get("intent", "unknown"),
                        "progress": sum(
                            1 for s in data.get("steps", []) if s.get("status") == "completed"
                        ),
                        "total_steps": len(data.get("steps", [])),
                        "created_at": data.get("created_at", ""),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return plans

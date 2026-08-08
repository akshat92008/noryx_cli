"""Optional Tree-sitter parsing and persistent Language Server Protocol clients."""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote


@dataclass(frozen=True)
class LanguageServerSpec:
    language: str
    command: tuple[str, ...]


DEFAULT_SERVERS: dict[str, tuple[LanguageServerSpec, ...]] = {
    "python": (
        LanguageServerSpec("python", ("basedpyright-langserver", "--stdio")),
        LanguageServerSpec("python", ("pyright-langserver", "--stdio")),
        LanguageServerSpec("python", ("pylsp",)),
    ),
    "typescript": (LanguageServerSpec("typescript", ("typescript-language-server", "--stdio")),),
    "javascript": (LanguageServerSpec("javascript", ("typescript-language-server", "--stdio")),),
    "go": (LanguageServerSpec("go", ("gopls",)),),
    "rust": (LanguageServerSpec("rust", ("rust-analyzer",)),),
}


class LSPError(RuntimeError):
    """Raised for protocol, startup, or request failures."""


class LSPClient:
    """Small JSON-RPC 2.0 LSP client with one persistent server process."""

    def __init__(
        self,
        root: str | Path,
        language: str,
        *,
        command: tuple[str, ...] | None = None,
        timeout_seconds: float = 10.0,
        network: bool = False,
    ):
        self.root = Path(root).expanduser().resolve()
        self.language = language.lower()
        self.command = command or self.discover(self.language)
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.network = bool(network)
        self.process: subprocess.Popen[bytes] | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._request_id = 0
        self._write_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._open_documents: set[str] = set()

    @staticmethod
    def discover(language: str) -> tuple[str, ...]:
        for candidate in DEFAULT_SERVERS.get(language.lower(), ()):
            if shutil.which(candidate.command[0]):
                return candidate.command
        raise LSPError(f"No language server found for {language}")

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        from nexus.process_gateway import ProcessExecutionGateway, ProcessRequest
        request = ProcessRequest.create(
            purpose="language_server",
            command=list(self.command),
            workspace=self.root,
            # Language servers can read large portions of the repository.
            # Keep outbound access denied unless a caller explicitly opts in.
            network_policy="allow" if self.network else "deny",
        )
        self.process = ProcessExecutionGateway.popen(
            request,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        root_uri = self._uri(self.root)
        self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": root_uri,
                "capabilities": {
                    "textDocument": {
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    }
                },
                "workspaceFolders": [{"uri": root_uri, "name": self.root.name}],
            },
        )
        self.notify("initialized", {})

    def close(self) -> None:
        process = self.process
        if not process:
            return
        try:
            if process.poll() is None:
                self.request("shutdown", None)
                self.notify("exit", None)
                process.wait(timeout=2)
        except (LSPError, subprocess.TimeoutExpired):
            process.terminate()
        finally:
            self.process = None
            self._open_documents.clear()

    def request(self, method: str, params: Any) -> Any:
        self._ensure_running(method)
        self._request_id += 1
        request_id = self._request_id
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self._pending[request_id] = response_queue
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            response = response_queue.get(timeout=self.timeout_seconds)
        except queue.Empty as exc:
            self._pending.pop(request_id, None)
            raise LSPError(f"LSP request timed out: {method}") from exc
        if "error" in response:
            raise LSPError(f"LSP {method} failed: {response['error']}")
        return response.get("result")

    def notify(self, method: str, params: Any) -> None:
        self._ensure_running(method)
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def document_symbols(self, path: str | Path) -> list[dict[str, Any]]:
        resolved = self._open(path)
        result = self.request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": self._uri(resolved)}},
        )
        return result if isinstance(result, list) else []

    def definition(self, path: str | Path, line: int, character: int) -> Any:
        resolved = self._open(path)
        return self.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": self._uri(resolved)},
                "position": {"line": max(0, line), "character": max(0, character)},
            },
        )

    def references(
        self,
        path: str | Path,
        line: int,
        character: int,
        *,
        include_declaration: bool = True,
    ) -> list[dict[str, Any]]:
        resolved = self._open(path)
        result = self.request(
            "textDocument/references",
            {
                "textDocument": {"uri": self._uri(resolved)},
                "position": {"line": max(0, line), "character": max(0, character)},
                "context": {"includeDeclaration": include_declaration},
            },
        )
        return result if isinstance(result, list) else []

    def _open(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = self.root / resolved
        resolved = resolved.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise LSPError(f"Document is outside repository: {resolved}") from exc
        if not resolved.is_file():
            raise LSPError(f"Document does not exist: {resolved}")
        uri = self._uri(resolved)
        if uri not in self._open_documents:
            text = resolved.read_text(encoding="utf-8")
            self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": self.language,
                        "version": 1,
                        "text": text,
                    }
                },
            )
            self._open_documents.add(uri)
        return resolved

    def _ensure_running(self, method: str) -> None:
        if method != "initialize" and (not self.process or self.process.poll() is not None):
            self.start()
        if not self.process or self.process.poll() is not None:
            raise LSPError("Language server is not running")

    def _send(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise LSPError("Language server stdin is unavailable")
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        framed = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        with self._write_lock:
            try:
                self.process.stdin.write(framed)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise LSPError(f"Could not write to language server: {exc}") from exc

    def _read_loop(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        while process.poll() is None:
            headers: dict[str, str] = {}
            while True:
                line = process.stdout.readline()
                if not line:
                    return
                if line in {b"\r\n", b"\n"}:
                    break
                decoded = line.decode("ascii", errors="replace").strip()
                if ":" in decoded:
                    key, value = decoded.split(":", 1)
                    headers[key.lower()] = value.strip()
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                continue
            if length <= 0:
                continue
            payload = process.stdout.read(length)
            try:
                message = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            request_id = message.get("id")
            if isinstance(request_id, int) and request_id in self._pending:
                self._pending.pop(request_id).put(message)
            else:
                self._messages.put(message)

    @staticmethod
    def _uri(path: Path) -> str:
        return "file://" + quote(str(path), safe="/:")


class TreeSitterAdapter:
    """Use ``tree-sitter-language-pack`` when installed; never fake a parse."""

    DECLARATION_TYPES = {
        "function_definition",
        "function_declaration",
        "method_definition",
        "method_declaration",
        "class_definition",
        "class_declaration",
        "interface_declaration",
        "struct_item",
        "function_item",
        "type_declaration",
    }

    def __init__(self):
        try:
            from tree_sitter_language_pack import get_parser
        except ImportError:
            self._get_parser = None
        else:
            self._get_parser = get_parser

    @property
    def available(self) -> bool:
        return self._get_parser is not None

    def symbols(self, source: str, language: str) -> list[dict[str, Any]]:
        if not self._get_parser:
            raise LSPError("Tree-sitter support is unavailable; install nexusai-cli[intelligence]")
        parser = self._get_parser(language)
        tree = parser.parse(source.encode("utf-8"))
        encoded = source.encode("utf-8")
        results: list[dict[str, Any]] = []

        def walk(node) -> None:
            if node.type in self.DECLARATION_TYPES:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    results.append(
                        {
                            "name": encoded[name_node.start_byte : name_node.end_byte].decode(
                                "utf-8", errors="replace"
                            ),
                            "kind": node.type,
                            "line": node.start_point[0] + 1,
                            "column": node.start_point[1],
                        }
                    )
            for child in node.children:
                walk(child)

        walk(tree.root_node)
        return results


class LanguageServicePool:
    """Keep one LSP client per repository/language for the process lifetime."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self._clients: dict[str, LSPClient] = {}

    def client(self, language: str) -> LSPClient:
        normalized = language.lower()
        if normalized not in self._clients:
            client = LSPClient(self.root, normalized)
            client.start()
            self._clients[normalized] = client
        return self._clients[normalized]

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()


def type_check(path: str = ".") -> str:
    """
    Run static type checking using pyright (or mypy as fallback) on the given path.
    """
    from nexus.process_gateway import ProcessExecutionGateway, ProcessRequest

    try:
        request = ProcessRequest.create(
            purpose="type_check",
            command=["pyright", path],
            workspace=Path(path).expanduser().resolve() if Path(path).is_dir() else Path(path).parent.resolve(),
            timeout_seconds=30
        )
        result = ProcessExecutionGateway.run(request)
        if result.timed_out:
            return "❌ Type check timed out after 30 seconds."
        if result.success or (result.exit_code is not None):
            return result.stdout if result.stdout else (result.stderr or "✅ No type errors found (pyright).")
        raise FileNotFoundError()
    except FileNotFoundError:
        try:
            req2 = ProcessRequest.create(
                purpose="type_check",
                command=["mypy", path],
                workspace=Path(path).expanduser().resolve() if Path(path).is_dir() else Path(path).parent.resolve(),
                timeout_seconds=30
            )
            result = ProcessExecutionGateway.run(req2)
            if result.timed_out:
                return "❌ Type check timed out after 30 seconds."
            if result.success or (result.exit_code is not None):
                return result.stdout if result.stdout else (result.stderr or "✅ No type errors found (mypy).")
            raise FileNotFoundError()
        except FileNotFoundError:
            return "❌ No type checker found (pyright or mypy not installed)."

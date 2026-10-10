"""CLI-backed AI providers. Codex runs without tools or persisted sessions."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.ai.provider_registry import ProviderConfigurationError
from app.ai.providers.base import AIProvider

CLI_BINARY_HINTS: dict[str, str] = {
    "future_cli_cursor": "cursor",
    "future_cli_gemini": "gemini",
    "future_cli_claude": "claude",
    "future_cli_codex": "codex",
}
CODEX_MODELS = ("gpt-6-luna", "gpt-6-sol", "gpt-6-astra")
CODEX_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _codex_binary() -> str | None:
    """Find the CLI in normal installs or the desktop app's bundled runtime."""
    found = shutil.which("codex")
    if found:
        return found
    bundled = Path("/usr/lib/chatgpt/resources/codex")
    return str(bundled) if bundled.is_file() else None


class CLIProvider(AIProvider):
    """Future: OAuth/browser-auth CLIs (Cursor, Gemini, Claude Code, Codex)."""

    provider_id: str = ""
    cli_name: str = ""
    auth_mode: str = "cli_oauth"

    def health_check(self) -> dict[str, Any]:
        binary = CLI_BINARY_HINTS.get(self.provider_id, self.cli_name)
        on_path = bool(binary and shutil.which(binary))
        return {
            "ok": False,
            "provider": self.provider_id,
            "auth_mode": self.auth_mode,
            "cli_binary": binary,
            "cli_on_path": on_path,
            "error": "CLI provider not implemented yet",
        }

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(
            f"CLI provider '{self.provider_id}' is not implemented; use openai_compatible or lmstudio."
        )

    def call_tools(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        raise NotImplementedError(f"CLI tool calling not implemented for '{self.provider_id}'")


class CursorCLIProvider(CLIProvider):
    provider_id = "future_cli_cursor"
    cli_name = "cursor"


class GeminiCLIProvider(CLIProvider):
    provider_id = "future_cli_gemini"
    cli_name = "gemini"


class ClaudeCodeCLIProvider(CLIProvider):
    provider_id = "future_cli_claude"
    cli_name = "claude"


class CodexCLIProvider(CLIProvider):
    provider_id = "future_cli_codex"
    cli_name = "codex"

    def __init__(self, model: str = "gpt-6-luna", reasoning_effort: str = "high") -> None:
        if model not in CODEX_MODELS or reasoning_effort not in CODEX_REASONING_EFFORTS:
            raise ProviderConfigurationError("Choose a supported Codex model and reasoning effort.")
        self.model = model
        self.reasoning_effort = reasoning_effort

    def health_check(self) -> dict[str, Any]:
        binary = _codex_binary()
        if not binary:
            return {"ok": False, "error": "Codex CLI is not installed."}
        try:
            result = subprocess.run(
                [binary, "login", "status"], capture_output=True, text=True,
                timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {"ok": False, "error": "Codex sign-in could not be checked."}
        return {"ok": result.returncode == 0, "provider": self.provider_id,
                "auth_mode": self.auth_mode,
                "error": None if result.returncode == 0 else "Codex CLI needs sign-in."}

    def generate_reply(self, prompt: str) -> str:
        binary = _codex_binary()
        if not binary:
            raise ProviderConfigurationError("Codex CLI is not installed.")
        with tempfile.TemporaryDirectory(prefix="ledgermind-codex-") as workspace:
            output = Path(workspace) / "answer.txt"
            command = [
                binary, "exec", "--model", self.model,
                "--config", f'model_reasoning_effort="{self.reasoning_effort}"',
                "--config", "features.shell_tool=false",
                "--config", "features.unified_exec=false",
                "--config", "apps._default.enabled=false",
                "--config", 'web_search="disabled"',
                "--sandbox", "read-only", "--ephemeral", "--ignore-user-config",
                "--ignore-rules", "--skip-git-repo-check", "--cd", workspace,
                "--output-last-message", str(output), "-",
            ]
            try:
                result = subprocess.run(
                    command, input=prompt, capture_output=True, text=True,
                    timeout=120, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ProviderConfigurationError("The financial assistant timed out. Try again.") from exc
            if result.returncode != 0 or not output.is_file():
                raise ProviderConfigurationError(
                    "Codex could not answer right now. Check sign-in in Advanced settings."
                )
            answer = output.read_text(encoding="utf-8").strip()
            if not answer:
                raise ProviderConfigurationError("Codex returned an empty answer. Try again.")
            return answer

    def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: dict[str, Any],
    ) -> dict[str, Any]:
        answer = self.generate_reply(
            f"{system_prompt}\nReturn only JSON matching this schema: {json.dumps(schema)}"
            f"\n\nRequest: {user_prompt}"
        )
        if answer.startswith("```"):
            answer = answer.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(answer)


_CLI_CLASSES: dict[str, type[CLIProvider]] = {
    "future_cli_cursor": CursorCLIProvider,
    "future_cli_gemini": GeminiCLIProvider,
    "future_cli_claude": ClaudeCodeCLIProvider,
    "future_cli_codex": CodexCLIProvider,
}


def get_cli_provider(provider_id: str, model: str = "gpt-6-luna", reasoning_effort: str = "high") -> CLIProvider:
    cls = _CLI_CLASSES.get(provider_id)
    if not cls:
        raise ProviderConfigurationError(f"No CLI adapter registered for '{provider_id}'")
    if provider_id == "future_cli_codex":
        return CodexCLIProvider(model=model, reasoning_effort=reasoning_effort)
    return cls()

import os
import json
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field

@dataclass
class NexusConfig:
    model_settings: Dict[str, Any] = field(default_factory=dict)
    provider_settings: Dict[str, Any] = field(default_factory=dict)
    execution_limits: Dict[str, int] = field(default_factory=lambda: {"max_steps": 50, "timeout_seconds": 300})
    security_settings: Dict[str, Any] = field(default_factory=lambda: {"sandbox_enabled": True})
    budget_settings: Dict[str, float] = field(default_factory=lambda: {"max_spend_usd": 10.0})
    workspace_settings: Dict[str, Any] = field(default_factory=lambda: {"use_git_worktree": False})

    @classmethod
    def load(cls, cli_args: Optional[Dict[str, Any]] = None) -> "NexusConfig":
        """
        Load configuration resolving in priority:
        CLI arguments > project config > user config > environment > defaults
        """
        config = cls()
        
        # 4. Environment
        if "NEXUS_MODEL" in os.environ:
            config.model_settings["default_model"] = os.environ["NEXUS_MODEL"]
        if "NEXUS_MAX_STEPS" in os.environ:
            config.execution_limits["max_steps"] = int(os.environ["NEXUS_MAX_STEPS"])
            
        # 3. User config (e.g. ~/.nexus/config.json)
        user_config_path = Path.home() / ".nexus" / "config.json"
        if user_config_path.exists():
            try:
                with open(user_config_path, "r") as f:
                    user_data = json.load(f)
                    config._merge(user_data)
            except Exception:
                pass
                
        # 2. Project config (.nexus/config.json)
        project_config_path = Path(".nexus") / "config.json"
        if project_config_path.exists():
            try:
                with open(project_config_path, "r") as f:
                    project_data = json.load(f)
                    config._merge(project_data)
            except Exception:
                pass
                
        # 1. CLI args
        if cli_args:
            config._merge(cli_args)
            
        return config

    def _merge(self, data: Dict[str, Any]) -> None:
        if "model_settings" in data:
            self.model_settings.update(data["model_settings"])
        if "provider_settings" in data:
            self.provider_settings.update(data["provider_settings"])
        if "execution_limits" in data:
            self.execution_limits.update(data["execution_limits"])
        if "security_settings" in data:
            self.security_settings.update(data["security_settings"])
        if "budget_settings" in data:
            self.budget_settings.update(data["budget_settings"])
        if "workspace_settings" in data:
            self.workspace_settings.update(data["workspace_settings"])

# Global singleton for current config
_config_instance: Optional[NexusConfig] = None

def get_config() -> NexusConfig:
    global _config_instance
    if _config_instance is None:
        _config_instance = NexusConfig.load()
    return _config_instance

def set_config(config: NexusConfig) -> None:
    global _config_instance
    _config_instance = config

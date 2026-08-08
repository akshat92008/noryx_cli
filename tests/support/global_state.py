"""Test support utilities for global state management."""

import secrets

def reset_global_state() -> None:
    """Reset shared module-level state for deterministic test isolation."""
    errors = []
    try:
        from nexus.runtime.process_state import reset_process_state
        reset_process_state(strict=True)
    except Exception as exc:
        errors.append(f"Process-state reset failed: {exc}")
    
    from nexus.sandbox import SandboxRunner
    SandboxRunner._backend_cache = None

    try:
        from nexus.webapp import server
        for agent in server._agents.values():
            try:
                if hasattr(agent, "close"):
                    agent.close()
            except Exception as e:
                errors.append(f"Failed to close agent: {e}")
        server._agents.clear()
        server._agent_busy.clear()
        server._agent_locks.clear()
        # Regenerate token securely instead of nullifying
        server._web_token = secrets.token_urlsafe(32)
    except ImportError:
        pass

    try:
        from nexus import tools
        try:
            tools.stop_all_background_processes()
        except Exception as e:
            errors.append(f"Failed to stop background processes: {e}")

        for pool in tools._language_service_pools.values():
            if hasattr(pool, "close"):
                try:
                    pool.close()
                except Exception as e:
                    errors.append(f"Failed to close language service pool: {e}")
        tools._language_service_pools.clear()
        tools._tool_working_dir.set(None)
        tools._tool_history.set(None)
        tools._tool_owner.set("")
    except ImportError:
        pass

    if errors:
        raise RuntimeError("Errors occurred during state reset:\n" + "\n".join(errors))

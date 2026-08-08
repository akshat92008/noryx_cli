import os
import threading
import time
from typing import Callable


class RoutineOrchestrator:
    """Manages scheduled background tasks and peer agent messages."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.routines = []
            cls._instance.peers = {}  # peer_name -> callback
            cls._instance.lock = threading.Lock()
            cls._instance.running = True
            cls._instance.thread = threading.Thread(target=cls._instance._loop, daemon=True)
            cls._instance.thread.start()
        return cls._instance
        
    def _loop(self):
        while self.running:
            now = time.time()
            to_run = []
            with self.lock:
                for r in self.routines:
                    if now >= r['next_run']:
                        to_run.append(r)
                        r['next_run'] = now + r['interval']
            
            for r in to_run:
                try:
                    # Run routine callback in a separate thread so we don't block the loop
                    t = threading.Thread(target=r['callback'], args=(r['task'],), daemon=True)
                    t.start()
                except Exception as e:
                    print(f"Routine error: {e}")
                    
            time.sleep(1)

    def schedule(self, interval_seconds: int, task: str, callback: Callable):
        with self.lock:
            self.routines.append({
                'interval': interval_seconds,
                'task': task,
                'callback': callback,
                'next_run': time.time() + interval_seconds
            })
            
    def register_peer(self, name: str, callback: Callable):
        with self.lock:
            self.peers[name] = callback
            
    def message_peer(self, name: str, message: str) -> str:
        with self.lock:
            cb = self.peers.get(name)
        if not cb:
            return f"❌ Peer '{name}' not found or not active."
        try:
            return cb(message)
        except Exception as e:
            return f"❌ Error messaging peer '{name}': {e}"

def schedule_routine(cron_or_interval: str, task: str, agent=None) -> str:
    """Schedule a routine task."""
    try:
        interval = int(cron_or_interval)
    except ValueError:
        # Fallback to simple interval if cron parsing not implemented
        return "❌ cron_expr_or_interval must be an integer (seconds) for now."
    
    from nexus.tools import _tool_working_dir
    working_dir = _tool_working_dir.get() or os.getcwd()
    
    def run_task(task_text):
        from nexus.subagents.orchestrator import SubagentOrchestrator
        from nexus.subagents.templates import create_subagent
        
        # Determine API key contextually
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("NVIDIA_API_KEY", "")
        
        # Use base_subagent for routines if a specific template doesn't exist
        subagent = create_subagent("base", task_text, working_dir)
        if not subagent:
            return
            
        orchestrator = SubagentOrchestrator(
            api_key=api_key,
            model_id="default",
            working_dir=working_dir,
        )
        orchestrator.run_single(subagent)
        
    orch = RoutineOrchestrator()
    orch.schedule(interval, task, run_task)
    return f"✅ Scheduled task '{task}' to run every {interval} seconds."

def message_peer(peer_name: str, message: str) -> str:
    orch = RoutineOrchestrator()
    return orch.message_peer(peer_name, message)

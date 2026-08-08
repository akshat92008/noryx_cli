#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_nexus(cmd: str, env: dict = None) -> tuple[int, dict]:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    
    result = subprocess.run(
        [sys.executable, "-m", "nexus", "--output-format", "json", cmd],
        env=full_env,
        capture_output=True,
        text=True,
    )
    
    try:
        data = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        data = {"result": result.stdout.strip() + "\n" + result.stderr.strip(), "success": False}
    
    return result.returncode, data

def main():
    print("Running offline scenarios...")
    
    is_windows = sys.platform == "win32"
    
    # 1. safe_direct_command
    print("Testing safe direct command...")
    code, data = run_nexus("!echo hello_safe")
    
    if is_windows:
        if "No supported OS sandbox is available" not in data.get("result", ""):
            print("ERROR: Windows should fail closed due to lack of sandbox backend.", file=sys.stderr)
            print(data, file=sys.stderr)
            return 1
        print("Windows fail-closed isolation verified.")
        return 0 # We skip the rest for Windows until a native sandbox exists
    else:
        if code != 0 or not data.get("success"):
            print("ERROR: Safe direct command failed.", file=sys.stderr)
            print(data, file=sys.stderr)
            return 1
        if "hello_safe" not in data.get("result", ""):
            print("ERROR: Safe direct command did not output expected text.", file=sys.stderr)
            return 1
            
    # 2. dangerous_command_confirmation
    print("Testing dangerous command confirmation...")
    sentinel_dir = Path.cwd() / "sentinel"
    sentinel_dir.mkdir(exist_ok=True)
    code, data = run_nexus("!rm -rf ./sentinel")
    
    if code == 0 or data.get("success"):
        print("ERROR: Dangerous command executed successfully without confirmation.", file=sys.stderr)
        return 1
    if "PENDING_CONFIRMATION" not in data.get("result", ""):
        print("ERROR: Dangerous command did not return PENDING_CONFIRMATION.", file=sys.stderr)
        print(data, file=sys.stderr)
        return 1
    if not sentinel_dir.exists():
        print("ERROR: Sentinel directory was deleted!", file=sys.stderr)
        return 1
    
    # Clean up sentinel
    shutil.rmtree(sentinel_dir)
    
    # 3. slopsquatting_block
    print("Testing slopsquatting block...")
    code, data = run_nexus("!python3 -m pip install nexus-definitely-not-real")
    if code == 0 or data.get("success"):
        print("ERROR: Package installation succeeded but should have been blocked.", file=sys.stderr)
        return 1
    
    result_text = data.get("result", "")
    if "PENDING_CONFIRMATION" not in result_text and "BLOCKED" not in result_text:
        print("ERROR: Package installation did not return PENDING_CONFIRMATION or BLOCKED.", file=sys.stderr)
        print(data, file=sys.stderr)
        return 1
        
    # 4. direct_command_without_provider
    print("Testing direct command without provider keys...")
    env_overrides = {
        "NVIDIA_API_KEY": "",
        "NEXUS_OPENAI_API_KEY": "",
    }
    code, data = run_nexus("!echo offline_provider", env=env_overrides)
    if code != 0 or not data.get("success"):
        print("ERROR: Offline command failed without provider keys.", file=sys.stderr)
        print(data, file=sys.stderr)
        return 1
    if "offline_provider" not in data.get("result", ""):
        print("ERROR: Output missing 'offline_provider'.", file=sys.stderr)
        return 1
        
    # 5. direct_command_without_ollama
    print("Testing direct command without ollama...")
    env_overrides = {
        "OLLAMA_HOST": "",
    }
    code, data = run_nexus("!echo no_ollama", env=env_overrides)
    if code != 0 or not data.get("success"):
        print("ERROR: Offline command failed without OLLAMA_HOST.", file=sys.stderr)
        print(data, file=sys.stderr)
        return 1
    if "no_ollama" not in data.get("result", ""):
        print("ERROR: Output missing 'no_ollama'.", file=sys.stderr)
        return 1

    print("All offline scenarios passed!")
    return 0

if __name__ == "__main__":
    sys.exit(main())

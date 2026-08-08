import os
import re
from pathlib import Path
import pytest

FORBIDDEN_MARKERS = [
    "STUB_PASS",
    "MOCK_PASS",
    "ASSUMED_PASS",
    "SIMULATED_SUCCESS",
    "FAKE_PASS",
    "PLACEHOLDER_SUCCESS",
]

def test_no_fake_success_markers_in_codebase():
    """Ensure no fake success markers exist in the production source code."""
    # Find the nexus directory
    current_dir = Path(__file__).parent
    product_dir = current_dir.parent
    nexus_dir = product_dir / "nexus"
    
    assert nexus_dir.exists() and nexus_dir.is_dir(), f"Nexus directory not found at {nexus_dir}"
    
    violating_files = []
    
    for root, _, files in os.walk(nexus_dir):
        for file in files:
            if not file.endswith(".py"):
                continue
                
            file_path = Path(root) / file
            
            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception:
                continue
                
            for marker in FORBIDDEN_MARKERS:
                if marker in content:
                    violating_files.append((str(file_path.relative_to(product_dir)), marker))
                    
    if violating_files:
        error_lines = [f"{f}: contains '{m}'" for f, m in violating_files]
        error_msg = "Found forbidden fake-success markers in production code:\n" + "\n".join(error_lines)
        pytest.fail(error_msg)

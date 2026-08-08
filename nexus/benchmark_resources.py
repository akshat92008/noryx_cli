"""Installed benchmark resource locator."""
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
@contextmanager
def installed_core_manifest():
    resource=resources.files("nexus.resources.benchmarks").joinpath("core.json")
    with resources.as_file(resource) as path:
        manifest=Path(path).resolve()
        if not (manifest.parent/"fixtures/calculator/verify.py").is_file(): raise FileNotFoundError("installed benchmark fixture missing")
        yield manifest

#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nexus.platform.sandbox_qualification import qualify_native_sandbox
p=argparse.ArgumentParser(); p.add_argument('--workspace', default='.'); p.add_argument('--output', default='sandbox-qualification.json'); p.add_argument('--require-autonomous', action='store_true'); a=p.parse_args()
q=qualify_native_sandbox(a.workspace,a.output); print(json.dumps(q.to_dict(),indent=2,sort_keys=True)); raise SystemExit(1 if a.require_autonomous and not q.autonomous_ready else 0)

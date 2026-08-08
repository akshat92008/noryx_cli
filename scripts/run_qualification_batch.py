#!/usr/bin/env python3
"""Run a bounded slice of the release test matrix in fresh processes."""
from __future__ import annotations
import argparse, os, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument('--start', type=int, required=True)
    p.add_argument('--end', type=int, required=True)
    p.add_argument('--output-dir', default='release_evidence')
    args=p.parse_args()
    files=sorted((ROOT/'tests').glob('test_*.py'))
    out=(ROOT/args.output_dir); out.mkdir(parents=True, exist_ok=True)
    if args.start < 0 or args.end > len(files) or args.start >= args.end:
        raise SystemExit(f'invalid range {args.start}:{args.end} for {len(files)} files')
    env=dict(os.environ); env['NEXUS_DISABLE_NETWORK']='1'
    for index in range(args.start,args.end):
        test_file=files[index]
        junit=out/f'junit-shard-{index+1:03d}.xml'
        file_env=dict(env); file_env['COVERAGE_FILE']=str(ROOT/f'.coverage.release-{index+1:03d}')
        command=[sys.executable,'-m','coverage','run','--branch','--source=nexus','-m','pytest','-q','--junitxml',str(junit),str(test_file.relative_to(ROOT))]
        started=time.monotonic()
        result=subprocess.run(command,cwd=ROOT,env=file_env,text=True,capture_output=True,timeout=180)
        elapsed=time.monotonic()-started
        if result.returncode:
            print(result.stdout); print(result.stderr,file=sys.stderr)
            raise SystemExit(f'FAIL {test_file} rc={result.returncode}')
        last=(result.stdout.strip().splitlines() or ['passed'])[-1]
        print(f'PASS {index+1:03d}/{len(files)} {test_file.name} ({elapsed:.2f}s) {last}',flush=True)
    return 0
if __name__=='__main__':
    raise SystemExit(main())

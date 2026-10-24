"""Repeat alternating isolated/concurrent workflow batches; compare job medians."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import shlex
from statistics import median
import subprocess
import time


def job_seconds(started_iso, completed_iso):
    if not started_iso or not completed_iso:
        return None
    return int((datetime.fromisoformat(completed_iso.replace('Z', '+00:00')) -
                datetime.fromisoformat(started_iso.replace('Z', '+00:00'))).total_seconds())


def parse_jobs(jobs_json):
    rows = []
    for job in jobs_json.get('jobs', []):
        seconds = job_seconds(job.get('startedAt'), job.get('completedAt'))
        if job.get('conclusion') != 'skipped' and seconds is not None:
            rows.append((job['name'], job.get('conclusion'), seconds))
    return rows


def table(single, parallel):
    rows = []
    for name in sorted(single.keys() | parallel.keys()):
        a, b = single.get(name, []), parallel.get(name, [])
        s, p = median(a) if a else None, median(b) if b else None
        ratio = p / s if s and p is not None else (1.0 if s == p == 0 else float('inf'))
        status = 'INSUFFICIENT' if min(len(a), len(b)) < 3 else ('PASS' if ratio <= 1.20 else 'FAIL')
        rows.append(dict(job=name, single=s, parallel=p, ratio=ratio,
                         n_single=len(a), n_parallel=len(b), status=status))
    ratios = [r['ratio'] for r in rows]
    return rows, dict(max_ratio=max(ratios, default=None), median_ratio=median(ratios) if ratios else None,
                      over_1_20=sum(r > 1.20 for r in ratios),
                      passed=bool(rows) and all(r['status'] == 'PASS' for r in rows))


def render(rows, summary):
    keys = ['job', 'single', 'parallel', 'ratio', 'n_single', 'n_parallel', 'status']
    cells = [keys] + [[str(r[k]) for k in keys] for r in rows]
    widths = [max(len(c[i]) for c in cells) for i in range(len(keys))]
    return '\n'.join('  '.join(v.ljust(w) for v, w in zip(c, widths)) for c in cells) + '\n' + str(summary) + '\n'


def render_md(rows, summary):
    keys = ['job', 'single', 'parallel', 'ratio', 'n_single', 'n_parallel', 'status']
    lines = [keys, ['---'] * len(keys)] + [[str(r[k]).replace('|', '\\|').replace('\n', ' ') for k in keys] for r in rows]
    return '\n'.join('| ' + ' | '.join(c) + ' |' for c in lines) + '\n\n' + str(summary) + '\n'


def dispatch_argv(repo, workflow, ref):
    return ['gh', 'workflow', 'run', workflow, '--repo', repo, '--ref', ref]


def list_argv(repo, workflow):
    return ['gh', 'run', 'list', '--repo', repo, '--workflow', workflow, '--limit', '100',
            '--json', 'databaseId,headBranch,event']


def view_argv(repo, run_id):
    return ['gh', 'run', 'view', str(run_id), '--repo', repo, '--json', 'jobs']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ['repo', 'workflow', 'ref', 'out']:
        parser.add_argument('--' + flag, required=True)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--parallel', type=int, default=4)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if min(args.runs, args.parallel) < 1:
        parser.error('runs and parallel must be positive')
    def call(argv):
        if args.dry_run:
            print(shlex.join(argv))
            return '[]'
        return subprocess.run(argv, check=True, capture_output=True, text=True).stdout
    listing = list_argv(args.repo, args.workflow)
    samples = {'single': {}, 'parallel': {}}
    if not args.dry_run:
        Path(args.out).mkdir(parents=True, exist_ok=True)
    raw = None if args.dry_run else (Path(args.out) / 'raw.tsv').open('w', newline='', encoding='utf-8')
    try:
        writer = csv.writer(raw, delimiter='\t') if raw else None
        if writer:
            writer.writerow(['phase', 'round', 'run_id', 'job', 'conclusion', 'seconds'])
        for round_id in range(1, args.runs + 1):
            for phase, count in [('single', 1), ('parallel', args.parallel)]:
                before = {r['databaseId'] for r in json.loads(call(listing))}
                for _ in range(count):
                    call(dispatch_argv(args.repo, args.workflow, args.ref))
                ids, deadline = [], time.monotonic() + 120
                while len(ids) < count and not args.dry_run:
                    ids = [r['databaseId'] for r in json.loads(call(listing)) if r['databaseId'] not in before
                           and r['headBranch'] == args.ref and r['event'] == 'workflow_dispatch']
                    if len(ids) > count or time.monotonic() > deadline:
                        raise RuntimeError('Ambiguous or missing dispatched runs; use a dedicated ref')
                    if len(ids) < count:
                        time.sleep(2)
                for run_id in ids or ['RUN_ID']:
                    print(f'{phase} round {round_id}: {run_id}')
                    call(['gh', 'run', 'watch', str(run_id), '--repo', args.repo])
                    payload = call(view_argv(args.repo, run_id))
                    for name, conclusion, seconds in parse_jobs(json.loads(payload)) if not args.dry_run else []:
                        writer.writerow([phase, round_id, run_id, name, conclusion, seconds])
                        raw.flush()
                        samples[phase].setdefault(name, []).append(seconds)
    finally:
        if raw:
            raw.close()
    if args.dry_run:
        return 0
    rows, summary = table(samples['single'], samples['parallel'])
    for filename, renderer in [('table.txt', render), ('table.md', render_md)]:
        (Path(args.out) / filename).write_text(renderer(rows, summary), encoding='utf-8')
    print(render(rows, summary))
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

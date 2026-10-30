"""Fresh-process, interleaved A/B benchmark against a frozen QSim package.

Run under a preflighted cgroup/scheduler budget. Never clears system or old caches.
All reports, child logs and results stay under the required new --directory.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',required=True,help='Directory containing frozen qsim package')
    parser.add_argument('--directory',required=True)
    parser.add_argument('--config',default='configs/readme_30m_5y.toml')
    parser.add_argument('--repeats',type=int,default=3)
    args=parser.parse_args()
    if args.repeats<3: parser.error('At least three interleaved repeats are required')
    root=Path(args.directory).resolve();root.mkdir(parents=True,exist_ok=False)
    project=Path(__file__).resolve().parents[1]
    baseline=Path(args.baseline).resolve()
    if not (baseline/'qsim').is_dir(): raise ValueError('Missing frozen qsim package')
    records=[];comparisons=[]
    cases={'30m':['--start','2023-01-03','--end','2023-01-31'],
           '1m':['--start','2023-01-03','--end','2023-01-04','--frequency','0930-1500-1','--warmup-days','1']}

    def execute(case,version,label,extra=()):
        target=root/label
        env=os.environ.copy();env['PYTHONPATH']=str(baseline if version=='baseline' else project)
        for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','ARROW_NUM_THREADS','ARROW_IO_THREADS'):
            env[key]='1'
        cmd=[sys.executable,str(project/'scripts/profile_backtest.py'),'--config',str(Path(args.config).resolve()),
             '--directory',str(target),*cases[case],*extra]
        with (root/(label+'.log')).open('x') as log:
            subprocess.run(cmd,cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        payload=json.loads((target/'profile.json').read_text())
        output=Path(payload['output_directory'])
        audit=json.loads((output/'audit.json').read_text())
        return dict(case=case,version=version,label=label,wall_seconds=payload['wall_seconds'],
                    cpu_seconds=payload['cpu_seconds'],stages=payload['stages'],
                    peak_rss_kib=payload['resources_after']['peak_rss_kib'],
                    io={k:payload['resources_after'].get(k) for k in ('rchar','wchar','read_bytes','write_bytes')},
                    parquet_files=len(list(output.glob('*.parquet/part-*.parquet'))),
                    parquet_bytes=audit['parquet_bytes'],output_directory=str(output))

    def check(a,b,label):
        target=root/(label+'.json')
        env=os.environ.copy();env['PYTHONPATH']=str(project)
        with (root/(label+'.log')).open('x') as log:
            subprocess.run([sys.executable,str(project/'scripts/compare_backtests.py'),
                            a['output_directory'],b['output_directory'],'--output',str(target)],
                           cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        report=json.loads(target.read_text());comparisons.append(dict(label=label,**report))

    for case in cases:
        # Application input caches for both versions are primed before timing;
        # this does not claim control over NAS/kernel cache state.
        for version in ('baseline','candidate'):
            execute(case,version,f'{case}_{version}_prime')
        for i in range(args.repeats):
            pair={}
            order=('baseline','candidate') if i%2==0 else ('candidate','baseline')
            for version in order:
                record=execute(case,version,f'{case}_{version}_{i}')
                records.append(record);pair[version]=record
                print(f"{case} {version} {i}: {record['wall_seconds']:.3f}s",flush=True)
            check(pair['baseline'],pair['candidate'],f'compare_{case}_{i}')
            (root/'progress.json').write_text(json.dumps(dict(records=records,comparisons=comparisons),indent=2))
    cold=[]
    for version in ('baseline','candidate'):
        cold.append(execute('30m',version,f'cold_{version}',extra=['--cache-directory',str(root/('new_cache_'+version))]))
    check(cold[0],cold[1],'compare_cold')
    summary={}
    for case in cases:
        values={v:[r['wall_seconds'] for r in records if r['case']==case and r['version']==v]
                for v in ('baseline','candidate')}
        med={k:statistics.median(v) for k,v in values.items()}
        summary[case]=dict(seconds=values,medians=med,speedup=med['baseline']/med['candidate'],
                           reduction=1-med['candidate']/med['baseline'])
    result=dict(status='PASS',summary=summary,records=records,cold_application_cache=cold,comparisons=comparisons,
                note='Cold means new application cache directories, not cleared NAS/kernel caches. cProfile disabled.')
    (root/'benchmark.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()

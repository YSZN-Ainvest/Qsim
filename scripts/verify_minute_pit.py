"""Bounded real-data minute future tests; run under external resource limits."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import resource
import sys
import time

import pyarrow as pa

# Also works as a direct script without an editable installation.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qsim.config import load_config
from qsim.livetest import run_livetest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='New NAS result directory')
    args=parser.parse_args()
    if not args.output.resolve().is_relative_to('/mnt/nas/qinml_cache'):
        parser.error('Output must be under /mnt/nas/qinml_cache')
    args.output.mkdir(parents=True,exist_ok=False)
    pa.set_cpu_count(1);pa.set_io_thread_count(1)
    base=load_config(Path(__file__).resolve().parents[1]/'configs/livetest_demo.toml')
    base['backtest'].update(start='2023-01-06',end='2023-01-09')
    base['data']['warmup_days']=1
    base['factors']=[{'name':'momentum3','kind':'momentum','lookback':3,'weight':1.}]
    cases=[];started=time.monotonic()
    for size in (1,5,30):
        for mode in ('StatsEvery','StatsInterval'):
            cfg=deepcopy(base)
            cfg['backtest'].update(frequency=f'0930-1500-{size}',mode=mode)
            cfg['stats']['nbar']=2 if mode=='StatsInterval' else 1
            # Final signal for a two-bar Interval is two bars before the close.
            last=900-size*cfg['stats']['nbar']
            last_clock=f'{last//60:02}{last%60:02}'
            if mode=='StatsEvery':
                cfg['stats']['signal_times']=['0930','1000','1300',last_clock]
            cfg['livetest']['times']=['2023-01-06 13:00',f'2023-01-06 {last_clock[:2]}:{last_clock[2:]}','2023-01-09 09:30']
            name=f'{size}m_{mode}'
            print(f'Minute PIT: {name}',flush=True)
            report=run_livetest(cfg,'f',report_path=args.output/name/'report.json')
            result=report['checks']['f']
            cases.append(dict(name=name,status=report['status'],valid_points=result['valid_points'],
                missing_records=result['missing_records'],inconsistent_points=result['inconsistent_points'],
                max_absolute_error=result['max_absolute_error'],targets=report.get('selected_times',[]),
                elapsed_seconds=report['elapsed_seconds'],report_path=report['report_path']))
    summary=dict(status='PASS' if all(c['status']=='PASS' for c in cases) else 'FAIL',cases=cases,
        elapsed_seconds=time.monotonic()-started,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        scope='6 securities; 2023-01-06 and 2023-01-09; one warmup day; price alpha only; no fitted/training preprocessing audit',
        limitations=['Bar data assumed available at bar_end; actual feed/computation latency not certified.',
            'PIT source availability timestamps must be truthful; daily universe/status snapshot timing is not certified.'])
    with (args.output/'summary.json').open('x') as stream:
        json.dump(summary,stream,ensure_ascii=False,indent=2)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    raise SystemExit(0 if summary['status']=='PASS' else 1)


if __name__=='__main__':
    main()

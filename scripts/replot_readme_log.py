"""Replot frozen README results; verify raw plotted values and input hashes."""
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qsim.report import plot_results


def main():
    repo=Path(__file__).resolve().parents[1]
    base=Path('/mnt/nas/qinml_cache/qsim')
    stamp=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
    output=base/'result'/f'log_plots_{stamp}'
    output.mkdir(parents=True,exist_ok=False)
    sources={
        'readme_daily_5y':'readme_daily_reversal5_candidate_20261030_135734_607571_82bfa4e9',
        'readme_30m_l1_4y':'readme_30m_l1_4y_20261030_160015_832678_7a6fb02b',
    }
    report=[]
    for prefix,run_id in sources.items():
        root=base/'result'/run_id
        assert (root/'COMPLETE').exists()
        files=['config.json','summary.json','daily.csv','diagnostics.csv','benchmark_daily.csv']
        def hashes():
            return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}
        before=hashes()
        result={'config':json.loads((root/'config.json').read_text()),
                'summary':pd.DataFrame(json.loads((root/'summary.json').read_text()))}
        for name in ['daily','diagnostics','benchmark_daily']:
            result[name]=pd.read_csv(root/(name+'.csv'))
        result['diagnostics']['timestamp']=pd.to_datetime(result['diagnostics']['timestamp'])
        groups=result['summary'].loc[lambda f:f.period.eq('ALL')&f.book.str.startswith('G')].sort_values('book')
        target=output/prefix
        target.mkdir()
        original=Figure.savefig
        checked=[]
        def save(fig,path,*args,**kwargs):
            name=Path(path).name
            ax=fig.axes[0]
            if name in ('group_nav.png','long_short_nav.png'):
                assert ax.get_yscale()=='log'
                names=groups.book if name=='group_nav.png' else ['LS']
                for line,book in zip(ax.lines,names):
                    expected=result['daily'].loc[lambda f:f.book.eq(book)].sort_values('date').net
                    np.testing.assert_array_equal(line.get_ydata(),expected)
                checked.append(name)
            elif name in ('group_returns.png','group_excess_returns.png'):
                assert ax.get_yscale()=='symlog'
                expected=groups['return'] if name=='group_returns.png' else groups.net/result['benchmark_daily'].sort_values('date').net.iloc[-1]-1
                np.testing.assert_array_equal([bar.get_height() for bar in ax.patches],expected)
                assert [tick.get_text() for tick in ax.get_xticklabels()]==groups.book.tolist()
                checked.append(name)
            return original(fig,path,*args,**kwargs)
        Figure.savefig=save
        try:
            plot_results(result,target)
        finally:
            Figure.savefig=original
        assert before==hashes()
        assert len(checked)==4
        for name in checked:
            destination=repo/'document/assets'/f'{prefix}_{name}'
            if destination.exists() and destination.stat().st_uid!=os.getuid():
                raise PermissionError(f'Unexpected asset owner: {destination}')
            shutil.copy2(target/name,destination)
        report.append(dict(source=str(root),plots=str(target),inputs_sha256=before,checked=checked,status='PASS'))
    payload=dict(results=report,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    (output/'verification.json').write_text(json.dumps(payload,indent=2))
    print(json.dumps(dict(output=str(output),peak_rss_kib=payload['peak_rss_kib'],status='PASS')))


if __name__=='__main__':
    main()

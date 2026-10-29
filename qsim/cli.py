import argparse
import json
from .config import load_config
from .runner import run
from .terminal import print_summary


def main():
    parser=argparse.ArgumentParser(description='QSim multi-factor VWAP research backtester')
    parser.add_argument('--config',required=True,nargs='+',help='One or more TOML configs')
    parser.add_argument('--validate',action='store_true',help='Validate config; no market reads')
    parser.add_argument('--pools',type=int,help='Maximum concurrent independent configuration jobs')
    parser.add_argument('--factor-pools',type=int,help='Maximum shared-memory factor threads per job')
    parser.add_argument('--no-dump',action='store_true',help='Return/print results without writing files')
    parser.add_argument('--compare-factors',action='store_true',help='Run 2–5 factors separately and together; check signals, trades, IC and accounting')
    parser.add_argument('--livetest',help='Alpha consistency checks: f, p, c, f,p or all; runs checks only')
    parser.add_argument('--livetest-times',help='Comma-separated Shanghai decision timestamps (f/c)')
    parser.add_argument('--livetest-warmup-days',type=int,help='Declared warmup trading days for each replay')
    parser.add_argument('--livetest-atol',type=float)
    parser.add_argument('--livetest-rtol',type=float)
    parser.add_argument('--livetest-report',help='New JSON report path; checkpoints are stored alongside it')
    args=parser.parse_args()
    configs=[load_config(path) for path in args.config]
    for count in (args.pools,args.factor_pools):
        if count is not None and count<1: parser.error('pools must be positive')
    if args.factor_pools:
        for cfg in configs: cfg['compute']['pools']=args.factor_pools
    if len(configs)>1:
        if args.livetest or args.compare_factors: parser.error('Use one config with --livetest or --compare-factors')
        if args.validate:
            print(json.dumps(configs,ensure_ascii=False,indent=2));return
        if args.no_dump: parser.error('Multiple-config jobs require dump=true for bounded outputs')
        from .batch import run_batch
        report=run_batch(configs,args.pools)
        raise SystemExit(report['exit_code'])
    config=configs[0]
    livetest_overrides = {key: value for key,value in dict(
        times=args.livetest_times.split(',') if args.livetest_times else None,
        warmup_days=args.livetest_warmup_days,atol=args.livetest_atol,rtol=args.livetest_rtol).items() if value is not None}
    if not args.livetest and (livetest_overrides or args.livetest_report):
        parser.error('livetest options require --livetest')
    if args.livetest:
        if args.compare_factors or args.validate:
            parser.error('--livetest cannot be combined with --compare-factors or --validate')
        from .livetest import run_livetest
        from .livetest_config import options,parse_checks
        try:
            checks=parse_checks(args.livetest)
            config['livetest']=options({**config.get('livetest',{}),**livetest_overrides})
        except ValueError as error:
            parser.error(str(error))
        report=run_livetest(config,checks,report_path=args.livetest_report)
        raise SystemExit(report['exit_code'])
    if args.no_dump:
        config['output']['dump']=False
    if args.compare_factors:
        from .experiments import comparison_cases,compare_factors
        comparison_cases(config)
    if args.validate:
        print(json.dumps(config,ensure_ascii=False,indent=2));return
    if args.compare_factors:
        result=compare_factors(config)
        print(result['comparison'].drop(columns='output_directory').to_string(index=False))
        print('Output:',result['output_directory'] or 'dump disabled')
        return
    result=run(config)
    print('Mode:',config['backtest']['mode'],'; summary basis:',result['audit'].get('summary_basis','single_cohort_portfolio'))
    print_summary(result['summary'])
    print('Output:',result['output_directory'] or 'dump disabled')


if __name__=='__main__':
    main()

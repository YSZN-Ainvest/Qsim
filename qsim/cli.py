import argparse
import json
from .config import load_config
from .runner import run


def main():
    parser=argparse.ArgumentParser(description='QSim multi-factor VWAP research backtester')
    parser.add_argument('--config',required=True,help='TOML config')
    parser.add_argument('--validate',action='store_true',help='Validate config including OS guard; no market reads')
    parser.add_argument('--no-dump',action='store_true',help='Return/print results without writing files')
    args=parser.parse_args()
    config=load_config(args.config)
    if args.no_dump:
        config['output']['dump']=False
    if args.validate:
        print(json.dumps(config,ensure_ascii=False,indent=2));return
    result=run(config)
    print('Mode:',config['backtest']['mode'],'; summary basis:',result['audit'].get('summary_basis','single_cohort_portfolio'))
    cols=['period','from','to','return','tvr','sharp','dd','IC','rank_IC','nHold']
    print(result['summary'].loc[result['summary'].book.eq('LS'),cols].to_string(index=False))
    print('Output:',result['output_directory'] or 'dump disabled')


if __name__=='__main__':
    main()

"""Monitor only this task's explicitly named systemd units; stop on pressure."""
import argparse
import json
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--unit', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--memory-stop-gib', type=float, default=11.)
    args = parser.parse_args()
    if not args.unit.startswith('qsim-readme-'):
        raise ValueError('Only README task units are permitted')
    with Path(args.output).open('x') as stream:
        while True:
            state = subprocess.check_output(['systemctl', '--user', 'show', args.unit,
                '-p', 'ActiveState', '-p', 'MainPID', '-p', 'MemoryCurrent', '-p', 'MemoryPeak',
                '-p', 'CPUUsageNSec', '-p', 'MemorySwapCurrent', '-p', 'ControlGroup'], text=True)
            record = dict(line.split('=', 1) for line in state.splitlines() if '=' in line)
            info = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
            available = int(info['MemAvailable'].split()[0])*1024
            record.update(timestamp=time.time(), system_available=available)
            memory = int(record['MemoryCurrent']) if record['MemoryCurrent'].isdigit() else 0
            stop = available < 8*1024**3 or memory > args.memory_stop_gib*1024**3
            record['stop_for_pressure'] = stop
            stream.write(json.dumps(record)+'\n')
            stream.flush()
            if stop:
                subprocess.run(['systemctl', '--user', 'stop', args.unit], check=True)
                raise SystemExit('Stopped own task for resource pressure; daily output is resumable')
            if record['ActiveState'] not in ('active', 'activating', 'deactivating'):
                break
            time.sleep(5)


if __name__ == '__main__':
    main()

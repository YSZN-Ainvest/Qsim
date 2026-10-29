"""Conservative worker budgets from observed idle CPU, memory and cgroups."""
import math
import os
from pathlib import Path
import time


def snapshot():
    result={'affinity':len(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else (os.cpu_count() or 1),
            'cgroup_visible':False,'memory_available':0,'cpu_quota':None}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'): result['memory_available']=int(line.split()[1])*1024
        def cpu():
            values=[int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
            return sum(values[:8]),values[3]+values[4]
        a=cpu();time.sleep(.05);b=cpu()
        result['idle_cores']=result['affinity']*max(0.,b[1]-a[1])/max(1,b[0]-a[0])
        group=next(line.split(':',2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
        path=Path('/sys/fs/cgroup')/group.lstrip('/')
        while path.is_relative_to('/sys/fs/cgroup'):
            if (path/'cpu.max').exists():
                result['cgroup_visible']=True
                quota,period=(path/'cpu.max').read_text().split()
                if quota!='max': result['cpu_quota']=min(result['cpu_quota'] or math.inf,int(quota)/int(period))
            if (path/'memory.max').exists():
                maximum=(path/'memory.max').read_text().strip()
                if maximum!='max':
                    remaining=max(0,int(maximum)-int((path/'memory.current').read_text()))
                    result['memory_available']=min(result['memory_available'],remaining)
            if path==Path('/sys/fs/cgroup'): break
            path=path.parent
    except (OSError,ValueError,StopIteration):
        result.setdefault('idle_cores',1.)
    result['cpu_budget']=max(0.,min(result['idle_cores'],result['cpu_quota'] or math.inf)*.5)
    result['memory_budget']=int(result['memory_available']*.5)
    return result


def allowed_workers(requested,memory_per_job_mb=0):
    state=snapshot()
    if not state['cgroup_visible']:
        return 1,state
    allowed=max(1,int(state['cpu_budget']))
    if memory_per_job_mb:
        memory_limit=state['memory_budget']//(memory_per_job_mb*1024**2)
        if memory_limit<1: raise RuntimeError('Insufficient memory budget even for one job; reduce memory_per_job_mb only after measurement')
        allowed=min(allowed,memory_limit)
    return min(requested,allowed),state

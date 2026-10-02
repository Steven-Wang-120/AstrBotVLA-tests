"""Verified Host import owners; fixtures never become part of this baseline."""
from __future__ import annotations

import threading


def framework_baseline():
    before = set(threading.enumerate())
    # Warm up real public Host API, not plugin/test modules. SharedPreferences has
    # no close/shutdown method in Host 4.26.7; both services are process lifetime.
    from astrbot.api.star import Context
    from astrbot.core import sp
    from astrbot.core.config.default import VERSION

    owners = {}
    for thread in threading.enumerate():
        target = getattr(thread, '_target', None)
        bound = getattr(target, '__self__', None)
        method = getattr(target, '__name__', None)
        if bound is sp._sync_loop and method == 'run_forever':
            owners[thread] = 'astrbot.core.sp._sync_loop.run_forever'
        elif bound is sp._scheduler and method == '_main_loop':
            owners[thread] = 'astrbot.core.sp._scheduler._main_loop'
    if len(owners) != 2 or not sp._sync_loop.is_running() or not sp._scheduler.running:
        raise RuntimeError('Host SharedPreferences framework ownership not verified')
    if not any(job.id == 'clear_sp_temp_cache' and getattr(job.func, '__self__', None) is sp
               for job in sp._scheduler.get_jobs()):
        raise RuntimeError('Host scheduler ownership not verified')
    # Do not accept an unrelated thread created by warmup.
    if set(threading.enumerate()) - before - set(owners):
        raise RuntimeError('unowned Host warmup thread')
    baseline = before | set(owners)
    return baseline, {'host_version': VERSION, 'scope': 'verified process-lifetime Host services only; not absolute zero threads',
                      'threads': [{'name': t.name, 'daemon': t.daemon, 'owner': owner}
                                  for t, owner in owners.items()]}


def thread_leaks(baseline):
    return [{'name': t.name, 'daemon': t.daemon}
            for t in threading.enumerate() if t not in baseline and t.is_alive()]

"""问答单次执行摘要：只记录编号、决策和耗时，不复制问题、密钥或回答。"""
from time import perf_counter
from uuid import uuid4


class ExecutionTrace:
    def __init__(self):
        self.trace_id = str(uuid4())
        self.started = perf_counter()
        self.active = None
        self.stages = []
        self.context = {}

    def begin(self, name: str):
        self.active = (name, perf_counter())

    def end(self, status: str = 'completed'):
        if self.active:
            name, started = self.active
            self.stages.append(dict(name=name, status=status, duration_ms=round(max(0, perf_counter()-started)*1000, 2)))
            self.active = None

    def skip(self, name: str):
        self.stages.append(dict(name=name, status='skipped', duration_ms=0))

    def snapshot(self, status: str):
        return dict(trace_id=self.trace_id, status=status, total_ms=round(max(0, perf_counter()-self.started)*1000, 2),
                    stages=list(self.stages), **self.context)

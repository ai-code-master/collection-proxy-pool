"""代理检测任务在流水线各阶段间的状态。"""
from dataclasses import dataclass


@dataclass
class ProbeJob:
    token: int
    item: tuple
    started_at: float

    @property
    def row(self):
        return self.item[0]

    @property
    def platform(self):
        return self.item[1]

    @property
    def target(self):
        return self.item[2]

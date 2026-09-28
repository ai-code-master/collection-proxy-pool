"""代理检测任务在流水线各阶段间的状态。"""
from dataclasses import dataclass, field


@dataclass
class ProbeJob:
    token: int
    item: tuple
    started_at: float
    capabilities: dict = field(default_factory=dict)

    @property
    def row(self):
        return self.item[0]

    @property
    def platform(self):
        return self.item[1]

    @property
    def target(self):
        return self.item[2]

    def add(self, region, result):
        self.capabilities[region] = result

    def regions_complete(self, regions):
        return all(region in self.capabilities for region in regions)

    def has_success(self):
        return any(value['state'] == 'available' for value in self.capabilities.values())

---
name: generic-proxy-pool
description: 运维 mini 上的通用采集代理池，负责收集、国内外连通性验证、轮询领取和导出。
---

# 通用代理池

- 只验证免认证 HTTP/SOCKS5 代理是否能访问国内或国外连通性端点。
- 不检测任何业务平台内容，不标记业务风控，不根据下游反馈改变节点状态。
- 默认可用期 1 小时；新节点 10 分钟复测，稳定节点 30 分钟复测。
- `/next?reach=any|domestic|overseas|both` 按需轮询。
- 部署前运行 `/opt/homebrew/bin/python3.11 -m unittest discover -s tests -v`。
- 主服务命令：`/opt/homebrew/bin/python3.11 scripts/pool.py serve`。

端点、导出和排障详见 [references/operations.md](references/operations.md)。

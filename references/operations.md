# 运维说明

## 职责边界

代理池保存候选节点、连通性结果和复测时间。业务反馈、账号风控、并发租约、浏览节奏均不属于本服务。

## 数据状态

- `available`：至少一个区域通过。
- `unreachable`：国内、国外端点均失败。
- `auth_required`：代理需要认证，不进入公共池。
- `expired` / `unverified` / `disabled`：看板计算出的有效性状态。

`connectivity_capabilities` 分别记录 `domestic` 和 `overseas`。备用端点只在主端点失败时请求，避免对每个 IP 做过多探测。

## 运行与验证

```sh
/opt/homebrew/bin/python3.11 scripts/pool.py status
/opt/homebrew/bin/python3.11 scripts/pool.py check
/opt/homebrew/bin/python3.11 scripts/pool.py export
curl --noproxy '*' http://127.0.0.1:21992/status.json
curl --noproxy '*' 'http://127.0.0.1:21992/next?reach=both'
```

空池时 `/next` 返回 503。`/next` 是轮询领取，不是全局独占租约。

## Mihomo 节点刷新器

仓库中的唯一源码位于 `scripts/mihomo/`，`scripts/mihomo_refresh.py` 只是定时任务入口：

- `settings.py`：加载本地 `mihomo.json`，源码不内置来源或探测目标。
- `source_io/download.py`：4 路有界并发下载、2 路失败重试、gzip 与分级总超时。
- `sources.py`：动态来源编排，并按配置顺序容错解析、字段校验与去重。
- `uris.py`：解析 Base64 订阅及 VMess、VLESS、SS、Trojan、Hysteria2、TUIC 分享 URI。
- `runtime.py`：生成配置、调用 Mihomo 校验与热重载。
- `probe.py`：国内外连通性及出口 IP 检测。
- `quality.py`：记录来源的跨轮次解析、新增、实活、独立出口 IP 和连续错误。
- `refresh.py`：候选队列、轮测、瘦身和状态落盘。

部署时通过 `PROXY_POOL_MIHOMO_CONFIG` 指向私有 `mihomo.json`，通过 `PROXY_POOL_MIHOMO_DATA` 指定状态目录。手动刷新必须使用包含 PyYAML 的项目解释器：

```sh
python3 scripts/mihomo_refresh.py
```

不要改用缺少 PyYAML 的 Python。上线前先在临时目录执行 `py_compile` 和 `mihomo -t`，再原子替换。每轮来源质量历史保存在 Mihomo 状态目录的 `source_quality.json`。

候选订阅先用隔离端口实测，该命令不修改正式状态：

```sh
python3 scripts/research/mihomo_candidates.py \
  --source name=&lt;候选订阅 URL&gt;
```

大型来源使用 `--sample-per-source 200 --sample-offset 200` 分段复测，避免只看排序最前的节点。只有用户主动运行发现后得到的候选，才会进入本地 `mihomo.json`。仓库不保存发现结果。

## 部署检查

1. 运行全量单元测试。
2. 备份 `data/pool.sqlite3` 及 WAL/SHM。
3. 替换代码后重启单一服务进程。
4. 检查 `/status.json`、`/next`、`reach=domestic` 和 `reach=overseas`。
5. 确认日志无持续异常，可用数量开始回升。

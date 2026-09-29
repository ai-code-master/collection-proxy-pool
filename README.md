# 通用采集代理池

> [!TIP]
> ⭐ **觉得项目有帮助？欢迎在 [GitHub](https://github.com/ai-code-master/collection-proxy-pool) 点一个 Star，帮助更多人发现它。下载和使用始终免费，Star 不是前置条件。**
>
> ⭐ **If this project helps you, please [star it on GitHub](https://github.com/ai-code-master/collection-proxy-pool) so more people can discover it. Downloading and using it is always free—starring is appreciated, never required.**

这是一个可自部署的代理来源发现、采集、连通性检测、历史复测和订阅服务。仓库只发布软件，不包含代理节点、代理来源、连通性检测目标、运行数据库或日志。发现结果和用户配置只保存在本地，不属于发布包。

这是一个只负责“代理是否能通”的独立代理池。它不判断任何业务平台的限流或风控，也不接收下游业务反馈来改变代理状态。

> [!WARNING]
> 公共代理是不可信网络。本项目仅限合法、授权的研究、测试和运维用途；不要传输敏感数据，不要将端口暴露到公网。使用前请阅读 [完整免责声明](DISCLAIMER.md)。

## 检测规则

每个代理先经过 TCP/协议握手预筛，再按配置顺序最多尝试 3 个小响应 HTTPS 目标。任意目标成功即判定代理可用并停止后续检测；全部失败才判定不可用。仓库不为任何第三方目标背书，也不内置 IP 回显服务。

`reach=domestic` 或 `overseas` 仅筛选最近实际命中过对应区域目标的记录；默认使用 `reach=any`。ICMP ping 和单纯端口开放不会被当作可用，因为它们不能证明代理完成了 HTTPS 转发。非预期 HTTP 响应只算当前探测目标失败，不会被解读为任何业务风控结论。

## 快速使用

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

cp examples/config.json config.json
cp examples/sources.json sources.json

# 搜索来源候选，不会改动配置
.venv/bin/python scripts/pool.py discover --limit 20

# 审核输出后再合并到本地 sources.json
.venv/bin/python scripts/pool.py discover --limit 20 --apply
```

然后在本地 `config.json` 中填入你信任或自建的 HTTPS 检测目标，将 `profiles.connectivity.enabled` 改为 `true`。未配置时服务可启动控制台，但不会向外发起代理检测。

```sh
.venv/bin/python scripts/pool.py serve
.venv/bin/python scripts/pool.py status
.venv/bin/python scripts/pool.py next --reach any
```

```sh
MINI_HOST=mini.local  # 也可填部署机的私网 IP 或内网名称
curl --noproxy '*' "http://${MINI_HOST}:21992/api/v1/proxies/random"
curl --noproxy '*' "http://${MINI_HOST}:21992/api/v1/proxies?limit=100"
```

v1 API 根据客户端访问 API 时使用的地址自动生成代理地址，不依赖写死 IP。Mihomo 节点每个使用一个独立端口，从 `mihomo.json` 的 `base_port` 开始，数量跟随当前节点数量；不提供统一轮换入口：

```sh
curl --noproxy '*' "http://${MINI_HOST}:21992/api/v1/proxies/random?reach=any"
```

原有 `/next` 和各种订阅地址继续保留，现有通用调用方无需修改。

## 内部调度

启动 `serve` 后，来源采集、新来源发现、候选来源审核、Mihomo 刷新和导出快照由服务内部调度，无需再配置 cron 或系统定时任务。默认每小时搜索并审核一次新来源；候选会按内容自动分流：HTTP/SOCKS4/SOCKS5 列表写入私有 `sources.json`，VMess、VLESS、Trojan、SS、Hysteria 和 TUIC 等订阅写入私有 `mihomo.json`，由 Mihomo 转换为本地代理端口后再进入通用池。

打开 Web 控制台的“调度设置”可修改发现、自动审核、采集、历史恢复、节点复测和导出间隔，并查看每个候选的审核指标与接入结果。配置会原子写入本地 `config.json`，无需重启；任务的上次结果、错误和下次运行时间保存在 SQLite 中。

需要让 Hermes 等外部调度器执行补充搜索时，调用 `scripts/external_discovery.py`。它可读取同目录的 `proxy_source_queries.json`，但只能写入统一候选库；正式接入仍由自动审核器决定，避免多个调度器同时覆盖私有配置。

常用地址：

- Web 控制台：`http://127.0.0.1:21992/`
- 标准随机领取：`/api/v1/proxies/random`
- 标准分页列表：`/api/v1/proxies`
- 标准状态：`/api/v1/stats`
- 调度状态与设置：`/api/schedule`
- 兼容领取：`/next`
- 文本清单：`/connectivity/proxies.txt`
- URI 清单：`/connectivity/proxies.uri`
- Base64 订阅：`/connectivity/proxies.base64`
- CSV：`/connectivity/proxies.csv`
- Clash 配置：`/connectivity/clash.yaml`
- 状态：`/status.json`

`POST /api/feedback` 已停用并返回 HTTP 410。下游如何使用、租约、限速或模拟真人，由下游自己负责。

更多运维说明见 [references/operations.md](references/operations.md)。

## 产品化部署

首次部署可复制 `examples/config.json`、`examples/sources.json` 和 `examples/mihomo.json` 为本地运行配置。示例文件故意不包含任何外部地址。Docker 部署：

```sh
docker compose -f deploy/docker/compose.yml up -d --build
curl --noproxy '*' http://127.0.0.1:21992/status.json
```

运行时数据通过 `PROXY_POOL_DATA` 指定，通用配置通过 `PROXY_POOL_CONFIG` 指定。参考 `examples/env.example`。请不要把 `data/`、代理列表、出口 IP 或日志提交到公开仓库。

本项目定位为可信局域网或 Tailscale 内自用，不提供登录鉴权。服务按客户端地址执行每分钟限速，默认 120 次，可通过 `api_rate_limit_per_minute` 调整。网络边界说明见 [references/security.md](references/security.md)。

Linux 服务模板位于 `deploy/systemd/`。部署时使用专用低权限用户和独立数据目录，不要把 21992 或 Mihomo 端口映射到公网。

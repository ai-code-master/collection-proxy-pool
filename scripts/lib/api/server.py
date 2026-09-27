import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs

from .. import settings
from ..worker import maintain
from .exports import EXPORT_FORMATS, available, render
from . import dashboard, v1
from .proxy_scope import scoped_proxy, scoped_rows
from .runtime import BoundedThreadingHTTPServer
from .runtime.cache import caches
from .traffic import Limiter
from .. import __version__
ALIASES = {f'/{name}': kind for name, kind in EXPORT_FORMATS.items()}
ALIASES['/pool.json'] = 'json'

# 高频入口短缓存：雷达轮询 /next 与看板刷新都会触发全表聚合，
# 未缓存时每次 0.7-4s，高峰期互相堆叠把响应拖到 10s+。
ROWS_CACHE_SECONDS = 10
OVERVIEW_CACHE_SECONDS = 30
LISTING_CACHE_SECONDS = 15
def reset_caches():
    # 测试与调试用：写入后需要立即看到新数据时清空全部短缓存。
    caches.clear()
def rows_for(store, config, profile, reach='any'):
    return caches.rows.get((str(store.path), profile, reach), ROWS_CACHE_SECONDS,
                           lambda: available(store, config, profile, reach))
def marks(store, config):
    # 全量标记渲染约 20MB，按数据路径缓存短时间，避免高频 GET 重复序列化。
    return caches.marks.get(str(store.path), 30,
                            lambda: json.dumps(store.records(config), ensure_ascii=False))
def response(store, config, path, index=0, cursor=None, advertised_host=None):
    parsed = urlsplit(path)
    query = parse_qs(parsed.query)
    lan_host = advertised_host or v1.advertised_host(config or {})
    if parsed.path == '/healthz':
        return 200, {'status': 'ok', 'version': __version__}, 'json'
    if parsed.path in dashboard.ASSETS:
        body, content_type = dashboard.static(parsed.path)
        return 200, body, content_type
    v1_result = v1.response(store, config, path, rows_for, cursor, lan_host)
    if v1_result is not None:
        return v1_result
    if parsed.path.startswith('/api/'):
        try:
            if parsed.path == '/api/overview':
                return 200, caches.overview.get(str(store.path), OVERVIEW_CACHE_SECONDS,
                                                lambda: dashboard.overview(store, config)), 'json'
            if parsed.path == '/api/proxies':
                return 200, caches.listing.get((str(store.path), parsed.query), LISTING_CACHE_SECONDS,
                                               lambda: dashboard.listing(store, config, query)), 'json'
            if parsed.path == '/api/history':
                return 200, dashboard.detail(store, config, query), 'json'
        except ValueError as error:
            return 400, {'error': str(error)}, 'json'
        return 404, {'error': 'not_found'}, 'json'
    profile = query.get('profile', query.get('platform', ['connectivity']))[0]
    if profile not in config['profiles']:
        return 400, {'error': 'unsupported_profile'}, 'json'
    reach = query.get('reach', ['any'])[0]
    if reach not in ('any', 'both', 'domestic', 'overseas'):
        return 400, {'error': 'unsupported_reach'}, 'json'
    if parsed.path in ('/status', '/status.json'):
        return 200, store.status(config), 'json'
    if parsed.path == '/marks.json':
        return 200, marks(store, config), 'application/json; charset=utf-8'
    rows = rows_for(store, config, profile, reach)
    if parsed.path in ('/next', '/next.json'):
        if not rows:
            return 503, {'error': 'no_fresh_reachable_proxy', 'profile': profile, 'reach': reach}, 'json'
        if cursor is not None:
            index = next(cursor)
        row = rows[index % len(rows)]
        proxy = scoped_proxy(row['url'], query.get('scope', ['local'])[0], lan_host)
        if proxy is None:
            return 400, {'error': 'invalid_scope'}, 'json'
        return 200, {'purpose': 'proxy_pool', 'profile': profile, 'reach': reach,
                     'proxy': proxy,
                     'domestic': bool(row.get('domestic')), 'overseas': bool(row.get('overseas')),
                     'domestic_latency_ms': row.get('domestic_latency_ms'),
                     'overseas_latency_ms': row.get('overseas_latency_ms'),
                     'exit_ip': row.get('exit_ip', ''), 'country': row['country'],
                     'exit_checked_at': row.get('geo_checked', 0),
                     'exit_info': row.get('exit_info', {}), 'checked_at': row['checked_at'],
                     'valid_until': row['valid_until']}, 'json'
    parts = parsed.path.strip('/').split('/')
    if len(parts) == 2 and parts[0] in config['profiles']:
        profile = parts[0]
        rows = rows_for(store, config, profile, reach)
        kind = EXPORT_FORMATS.get(parts[1])
    else:
        kind = ALIASES.get(parsed.path)
    if kind is None:
        return 404, {'error': 'not_found'}, 'json'
    rows = scoped_rows(rows, query.get('scope', ['local'])[0], lan_host)
    if rows is None:
        return 400, {'error': 'invalid_scope'}, 'json'
    body, content_type = render(rows, config['profiles'][profile], kind, profile)
    return 200, body, content_type
def make_handler(store):
    cursor = itertools.count()
    limiter = Limiter()

    class Handler(BaseHTTPRequestHandler):
        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            if not limiter.allow(self, settings.load()):
                return
            if self.path == '/api/power':
                # 看板总开关：暂停到 2100 年即停止一切收集与校验，0 即恢复；
                # 网页服务本身保持运行，随时可以再开回来。
                length = int(self.headers.get('Content-Length', '0') or 0)
                try:
                    data = json.loads(self.rfile.read(length)) if length else {}
                except ValueError:
                    data = {}
                on = data.get('on') is True
                store.put_meta('pause:connectivity', 0 if on else 4102444800)
                status, value = 200, {'on': on}
            elif self.path == '/api/feedback':
                status, value = 410, {'error': 'business_feedback_not_supported'}
            else:
                self.send_error(404)
                return
            content = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            config = settings.load()
            if not limiter.allow(self, config):
                return
            try:
                host = v1.advertised_host(config, self.headers.get('Host', ''))
                status, body, kind = response(store, config, self.path, cursor=cursor,
                                              advertised_host=host)
                if kind == 'json':
                    body, kind = json.dumps(body, ensure_ascii=False), 'application/json; charset=utf-8'
                content = body.encode()
            except (OSError, ValueError, KeyError):
                self.send_error(503)
                return
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    return Handler
def warm(store, stopped):
    # 后台预热：/next、看板总览、默认列表的聚合在请求到来前算好，
    # 首屏「正在读取调度状态」不再等数秒的现算全表扫描。
    default_listing = 'state=available&grade=&retry=&country=&protocol=&project=connectivity&search=&sort=speed&direction=asc&page=1'
    while not stopped.is_set():
        try:
            config = settings.load()
            rows_for(store, config, 'connectivity')
            caches.overview.get(str(store.path), OVERVIEW_CACHE_SECONDS,
                                lambda: dashboard.overview(store, config))
            caches.listing.get((str(store.path), default_listing), LISTING_CACHE_SECONDS,
                               lambda: dashboard.listing(store, config, parse_qs(default_listing)))
        except Exception:
            pass
        stopped.wait(5)

def serve(store, stopped):
    config = settings.load()
    server = BoundedThreadingHTTPServer((config['host'], config['port']), make_handler(store))
    server.timeout = 1
    worker = threading.Thread(target=maintain, args=(store, stopped), daemon=True)
    worker.start()
    threading.Thread(target=warm, args=(store, stopped), daemon=True).start()
    print(f'采集代理池：http://127.0.0.1:{config["port"]}/status.json', flush=True)
    try:
        while not stopped.is_set():
            server.handle_request()
    finally:
        stopped.set()
        server.server_close()
        worker.join(timeout=25)

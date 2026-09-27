import json

from ... import settings
from ...tasks import state as task_state


def body(handler):
    length = int(handler.headers.get('Content-Length', '0') or 0)
    if not 0 <= length <= 16_384:
        raise ValueError('请求体过大')
    return json.loads(handler.rfile.read(length)) if length else {}


def reply(handler, status, value):
    content = json.dumps(value, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(content)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.end_headers()
    handler.wfile.write(content)


def post(handler, store, limiter):
    if not limiter.allow(handler, settings.load()):
        return
    try:
        data = body(handler)
        if handler.path == '/api/power':
            on = data.get('on') is True
            store.put_meta('pause:connectivity', 0 if on else 4102444800)
            value = {'on': on}
        elif handler.path == '/api/schedule':
            settings.update_schedule(data)
            config = settings.load()
            task_state.sync(store, config)
            value = task_state.snapshot(store, config)
        elif handler.path == '/api/feedback':
            reply(handler, 410, {'error': 'business_feedback_not_supported'})
            return
        else:
            handler.send_error(404)
            return
        reply(handler, 200, value)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        reply(handler, 400, {'error': str(error)})

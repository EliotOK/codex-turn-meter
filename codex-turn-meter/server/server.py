"""Local stdio MCP server for the turn meter. Python 3.10+, standard library."""
import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collector import Store

ROOT = Path(__file__).resolve().parents[1]
URI = 'ui://codex-turn-meter/panel.html'
MIME = 'text/html;profile=mcp-app'
ID = {'type': 'string', 'minLength': 1, 'maxLength': 128, 'pattern': '^[A-Za-z0-9_-]+$'}


def tool(name, description, props, required=(), app=False, panel=False):
    out = {'name': name, 'description': description,
           'inputSchema': {'type': 'object', 'properties': props, 'required': list(required), 'additionalProperties': False},
           'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}}
    if app:
        out['_meta'] = {'ui': {'visibility': ['app']}}
    if panel:
        out['_meta'] = {'ui': {'resourceUri': URI}, 'openai/ui': {'entrypoints': [{'type': 'thread'}, {'type': 'global'}], 'preferredModelDisplayMode': 'inline'}}
    return out


TOOLS = [
    tool('show_meter', 'Open the compact Codex turn usage panel. Supply the exact current task ID if known; otherwise the user selects it. Open once; the UI watches subsequent turns itself.', {'thread_id': ID}, panel=True),
    tool('read_meter', 'Read numeric local usage for one exact task. Missing statistics are unknown; context is estimated from the last input.', {'thread_id': ID}, ['thread_id']),
    tool('list_meter_tasks', 'List local conversation names and project labels, with IDs for internal binding.', {}, app=True),
    tool('wait_for_turn', 'UI-only bounded wait for a start/completion boundary; unchanged timeout carries no usage. Does not call a model.',
         {'thread_id': ID, 'after': {'type': 'string', 'maxLength': 64}}, ['thread_id', 'after'], app=True),
]


class Server:
    def __init__(self, home):
        self.store = Store(home)
        self.stop = threading.Event()

    def call(self, name, args):
        definition = next((t for t in TOOLS if t['name'] == name), None)
        if not definition or not isinstance(args, dict):
            raise ValueError('工具参数无效')
        spec = definition['inputSchema']
        if set(args) - set(spec['properties']) or set(spec['required']) - set(args):
            raise ValueError('工具参数无效')
        if 'after' in args and (not isinstance(args['after'], str) or len(args['after']) > 64):
            raise ValueError('更新游标无效')
        if name == 'list_meter_tasks':
            data = self.store.sessions()
        elif name == 'wait_for_turn':
            data = self.store.wait(args['thread_id'], args['after'], self.stop)
        elif args.get('thread_id'):
            data = {'snapshot': self.store.snapshot(args['thread_id'])}
        else:
            data = {'selection_required': True}
        out = {'structuredContent': data, 'content': [{'type': 'text', 'text':
            '用量面板已准备；自动更新由面板处理。' if name == 'show_meter' else '本地统计已读取。'}]}
        if name == 'show_meter':
            out['_meta'] = {'ui': {'resourceUri': URI}}
        return out

    def handle(self, msg):
        ident = msg.get('id') if isinstance(msg, dict) else None
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or not isinstance(msg.get('method'), str):
            return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32600, 'message': 'Invalid request'}}
        if 'id' not in msg:
            return None
        method, params = msg['method'], msg.get('params', {})
        try:
            if not isinstance(params, dict):
                raise ValueError('参数无效')
            if method == 'initialize':
                version = params.get('protocolVersion')
                result = {'protocolVersion': version if version in ('2024-11-05','2025-03-26','2025-06-18','2025-11-25') else '2025-06-18',
                          'capabilities': {'tools': {}, 'resources': {}},
                          'serverInfo': {'name': 'codex-turn-meter', 'version': '0.1.0'},
                          'instructions': 'Open show_meter once. UI waits directly for turn events. Never call wait_for_turn in a model loop. Task IDs must be exact; never assume the latest task is current.'}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': TOOLS}
            elif method == 'tools/call':
                try:
                    result = self.call(params.get('name'), params.get('arguments', {}))
                except Exception:
                    result = {'isError': True, 'content': [{'type': 'text', 'text': '无法读取本地统计，请核对任务 ID、日志和访问权限。'}]}
            elif method == 'resources/list':
                result = {'resources': [{'uri': URI, 'mimeType': MIME, 'name': 'turn-meter'}]}
            elif method == 'resources/templates/list':
                result = {'resourceTemplates': []}
            elif method == 'resources/read' and params.get('uri') == URI:
                html = (ROOT/'ui/panel.html').read_text(encoding='utf-8')
                html = html.replace('/* PANEL_CSS */', (ROOT/'ui/panel.css').read_text(encoding='utf-8'))
                html = html.replace('/* PANEL_JS */', (ROOT/'ui/panel.js').read_text(encoding='utf-8'))
                result = {'contents': [{'uri': URI, 'mimeType': MIME, 'text': html,
                    '_meta': {'ui': {'prefersBorder': True, 'csp': {'connectDomains': [], 'resourceDomains': []}}}}]}
            else:
                return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': 'Unknown method'}}
            return {'jsonrpc': '2.0', 'id': ident, 'result': result}
        except Exception:
            return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32603, 'message': 'Local meter error'}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', default=os.environ.get('CODEX_HOME') or str(Path.home()/'.codex'))
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    server = Server(args.home)
    output_lock = threading.Lock()
    slots = threading.BoundedSemaphore(8)
    pool = ThreadPoolExecutor(max_workers=8)

    def emit(value):
        if value is not None:
            with output_lock:
                print(json.dumps(value, ensure_ascii=False), flush=True)

    def work(msg):
        try:
            emit(server.handle(msg))
        finally:
            slots.release()

    try:
        for line in sys.stdin.buffer:
            try:
                msg = json.loads(line)
            except ValueError:
                emit({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid JSON'}})
                continue
            if not slots.acquire(blocking=False):
                emit({'jsonrpc':'2.0','id':msg.get('id') if isinstance(msg,dict) else None,
                      'error':{'code':-32000,'message':'Too many pending requests'}})
            else:
                pool.submit(work, msg)
    finally:
        server.stop.set()
        pool.shutdown(wait=True)


if __name__ == '__main__':
    main()

"""Read local Codex accounting; emit revisions at turn boundaries."""
import hashlib
import json
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens',
          'reasoning_output_tokens', 'total_tokens')
LIMIT = 8 * 1024 * 1024


def canonical(path):
    value = str(Path(path).expanduser().resolve())
    slash = chr(92)
    prefix = slash * 2 + '?' + slash
    if value.startswith(prefix):
        value = slash * 2 + value[8:] if value.startswith(prefix + 'UNC' + slash) else value[4:]
    return Path(value).resolve()


def count(value):
    return value if type(value) is int and value >= 0 else None


def accounting(raw):
    if not isinstance(raw, dict):
        return None
    out = {k: count(raw.get(k)) for k in FIELDS}
    inp, cache = out['input_tokens'], out['cached_input_tokens']
    out['cache_percent'] = round(100 * cache / inp, 1) if inp and cache is not None and cache <= inp else None
    return out


class Reader:
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.identity = None
        self.partial = False
        self.total = self.last = self.baseline = None
        self.window = self.turn_id = self.at = None
        self.status = 'unknown'
        self.turn_usage = None
        self.requests = 0
        self.baseline_known = False
        self.epoch = 0
        self.signature = None
        self.duration_ms = self.ttft_ms = None

    def read(self):
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self.identity is not None and (identity != self.identity or stat.st_size < self.offset):
            previous = self.epoch
            self.__init__(self.path)
            self.epoch = previous + 1
        if self.identity is None:
            self.offset = max(0, stat.st_size - LIMIT)
            self.partial = self.offset > 0
        first = self.identity is None
        self.identity = identity
        with self.path.open('rb') as f:
            f.seek(self.offset)
            if first and self.partial:
                f.readline()
                self.offset = f.tell()
            while f.tell() < stat.st_size:
                pos = f.tell()
                line = f.readline()
                if not line.endswith(b'\n'):
                    self.offset = pos
                    break
                self.offset = f.tell()
                if b'"event_msg"' not in line[:400]:
                    continue
                try:
                    event = json.loads(line)
                    if isinstance(event, dict) and event.get('type') == 'event_msg':
                        self.consume(event.get('payload'), event.get('timestamp'))
                except (ValueError, TypeError):
                    continue

    def consume(self, p, timestamp):
        if not isinstance(p, dict):
            return
        kind = p.get('type')
        if kind == 'task_started':
            if p.get('turn_id') == self.turn_id and self.status == 'running':
                return
            self.turn_id = p.get('turn_id')
            self.baseline = dict(self.total) if self.total else dict.fromkeys(FIELDS, 0)
            self.baseline_known = self.total is not None or not self.partial
            self.status = 'running'
            self.turn_usage = None
            self.requests = 0
            self.duration_ms = self.ttft_ms = None
            self.window = count(p.get('model_context_window')) or self.window
            self.epoch += 1
        elif kind in ('task_complete', 'task_completed', 'turn_aborted'):
            if p.get('turn_id') and self.turn_id and p['turn_id'] != self.turn_id:
                return
            self.status = 'interrupted' if kind == 'turn_aborted' else 'completed'
            self.duration_ms = count(p.get('duration_ms'))
            self.ttft_ms = count(p.get('time_to_first_token_ms'))
            self.epoch += 1
        elif kind == 'token_count' and isinstance(p.get('info'), dict):
            info = p['info']
            total, last = accounting(info.get('total_token_usage')), accounting(info.get('last_token_usage'))
            self.window = count(info.get('model_context_window')) or self.window
            if total is None or last is None or total['total_tokens'] is None:
                return
            signature = tuple(total[k] for k in FIELDS)
            if signature == self.signature:
                return
            if self.total and any(total[k] is not None and self.total[k] is not None and total[k] < self.total[k] for k in FIELDS):
                self.baseline_known = False
            self.total, self.last, self.signature, self.at = total, last, signature, timestamp
            if self.turn_id:
                self.requests += 1
                self.turn_usage = accounting({k: total[k] - self.baseline[k]
                    if total[k] is not None and self.baseline[k] is not None and total[k] >= self.baseline[k]
                    else None for k in FIELDS}) if self.baseline_known else None
            # Accounting can be flushed after completion. Publish that correction too.
            if self.status != 'running':
                self.epoch += 1

    def snapshot(self):
        inp = self.last['input_tokens'] if self.last else None
        return {'status': self.status, 'turn_id': self.turn_id, 'last': self.last,
                'turn': self.turn_usage, 'total': self.total, 'requests': self.requests,
                'context': {'input': inp, 'window': self.window,
                            'percent': round(inp / self.window * 100, 1) if inp is not None and self.window else None},
                'updated_at': self.at, 'partial_history': self.partial,
                'turn_complete_data': self.baseline_known, 'duration_ms': self.duration_ms,
                'ttft_ms': self.ttft_ms}


class Store:
    def __init__(self, home):
        self.home = canonical(home)
        self.readers = OrderedDict()
        self.lock = threading.RLock()

    def db(self, prefix='state'):
        files = [p for p in self.home.glob(prefix+'_*.sqlite') if re.fullmatch(re.escape(prefix)+r'_\d+\.sqlite', p.name)]
        if not files:
            raise ValueError('未找到本地任务数据库')
        file = max(files, key=lambda p: int(p.stem.split('_')[-1]))
        db = sqlite3.connect(file.as_uri() + '?mode=ro', uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        return db

    @staticmethod
    def labels(row):
        row = dict(row)
        name = row.get('name')
        if not (isinstance(name, str) and name.strip()):
            # Untitled user threads fall back to their first message; machine stubs stay unnamed.
            first = row.get('first_user_message')
            name = ' '.join(first.split())[:30] if isinstance(first, str) else ''
        return {'name': name or '未命名对话', 'project': Path(row.get('cwd') or '').name}

    @staticmethod
    def route(path):
        try:
            text = str(canonical(path)) if path else ''
        except (OSError, ValueError):
            text = str(path or '')
        return text.replace(chr(92), '/').casefold().rstrip('/')

    def project_for(self, cwd, project_id, projects, roots):
        if project_id and project_id in projects:
            return projects[project_id], 'project:'+str(project_id)
        text = self.route(cwd)
        best_pid, best_len = None, -1
        for pid, root in roots:
            candidate = self.route(root)
            if candidate and text and (text == candidate or text.startswith(candidate+'/')) and len(candidate) > best_len:
                best_pid, best_len = pid, len(candidate)
        if best_pid is not None and projects.get(best_pid):
            return projects[best_pid], 'project:'+str(best_pid)
        if text:
            return Path(cwd).name, 'path:'+text
        return '未分组', 'path:'

    def sessions(self, now=None):
        db = self.db()
        try:
            columns = {r[1] for r in db.execute('PRAGMA table_info(threads)')}
            fields = ['id', 'updated_at', 'archived'] + [k for k in ('name', 'cwd', 'project_id', 'thread_source', 'first_user_message') if k in columns]
            where = "WHERE thread_source='user'" if 'thread_source' in columns else ''
            rows = [dict(r) for r in db.execute('SELECT '+','.join(fields)+' FROM threads '+where)]
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            projects, roots = {}, []
            if 'projects' in tables:
                projects = {r['id']:r['name'] for r in db.execute('SELECT id,name FROM projects')}
                if 'project_roots' in tables:
                    roots = [(r['project_id'], r['path']) for r in db.execute('SELECT project_id,path FROM project_roots')]
        finally:
            db.close()
        replies = {}
        history = None
        try:
            history = self.db('thread_history')
            replies = dict(history.execute("SELECT thread_id,MAX(completed_at) FROM thread_turns WHERE final_agent_item_id IS NOT NULL AND completed_at IS NOT NULL GROUP BY thread_id").fetchall())
        except (ValueError, sqlite3.Error):
            pass  # Missing reply history is displayed as unknown, never as a modification time.
        finally:
            if history is not None:
                history.close()
        result = []
        for row in rows:
            labels = self.labels(row)
            project, key = self.project_for(row.get('cwd'), row.get('project_id'), projects, roots)
            result.append({'id':row['id'], 'updated_at':row['updated_at'], 'archived':row['archived'],
                           'name':labels['name'], 'project':project, 'project_key':key,
                           'last_reply_at':replies.get(row['id'])})
        # Newest group first, then the newest session inside each group.
        for row in result:
            row['activity'] = max(row['last_reply_at'] or 0, row['updated_at'] or 0)
        groups = OrderedDict()
        for row in result:
            groups.setdefault(row['project_key'], []).append(row)
        ordered = []
        for key in sorted(groups, key=lambda k: (-max(r['activity'] for r in groups[k]), groups[k][0]['project'].casefold(), k)):
            ordered.extend(sorted(groups[key], key=lambda r: (-r['activity'], r['name'].casefold(), r['id'])))
        omitted = 0
        if 'thread_source' in columns:
            # Subagent/review stubs were already dropped in SQL; keep recent user activity only.
            cutoff = (now if now is not None else time.time()) - 30*86400
            active = [r for r in ordered if r['activity'] >= cutoff]
            omitted = len(ordered) - len(active) + max(0, len(active) - 50)
            ordered = active[:50]
        for row in ordered:
            del row['activity']
        return {'sessions': ordered, 'omitted': omitted}

    def path(self, tid, with_labels=False):
        if not isinstance(tid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', tid):
            raise ValueError('任务 ID 无效')
        db = self.db()
        try:
            columns = {r[1] for r in db.execute('PRAGMA table_info(threads)')}
            fields = ['rollout_path'] + [k for k in ('name', 'cwd', 'first_user_message') if k in columns]
            row = db.execute('SELECT '+','.join(fields)+' FROM threads WHERE id=?', (tid,)).fetchone()
        finally:
            db.close()
        if not row or not row['rollout_path']:
            raise ValueError('此任务没有本地统计日志')
        path = canonical(row['rollout_path'])
        if not any(path.is_relative_to(canonical(self.home / folder)) for folder in ('sessions', 'archived_sessions')):
            raise ValueError('统计日志位于允许目录之外')
        return (path, self.labels(row)) if with_labels else path

    def snapshot(self, tid):
        with self.lock:
            path, labels = self.path(tid, with_labels=True)
            reader = self.readers.get(tid)
            if reader is None or reader.path != path:
                reader = Reader(path)
                self.readers[tid] = reader
            self.readers.move_to_end(tid)
            while len(self.readers) > 16:
                self.readers.popitem(last=False)
            reader.read()
            out = reader.snapshot()
            out['thread_id'] = tid
            out['thread_name'] = labels['name']
            out['project'] = labels['project']
            # Stable across unchanged reads; includes file identity for replacement detection.
            out['revision'] = hashlib.sha256(f'{tid}:{path}:{reader.identity}:{reader.epoch}:{reader.turn_id}:{labels}'.encode()).hexdigest()
            return out

    def wait(self, tid, after, stop, seconds=20):
        deadline = time.monotonic() + seconds
        while not stop.is_set():
            snapshot = self.snapshot(tid)
            if snapshot['revision'] != after:
                return {'changed': True, 'snapshot': snapshot}
            left = deadline - time.monotonic()
            if left <= 0:
                break
            stop.wait(min(.5, left))
        return {'changed': False}

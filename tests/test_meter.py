import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/codex-turn-meter/server'))
from collector import Reader, Store, accounting
from server import Server, URI


def usage(n,c=None):
    return {'input_tokens':n,'cached_input_tokens':n//2 if c is None else c,
            'output_tokens':10,'reasoning_output_tokens':3,'total_tokens':n+10}


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);(self.home/'sessions').mkdir()
        self.path=self.home/'sessions/a.jsonl';self.path.touch()
        db=sqlite3.connect(self.home/'state_5.sqlite')
        db.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,updated_at INTEGER,archived INTEGER)')
        db.execute('INSERT INTO threads VALUES(?,?,1,0)',('task-a',str(self.path)))
        db.commit();db.close();self.store=Store(self.home)

    def event(self,kind,**props):
        with self.path.open('a',encoding='utf-8') as f:
            f.write(json.dumps({'type':'event_msg','timestamp':'2026-09-12T10:00:00Z','payload':{'type':kind,**props}})+'\n')

    def tokens(self,n,last=None):
        self.event('token_count',info={'last_token_usage':usage(n if last is None else last),
                   'total_token_usage':usage(n),'model_context_window':1000})

    def snap(self):return self.store.snapshot('task-a')

    def test_project_and_reply_order(self):
        db=sqlite3.connect(self.home/'state_5.sqlite')
        for column in ('name TEXT', 'cwd TEXT', 'project_id TEXT'):db.execute('ALTER TABLE threads ADD COLUMN '+column)
        db.execute('CREATE TABLE projects(id TEXT,name TEXT)')
        db.executemany('INSERT INTO projects VALUES(?,?)',[('p1','Alpha'),('p2','Beta')])
        db.execute("UPDATE threads SET name='old',cwd='/x',project_id='p1',updated_at=999 WHERE id='task-a'")
        for tid,pid in [('b','p1'),('c','p2'),('d','p1')]:
            db.execute('INSERT INTO threads(id,rollout_path,updated_at,archived,name,cwd,project_id) VALUES(?,?,1,0,?,?,?)',(tid,str(self.path),tid,'/x',pid))
        db.commit();db.close()
        h=sqlite3.connect(self.home/'thread_history_1.sqlite')
        h.execute('CREATE TABLE thread_turns(thread_id TEXT,completed_at INTEGER,final_agent_item_id TEXT)')
        h.executemany('INSERT INTO thread_turns VALUES(?,?,?)',[('task-a',10,'a'),('b',20,'b'),('c',100,'c'),('task-a',999,None)])
        h.commit();h.close()
        data=self.store.sessions()
        rows=data['sessions']
        # Groups ordered by newest activity (Alpha 999 before Beta 100), then newest session first.
        self.assertEqual([r['id'] for r in rows],['task-a','b','d','c'])
        self.assertEqual(rows[1]['last_reply_at'],20)
        self.assertIsNone(rows[2]['last_reply_at'])
        self.assertEqual(rows[0]['project'],'Alpha')
        self.assertEqual(data['omitted'],0)

    def test_active_list_and_cap(self):
        db=sqlite3.connect(self.home/'state_5.sqlite')
        for column in ('name TEXT','cwd TEXT','thread_source TEXT'):db.execute('ALTER TABLE threads ADD COLUMN '+column)
        now=1800000000
        db.execute("UPDATE threads SET name='近期',cwd='C:/w',thread_source='user',updated_at=? WHERE id='task-a'",(now,))
        db.execute("INSERT INTO threads(id,rollout_path,updated_at,archived,name,cwd,thread_source) VALUES('old',?,1,0,'旧会话','C:/w','user')",(str(self.path),))
        db.execute("UPDATE threads SET updated_at=? WHERE id='old'",(now-40*86400,))
        for i in range(60):
            db.execute('INSERT INTO threads(id,rollout_path,updated_at,archived,name,cwd,thread_source) VALUES(?,?,?,0,?,?,?)',
                       (f's{i}',str(self.path),now-i,f'近期{i}','C:/w','user'))
        for i,source in enumerate(('subagent','guardian_review')):
            db.execute("INSERT INTO threads(id,rollout_path,updated_at,archived,cwd,thread_source) VALUES(?,?,?,0,'C:/w',?)",
                       (f'x{i}',str(self.path),now,source))
        db.commit();db.close()
        data=self.store.sessions(now=now)
        ids={r['id'] for r in data['sessions']}
        self.assertNotIn('x0',ids);self.assertNotIn('x1',ids)  # machine stubs dropped
        self.assertNotIn('old',ids)  # outside the 30-day window
        self.assertIn('task-a',ids)
        self.assertEqual(len(data['sessions']),50)  # hard cap
        self.assertEqual(data['omitted'],12)  # 1 too old + 11 beyond the cap, of 62 user sessions

    def test_project_roots_resolution(self):
        db=sqlite3.connect(self.home/'state_5.sqlite')
        for column in ('name TEXT','cwd TEXT','project_id TEXT','thread_source TEXT'):db.execute('ALTER TABLE threads ADD COLUMN '+column)
        db.execute('CREATE TABLE projects(id TEXT,name TEXT)')
        db.execute('CREATE TABLE project_roots(project_id TEXT,path TEXT)')
        db.executemany('INSERT INTO projects VALUES(?,?)',[('p-arid','Arid'),('p-career','career'),('p-col','colonization')])
        db.executemany('INSERT INTO project_roots VALUES(?,?)',
                       [('p-arid','D:'+chr(92)+'sci'+chr(92)+'arid'),('p-career','D:'+chr(92)+'sci'+chr(92)+'arid'+chr(92)+'work'+chr(92)+'career'),
                        ('p-col','D:'+chr(92)+'sci'+chr(92)+'colonization')])
        now=1800000000
        cwds={'t1':chr(92)*2+'?'+chr(92)+'D:'+chr(92)+'sci'+chr(92)+'arid'+chr(92)+'work'+chr(92)+'career',
              't2':chr(92)*2+'?'+chr(92)+'D:'+chr(92)+'sci'+chr(92)+'arid',
              't3':chr(92)*2+'?'+chr(92)+'D:'+chr(92)+'sci'+chr(92)+'arid'+chr(92)+'other',
              't4':chr(92)*2+'?'+chr(92)+'E:'+chr(92)+'nowhere'+chr(92)+'zoo',
              't5':chr(92)*2+'?'+chr(92)+'D:'+chr(92)+'sci'+chr(92)+'colonization'+chr(92)+'am-colonization-global',
              't6':chr(92)*2+'?'+chr(92)+'D:'+chr(92)+'ShareCache'+chr(92)+'gzy'+chr(92)+'sci'+chr(92)+'ColonizationModelData'+chr(92)+'am-colonization-global'}
        for tid,cwd in cwds.items():
            db.execute("INSERT INTO threads(id,rollout_path,updated_at,archived,name,cwd,thread_source) VALUES(?,?,?,0,?,?, 'user')",(tid,str(self.path),now,tid,cwd))
        db.commit();db.close()
        data=self.store.sessions(now=now)
        projects={r['id']:r['project'] for r in data['sessions']}
        self.assertEqual(projects['t1'],'career')  # longest matching root wins
        self.assertEqual(projects['t2'],'Arid')
        self.assertEqual(projects['t3'],'Arid')  # prefix match stays inside path boundaries
        self.assertEqual(projects['t4'],'zoo')  # unmatched cwd falls back to folder name
        self.assertEqual(projects['t5'],'colonization')
        self.assertEqual(projects['t6'],'am-colonization-global')  # distinct tree stays its own group

    def test_first_message_name_fallback(self):
        db=sqlite3.connect(self.home/'state_5.sqlite')
        for column in ('name TEXT','cwd TEXT','first_user_message TEXT','thread_source TEXT'):db.execute('ALTER TABLE threads ADD COLUMN '+column)
        now=1800000000
        db.execute("UPDATE threads SET thread_source='user',cwd='C:/w',updated_at=?,first_user_message=? WHERE id='task-a'",
                   (now,'  你好'+chr(10)+'世界  '+'长'*40+'  '))
        db.execute("INSERT INTO threads(id,rollout_path,updated_at,archived,cwd,thread_source) VALUES('mute',?,?,0,'C:/w','user')",(str(self.path),now))
        db.commit();db.close()
        data=self.store.sessions(now=now)
        names={r['id']:r['name'] for r in data['sessions']}
        self.assertEqual(names['task-a'],'你好 世界 '+'长'*24)  # first message, collapsed, 30 chars
        self.assertEqual(names['mute'],'未命名对话')
        self.assertEqual(self.store.snapshot('task-a')['thread_name'],names['task-a'])

    def test_start_end_and_no_intermediate_revision(self):
        self.event('task_started',turn_id='t1');a=self.snap()
        self.tokens(100);b=self.snap()
        self.assertEqual(a['revision'],b['revision'])
        self.assertEqual(b['turn']['input_tokens'],100)
        self.event('task_complete',turn_id='t1',duration_ms=1000)
        c=self.snap();self.assertNotEqual(c['revision'],b['revision'])
        self.assertEqual(c['context']['percent'],10)
        self.assertEqual(c['turn']['cache_percent'],50)

    def test_baseline_dedup_reset(self):
        self.event('task_started',turn_id='t1');self.tokens(100);self.event('task_complete',turn_id='t1')
        self.event('task_started',turn_id='t2');self.tokens(300,last=200);self.tokens(300,last=200)
        a=self.snap();self.assertEqual(a['turn']['input_tokens'],200);self.assertEqual(a['requests'],1)
        self.tokens(5);a=self.snap();self.assertIsNone(a['turn'])

    def test_partial_line(self):
        raw=json.dumps({'type':'event_msg','payload':{'type':'task_started','turn_id':'t'}})
        self.path.write_text(raw[:20],encoding='utf-8');self.assertEqual(self.snap()['status'],'unknown')
        with self.path.open('a') as f:f.write(raw[20:]+'\n')
        self.assertEqual(self.snap()['status'],'running')

    def test_wait_only_boundary(self):
        self.event('task_started',turn_id='t');a=self.snap()
        self.tokens(100)
        self.assertFalse(self.store.wait('task-a',a['revision'],threading.Event(),.01)['changed'])
        timer=threading.Timer(.05,lambda:self.event('task_complete',turn_id='t'))
        timer.start();self.addCleanup(timer.join)
        result=self.store.wait('task-a',a['revision'],threading.Event(),1)
        self.assertTrue(result['changed']);self.assertEqual(result['snapshot']['status'],'completed')

    def test_late_accounting_publishes(self):
        self.event('task_started',turn_id='t');self.event('task_complete',turn_id='t');a=self.snap()
        self.tokens(100);self.assertNotEqual(a['revision'],self.snap()['revision'])

    def test_path_restriction_and_missing_task(self):
        with self.assertRaises(ValueError):self.store.snapshot('other')
        db=sqlite3.connect(self.home/'state_5.sqlite');db.execute('UPDATE threads SET rollout_path=?',(str(self.home/'auth.json'),));db.commit();db.close()
        with self.assertRaises(ValueError):self.snap()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows extended paths')
    def test_windows_extended_path(self):
        extended=chr(92)*2+'?'+chr(92)+str(self.path)
        db=sqlite3.connect(self.home/'state_5.sqlite')
        db.execute('UPDATE threads SET rollout_path=?',(extended,));db.commit();db.close()
        self.event('task_started',turn_id='t');self.tokens(100)
        self.assertEqual(self.snap()['last']['input_tokens'],100)

    def test_invalid_and_missing_usage(self):
        self.assertIsNone(accounting(usage(10,20))['cache_percent'])
        self.assertIsNone(accounting(usage(0))['cache_percent'])
        self.assertIsNone(accounting({'input_tokens':True})['input_tokens'])
        self.assertIsNone(self.snap()['last'])

    def test_protocol_resource_and_read_only(self):
        server=Server(self.home)
        r=server.handle({'jsonrpc':'2.0','id':1,'method':'resources/read','params':{'uri':URI}})
        resource=r['result']['contents'][0]
        self.assertEqual(resource['mimeType'],'text/html;profile=mcp-app')
        self.assertIn('wait_for_turn',resource['text']);self.assertNotIn('/* PANEL_JS */',resource['text'])
        before=(self.home/'state_5.sqlite').read_bytes()
        server.call('read_meter',{'thread_id':'task-a'})
        self.assertEqual(before,(self.home/'state_5.sqlite').read_bytes())
        self.assertIsNone(server.handle({'jsonrpc':'2.0','method':'notifications/initialized'}))

    def test_bounded_tail_unknown_baseline(self):
        reader=Reader(self.path);reader.partial=True
        reader.consume({'type':'task_started','turn_id':'t'},None)
        reader.consume({'type':'token_count','info':{'last_token_usage':usage(100),'total_token_usage':usage(900)}},None)
        self.assertIsNone(reader.snapshot()['turn'])


if __name__=='__main__':unittest.main()

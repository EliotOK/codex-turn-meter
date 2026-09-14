import collections, json, sqlite3
from pathlib import Path
home=Path.home()/'.codex'
db=sqlite3.connect((home/'state_5.sqlite').as_uri()+'?mode=ro',uri=True)
print('thread columns:', [r[1] for r in db.execute('pragma table_info(threads)')])
tid='01a094ae-72d6-71e2-abaa-de2ef286b59a'
row=db.execute('select rollout_path from threads where id=?',(tid,)).fetchone()
counts=collections.Counter(); example={}; keys={}
if row:
    for line in Path(row[0]).open(encoding='utf-8'):
        try: e=json.loads(line)
        except ValueError: continue
        p=e.get('payload',{}); t=e.get('type')
        if isinstance(p,dict) and t=='event_msg':
            kind=p.get('type'); counts[kind]+=1
            if kind in ('task_started','task_complete','task_completed','user_message','token_count','turn_aborted'):
                keys[kind]=list(p)
            if kind=='token_count': example=p.get('info')
print('event counts:',dict(counts)); print('event keys:',keys)
print('latest numeric usage:',json.dumps(example))

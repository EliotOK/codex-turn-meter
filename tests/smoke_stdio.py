"""Exercise actual MCP stdio concurrency and optionally read a real local task."""
import argparse
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

parser=argparse.ArgumentParser();parser.add_argument('--thread-id');args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
proc=subprocess.Popen([sys.executable,'-u',str(root/'codex-turn-meter/server/server.py')],
                      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
replies=queue.Queue()
threading.Thread(target=lambda:[replies.put(json.loads(line)) for line in proc.stdout],daemon=True).start()
def send(ident,method,params={}):
    proc.stdin.write(json.dumps({'jsonrpc':'2.0','id':ident,'method':method,'params':params})+'\n');proc.stdin.flush()
def receive(ident):
    msg=replies.get(timeout=5);assert msg['id']==ident,msg
    assert 'error' not in msg,msg
    return msg['result']
try:
    send(1,'initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'meter-test','version':'1'}})
    assert receive(1)['serverInfo']['name']=='codex-turn-meter'
    send(2,'tools/list');assert len(receive(2)['tools'])==4
    send(3,'resources/read',{'uri':'ui://codex-turn-meter/panel.html'})
    assert receive(3)['contents'][0]['mimeType']=='text/html;profile=mcp-app'
    report={'stdio_handshake':True,'tools':4,'resource':True}
    if args.thread_id:
        send(4,'tools/call',{'name':'read_meter','arguments':{'thread_id':args.thread_id}})
        data=receive(4);assert not data.get('isError'),data
        s=data['structuredContent']['snapshot']
        assert s['last']['input_tokens'] is not None and s['context']['window']>0
        send(5,'tools/call',{'name':'wait_for_turn','arguments':{'thread_id':args.thread_id,'after':s['revision']}})
        send(6,'ping');receive(6)
        report.update(real_local_usage=True,ping_during_wait=True,numeric_fields_present=True)
    proc.stdin.close();proc.wait(timeout=3);assert proc.returncode==0
    report['clean_shutdown']=True
    (root/'stdio-test-result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
finally:
    if proc.poll() is None:proc.kill();proc.wait()

import json
from pathlib import Path

root=Path(__file__).resolve().parent/'plugins'/'codex-turn-meter'
manifest={
 'name':'codex-turn-meter','version':'0.1.0',
 'description':'按交互更新的本地 Codex 用量面板。',
 'author':{'name':'Local developer'},'skills':'./skills/', 'mcpServers':'./.mcp.json',
 'interface':{'displayName':'用量 · 每轮更新','shortDescription':'上下文、缓存命中、本轮输入输出',
 'longDescription':'打开一次，绑定当前任务；本轮开始更新状态，结束更新统计。支持手动刷新和切换统计范围。',
 'developerName':'Local developer','category':'Productivity','capabilities':['Interactive'],
 'defaultPrompt':['打开当前任务的用量面板'],'brandColor':'#8AB8A1'}}
(root/'.codex-plugin/plugin.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(root/'.mcp.json').write_text(json.dumps({'mcpServers':{'meter':{'command':'python','args':['-u','./server/server.py'],'cwd':'.','env_vars':['CODEX_HOME']}}},indent=2)+'\n',encoding='utf-8')

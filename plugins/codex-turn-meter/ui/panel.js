'use strict';
const $ = id => document.getElementById(id);
let nextId=0, ready=false, disposed=false, paused=false, selected='', generation=0;
let snapshot=null, revision='', watching=false, scope='turn', activeRead=0;
const pending=new Map();
const taskNames=new Map();
const fmt=n=>n==null?'—':Intl.NumberFormat('en',{notation:'compact',maximumFractionDigits:1}).format(n);
const pct=n=>n==null?'—':`${n.toFixed(1)}%`;
function error(message){$('error').hidden=!message;$('error').textContent=message||'';}
function send(method,params={}){window.parent.postMessage({jsonrpc:'2.0',method,params},'*');}
function rpc(method,params={}){
  return new Promise((resolve,reject)=>{
    const id=`meter-${++nextId}`;
    const timer=setTimeout(()=>{pending.delete(id);reject(Error('面板连接超时，请手动刷新或重新打开。'));},30000);
    pending.set(id,{resolve,reject,timer});
    window.parent.postMessage({jsonrpc:'2.0',id,method,params},'*');
  });
}
async function call(name,args={}){
  const result=await rpc('tools/call',{name,arguments:args});
  if(result?.isError) throw Error(result.content?.find(x=>x.type==='text')?.text||'统计读取失败');
  return result?.structuredContent||{};
}
function render(){
  if(!snapshot)return;
  // Turn diff needs its start baseline; without it the main cells fall back to the latest request, clearly labelled.
  const fallback=scope==='turn'&&!snapshot.turn&&!!snapshot.last;
  const u=fallback?snapshot.last:snapshot[scope];
  $('status').textContent=paused?'已暂停':({running:'本轮进行中',completed:'本轮已完成',interrupted:'本轮已中断'}[snapshot.status]||'等待本轮记录');
  $('context').textContent=pct(snapshot.context?.percent);
  $('window').textContent=`${fmt(snapshot.context?.input)} / ${fmt(snapshot.context?.window)}`;
  $('cache').textContent=pct(u?.cache_percent);
  $('cached').textContent=`命中 ${fmt(u?.cached_input_tokens)} tok`;
  $('input').textContent=fmt(u?.input_tokens);$('output').textContent=fmt(u?.output_tokens);
  $('fill').style.width=`${Math.max(0,Math.min(100,snapshot.context?.percent||0))}%`;
  $('scope-label').textContent=fallback?'最近请求 · 本轮起点未知':{turn:'本轮提问',last:'最近请求',total:'任务累计'}[scope];
  $('timestamp').textContent=snapshot.status==='running'?'统计截至最近读取 · 完成后更新':
    snapshot.updated_at?`统计上报 ${new Date(snapshot.updated_at).toLocaleTimeString('zh-CN',{hour12:false})}`:'等待统计上报';
  taskNames.set(snapshot.thread_id,snapshot.thread_name||taskNames.get(snapshot.thread_id)||'未命名对话');
  $('binding').textContent=`观察：${taskNames.get(snapshot.thread_id)}`;
  const option=Array.from($('tasks').options).find(o=>o.value===snapshot.thread_id);
  if(option&&option.dataset.name!==snapshot.thread_name&&snapshot.thread_name){option.textContent=snapshot.thread_name;option.dataset.name=snapshot.thread_name;}
  $('more').textContent=`本轮已记录 ${snapshot.requests} 次请求 · 推理输出 ${fmt(u?.reasoning_output_tokens)} tok`+
    (snapshot.duration_ms!=null?` · 本轮耗时 ${(snapshot.duration_ms/1000).toFixed(1)} 秒`:'')+
    (snapshot.partial_history?' · 历史仅读取尾部':'')+
    (!snapshot.turn_complete_data?' · 本轮起点不足，暂不计算本轮累计':'');
  send('ui/notifications/size-changed',{height:document.body.scrollHeight});
}
function accept(data){if(data?.thread_id!==selected)return;snapshot=data;revision=data.revision;render();}
async function refresh(){
  if(!ready||!selected||disposed)return;
  const g=generation,read=++activeRead;
  try{const data=await call('read_meter',{thread_id:selected});if(g===generation&&read===activeRead){accept(data.snapshot);error('');}}
  catch(e){if(g===generation)error(e.message);}
}
async function watch(){
  if(watching||!ready||!selected||paused||disposed||document.hidden)return;
  watching=true;
  try{
    while(ready&&selected&&!paused&&!disposed&&!document.hidden){
      const g=generation,r=activeRead;
      const data=await call('wait_for_turn',{thread_id:selected,after:revision});
      if(g!==generation||paused||document.hidden||disposed)continue;
      if(data.changed&&r===activeRead){accept(data.snapshot);error('');}
    }
  }catch(e){if(!disposed)error(e.message);}
  finally{watching=false;}
}
async function select(tid){
  selected=tid;generation++;revision='';snapshot=null;
  for(const id of ['context','cache','input','output'])$(id).textContent='—';
  $('binding').textContent=tid?`观察：${taskNames.get(tid)||'正在读取对话名称'}`:'';
  if(tid){await refresh();watch();}
}
async function tasks(){
  const data=await call('list_meter_tasks');
  const rows=data.sessions||[], groups=new Map(), groupNames=new Map(), values=new Set();
  const nodes=[new Option('选择对话','')];
  for(const row of rows){
    const name=row.name||'未命名对话';taskNames.set(row.id,name);
    const project=row.project||'未分组', key=row.project_key||project;
    if(!groups.has(key)){
      const group=document.createElement('optgroup');
      const n=(groupNames.get(project)||0)+1;groupNames.set(project,n);
      group.label=project+(n>1?` · 项目 ${n}`:'');groups.set(key,{group,used:new Map()});nodes.push(group);
    }
    const date=row.last_reply_at?new Date(row.last_reply_at*1000).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'暂无回复记录';
    let label=`${name} · ${date}`+(row.archived?' · 已归档':'');
    const entry=groups.get(key), n=(entry.used.get(label)||0)+1;entry.used.set(label,n);
    const option=new Option(label+(n>1?` · 会话 ${n}`:''),row.id);option.dataset.name=name;
    entry.group.append(option);values.add(row.id);
  }
  if(data.omitted>0){const omitted=new Option(`… 已省略 ${data.omitted} 个较早会话`,'');omitted.disabled=true;nodes.push(omitted);}
  if(selected&&!values.has(selected)){
    const option=new Option(snapshot?.thread_name||taskNames.get(selected)||'当前观察对话',selected);
    option.dataset.name=snapshot?.thread_name||'';nodes.push(option);
  }
  $('tasks').replaceChildren(...nodes);$('tasks').value=selected;
}
window.addEventListener('message',event=>{
  if(event.source!==window.parent||disposed)return;
  const msg=event.data;if(!msg||msg.jsonrpc!=='2.0')return;
  if(pending.has(msg.id)){const p=pending.get(msg.id);pending.delete(msg.id);clearTimeout(p.timer);msg.error?p.reject(Error(msg.error.message||'宿主调用失败')):p.resolve(msg.result);return;}
  if(msg.method==='ui/notifications/host-context-changed'&&['dark','light'].includes(msg.params?.theme))document.documentElement.dataset.theme=msg.params.theme;
  if(msg.method==='ui/notifications/tool-input'&&!selected&&msg.params?.arguments?.thread_id){selected=msg.params.arguments.thread_id;if(ready)select(selected);}
  if(msg.method==='ui/notifications/tool-result'){
    const data=msg.params?.structuredContent?.snapshot;
    if(data&&!selected)selected=data.thread_id;
    if(data){accept(data);if(ready)watch();}
  }
  if(msg.method==='ui/resource-teardown'){
    disposed=true;generation++;
    for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('面板已关闭'));}pending.clear();
    if(msg.id!==undefined)window.parent.postMessage({jsonrpc:'2.0',id:msg.id,result:{}},'*');
  }
});
$('refresh').onclick=async()=>{await refresh();watch();};
$('pause').onclick=()=>{paused=!paused;$('pause').textContent=paused?'继续':'暂停';render();if(!paused){refresh().then(watch);}};
$('expand').onclick=()=>{const open=$('details').hidden;$('details').hidden=!open;$('expand').setAttribute('aria-expanded',String(open));if(open&&ready)tasks().catch(e=>error(e.message));send('ui/notifications/size-changed',{height:document.body.scrollHeight});};
$('tasks').onchange=()=>select($('tasks').value);
$('scope').onchange=()=>{scope=$('scope').value;render();};
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!paused){refresh().then(watch);}});
async function initialize(){
  if(window.parent===window){error('请在 Codex 插件中打开此面板。');return;}
  try{
    const init=await rpc('ui/initialize',{appInfo:{name:'codex-turn-meter',version:'0.1.0'},appCapabilities:{},protocolVersion:'2026-01-26'});
    if(!init?.hostCapabilities?.serverTools)throw Error('当前客户端未提供 MCP App 工具交互能力，可使用 read_meter 查询。');
    ready=true;if(['dark','light'].includes(init.hostContext?.theme))document.documentElement.dataset.theme=init.hostContext.theme;
    send('ui/notifications/initialized');
    await tasks();
    if(selected){await refresh();watch();}else{$('details').hidden=false;$('expand').setAttribute('aria-expanded','true');$('status').textContent='请选择观察任务';}
  }catch(e){error(e.message);}
}
initialize();

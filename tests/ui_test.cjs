const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.METER_PLAYWRIGHT||'C:/Users/19000/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
let html=fs.readFileSync(path.join(root,'plugins/codex-turn-meter/ui/panel.html'),'utf8')
 .replace('/* PANEL_CSS */',fs.readFileSync(path.join(root,'plugins/codex-turn-meter/ui/panel.css'),'utf8'))
 .replace('/* PANEL_JS */',fs.readFileSync(path.join(root,'plugins/codex-turn-meter/ui/panel.js'),'utf8'));
const sample=(status,revision,input=12500)=>({thread_id:'task-a',thread_name:'查询 Codex 缓存与上下文用量',turn_id:'turn-a',status,revision,
 turn:{input_tokens:input,cached_input_tokens:input*.8,cache_percent:80,output_tokens:1200,reasoning_output_tokens:200},
 last:{input_tokens:input,cached_input_tokens:input*.8,cache_percent:80,output_tokens:1200},
 total:{input_tokens:250000,cached_input_tokens:200000,cache_percent:80,output_tokens:24000},
 context:{input:114380,window:258400,percent:44.3},requests:3,updated_at:'2026-09-12T10:00:00Z',turn_complete_data:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:760,height:500}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let current=sample('completed','a'), waits=[], calls=[];
 await page.exposeFunction('hostRpc',async msg=>{
   if(msg.method==='ui/initialize')return {hostCapabilities:{serverTools:{}},hostContext:{theme:'dark'}};
   if(msg.method!=='tools/call')return {};
   calls.push(msg.params.name);
   switch(msg.params.name){
    case 'list_meter_tasks':return {structuredContent:{sessions:[{id:'task-a',name:'查询 Codex 缓存与上下文用量',project:'career',project_key:'p1',updated_at:1,last_reply_at:1789220857},{id:'task-b',name:'查询 Codex 缓存与上下文用量',project:'demo',project_key:'p2',updated_at:2,last_reply_at:1789220791},{id:'task-c',name:'起点未知示例',project:'demo',project_key:'p2',updated_at:3,last_reply_at:1789220000}],omitted:7}};
    case 'read_meter':{
      const tid=msg.params.arguments.thread_id;
      const snap=tid==='task-c'?{...current,turn:null}:current;
      return {structuredContent:{snapshot:{...snap,thread_id:tid}}};
    }
    case 'wait_for_turn':return new Promise(resolve=>waits.push({resolve,args:msg.params.arguments}));
   }
 });
 await page.setContent('<iframe id="app" style="width:100%;height:470px;border:0"></iframe>');
 await page.evaluate(()=>window.addEventListener('message',async e=>{
   if(e.source!==document.getElementById('app').contentWindow||e.data.id===undefined)return;
   const result=await window.hostRpc(e.data);
   e.source.postMessage({jsonrpc:'2.0',id:e.data.id,result},'*');
 }));
 await page.locator('#app').evaluate((el,html)=>el.srcdoc=html,html);
 const app=page.frameLocator('#app');
 await app.locator('#tasks optgroup[label=career]').waitFor({state:'attached'});
 assert((await app.locator('#tasks').innerText()).includes('查询 Codex 缓存与上下文用量'));
 assert(!(await app.locator('#tasks').innerText()).includes('task-a'));
 assert(await app.locator('#tasks option:disabled').filter({hasText:'已省略 7 个较早会话'}).waitFor({state:'attached'}).then(()=>true));
 await app.locator('#tasks').selectOption('task-a');
 await app.locator('#input').filter({hasText:'12.5K'}).waitFor();
 await page.waitForFunction(()=>true);
 async function waitPending(){for(let i=0;i<100&&!waits.length;i++)await new Promise(r=>setTimeout(r,10));assert(waits.length);}
 await waitPending();
 // Empty long-wait replies do not request a fresh snapshot or change the displayed values.
 const reads=calls.filter(n=>n==='read_meter').length;
 waits.shift().resolve({structuredContent:{changed:false}});
 await waitPending();assert.equal(calls.filter(n=>n==='read_meter').length,reads);
 current=sample('running','b');waits.shift().resolve({structuredContent:{changed:true,snapshot:current}});
 await app.locator('#status').filter({hasText:'本轮进行中'}).waitFor();
 await waitPending();current=sample('completed','c',24000);
 waits.shift().resolve({structuredContent:{changed:true,snapshot:current}});
 await app.locator('#input').filter({hasText:'24K'}).waitFor();
 await waitPending();
 await app.locator('#pause').click();
 waits.shift().resolve({structuredContent:{changed:false}});
 await new Promise(r=>setTimeout(r,100));assert.equal(waits.length,0);
 await app.locator('#pause').click();await waitPending();
 // Change tasks while an old wait is pending; its result must not overwrite selection.
 await app.locator('#tasks').selectOption('task-b');
 await app.locator('#binding').filter({hasText:'查询 Codex 缓存与上下文用量'}).waitFor();
 waits.shift().resolve({structuredContent:{changed:true,snapshot:sample('completed','stale',999999)}});
 await waitPending();assert.equal(waits[0].args.thread_id,'task-b');
 assert.notEqual(await app.locator('#input').innerText(),'1M');
 // Turn baseline unknown: the main cells fall back to the latest request with a visible label.
 await app.locator('#tasks').selectOption('task-c');
 await app.locator('#input').filter({hasText:'24K'}).waitFor();
 assert((await app.locator('#scope-label').innerText()).includes('最近请求'));
 assert.equal(await app.locator('#cache').innerText(),'80.0%');
 await app.locator('#expand').click();
 for(const width of [760,390,280]){
   await page.setViewportSize({width,height:500});
   const overflow=await app.locator('body').evaluate(el=>el.scrollWidth>el.clientWidth+1);
   assert.equal(overflow,false,`overflow at ${width}`);
 }
 await page.setViewportSize({width:760,height:320});
 await app.locator('main').screenshot({path:path.join(root,'preview-simulated.png')});
 assert.deepEqual(errors,[]);
 await page.evaluate(()=>document.getElementById('app').contentWindow.postMessage({jsonrpc:'2.0',id:'dispose',method:'ui/resource-teardown'},'*'));
 for(const w of waits)w.resolve({structuredContent:{changed:false}});waits=[];
 await new Promise(r=>setTimeout(r,100));assert.equal(waits.length,0);
 fs.writeFileSync(path.join(root,'ui-test-result.json'),JSON.stringify({passed:true,checks:['handshake','task selection','omitted note','unchanged wait','turn start','turn completion','pause/resume','stale task response','turn fallback','teardown','layout 760/390/280'],pageErrors:errors,host:'simulated MCP App bridge, real Edge'},null,2));
 await browser.close();console.log('UI bridge + turn lifecycle + responsive checks passed');
})().catch(e=>{console.error(e);process.exit(1)});

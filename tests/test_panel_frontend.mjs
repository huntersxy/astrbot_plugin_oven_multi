import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
class Element {
  constructor(tag='div') { this.tagName=tag; this.children=[];this.hidden=false;this.disabled=false;this.dataset={};this.attributes={};this.textContent='';this.classList={add(){},remove(){},toggle(){}};this.maxLength=524288;this._value=''; }
  get value() {
    if (this.tagName==='select') { const selected=this.children.find((child)=>child.selected); return selected?selected.value:''; }
    return this._value;
  }
  set value(v) { this._value=v; }
  set innerHTML(_) { throw Error('Unsafe HTML insertion'); }
  setAttribute(k,v) { this.attributes[k]=v; }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
  addEventListener(type, fn) { (this.listeners ??= {})[type] ??= []; this.listeners[type].push(fn); }
  click() { for (const fn of this.listeners?.click ?? []) fn(); }
  focus() {}
  querySelector() { return this.span ??= new Element('span'); }
  querySelectorAll(selector) {
    const parts = selector.split(',').map((part) => part.trim());
    const match = (child) => {
      if (child.dataset?.key === undefined) return false;
      const isPair = child.dataset.pair !== undefined;
      return parts.some((part) => {
        if (part.includes('input[data-pair]')) return child.tagName === 'input' && isPair;
        if (part.includes('textarea')) return child.tagName === 'textarea';
        if (part.includes('input')) return child.tagName === 'input' && !isPair;
        if (part.includes('select')) return child.tagName === 'select';
        return false;
      });
    };
    const out = [];
    const walk = (el) => {
      for (const child of el.children) {
        if (match(child)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}
const elements=new Map();
const get=(id)=>{ if(!elements.has(id))elements.set(id,new Element());return elements.get(id); };
const tabs=['overview','compression','balances','settings'].map(name=>{const e=get('tab-'+name);e.dataset.tab=name;return e;});
const calls=[];const waiting=[];
const apiGet=(name)=>{calls.push(name);return new Promise((resolve,reject)=>waiting.push({name,resolve,reject}));};
const posts=[];
const apiPost=(name,body)=>{calls.push(name);posts.push(body);return new Promise((resolve,reject)=>waiting.push({name,resolve,reject}));};
const sandbox={document:{getElementById:get,createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),querySelectorAll:()=>tabs},window:{AstrBotPluginPage:{ready:async()=>{},apiGet,apiPost},addEventListener(){}},console:{warn(){}},Date,Set,setTimeout:()=>0};
vm.createContext(sandbox);
// The host appends its bridge after the view's scripts. Simulate parser order.
const bridge = sandbox.window.AstrBotPluginPage;
delete sandbox.window.AstrBotPluginPage;
let resolveReady;
bridge.ready = () => new Promise(resolve => { resolveReady = resolve; });
const source = fs.readFileSync('views/status/app.js','utf8')+'\nthis.api={state,loadStatus,loadBalances,selectTab,loadSettings,saveSettings,loadCompression,loadCompressionDetail,quantity};';
const html = fs.readFileSync('views/status/index.html','utf8');
const scripts = [...html.matchAll(/<script\b([^>]*)>\s*<\/script>/g)];
assert.equal(scripts.length, 1, 'Let the host inject exactly one bridge SDK');
assert.match(scripts[0][1], /src=["']\.\/app\.js["']/);
const deferred = /\bdefer\b/.test(scripts[0][1]);
if (!deferred) vm.runInContext(source, sandbox);
assert.notEqual(get('feature-summary').textContent, '未连接', 'Do not initialize before the host injects its bridge');
sandbox.window.AstrBotPluginPage = bridge;
if (deferred) vm.runInContext(source, sandbox);
assert.deepEqual(calls, [], 'Wait for the host context before requesting status');
assert.equal(typeof resolveReady, 'function');
resolveReady({});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const shiftNamed=(name)=>{const index=waiting.findIndex(w=>w.name===name);const [call]=waiting.splice(index,1);return call;};
const status={plugin:{version:'1.51.0'},features:[{id:'jev',name:'<img src=x onerror=bad()>',enabled:true,detail:'configured'}],notices:[],balance_configured:true};
await tick();assert.deepEqual(calls,['status']);waiting.shift().resolve(status);await tick();
assert.equal(get('query-balances').disabled,false);
assert.equal(get('feature-groups').children[0].children[1].children[0].children[0].children[0].textContent,'<img src=x onerror=bad()>');
const one=sandbox.api.loadBalances();const duplicate=sandbox.api.loadBalances();
assert.deepEqual(calls,['status','balance']);
waiting.shift().resolve({items:[{name:'A',value:'21 元',success:true}],checked_at:100,cache_seconds:30});await one;await duplicate;
assert.equal(get('balance-results').children.length,1);
const old=sandbox.api.loadBalances();const refresh=sandbox.api.loadStatus();
assert.equal(get('query-balances').disabled,true);
await sandbox.api.loadBalances();assert.equal(calls.length,4);
waiting.shift().resolve({items:[{name:'stale',value:'old',success:true}]});await old;
assert.equal(get('query-balances').disabled,true);
waiting.shift().resolve({...status,balance_configured:false});await refresh;
assert.equal(get('balance-results').children.length,0);
assert.equal(get('balance-empty').hidden,false);
assert.equal(get('query-balances').disabled,true);
const failed=sandbox.api.loadStatus();waiting.shift().reject(Error('offline'));await failed;
assert.equal(get('status-error').hidden,false);
assert.match(get('status-error').textContent,/保留上次结果/);
sandbox.api.selectTab('balances');assert.equal(get('panel-overview').hidden,true);assert.equal(get('panel-balances').hidden,false);

// 设置页：懒加载、渲染、保存与陈旧回执
sandbox.api.selectTab('settings');
assert.equal(get('panel-balances').hidden,true);assert.equal(get('panel-settings').hidden,false);
assert.deepEqual(calls[calls.length-1],'settings'); // 首次进入才拉取
const groupsPayload={revision:'rev-1',groups:[
  {name:'分段回复',note:'分组指引文本',fields:[
    {key:'mention_parser.enabled',label:'成员提及',type:'bool',value:true,effective_value:false,readonly:true},
    {key:'segment_reply.enabled',label:'启用分段回复',type:'bool',value:false},
    {key:'segment_reply.exclude_keywords',label:'无需分段的关键词列表',type:'list',value:['帮助','菜单']},
    {key:'segment_reply.delay_type',label:'发送间隔类型',type:'enum',value:'random',options:['fixed','random','smart']},
    {key:'segment_reply.max_length',label:'常规最大字数',type:'int',value:50,min:1,max:2000,step:1},
    {key:'segment_reply.random_delay_range',label:'随机间隔范围（秒）',type:'pair',value:[1,3],min:0,max:60,step:0.5},
  ]},
]};
const settingsLoad=waiting.shift();assert.equal(settingsLoad.name,'settings');settingsLoad.resolve(groupsPayload);await tick();
assert.equal(sandbox.api.state.settingsLoaded,true);
assert.equal(get('settings-groups').children.length,1);
const settingsSection=get('settings-groups').children[0];
assert.equal(settingsSection.children[1].textContent,'分组指引文本'); // 组指引行
const managedInput=settingsSection.querySelectorAll('input').find(input=>input.dataset.key==='mention_parser.enabled');
assert.equal(managedInput.disabled,true);
assert.equal(managedInput.checked,false);
const listArea=settingsSection.querySelectorAll('textarea')[0];
assert.equal(listArea.value,'帮助\n菜单'); // 列表项按行渲染
listArea.value=' 帮助 \n\n菜单\n'; // 保存时去空白、丢空行
assert.equal(get('save-settings').disabled,true); // 未修改时不可保存
sandbox.api.state.settingsDirty=true;
const saving=sandbox.api.saveSettings();
const saveCall=shiftNamed('settings/save');
assert.deepEqual(JSON.parse(JSON.stringify(posts[0])),{revision:'rev-1',values:{"segment_reply.enabled":false,"segment_reply.exclude_keywords":['帮助','菜单'],"segment_reply.delay_type":'random',"segment_reply.max_length":50,"segment_reply.random_delay_range":[1,3]}});
saveCall.resolve({revision:'rev-2',message:'ok'});await saving;
assert.equal(sandbox.api.state.settingsRevision,'rev-2');
assert.equal(sandbox.api.state.settingsDirty,false);
assert.deepEqual(calls[calls.length-1],'status'); // 保存后刷新运行概览
shiftNamed('status').resolve(status);await tick(); // 排空刷新请求

// 上下文压缩状态卡：设置页加载即并发拉取，按钮二次确认后提交
const biliStatus=shiftNamed('billion/status'); // 来自初始 loadSettings
biliStatus.resolve({enabled:true,proxy:{state:'spawned',mode:'managed',url:'http://127.0.0.1:8787',detail:'',healthy:true},wire:null,doctor:['巡检示例']});
await tick();
assert.equal(get('bili-card').hidden,true); // 压缩状态仅显示在压缩分类
assert.match(get('bili-state').textContent,/代理运行中/);
assert.equal(get('bili-wire').hidden,false);
assert.equal(get('bili-doctor').children.length,1);
get('bili-wire').click(); // 第一次：进入确认态
assert.equal(get('bili-wire').textContent,'再次点击确认');
get('bili-wire').click(); // 第二次：执行接管
const biliCall=shiftNamed('billion/wire');
assert.equal(JSON.stringify(posts[posts.length-1]),JSON.stringify({confirm:true}));
biliCall.resolve({message:'已接管'});await tick(); // 完成后刷新状态卡与运行概览
shiftNamed('billion/status').resolve({enabled:true,proxy:{state:'spawned',mode:'managed',url:'http://127.0.0.1:8787',detail:'',healthy:true},wire:{wired_at:'t',proxy_url:'u',providers:['main']},doctor:[]});
await tick(); // 状态卡刷新完成后才会发起运行概览刷新
shiftNamed('status').resolve(status);await tick();
assert.equal(get('bili-restore').hidden,false); // 接管后出现恢复按钮
assert.equal(get('bili-wire').hidden,true);
assert.equal(get('bili-wire').textContent,'接管'); // 按钮文案回到原始标签
sandbox.api.state.settingsDirty=true;
const stale=sandbox.api.saveSettings();
const staleCall=shiftNamed('settings/save');
staleCall.resolve({success:false,stale:true,message:'配置已在其他页面修改'});
await stale;
assert.match(get('settings-error').textContent,/配置已在其他页面修改/);
assert.equal(get('settings-error').children.length,1); // 附带「重新读取设置」按钮
const reloadBtn=get('settings-error').children.find((child)=>child.tagName==='button');
reloadBtn.click();
const reload=shiftNamed('settings');reload.resolve(groupsPayload);await tick();
assert.equal(sandbox.api.state.settingsRevision,'rev-1');
console.log('Panel behavior passed: manual queries, XSS-safe text, duplicate suppression, stale-result rejection, disabled-config reset, error retention, tabs, settings render/save/stale-reload.');

// Compression data: lazy load, measured zero vs unknown, XSS-safe sessions and stale retention.
sandbox.api.selectTab('compression');
const compressionCall=shiftNamed('billion/dashboard?page=1&q=');
assert.ok(compressionCall);
const compressionData={available:true,stale:false,checked_at:100,page:1,page_size:30,total:1,runtime:{version:'0.1.206',diskVersion:'0.1.206',inFlight:0},overview:{sessions:1,live:1,requests:3,hitPct:0,inputTokens:1000,cachedTokens:0,hasFoldData:true,netSavedTotal:-12,grossSavedTotal:500,repayTotal:500,summaryCostTotal:12,byProtocol:[]},sessions:[{id:'<unsafe/id>',title:'<img onerror=bad()>',live:true,inputTokens:1000,cachedTokens:0,cacheHitPct:0,contextBest:{tokens:333,kind:'usage'},netSaved:-12}]};
compressionCall.resolve(compressionData);await tick();
assert.equal(get('compression-content').hidden,false);
assert.equal(get('compression-metrics').children.length,8);
assert.equal(get('compression-metrics').children[4].children[1].textContent,'0.0%');
assert.equal(get('compression-metrics').children[7].children[1].textContent,'—');
assert.equal(get('compression-sessions').children[0].children[0].children[0].textContent,'<img onerror=bad()>');
assert.equal(get('compression-sessions').children[0].children[2].textContent,'333');
assert.equal(get('compression-next').disabled,true);
assert.equal(sandbox.api.quantity(null),'—');
assert.equal(sandbox.api.quantity(0),'0');
assert.equal(sandbox.api.quantity(-1200),'-1.2K');
const failingCompression=sandbox.api.loadCompression();shiftNamed('billion/dashboard?page=1&q=').reject(Error('offline'));await failingCompression;
assert.equal(get('compression-content').hidden,false);
assert.match(get('compression-error').textContent,/保留上次/);
const detail=sandbox.api.loadCompressionDetail('<unsafe/id>');
shiftNamed('billion/dashboard?session=%3Cunsafe%2Fid%3E').resolve({available:true,session:compressionData.sessions[0]});await detail;
assert.equal(get('compression-detail-title').textContent,'<img onerror=bad()>');
assert.equal(get('compression-detail-metrics').children.length,14);
const refreshedOverview=sandbox.api.loadCompression();
shiftNamed('billion/dashboard?page=1&q=').resolve(compressionData);await tick();
const refreshedDetail=shiftNamed('billion/dashboard?session=%3Cunsafe%2Fid%3E');
assert.ok(refreshedDetail,'Visible session detail refreshes with the overview');
refreshedDetail.resolve({available:true,checked_at:200,session:{...compressionData.sessions[0],title:'updated detail'}});await refreshedOverview;
assert.equal(get('compression-detail-title').textContent,'updated detail');
assert.match(get('compression-detail-state').textContent,/详情采集于/);
const lateDetail=sandbox.api.loadCompressionDetail('late');
get('close-compression-detail').click();
shiftNamed('billion/dashboard?session=late').resolve({available:true,session:{title:'stale'}});await lateDetail;
assert.equal(get('compression-detail').hidden,true);
assert.notEqual(get('compression-detail-title').textContent,'stale');
console.log('Compression dashboard behavior passed.');

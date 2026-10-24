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
const tabs=['overview','balances','settings'].map(name=>{const e=get('tab-'+name);e.dataset.tab=name;return e;});
const calls=[];const waiting=[];
const apiGet=(name)=>{calls.push(name);return new Promise((resolve,reject)=>waiting.push({name,resolve,reject}));};
const posts=[];
const apiPost=(name,body)=>{calls.push(name);posts.push(body);return new Promise((resolve,reject)=>waiting.push({name,resolve,reject}));};
const sandbox={document:{getElementById:get,createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),querySelectorAll:()=>tabs},window:{AstrBotPluginPage:{ready:async()=>{},apiGet,apiPost},addEventListener(){}},console:{warn(){}},Date,Set};
vm.createContext(sandbox);
// The host appends its bridge after the view's scripts. Simulate parser order.
const bridge = sandbox.window.AstrBotPluginPage;
delete sandbox.window.AstrBotPluginPage;
let resolveReady;
bridge.ready = () => new Promise(resolve => { resolveReady = resolve; });
const source = fs.readFileSync('views/status/app.js','utf8')+'\nthis.api={state,loadStatus,loadBalances,selectTab,loadSettings,saveSettings};';
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
  {name:'分段回复',fields:[
    {key:'segment_reply.enabled',label:'启用分段回复',type:'bool',value:false},
    {key:'segment_reply.delay_type',label:'发送间隔类型',type:'enum',value:'random',options:['fixed','random','smart']},
    {key:'segment_reply.max_length',label:'常规最大字数',type:'int',value:50,min:1,max:2000,step:1},
    {key:'segment_reply.random_delay_range',label:'随机间隔范围（秒）',type:'pair',value:[1,3],min:0,max:60,step:0.5},
  ]},
]};
const settingsLoad=waiting.shift();assert.equal(settingsLoad.name,'settings');settingsLoad.resolve(groupsPayload);await tick();
assert.equal(sandbox.api.state.settingsLoaded,true);
assert.equal(get('settings-groups').children.length,1);
assert.equal(get('save-settings').disabled,true); // 未修改时不可保存
sandbox.api.state.settingsDirty=true;
const saving=sandbox.api.saveSettings();
const saveCall=shiftNamed('settings/save');
assert.deepEqual(JSON.parse(JSON.stringify(posts[0])),{revision:'rev-1',values:{"segment_reply.enabled":false,"segment_reply.delay_type":'random',"segment_reply.max_length":50,"segment_reply.random_delay_range":[1,3]}});
saveCall.resolve({revision:'rev-2',message:'ok'});await saving;
assert.equal(sandbox.api.state.settingsRevision,'rev-2');
assert.equal(sandbox.api.state.settingsDirty,false);
assert.deepEqual(calls[calls.length-1],'status'); // 保存后刷新运行概览
shiftNamed('status').resolve(status);await tick(); // 排空刷新请求
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

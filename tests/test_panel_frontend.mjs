import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
class Element {
  constructor(tag='div') { this.tagName=tag; this.children=[];this.hidden=false;this.disabled=false;this.dataset={};this.attributes={};this.textContent=''; }
  set innerHTML(_) { throw Error('Unsafe HTML insertion'); }
  setAttribute(k,v) { this.attributes[k]=v; }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
  addEventListener() {}
  focus() {}
  querySelector() { return this.span ??= new Element('span'); }
}
const elements=new Map();
const get=(id)=>{ if(!elements.has(id))elements.set(id,new Element());return elements.get(id); };
const tabs=['overview','balances'].map(name=>{const e=get('tab-'+name);e.dataset.tab=name;return e;});
const calls=[];const waiting=[];
const apiGet=(name)=>{calls.push(name);return new Promise((resolve,reject)=>waiting.push({name,resolve,reject}));};
const sandbox={document:{getElementById:get,createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),querySelectorAll:()=>tabs},window:{AstrBotPluginPage:{ready:async()=>{},apiGet}},console:{warn(){}},Date,Set};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('views/status/app.js','utf8')+'\nthis.api={state,loadStatus,loadBalances,selectTab};',sandbox);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const status={plugin:{version:'1.49.0'},features:[{id:'jev',name:'<img src=x onerror=bad()>',enabled:true,detail:'configured'}],notices:[],balance_configured:true};
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
console.log('Panel behavior passed: manual queries, XSS-safe text, duplicate suppression, stale-result rejection, disabled-config reset, error retention, tabs.');

// Exercise the import state machine: stale previews and unrelated edits matter.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const ui=path.join(__dirname,'../ui'),listeners={},controls={};
function control(id){return controls[id]??=( {value:'',disabled:false,files:[],classList:{remove(){},add(){}},style:{},contentWindow:{},addEventListener(type,fn){this[type]=fn;},removeAttribute(){},replaceChildren(){}} );}
const ctx={$:control,busy:false,info:{models:[{id:'USER_Demo'}]},document:{querySelectorAll(){return[];}},window:{addEventListener(type,fn){listeners[type]=fn;}},location:{origin:'http://localhost'},lock(value){ctx.busy=value;},updateImportTarget(){},importFootprint(){return ctx.target;},target:'BGA96',readBase64:async()=>({data:'demo'}),parseObject:()=>JSON.parse(control('mapping').value),refreshModelLibrary:async()=>{},refreshReview:async()=>{},Number,Error};
let commits=[];
ctx.api=async(route,data)=>{
  if(route==='/api/models/import')return{draft:'draft',model:'USER_New',preview:'/draft',dimensions_mm:[3,2,1],solids:1};
  if(route==='/api/models/commit'){commits.push(data);return{model:'USER_New',footprint:data.footprint,library:'test'};}
  throw Error(route);
};
vm.createContext(ctx);
const matching=fs.readFileSync(path.join(ui,'matching.js'),'utf8');
vm.runInContext(matching.slice(matching.indexOf('function projectBinding('),matching.indexOf('async function refreshModelLibrary(')),ctx);
vm.runInContext(fs.readFileSync(path.join(ui,'models.js'),'utf8'),ctx);
async function ready(){await control('prepareModel').onclick();listeners.message({origin:'http://localhost',source:control('modelPreview').contentWindow,data:{type:'pcba-model-preview-ready',model:'USER_New'}});}
(async()=>{
  control('customModelFile').files=[{name:'Demo.step',size:100}];control('customModelFile').change();
  assert.equal(control('customModelName').value,'Demo_2');
  control('mapping').value=JSON.stringify({'Other':'unsaved','BGA-96':'old'});
  await ready();assert.equal(control('commitModel').disabled,false);
  control('customRz').input();assert.equal(control('commitModel').disabled,true);
  await control('commitModel').onclick();assert.equal(commits.length,0);
  await ready();await control('commitModel').onclick();
  assert.deepEqual(JSON.parse(control('mapping').value),{'Other':'unsaved','BGA96':'USER_New'});
  ctx.target=null;await ready();await control('commitModel').onclick();assert.equal(commits.at(-1).footprint,null);
  assert.equal(JSON.parse(control('mapping').value).Other,'unsaved');
  console.log('PASS: auto unique name, invalidated draft blocked, one-target binding preserves edits, library-only save');
})().catch(error=>{console.error(error);process.exitCode=1;});

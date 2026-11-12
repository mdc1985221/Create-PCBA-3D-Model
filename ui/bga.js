'use strict';
const $=id=>document.getElementById(id),job=new URLSearchParams(location.search).get('id');
let token='',draft=null,inference=null,working=false,ready=false,model='';
async function api(path,body){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-PCBA-Token':token},body:JSON.stringify({id:job,...body})});const data=await response.json();if(!response.ok)throw Error(data.error||'请求失败');return data;}
function autoPin(){return inference?.pin1?.status==='detected'&&$('a1').value===inference.pin1.corner;}
function pinConfirmed(){return $('a1').value==='unknown'||$('a1_confirmed').checked||autoPin();}
function buttons(){for(const id of ['ref','generate','width','depth','height','ball_diameter','a1','marking','pin1_style','a1_confirmed','refresh'])$(id).disabled=working;$('generate').disabled=working||!$('ref').value||!pinConfirmed();$('save').disabled=working||!draft||!ready||!pinConfirmed();}
function invalidate(){draft=null;ready=false;model='';$('save').disabled=true;$('preview').removeAttribute('src');$('saved').textContent='';}
function draw(data){
  const svg=$('pads');svg.replaceChildren();const points=data.points,w=Number($('width').value),h=Number($('depth').value),span=Math.max(data.reference.span,Math.max(w,h)*1.25),scale=270/span;
  function add(tag,attributes){const el=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attributes))el.setAttribute(k,v);svg.append(el);}
  if($('showBoard').checked&&data.reference.image){const size=data.reference.span*scale;add('image',{href:data.reference.image,x:200-size/2,y:160-size/2,width:size,height:size});}
  add('rect',{x:200-w/2*scale,y:160-h/2*scale,width:w*scale,height:h*scale,fill:'none',stroke:'#ffbf64','stroke-width':1.5});
  if($('showPads').checked)for(const [x,y]of points)add('circle',{cx:200+x*scale,cy:160-y*scale,r:Math.max(1.5,data.pad_diameter/2*scale),fill:'#83db9d','fill-opacity':.75});
  const corner=$('a1').value;
  if(corner!=='unknown'){
    const x=200+(corner.endsWith('l')?-1:1)*w/2*scale,y=160+(corner.startsWith('t')?-1:1)*h/2*scale;
    add('circle',{cx:x,cy:y,r:9,fill:'none',stroke:'#ff8d67','stroke-width':3});
    const label=document.createElementNS('http://www.w3.org/2000/svg','text');label.setAttribute('x',x+(corner.endsWith('l')?-5:5));label.setAttribute('y',y+(corner.startsWith('t')?-13:24));label.setAttribute('fill','#ffba8c');label.setAttribute('text-anchor',corner.endsWith('l')?'end':'start');label.textContent='A1';svg.append(label);
  }
  const angle=data.rotation*Math.PI/180,dx=data.xy[0]-data.center[0],dy=data.xy[1]-data.center[1],x=Math.cos(angle)*dx+Math.sin(angle)*dy,y=(-Math.sin(angle)*dx+Math.cos(angle)*dy)*(data.side==='top'?1:-1),cx=200+x*scale,cy=160-y*scale;
  add('path',{d:`M${cx-7},${cy}h14 M${cx},${cy-7}v14`,stroke:'#65d9ff','stroke-width':2});
}
function pinHint(){const corner=$('a1').value;if(autoPin()&&!$('a1_confirmed').checked){$('pin1_hint').textContent=inference.pin1.note+' 顶面将绘制下沉标识；自动识别不等同于人工确认。';return;}$('pin1_hint').textContent=corner==='unknown'?'未识别第一脚：可先生成无标识模型，或对照制板丝印手动选择。':$('a1_confirmed').checked?'已由你确认：该角位将在芯片顶面绘制下沉标识，球阵列保持原方向。':'已选择角位，请核对板面圆点、三角或缺口，并勾选确认后生成。';}
async function act(fn){if(working)return;working=true;buttons();try{await fn();}catch(error){$('status').textContent=error.message;}finally{working=false;buttons();}}
$('ref').onchange=()=>act(async()=>{invalidate();inference=null;$('pads').replaceChildren();$('facts').textContent='';$('status').textContent='识别实际焊盘阵列…';inference=await api('/api/bga/infer',{ref:$('ref').value});for(const key of ['width','depth','height','ball_diameter'])$(key).value=inference[key];$('a1').value=inference.pin1?.corner||'unknown';$('pin1_style').value=inference.pin1?.style||'dot';$('a1_confirmed').checked=false;pinHint();$('marking').value=inference.marking;$('description').textContent='BOM 描述：'+inference.description;draw(inference);$('facts').textContent=`${inference.ref} · ${inference.pkg}\n${inference.count} 个焊盘 · X/Y 主间距 ${inference.pitch.join(' / ')} mm\n实际球位保留混合间距与缺球。\n数据来源：${inference.source}；${inference.side==='top'?'顶面':'底面'}器件，已转换为器件顶视。`;const mismatch=inference.expected_count&&inference.expected_count!==inference.count;$('status').textContent=mismatch?`识别 ${inference.count} 个，封装名标注 ${inference.expected_count} 个；请核对阵列，暂不允许保存。`:'已识别焊盘。点击“从焊盘生成”即可预览；尺寸可按规格书修正。';});
$('generate').onclick=()=>act(async()=>{if(!inference)throw Error('请先选择并识别器件');if(!pinConfirmed())throw Error('请先核对并确认第一脚方向');invalidate();if(inference.expected_count&&inference.expected_count!==inference.count)throw Error('球数与封装名不一致，请核对图层，暂不生成');const options={a1:$('a1').value,a1_confirmed:$('a1_confirmed').checked,pin1_style:$('pin1_style').value,marking:$('marking').value};for(const key of ['width','depth','height','ball_diameter'])options[key]=Number($(key).value);$('status').textContent='正在生成球阵列和本体，请稍候…';const result=await api('/api/bga/preview',{ref:$('ref').value,options});draft=result.draft;model=result.model;draw(inference);$('preview').src=result.preview;$('status').textContent=`已生成 ${inference.count} 球模型。黑色本体、下沉文字与顶面第一脚标识；外形尺寸与球径为估算；A1 ${options.a1==='unknown'?'未确认':options.a1_confirmed?'人工确认':'制板丝印自动识别'}。核对后可保存。`;});
for(const key of ['width','depth','height','ball_diameter','a1','marking','pin1_style','a1_confirmed'])$(key).addEventListener('input',()=>{invalidate();if(key==='a1')$('a1_confirmed').checked=false;pinHint();buttons();$('status').textContent='参数已修改，请重新生成预览。';if(inference)draw(inference);});
for(const key of ['showBoard','showPads'])$(key).onchange=()=>{if(inference)draw(inference);};
window.addEventListener('message',event=>{if(event.origin===location.origin&&event.source===$('preview').contentWindow&&event.data?.type==='pcba-model-preview-ready'&&event.data.model===model){ready=true;buttons();}});
$('save').onclick=()=>act(async()=>{if(!draft||!ready)return;const result=await api('/api/bga/save',{draft});draft=null;ready=false;$('saved').textContent=`已保存 ${result.model}，绑定 ${result.footprint}。点击“应用绑定并刷新 3D”即可显示。`;$('status').textContent='绑定已保存。下一步：点击“应用绑定并刷新 3D”。';});
$('refresh').onclick=()=>act(async()=>{
  const channel=new BroadcastChannel('pcba-project-'+job),nonce=String(Date.now());let dirty=0;
  channel.onmessage=event=>{if(event.data?.type==='dirty-result'&&event.data.nonce===nonce)dirty+=Number(event.data.dirty)||0;};channel.postMessage({type:'check-dirty',nonce});await new Promise(resolve=>setTimeout(resolve,250));channel.close();
  if(dirty)throw Error('整板预览里还有未保存的校准，请先保存或撤销，再刷新。');
  $('status').textContent='正在应用最新绑定并更新整板…';const result=await api('/api/project/refresh',{});
  const text=result.changed?`已刷新：新增 ${result.added.length} 个器件，更新 ${result.replaced.length} 个；还需处理 ${result.pending} 个。`:`当前已是最新绑定；还需处理 ${result.pending} 个。`;
  sessionStorage.setItem('pcba-refresh-'+job,JSON.stringify({text}));location.href=result.preview+'?updated='+Date.now()+'&focus='+encodeURIComponent($('ref').value);
});
$('back').onclick=()=>location.href='/project/'+encodeURIComponent(job)+'/index.html';
(async()=>{try{token=(await(await fetch('/api/info')).json()).token;const data=await api('/api/bga/list',{});for(const item of data.items)$('ref').add(new Option(`${item.ref} · ${item.pkg}${item.missing?' · 缺模型':''}`,item.ref));if(!data.items.length){$('status').textContent='当前工程没有 BGA 器件。';return;}const first=data.items.find(q=>q.missing)||data.items[0];$('ref').value=first.ref;buttons();await $('ref').onchange();}catch(error){$('status').textContent=error.message;}})();

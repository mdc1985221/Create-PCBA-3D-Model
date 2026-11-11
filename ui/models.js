'use strict';
(()=>{
  let draft=null,working=false,previewReady=false,draftModel='';
  const actionIds=['prepareModel','prepareParametric','commitModel','generateAiModel','searchAiModel','loadAiSettings','saveAiSettings','clearAiKeys','testAiConnection','readDatasheet'];
  function status(text,error=false){$('modelStatus').classList.remove('hidden');$('modelStatus').textContent=text;$('modelStatus').style.color=error?'#a12d27':'';}
  async function act(fn){
    if(working||busy){status('正在处理，请等待当前任务完成。',true);return;}
    working=true;lock(true);actionIds.forEach(id=>$(id).disabled=true);document.querySelectorAll('#modelManager input,#modelManager select,#modelManager textarea').forEach(control=>control.disabled=true);
    try{await fn();}catch(error){status(error.message,true);}
    finally{working=false;lock(false);actionIds.forEach(id=>$(id).disabled=false);document.querySelectorAll('#modelManager input,#modelManager select,#modelManager textarea').forEach(control=>control.disabled=false);updateImportTarget();$('commitModel').disabled=!draft||!previewReady;}
  }
  function newDraft(result){
    draft=result.draft;draftModel=result.model;previewReady=false;$('modelPreview').src=result.preview;$('modelPreview').classList.remove('hidden');
    status(`待保存模型：${result.model}\n长 X × 宽 Y × 高 Z：${result.dimensions_mm.map(v=>v.toFixed(3)).join(' × ')} mm；${result.solids} 个实体\n${(result.notes||[]).join('\n')}\n核对预览、方向和贴板原点后确认保存。`);
    $('modelManager').open=true;
  }
  function invalidate(){if(!draft)return;draft=null;previewReady=false;draftModel='';$('commitModel').disabled=true;$('modelPreview').removeAttribute('src');$('modelPreview').classList.add('hidden');status('模型文件或参数已变化，请重新预览后保存。');}
  for(const id of ['customModelName','customMaterial','customOrigin','customRx','customRy','customRz','customOx','customOy','customOz','modelSpec'])$(id).addEventListener('input',invalidate);
  $('customModelFile').addEventListener('change',()=>{
    invalidate();const file=$('customModelFile').files[0];if(!file)return;
    let base=file.name.replace(/\.(step|stp)$/i,'').replace(/[^A-Za-z0-9_-]+/g,'_').replace(/^USER_/i,'').slice(0,54)||'ImportedModel',name=base,index=2;
    while(info?.models.some(m=>m.id==='USER_'+name))name=base+'_'+index++;
    $('customModelName').value=name;
  });
  window.addEventListener('message',event=>{
    if(event.origin===location.origin&&event.source===$('modelPreview').contentWindow&&event.data?.type==='pcba-model-preview-ready'&&event.data.model===draftModel&&draft){previewReady=true;$('commitModel').disabled=working;}
  });
  $('prepareModel').onclick=()=>act(async()=>{
    invalidate();importFootprint();
    const file=$('customModelFile').files[0];if(!file)throw Error('请先选择 STEP / STP 模型文件。');
    if(!/\.(step|stp)$/i.test(file.name)||file.size>30*1024*1024)throw Error('请选择不超过 30 MB 的 STEP / STP 文件。');
    status('读取并网格化模型，请稍候…');
    const uploaded=await readBase64({file,path:file.name,encoded:null});
    const options={material:$('customMaterial').value,origin:$('customOrigin').value,
      rotation:['customRx','customRy','customRz'].map(id=>Number($(id).value)),
      origin_xyz:['customOx','customOy','customOz'].map(id=>Number($(id).value))};
    newDraft(await api('/api/models/import',{name:$('customModelName').value.trim(),filename:file.name,data:uploaded.data,options}));
  });
  $('prepareParametric').onclick=()=>act(async()=>{
    invalidate();importFootprint();
    let spec;try{spec=JSON.parse($('modelSpec').value);}catch{throw Error('参数模型 JSON 格式错误。');}
    status('生成参数模型与预览…');newDraft(await api('/api/models/parametric',{name:$('customModelName').value.trim(),spec}));
  });
  $('commitModel').onclick=()=>act(async()=>{
    if(!draft||!previewReady)throw Error('请先等待模型预览完成。');
    const footprint=importFootprint();const mapping=footprint?projectBinding(parseObject('mapping','封装映射'),footprint,draftModel):null;
    const result=await api('/api/models/commit',{draft,footprint});
    if(mapping)$('mapping').value=JSON.stringify(mapping,null,2);await refreshModelLibrary();await refreshReview();draft=null;previewReady=false;
    status(`已加入本地模型库：${result.model}\n${result.footprint?'封装 '+result.footprint+' 已绑定。重新生成 PCBA 后生效。':'仅保存到库，未绑定任何封装。可从“选模型”窗口绑定。'}\n${result.library}`);
  });
  function showSettings(cfg){
    $('aiEnabled').checked=cfg.enabled;$('aiBase').value=cfg.base_url;$('aiModel').value=cfg.model;
    $('aiMode').value=cfg.mode;$('searchBase').value=cfg.search_url||'';$('aiKey').value='';$('searchKey').value='';
    $('aiStatus').textContent=`${cfg.enabled?'AI 已启用':'AI 已关闭'}；API 密钥${cfg.has_key?'已保存':'未设置'}，搜索密钥${cfg.has_search_key?'已保存':'未设置'}。`;
  }
  $('loadAiSettings').onclick=()=>act(async()=>showSettings(await api('/api/ai/settings')));
  $('saveAiSettings').onclick=()=>act(async()=>showSettings(await api('/api/ai/settings',{
    enabled:$('aiEnabled').checked,base_url:$('aiBase').value,model:$('aiModel').value,mode:$('aiMode').value,
    api_key:$('aiKey').value,search_url:$('searchBase').value,search_key:$('searchKey').value})));
  $('clearAiKeys').onclick=()=>act(async()=>showSettings(await api('/api/ai/settings',{enabled:false,clear_api_key:true,clear_search_key:true})));
  $('testAiConnection').onclick=()=>act(async()=>{
    $('aiStatus').textContent='正在测试已保存的 API 设置…';
    try{const result=await api('/api/ai/test',{});$('aiStatus').textContent=result.message+'\n模型：'+result.model+'；回复：'+result.reply;}
    catch(error){$('aiStatus').textContent=error.message+'。请先保存正确的接口地址、模型和密钥。';throw error;}
  });
  $('readDatasheet').onclick=()=>act(async()=>{
    const file=$('datasheetFile').files[0];if(!file)throw Error('请先选择规格书 PDF。');
    if(!/\.pdf$/i.test(file.name)||file.size>20*1024*1024)throw Error('请选择不超过 20 MB 的 PDF。');
    $('datasheetStatus').textContent='正在本地提取文字，不调用 AI…';
    const upload=await readBase64({file,path:file.name,encoded:null});
    let result;
    try{result=await api('/api/ai/datasheet',{filename:file.name,data:upload.data,pages:$('datasheetPages').value});}
    catch(error){$('datasheetStatus').textContent=error.message;throw error;}
    const text=[$('aiDescription').value.trim(),'规格书：'+result.filename,result.text].filter(Boolean).join('\n\n');
    if(text.length>12000)throw Error('合并后的说明超过 12000 字，请减少页码或先精简下方说明。');
    $('aiDescription').value=text;
    $('datasheetStatus').textContent=`共 ${result.pages} 页，已读取 ${result.selected_pages.join(', ')} 页。${result.truncated?'文字过长已截断，请减少页码。':''}\n${result.note}`;
  });
  $('generateAiModel').onclick=()=>act(async()=>{
    status('正在调用配置的 AI 接口，仅发送当前器件说明…');
    const result=await api('/api/ai/generate',{description:$('aiDescription').value});
    invalidate();$('modelSpec').value=JSON.stringify(result.spec,null,2);$('modelManager').open=true;
    status('AI 参数草稿已写入上方 JSON。请核对尺寸，填写新模型名称，再点击“预览参数模型”；确认后保存入库。');
  });
  $('searchAiModel').onclick=()=>act(async()=>{
    status('正在通过配置的联网接口检索模型来源…');
    const result=await api('/api/ai/search',{query:$('aiDescription').value});
    const box=$('aiResults');box.replaceChildren();box.classList.remove('hidden');
    const text=document.createElement('div');text.textContent=result.text||'';box.append(text);
    for(const item of result.links||[]){
      const line=document.createElement('p'),link=document.createElement('a'),snippet=document.createElement('div');
      link.href=item.url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=item.title||item.url;
      snippet.textContent=item.snippet||'';line.append(link,snippet);box.append(line);
    }
    status('检索完成。请核对型号和来源，下载 STEP 后从手动模型区导入。');
  });
})();

'use strict';
(()=>{
  let draft=null,working=false;
  const actionIds=['prepareModel','prepareParametric','commitModel','generateAiModel','searchAiModel','loadAiSettings','saveAiSettings','clearAiKeys','testAiConnection','readDatasheet'];
  function status(text,error=false){$('modelStatus').classList.remove('hidden');$('modelStatus').textContent=text;$('modelStatus').style.color=error?'#a12d27':'';}
  async function act(fn){
    if(working||busy){status('正在处理，请等待当前任务完成。',true);return;}
    working=true;lock(true);actionIds.forEach(id=>$(id).disabled=true);
    try{await fn();}catch(error){status(error.message,true);}
    finally{working=false;lock(false);actionIds.forEach(id=>$(id).disabled=false);$('commitModel').disabled=!draft;}
  }
  function newDraft(result){
    draft=result.draft;$('modelPreview').src=result.preview;$('modelPreview').classList.remove('hidden');
    status(`待保存模型：${result.model}\n尺寸 XYZ：${result.dimensions_mm.map(v=>v.toFixed(3)).join(' × ')} mm；${result.solids} 个实体\n${(result.notes||[]).join('\n')}\n核对预览、方向和贴板原点后，点击“确认保存模型并绑定封装”。`);
    $('modelManager').open=true;
  }
  $('prepareModel').onclick=()=>act(async()=>{
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
    let spec;try{spec=JSON.parse($('modelSpec').value);}catch{throw Error('参数模型 JSON 格式错误。');}
    status('生成参数模型与预览…');newDraft(await api('/api/models/parametric',{name:$('customModelName').value.trim(),spec}));
  });
  $('commitModel').onclick=()=>act(async()=>{
    if(!draft)throw Error('请先预览一个模型。');
    const footprint=$('customFootprint').value.trim();if(!footprint)throw Error('请填写生产坐标表中的封装名。');
    const result=await api('/api/models/commit',{draft,footprint});
    info=await api('/api/info');$('mapping').value=JSON.stringify({...parseObject('mapping','封装映射'),...info.user_aliases},null,2);
    $('libraryCount').textContent='('+info.models.length+' 个模型)';renderModels();void refreshReview();draft=null;
    status(`已加入本地模型库：${result.model}\n封装 ${result.footprint} 已绑定。重新生成 PCBA 即可使用；下次启动也能复用。\n${result.library}`);
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
    $('modelSpec').value=JSON.stringify(result.spec,null,2);$('modelManager').open=true;
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

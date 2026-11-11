'use strict';
let chosenModel='',chosenReady=false,choosing=false;
const footprintGroups=()=>reviewData?.footprints||[];
function footprintContext(pkg){const group=footprintGroups().find(g=>g.pkg===pkg);return group?`${group.refs.length} 个器件 · ${group.refs.join(', ')}\n${(group.examples||[]).join('；')}`:'导入生产资料后，可从真实封装列表选择绑定目标。';}
function fillTargets(id,value){const select=$(id);select.replaceChildren(new Option('请选择完整封装（仅看模型可不选）',''));for(const g of footprintGroups())if(g.pkg)select.add(new Option(`${g.pkg} ×${g.refs.length}`,g.pkg));select.value=footprintGroups().some(g=>g.pkg===value)?value:'';}
function syncModelTargets(){
  fillTargets('customTarget',$('customTarget').value);fillTargets('chooserTarget',$('chooserTarget').value);
  $('manualFootprintField').classList.toggle('hidden',!!reviewData);
  updateImportTarget();if($('modelChooser').open)updateChooserTarget();
}
function updateImportTarget(){const only=$('libraryOnly').checked;const target=$('customTarget').value;$('customTarget').disabled=only||busy;$('customFootprint').disabled=only||busy;$('customTargetContext').textContent=only?'模型先入库；以后可从“选模型”窗口绑定。':footprintContext(target);$('commitModel').textContent=only?'确认保存到模型库':'确认保存模型并绑定封装';}
function importFootprint(){if($('libraryOnly').checked)return null;const value=(reviewData?$('customTarget').value:$('customFootprint').value).trim();if(!value)throw Error('请从绑定目标中选择生产资料的完整封装名；也可以勾选“只保存到模型库”。');if(reviewData&&!footprintGroups().some(g=>g.pkg===value))throw Error('封装列表已变化，请重新选择绑定目标。');return value;}
function importForFootprint(pkg){if(busy)return;$('modelChooser').close();$('modelManager').open=true;fillTargets('customTarget',pkg);$('libraryOnly').checked=!pkg;if(!reviewData)$('customFootprint').value=pkg;updateImportTarget();$('modelManager').scrollIntoView({behavior:'smooth',block:'start'});}
function dimensions(model){return model.dimensions_mm?.length?model.dimensions_mm.map(v=>v.toFixed(3)).join(' × ')+' mm（X × Y × 高）':'尺寸待核对';}
function chooserEnabled(){$('applyModel').disabled=busy||choosing||!chosenModel||!chosenReady||!$('chooserTarget').value;}
function updateChooserTarget(){const pkg=$('chooserTarget').value;$('chooserContext').textContent=pkg?footprintContext(pkg):'当前为模型浏览模式。选择生产资料中的目标封装后，才能绑定。';chooserEnabled();}
function selectLibraryModel(id){
  chosenModel=id;chosenReady=false;const model=info.models.find(m=>m.id===id);if(!model)return;
  $('chooserDetails').textContent=`${model.id}\n${dimensions(model)}${model.source_file?' · 来源：'+model.source_file:''}`;
  $('chooserStatus').textContent='正在载入模型预览…';$('chooserPreview').src='/library-preview/'+encodeURIComponent(id)+'/index.html';renderChooserModels();chooserEnabled();
}
function renderChooserModels(){
  const root=$('chooserModels'),query=$('chooserSearch').value.toLowerCase().trim();root.replaceChildren();
  for(const model of info.models.filter(m=>JSON.stringify([m.id,m.aliases,m.source_file]).toLowerCase().includes(query))){
    const button=document.createElement('button'),name=document.createElement('strong'),size=document.createElement('div');button.type='button';button.role='option';button.setAttribute('aria-selected',String(model.id===chosenModel));name.textContent=model.id;size.textContent=dimensions(model);size.className='small muted';button.append(name,size);button.disabled=busy||choosing;button.onclick=()=>selectLibraryModel(model.id);root.append(button);
  }
  if(!root.children.length)root.textContent='没有找到模型，可清空搜索或导入新的 STEP。';
}
function openModelChooser(pkg='',id=''){
  if(busy||!info)return;fillTargets('chooserTarget',pkg);$('chooserSearch').value='';chosenModel='';chosenReady=false;$('chooserDetails').textContent='选择左侧模型，查看 3D 和尺寸。';$('chooserPreview').removeAttribute('src');$('chooserStatus').textContent='';updateChooserTarget();renderChooserModels();if(!$('modelChooser').open)$('modelChooser').showModal();if(id)selectLibraryModel(id);
}
function projectBinding(mapping,pkg,model){
  // Remove equivalent spellings only for this target. Keep other unsaved edits.
  const norm=s=>s.toLowerCase().replace(/[\s_()（）\[\].-]/g,'');
  for(const key of Object.keys(mapping))if(norm(key)===norm(pkg))delete mapping[key];mapping[pkg]=model;return mapping;
}
async function refreshModelLibrary(){info=await api('/api/info');$('libraryCount').textContent='('+info.models.length+' 个模型)';renderModels();}
$('chooserSearch').oninput=renderChooserModels;
$('chooserTarget').onchange=updateChooserTarget;
$('customTarget').onchange=updateImportTarget;
$('libraryOnly').onchange=updateImportTarget;
$('closeChooser').onclick=()=>{if(!choosing)$('modelChooser').close();};
$('modelChooser').addEventListener('cancel',event=>{if(choosing)event.preventDefault();});
$('importForTarget').onclick=()=>importForFootprint($('chooserTarget').value);
window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==$('chooserPreview').contentWindow||event.data?.type!=='pcba-model-preview-ready'||event.data.model!==chosenModel)return;
  chosenReady=true;$('chooserStatus').textContent='预览已就绪。请核对模型与目标封装是否一致。';chooserEnabled();
});
$('applyModel').onclick=async()=>{
  if(busy||choosing||!chosenReady)return;
  const pkg=$('chooserTarget').value,id=chosenModel;if(!pkg||!id)return;
  choosing=true;lock(true);chooserEnabled();renderChooserModels();$('chooserTarget').disabled=true;$('importForTarget').disabled=true;
  try{
    const mapping=projectBinding(parseObject('mapping','封装映射'),pkg,id);
    if($('rememberBinding').checked)await api('/api/models/bind',{footprint:pkg,model:id});
    $('mapping').value=JSON.stringify(mapping,null,2);await refreshModelLibrary();await refreshReview();$('modelChooser').close();message(`已绑定 ${pkg} → ${id}。重新生成 3D 预览后生效。`);
  }catch(error){$('chooserStatus').textContent=error.message;}
  finally{choosing=false;lock(false);$('chooserTarget').disabled=false;$('importForTarget').disabled=false;chooserEnabled();renderChooserModels();}
};

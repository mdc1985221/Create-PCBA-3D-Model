'use strict';
const excludedRows={bom:new Set(),xy:new Set()};
let reviewData=null,reviewVersion=0;
function resetReview(){reviewVersion++;reviewData=null;excludedRows.bom.clear();excludedRows.xy.clear();for(const id of ['bomReview','xyReview','footprintReview'])$(id).replaceChildren();$('reviewStatus').textContent='';$('footprintStatus').textContent='导入资料后显示封装匹配情况。';}
async function removeSelectedFile(index){if(busy)return;selected.splice(index,1);await chooseFiles([]);}
async function refreshReview(){
  const version=++reviewVersion;
  if(!info||!selected.length||!$('xy').value)return;
  $('reviewStatus').textContent='正在核对 BOM、坐标和模型映射…';
  try{
    const files=await Promise.all(selected.map(readBase64));
    const result=await api('/api/review',{files,config:{xy:$('xy').value,bom:$('bom').value,mapping:parseObject('mapping','封装映射'),exclude_bom_rows:[...excludedRows.bom],exclude_xy_rows:[...excludedRows.xy]}});
    if(version!==reviewVersion)return;
    reviewData=result;renderReview();
  }catch(error){if(version===reviewVersion)$('reviewStatus').textContent='核对失败：'+error.message;}
}
function renderReview(){
  if(!reviewData)return;
  const missing=new Set(reviewData.missing_coordinates),query=$('reviewFilter').value.trim().toLowerCase();
  let duplicates=0,quantities=0;
  for(const role of ['bom','xy']){
    const root=$(role+'Review');root.replaceChildren();
    const table=document.createElement('table');table.className='review-table';
    const head=document.createElement('tr');for(const name of ['源文件行 / BOM 序号 / 状态','位号 / 物料','操作']){const th=document.createElement('th');th.textContent=name;head.append(th);}table.append(head);
    for(const row of reviewData[role+'_rows']){
      if(row.duplicates.length&&!row.excluded)duplicates++;
      if(!row.excluded&&(row.issues||[]).some(issue=>issue.includes('不符')||issue.includes('无效用量')))quantities++;
      if(query&&!JSON.stringify([row.refs,row.val,row.pkg,row.desc,row.name]).toLowerCase().includes(query))continue;
      const tr=document.createElement('tr'),status=document.createElement('td'),body=document.createElement('td'),action=document.createElement('td'),button=document.createElement('button');
      if(row.excluded)tr.className='review-excluded';
      status.textContent=(row.location||'第 '+row.row+' 行')+(row.excluded?' · 已排除':!row.active?' · NC':'');
      const problems=[...(row.issues||[])];if(row.duplicates.length&&!problems.some(issue=>issue.includes('位号重复')))problems.push('重复位号：'+row.duplicates.join(', '));if(role==='bom'&&!row.excluded&&row.active){const refs=row.refs.filter(ref=>missing.has(ref));if(refs.length)problems.push('缺坐标：'+refs.join(', '));}
      body.textContent=row.refs.join(', ')+' · '+(row.name||row.val||row.pkg||'—');
      if(problems.length){const problem=document.createElement('div');problem.className='review-problem';problem.textContent=problems.join('；');body.append(problem);}
      button.textContent=row.excluded?'恢复此行':'排除此行';button.disabled=busy;
      button.setAttribute('aria-label',(row.excluded?'恢复':'排除')+' '+role.toUpperCase()+' '+(row.location||'第 '+row.row+' 行')+' '+row.refs.join(','));
      button.onclick=async()=>{if(busy)return;if(excludedRows[role].has(row.row))excludedRows[role].delete(row.row);else excludedRows[role].add(row.row);await refreshReview();};
      action.append(button);tr.append(status,body,action);table.append(tr);
    }
    if(!reviewData[role+'_rows'].length)root.textContent=role==='bom'?'未选择 BOM。':'没有坐标行。';else root.append(table);
  }
  $('reviewStatus').textContent='核对完成 · '+duplicates+' 行存在重复位号 · '+quantities+' 行用量异常 · 缺坐标 '+missing.size+' 个 · 已排除 BOM '+excludedRows.bom.size+' 行 / XY '+excludedRows.xy.size+' 行';
  if(duplicates||quantities)$('dataReview').open=true;
  $('footprintStatus').textContent='已匹配 '+reviewData.matched+' 个器件 · 待匹配 '+reviewData.unmatched+' 个 · 缺坐标 '+missing.size+' 个';
  const root=$('footprintReview');root.replaceChildren();
  const table=document.createElement('table');table.className='review-table';
  for(const group of reviewData.footprints){
    if(group.model&&!$('showMatched').checked)continue;
    const tr=document.createElement('tr'),label=document.createElement('td'),cell=document.createElement('td'),select=document.createElement('select');
    label.textContent=(group.pkg||'(空封装)')+' ×'+group.refs.length;label.title=group.refs.join(', ');
    select.setAttribute('aria-label','封装 '+(group.pkg||'(空封装)')+' 的模型');select.add(new Option('待匹配（暂不显示）',''));
    for(const model of info.models)select.add(new Option(model.id,model.id));select.value=group.model;select.disabled=busy;
    select.onchange=async()=>{if(busy)return;try{const mapping=parseObject('mapping','封装映射');if(select.value)mapping[group.pkg]=select.value;else delete mapping[group.pkg];$('mapping').value=JSON.stringify(mapping,null,2);await refreshReview();}catch(error){message(error.message,true);}};
    cell.append(select);tr.append(label,cell);table.append(tr);
  }
  if(!table.children.length)root.textContent=reviewData.unmatched?'暂无可显示封装。':'所有有坐标器件已匹配；勾选上方可查看已有映射。';else root.append(table);
}
$('reviewInputs').onclick=()=>refreshReview();
$('reviewFilter').oninput=renderReview;
$('showMatched').onchange=renderReview;
$('mapping').addEventListener('change',()=>refreshReview());
for(const role of ['xy','bom'])$(role).addEventListener('change',()=>{excludedRows[role].clear();void refreshReview();});
$('showMappingTemplate').onclick=()=>{const example={R0402:'R_0402','SOT23-3':'SOT-23','我的自定义封装':'MyPart_01'};$('mappingTemplate').textContent='左侧：坐标表封装名；右侧：下方模型库中的模型名。\n示例不会覆盖你填写的映射；自定义模型需先导入本机库。\n\n'+JSON.stringify(example,null,2);$('mappingTemplate').classList.toggle('hidden');};

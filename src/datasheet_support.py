"""Offline text extraction only. Uploaded PDFs are never executed or sent automatically."""
import io,re

def extract_pdf(filename,content,pages=''):
    from pypdf import PdfReader
    if not str(filename).lower().endswith('.pdf') or not 0<len(content)<=20*1024*1024:raise ValueError('请选择不超过 20 MB 的 PDF 规格书')
    try:reader=PdfReader(io.BytesIO(content),strict=False)
    except Exception:raise ValueError('PDF 无法读取，请重新导出或使用可复制文字的规格书') from None
    if reader.is_encrypted and not reader.decrypt(''):raise ValueError('PDF 已加密，请提供可读取版本')
    count=len(reader.pages)
    if not 0<count<=1200:raise ValueError('PDF 页数超出支持范围')
    selected=[]
    if not str(pages).strip():selected=list(range(min(count,10)))
    else:
        for item in re.split(r'[,，\s]+',str(pages).strip()):
            match=re.fullmatch(r'(\d+)(?:-(\d+))?',item)
            if not match:raise ValueError('页码格式如 1,3-5，最多选择 10 页')
            start=int(match[1]);end=int(match[2] or match[1])
            if not 1<=start<=end<=count:raise ValueError(f'页码需在 1–{count} 内')
            if end-start>=10:raise ValueError('每次最多读取 10 页，请优先选择封装尺寸图页')
            selected.extend(range(start-1,end))
        selected=list(dict.fromkeys(selected))
    if len(selected)>10:raise ValueError('每次最多读取 10 页，请优先选择封装尺寸图页')
    chunks=[];truncated=False;used=0
    for index in selected:
        try:text=reader.pages[index].extract_text() or ''
        except Exception:raise ValueError(f'第 {index+1} 页文字提取失败，请选择其他页或粘贴尺寸说明') from None
        chunk=f'【第 {index+1} 页】\n'+text
        if used+len(chunk)>11000:chunk=chunk[:max(0,11000-used)];truncated=True
        chunks.append(chunk);used+=len(chunk)
        if truncated:break
    text='\n\n'.join(chunks)
    if len(re.sub(r'【第 \d+ 页】|\s','',text))<20:raise ValueError('没有读到足够文字，可能是扫描图片。请粘贴规格书尺寸或提供可复制文字的 PDF；当前没有 OCR')
    return dict(filename=str(filename)[:200],pages=count,selected_pages=[i+1 for i in selected],text=text,truncated=truncated,note='文字已在本地提取，尚未发送 API。请核对尺寸图的单位、视图、引脚节距及原点；尺寸图中的数字不一定能正确对应结构。')

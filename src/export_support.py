"""Verified project STEP with optional user-selected destination."""
from pathlib import Path
import base64,json,os,shutil,subprocess,tempfile,time


def folder_path(value,home,assets):
    if value is None or value=='':return ''
    if not isinstance(value,str) or len(value)>2048 or any(ord(c)<32 for c in value):
        raise ValueError('STEP 保存目录应为本机文件夹路径')
    if not value.strip():return ''
    path=Path(value.strip()).expanduser()
    if not path.is_absolute():path=Path(home)/path
    path=path.resolve();assets=Path(assets).resolve()
    if path==assets or assets in path.parents:raise ValueError('STEP 保存目录不能放在工具的内置资源中')
    if path.exists() and not path.is_dir():raise ValueError('STEP 保存位置应为文件夹，不能填写文件名')
    return str(path)


def save_verified_step(project,source,selected=''):
    project,source=Path(project).resolve(),Path(source).resolve()
    if source.parent!=project or source.suffix.lower() not in {'.stp','.step'}:raise ValueError('STEP 原件必须位于当前工程目录')
    result=json.loads(source.with_suffix('.validation.json').read_text(encoding='utf8'))
    if not result.get('valid') or not result.get('read_back'):raise ValueError('STEP 尚未通过回读验证')
    target=source
    if selected and Path(selected).resolve()!=project:
        folder=Path(selected).resolve();folder.mkdir(parents=True,exist_ok=True);target=folder/source.name
        if target.exists() or target.with_suffix('.validation.json').exists():target=folder/(source.stem+'_'+str(time.time_ns())+source.suffix)
        reserved=[]
        try:
            for path in [target,target.with_suffix('.validation.json')]:
                with path.open('xb'):pass
                reserved.append(path)
            with tempfile.TemporaryDirectory(prefix='.pcba-step-',dir=folder) as temp:
                staged=Path(temp)/target.name;shutil.copy2(source,staged)
                proof=staged.with_suffix('.validation.json');proof.write_text(json.dumps({**result,'file':target.name},ensure_ascii=False,indent=2),encoding='utf8')
                staged.replace(target);proof.replace(target.with_suffix('.validation.json'))
        except Exception:
            for path in reserved:path.unlink(missing_ok=True)
            raise
    return dict(path=str(target),project_path=str(source),external=target!=source)


def choose_folder(initial=''):
    if os.name!='nt':raise ValueError('当前系统请直接输入 STEP 保存文件夹路径')
    script="""Add-Type -AssemblyName System.Windows.Forms
$pcbaDialog=New-Object System.Windows.Forms.FolderBrowserDialog
$pcbaDialog.Description='选择 STEP 保存文件夹'
$pcbaDialog.SelectedPath=$env:PCBA3D_INITIAL_FOLDER
try { if($pcbaDialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
[Console]::Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pcbaDialog.SelectedPath)))
} } finally { $pcbaDialog.Dispose() }
"""
    exe=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        run=subprocess.run([str(exe),'-NoProfile','-STA','-Command',script],env={**os.environ,'PCBA3D_INITIAL_FOLDER':initial},capture_output=True,timeout=300,creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as error:raise ValueError('文件夹选择已超时，请重试或直接输入路径') from error
    if run.returncode:raise ValueError('无法打开文件夹选择窗口，请直接输入路径')
    encoded=run.stdout.strip()
    return dict(cancelled=not bool(encoded),path=base64.b64decode(encoded,validate=True).decode('utf8') if encoded else '')

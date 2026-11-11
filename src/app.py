"""Standalone loopback UI for the reusable PCBA model generator."""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit
import webbrowser
from input_support import INPUT_EXTENSIONS, inspect_files, review_files

VERSION = "1.3.2"
MAX_JSON = 142 * 1024 * 1024
MAX_UPLOAD = 100 * 1024 * 1024
DEFAULT_PORT = 8234


def locations():
    if getattr(sys, "frozen", False):
        resources = Path(sys._MEIPASS)
        home = Path(sys.executable).resolve().parent
    else:
        resources = Path(__file__).resolve().parent.parent
        home = resources
    return resources / "assets", resources / "ui", home


def read_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")[:100]
    if not value:
        value = "PCBA"
    if value.upper() in {"CON", "PRN", "AUX", "NUL", *["COM" + str(i) for i in range(1, 10)], *["LPT" + str(i) for i in range(1, 10)]}:
        value = "PCBA_" + value
    return value


def decode_uploads(uploaded):
    if not isinstance(uploaded, list) or len(uploaded) > 500:
        raise ValueError("最多选择 500 个输入文件；可把 Gerber 打包为 ZIP")
    files, names, total = {}, {}, 0
    for entry in uploaded:
        if not isinstance(entry, dict):
            raise ValueError("输入文件格式错误")
        name = str(entry.get("name", ""))
        if name != Path(name).name or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or not name or name.endswith((".", " ")):
            raise ValueError("输入文件名包含路径")
        if safe_name(Path(name).stem) != Path(name).stem:
            raise ValueError("输入文件名不可使用系统保留名称：" + name)
        if Path(name).suffix.lower() not in INPUT_EXTENSIONS:
            raise ValueError("不支持的输入文件：" + name + "；文件夹请用“选择文件夹”，旧 XLS 支持真实 Excel 工作簿")
        try:
            data = base64.b64decode(entry.get("data", ""), validate=True)
        except (ValueError, TypeError, binascii.Error) as error:
            raise ValueError("输入文件编码错误：" + name) from error
        if not data:
            raise ValueError(name + " 是空文件；如果它是资料文件夹，请用“选择文件夹”或重新拖入文件夹")
        total += len(data)
        if total > MAX_UPLOAD:
            raise ValueError("输入文件总大小超过 100 MB")
        lower = name.lower()
        if lower in names:
            if files[names[lower]] != data:
                raise ValueError("输入资料存在不同内容的同名文件：" + name + "；请重命名后重试")
            continue
        names[lower] = name
        files[name] = data
    if not files:
        raise ValueError("没有可导入的文件。请选择生产资料文件夹，或选择 Gerber ZIP 和 XY 坐标表")
    return files


class Application:
    def __init__(self, assets, ui, home):
        self.assets, self.ui, self.home = Path(assets), Path(ui), Path(home)
        self.alias_path = self.home / "aliases.json"
        self.default_output = self.home / "工程"
        self.token = secrets.token_urlsafe(32)
        self.jobs = {}
        self.lock = threading.Lock()
        self.busy = False
        self.server = None

    def library(self):
        from user_models import read_catalog
        return {**read_json(self.assets / "library" / "models.json", {}), **read_catalog(self.home / "用户模型库")}

    def user_aliases(self):
        value = read_json(self.alias_path, {})
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
            raise ValueError("本机 aliases.json 应为封装名到模型名的 JSON 对象，请修复或移走该文件后重启")
        return value

    def info(self):
        library = self.library()
        builtins = read_json(self.assets / "aliases.json", {})
        user = self.user_aliases()
        return dict(version=VERSION, default_output=str(self.default_output), token=self.token,
                    models=[dict(id=k, parts=len(v.get("parts", []))) for k, v in sorted(library.items())],
                    aliases={**builtins, **user}, user_aliases=user, alias_path=str(self.alias_path),
                    sample_available=(self.assets / "sample" / "index.html").is_file(),
                    max_upload_mb=100)

    def model_operation(self, route, request):
        from user_models import import_step, stage_parametric, commit
        import ai_support
        if route == '/api/ai/settings':
            return ai_support.save_settings(self.home, request)
        with self.lock:
            if self.busy: raise ValueError('正在生成工程或处理模型，请等待完成')
            self.busy = True
        try:
            if route == '/api/models/import':
                try: content = base64.b64decode(request.get('data', ''), validate=True)
                except (ValueError, TypeError, binascii.Error): raise ValueError('模型文件编码无效')
                return import_step(self.home, request.get('name'), request.get('filename', ''), content, request.get('options', {}))
            if route == '/api/ai/datasheet':
                from datasheet_support import extract_pdf
                try:content=base64.b64decode(request.get('data',''),validate=True)
                except (ValueError,TypeError,binascii.Error):raise ValueError('PDF 文件编码无效') from None
                return extract_pdf(request.get('filename',''),content,request.get('pages',''))
            if route == '/api/ai/test':
                return ai_support.test_connection(self.home)
            if route == '/api/models/parametric':
                return stage_parametric(self.home, request.get('name'), request.get('spec'))
            if route == '/api/models/commit':
                result = commit(self.home, request.get('draft'), request.get('footprint'))
                self.save_aliases({**self.user_aliases(), result['footprint']: result['model']})
                return result
            if route == '/api/ai/generate':
                return dict(spec=ai_support.generate_spec(self.home, request.get('description', '')))
            if route == '/api/ai/search':
                return ai_support.search_models(self.home, request.get('query', ''))
            raise ValueError('模型操作不存在')
        finally:
            with self.lock: self.busy = False

    def save_aliases(self, aliases):
        if not isinstance(aliases, dict) or len(aliases) > 3000:
            raise ValueError("封装映射应为 JSON 对象，最多 3000 项")
        library = self.library()
        for key, model in aliases.items():
            if not isinstance(key, str) or not key.strip() or len(key) > 200 or not isinstance(model, str) or model not in library:
                raise ValueError("无效封装映射：" + str(key) + "；模型名必须在模型库中")
        self.home.mkdir(parents=True, exist_ok=True)
        temporary = self.alias_path.with_name("aliases.json.tmp")
        temporary.write_text(json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf8")
        temporary.replace(self.alias_path)
        return dict(saved=True, path=str(self.alias_path), count=len(aliases))

    def calibration_operation(self, request):
        from calibration import read,dump,values,make_rule,rule_key
        with self.lock:
            if self.busy:raise ValueError('正在生成或导出，请完成后再保存校准')
            job=self.jobs.get(request.get('id'))
            if not job or not job.get('preview'):raise ValueError('工程不存在，请从当前工具生成的预览中操作')
            root=Path(job['output']);data=read(root/'data.json');ref=request.get('ref')
            if ref not in data['placements']:raise ValueError('请选择一个已显示器件')
            p=data['placements'][ref];mapping=read(root/'model_mapping.json',{})
            if not mapping:
                html=(root/'index.html').read_text(encoding='utf8')
                mapping=dict(re.findall(r"'([^']+)':\s*'([^']+)'",html.split('const MODEL_MAP = {')[1].split('};')[0]))
            model=mapping.get(p['pkg'])
            html=(root/'index.html').read_text(encoding='utf8')
            overrides=dict(re.findall(r"(\w+):\s*'([^']+)'",html.split('const MODEL_BY_REF = {')[1].split('};')[0]))
            model=overrides.get(ref,model)
            if not model:raise ValueError('没有找到该器件的模型映射')
            rules=read(self.home/'calibration_rules.json',{});key=rule_key(p['pkg'],model)
            if request.get('action')=='remove_rule':
                rules.pop(key,None);dump(self.home/'calibration_rules.json',rules)
                return dict(saved=True,rule_removed=True)
            from manufacturing_export import MOUNT_TYPES
            if 'mount_type' in request and request['mount_type'] not in MOUNT_TYPES:raise ValueError('无效安装类型')
            v=values(request.get('values'));poses=read(root/'posefix.json',{})
            if request.get('save_rule'):
                rules[key]=make_rule(p,poses.get(ref,{}),model,v)
            state=read(root/'calibration.json',dict(version=1,items={}))
            previous=state['items'].get(ref,{})
            state['items'][ref]={**v,'source':'reference'}
            config=read(root/'生成配置.json',{})
            config.setdefault('ref_calibration',{})[ref]=v
            config['calibration_rules']=rules
            manufacturing=read(root/'manufacturing.json',dict(version=1,mount_types={}))
            if 'mount_type' in request:manufacturing.setdefault('mount_types',{})[ref]=request['mount_type']
            manufacturing.pop('last_export',None);config['manufacturing']=dict(mount_types=manufacturing.get('mount_types',{}))
            report=read(root/'导入检查.json',job.get('report') or {})
            report['manual_calibration']=state['items'];report['step_needs_export']=True
            report.pop('coordinate_export',None);dump(root/'manufacturing.json',manufacturing)
            dump(root/'calibration.json',state);dump(root/'生成配置.json',config);dump(root/'导入检查.json',report)
            if request.get('save_rule'):dump(self.home/'calibration_rules.json',rules)
            with (root/'校准记录.jsonl').open('a',encoding='utf8') as stream:
                stream.write(json.dumps(dict(time=time.strftime('%Y-%m-%d %H:%M:%S'),ref=ref,previous=previous,values=v,saved_rule=bool(request.get('save_rule'))),ensure_ascii=False)+'\n')
            exports=list(root.glob('*.stp'))+list(root.glob('*.step'))+list(root.glob('*.validation.json'))
            if exports:
                history=root/'历史导出'/str(time.time_ns());history.mkdir(parents=True)
                for path in exports:path.replace(history/path.name)
            job.update(report=report,downloads=self.project_downloads(request['id'],root))
            return dict(saved=True,calibration=state,manufacturing=manufacturing,rule_saved=bool(request.get('save_rule')),step_needs_export=True)

    def manufacturing_operation(self,request,export=False):
        from manufacturing_export import snapshot,dfm_checks,export_centres
        from calibration import read,dump
        with self.lock:
            if self.busy:raise ValueError('正在生成或导出，请等待完成')
            job_id=request.get('id');job=self.jobs.get(job_id)
            if not job or not job.get('preview'):raise ValueError('工程不存在')
            self.busy=True;root=Path(job['output'])
        try:
            options=request.get('options',{})
            if not isinstance(options,dict):raise ValueError('导出选项应为对象')
            if not export:
                state=snapshot(root,options.get('basis','body'),options.get('origin','source'),options.get('angle_mode','board'))
                return dict(rows=len(state['rows']),pending=state['pending'],dfm_issues=dfm_checks(state,root))
            dest,summary=export_centres(root,options)
            files=[dict(name=p.name,size=p.stat().st_size,url='/project/'+job_id+'/'+quote(p.relative_to(root).as_posix())) for p in sorted(dest.iterdir()) if p.is_file()]
            result=dict(summary=summary,files=files,directory=str(dest))
            state=read(root/'manufacturing.json',dict(version=1,mount_types={}))
            state['last_export']=result;dump(root/'manufacturing.json',state)
            report=read(root/'导入检查.json',job.get('report') or {});report['coordinate_export']=summary;dump(root/'导入检查.json',report)
            with self.lock:job.update(report=report,downloads=self.project_downloads(job_id,root))
            return result
        finally:
            with self.lock:self.busy=False

    def assembly_operation(self,request):
        from assembly_estimate import estimate
        with self.lock:
            if self.busy:raise ValueError('正在生成或导出，请完成后再统计')
            job=self.jobs.get(request.get('id'))
            if not job or not job.get('preview'):raise ValueError('工程不存在')
            self.busy=True
        try:
            result=estimate(Path(job['output']),request.get('options',{}),request.get('export') is True)
            result['files']=[dict(name=p.name,url='/project/'+request['id']+'/'+quote(p.relative_to(Path(job['output'])).as_posix())) for p in result.pop('paths',[])]
            return result
        finally:
            with self.lock:self.busy=False

    def project_downloads(self,job_id,root):
        files=[dict(name=p.name,size=p.stat().st_size,url='/project/'+job_id+'/'+quote(p.name)) for p in sorted(Path(root).iterdir())
                if p.is_file() and (p.suffix.lower() in {'.stp','.step'} or p.name in {'导入检查.json','生成配置.json','BOM核对说明.md','待处理器件.json','calibration.json','校准记录.jsonl'})]
        latest=read_json(Path(root)/'manufacturing.json',{}).get('last_export',{})
        return files+latest.get('files',[])

    def export_project(self,request):
        with self.lock:
            if self.busy:raise ValueError('正在生成或导出，请等待完成')
            job_id=request.get('id');job=self.jobs.get(job_id)
            if not job or not job.get('preview'):raise ValueError('工程不存在')
            if (job.get('report') or {}).get('incomplete'):raise ValueError('请先补齐待处理器件，再导出完整 STP；已保存的校准会保留')
            self.busy=True;job.update(state='running',started=time.time(),error=None,messages=['按已保存校准重新导出 STP'])
        threading.Thread(target=self.export_project_worker,args=(job_id,),daemon=True).start()
        return dict(id=job_id)

    def export_project_worker(self,job_id):
        from calibration import read,dump
        try:
            from cad_worker import export_isolated
            job=self.jobs[job_id];root=Path(job['output']);name=read(root/'data.json')['board']['name']
            export_isolated(root,root/(safe_name(name)+'.stp'),lambda message:self.progress(job_id,message))
            report=read(root/'导入检查.json',job.get('report') or {});report['step_needs_export']=False;dump(root/'导入检查.json',report)
            with self.lock:job.update(state='done',report=report,downloads=self.project_downloads(job_id,root),finished=time.time());job['messages'].append('校准 STP 导出和回读验证完成')
        except Exception as error:
            with self.lock:self.jobs[job_id].update(state='error',error=str(error),finished=time.time())
        finally:
            with self.lock:self.busy=False

    def submit(self, request, sample=False):
        config = request.get("config", {})
        if not isinstance(config, dict):
            raise ValueError("配置格式错误")
        config = dict(config)
        config["name"] = safe_name(config.get("name", "GK7205V10示例" if sample else "PCBA"))
        mapping = config.get("mapping", {})
        if not isinstance(mapping, dict):
            raise ValueError("封装映射应为 JSON 对象")
        library = self.library()
        if not library:
            raise ValueError("模型库缺失，请保留完整工具文件后重试")
        for key, value in mapping.items():
            if not isinstance(key, str) or not isinstance(value, str) or value not in library:
                raise ValueError("封装映射中的模型不存在：" + str(key))
        config["mapping"] = {**self.user_aliases(), **mapping}
        config['user_library_root'] = str(self.home / '用户模型库')
        config['calibration_rules'] = read_json(self.home / 'calibration_rules.json', {})
        files = {}
        if not sample:
            files = decode_uploads(request.get("files", []))
            if not config.get("xy"):
                raise ValueError("请选择 XY 坐标文件")
        elif not (self.assets / "sample" / "index.html").is_file():
            raise ValueError("当前工具没有打包示例工程")
        output_root = Path(str(request.get("output") or self.default_output)).expanduser()
        if not output_root.is_absolute():
            output_root = self.home / output_root
        output_root = output_root.resolve()
        if output_root == self.assets or self.assets in output_root.parents:
            raise ValueError("输出目录不能放在工具的内置资源中")
        with self.lock:
            if self.busy:
                raise ValueError("正在生成一个工程，请等待完成后再生成")
            self.busy = True
            job_id = secrets.token_hex(8)
            destination = output_root / (config["name"] + "_" + time.strftime("%Y%m%d_%H%M%S") + "_" + job_id[:4])
            job = dict(id=job_id, state="running", messages=["准备生成"], output=str(destination), report=None, error=None,
                       started=time.time(), preview=None, downloads=[])
            self.jobs[job_id] = job
        threading.Thread(target=self.run_job, args=(job_id, destination, files, config, sample), daemon=True).start()
        return {"id": job_id}

    def progress(self, job_id, message):
        with self.lock:
            self.jobs[job_id]["messages"].append(str(message))
            self.jobs[job_id]["messages"] = self.jobs[job_id]["messages"][-250:]

    def run_job(self, job_id, destination, files, config, sample):
        try:
            step_destination = None
            if sample:
                self.progress(job_id, "复制已校验的 GK7205V10 工程")
                shutil.copytree(self.assets / "sample", destination)
                original=(destination/'index.html').read_text(encoding='utf8')
                viewer=(self.assets/'viewer.html').read_text(encoding='utf8')
                for variable in ['MODEL_MAP','MODEL_BY_REF']:
                    declaration=re.search(r'const '+variable+r' = \{.*?\};',original,re.S)
                    if declaration:viewer=re.sub(r'const '+variable+r' = \{.*?\};',lambda m:declaration[0],viewer,flags=re.S)
                (destination/'index.html').write_text(viewer,encoding='utf8')
                from calibration import prepare,dump
                sample_data=read_json(destination/'data.json');sample_mapping=dict(re.findall(r"'([^']+)':\s*'([^']+)'",viewer.split('const MODEL_MAP = {')[1].split('};')[0]))
                sample_mapping.update(dict(re.findall(r"(\w+):\s*'([^']+)'",viewer.split('const MODEL_BY_REF = {')[1].split('};')[0])))
                dump(destination/'calibration.json',prepare(sample_data['placements'],read_json(destination/'posefix.json',{}),sample_mapping,config.get('calibration_rules')))
                report = read_json(destination / "导入检查.json", {})
                data = read_json(destination / "data.json", {})
                if not report:
                    placements = data.get("placements", {})
                    populated = [p for p in placements.values() if p.get("population") != "accessory"]
                    report = dict(components=len(populated), top=sum(p.get("side") == "top" for p in populated),
                                  bottom=sum(p.get("side") == "bottom" for p in populated), accessories=len(placements) - len(populated),
                                  warnings=["示例保留原工程已校验的板框、孔位和位号模型配置。"])
                if config.get("step", True) and not list(destination.glob("*.stp")):
                    step_destination = destination / "GK7205V10_MB_V01_RA.stp"
            else:
                from generator import build
                report = build(self.assets, destination, files, {**config, "step": False}, lambda message: self.progress(job_id, message))
                config.update(report.get("resolved_config", {}))
                if config.get("step", True) and not report.get('incomplete'):
                    step_destination = destination / (config["name"] + ".stp")
                elif config.get("step", True):
                    self.progress(job_id, "部分预览已生成；补齐待处理器件后可生成完整 STP")
            if not (destination/'manufacturing.json').exists():
                from calibration import dump
                dump(destination/'manufacturing.json',dict(version=1,mount_types=config.get('manufacturing',{}).get('mount_types',{})))
            (destination / "生成配置.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf8")
            with self.lock:
                self.jobs[job_id].update(report=report, preview="/project/" + job_id + "/index.html")
            if step_destination:
                self.progress(job_id, "预览已就绪，正在导出 STP")
                from cad_worker import export_isolated
                export_isolated(destination, step_destination, lambda message: self.progress(job_id, message))
            downloads = self.project_downloads(job_id,destination)
            with self.lock:
                job = self.jobs[job_id]
                job.update(state="done", report=report, preview="/project/" + job_id + "/index.html", downloads=downloads, finished=time.time())
                job["messages"].append("生成完成")
        except Exception as error:
            with self.lock:
                self.jobs[job_id].update(state="error", error=str(error) or type(error).__name__, finished=time.time())
                self.jobs[job_id]["messages"].append("生成失败：" + str(error))
            try:
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "错误日志.txt").write_text(traceback.format_exc(), encoding="utf8")
            except OSError:
                pass
        finally:
            with self.lock:
                self.busy = False


class Handler(BaseHTTPRequestHandler):
    server_version = "PCBA3D/" + VERSION

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        if sys.stderr:
            super().log_message(fmt, *args)

    def host_allowed(self):
        host = self.headers.get("Host", "")
        port = self.server.server_port
        return host in {"127.0.0.1:" + str(port), "localhost:" + str(port)}

    def respond(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.host_allowed():
            return self.respond({"error": "仅支持本机访问"}, 403)
        url = urlsplit(self.path)
        try:
            if url.path == "/api/info":
                return self.respond(self.app.info())
            if url.path == '/api/ai/settings':
                import ai_support
                return self.respond(ai_support.settings(self.app.home, public=True))
            if url.path.startswith('/model-vendor/'):
                return self.file(self.app.assets / 'vendor', unquote(url.path[len('/model-vendor/'):]))
            if url.path.startswith('/model-preview/'):
                pieces=unquote(url.path).split('/',3)
                if len(pieces)!=4 or not re.fullmatch(r'[a-f0-9]{24}',pieces[2]):
                    return self.respond({'error':'模型预览不存在'},404)
                return self.file(self.app.home / '模型草稿' / pieces[2],pieces[3])
            if url.path == "/api/job":
                job_id = parse_qs(url.query).get("id", [""])[0]
                with self.app.lock:
                    job = self.app.jobs.get(job_id)
                    if job:
                        job = json.loads(json.dumps(job))
                return self.respond(job if job else {"error": "工程任务不存在"}, 200 if job else 404)
            if url.path.startswith("/project/"):
                pieces = unquote(url.path).split("/", 3)
                if len(pieces) != 4:
                    return self.respond({"error": "文件路径错误"}, 404)
                with self.app.lock:
                    job = self.app.jobs.get(pieces[2])
                if not job or not job.get("preview"):
                    return self.respond({"error": "工程尚未生成完成"}, 404)
                if Path(pieces[3]).suffix.lower() in {".stp", ".step"} and job["state"] != "done":
                    return self.respond({"error": "STP 尚未完成验证，请等待导出完成"}, 409)
                root = Path(job["output"])
                return self.file(root, pieces[3])
            if url.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            return self.file(self.app.ui, "index.html" if url.path in {"/", "/index.html"} else unquote(url.path.lstrip("/")))
        except (OSError, ValueError) as error:
            return self.respond({"error": str(error)}, 400)

    def file(self, root, relative):
        root = Path(root).resolve()
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            return self.respond({"error": "文件不存在"}, 404)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix.lower() in {".js", ".mjs"}:
            mime = "text/javascript"
        if target.suffix.lower() in {".stp", ".step"}:
            mime = "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if target.suffix.lower() in {".stp", ".step"}:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(target.name))
        self.end_headers()
        with target.open("rb") as stream:
            shutil.copyfileobj(stream, self.wfile)

    def do_POST(self):
        if not self.host_allowed():
            return self.respond({"error": "仅支持本机访问"}, 403)
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            return self.respond({"error": "请求来源错误"}, 403)
        if not secrets.compare_digest(self.headers.get("X-PCBA-Token", ""), self.app.token):
            return self.respond({"error": "会话已失效，请刷新工具页面"}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_JSON:
                return self.respond({"error": "请求为空或超过 100 MB 文件限制"}, 413)
            request = json.loads(self.rfile.read(length).decode("utf8"))
            if not isinstance(request, dict):
                raise ValueError("请求格式错误")
            path = urlsplit(self.path).path
            if path in {'/api/ai/settings','/api/ai/generate','/api/ai/search','/api/ai/test','/api/ai/datasheet','/api/models/import','/api/models/parametric','/api/models/commit'}:
                return self.respond(self.app.model_operation(path,request))
            if path == "/api/inspect":
                return self.respond(inspect_files(decode_uploads(request.get("files", []))))
            if path == "/api/review":
                return self.respond(review_files(decode_uploads(request.get("files", [])), request.get('config', {}), self.app.info()['aliases'], self.app.library()))
            if path == "/api/build":
                return self.respond(self.app.submit(request), 202)
            if path == '/api/calibration':
                return self.respond(self.app.calibration_operation(request))
            if path == '/api/project/export':
                return self.respond(self.app.export_project(request),202)
            if path in {'/api/project/coordinates','/api/project/dfm'}:
                return self.respond(self.app.manufacturing_operation(request,export=path.endswith('/coordinates')))
            if path == '/api/project/assembly':
                return self.respond(self.app.assembly_operation(request))
            if path == "/api/sample":
                return self.respond(self.app.submit(request, sample=True), 202)
            if path == "/api/aliases":
                return self.respond(self.app.save_aliases(request.get("aliases")))
            if path == "/api/shutdown":
                with self.app.lock:
                    busy = self.app.busy
                if busy:
                    raise ValueError("正在生成工程，请完成后再退出")
                self.respond({"closed": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            return self.respond({"error": "接口不存在"}, 404)
        except (ValueError, TypeError, KeyError, OSError) as error:
            return self.respond({"error": str(error)}, 400)


class LocalHTTPServer(ThreadingHTTPServer):
    # A viewer loads modules, JSON and textures concurrently, sometimes in
    # several tabs. The default backlog of five drops local burst requests.
    request_queue_size = 64
    # SO_REUSEADDR permits simultaneous Windows listeners on the same port.
    # Each browser request could otherwise reach a different EXE/version.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(app, port):
    server = LocalHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.app = app
    app.server = server
    return server


def self_test(app):
    import faulthandler
    diagnostic = (app.home / '自检诊断.log').open('w', encoding='utf8')
    faulthandler.dump_traceback_later(30, file=diagnostic)
    def phase(message):
        diagnostic.write(message + '\n')
        diagnostic.flush()
    phase('加载 numpy / scipy')
    import urllib.error
    import urllib.request
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    from scipy.spatial import cKDTree
    phase('加载原生 CAD 内核')
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepCheck import BRepCheck_Analyzer
    phase('验证几何运算')
    rows, columns = linear_sum_assignment(np.array([[0, 3], [3, 0]]))
    assert list(rows) == [0, 1] and list(columns) == [0, 1]
    assert int(cKDTree([[0, 0], [2, 2]]).query([.1, .1])[1]) == 0
    shape = BRepPrimAPI_MakeBox(1, 2, 3).Shape()
    assert not shape.IsNull() and BRepCheck_Analyzer(shape).IsValid()
    for path in [app.ui / "index.html", app.assets / "viewer.html", app.assets / "library" / "models.json", app.assets / "aliases.json"]:
        if not path.is_file():
            raise ValueError("资源文件缺失：" + str(path))
    info = app.info()
    if not info["models"]:
        raise ValueError("模型库为空")
    phase('验证 HTTP 和独占端口')
    server = make_server(app, 0)
    blocked = False
    try:
        duplicate = make_server(app, server.server_port)
    except OSError:
        blocked = True
    else:
        duplicate.server_close()
    assert blocked, "Another instance can bind the same local port"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(server.server_port)
    try:
        assert json.load(urllib.request.urlopen(url + "/api/info"))["models"]
        assert "PCBA" in urllib.request.urlopen(url).read().decode("utf8")
        try:
            urllib.request.urlopen(urllib.request.Request(url + "/api/aliases", data=b"{}", headers={"Content-Type": "application/json"}))
            raise AssertionError("Missing-token request was accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 403
        try:
            urllib.request.urlopen(url + "/../app.py")
            raise AssertionError("Path traversal was accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 404
        result = dict(ok=True, native_kernel=True, models=len(info["models"]), aliases=len(info["aliases"]), sample=info["sample_available"], checks=["native CAD kernel", "scipy assignment/tree", "resources", "exclusive local port", "HTTP UI", "info API", "session token", "path containment"])
        (app.home / "自检结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
        if sys.stdout:
            print(json.dumps(result, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()
        faulthandler.cancel_dump_traceback_later()
        diagnostic.close()


def generate_config(app, filename):
    source = Path(filename).resolve()
    request = read_json(source)
    if not isinstance(request, dict):
        raise ValueError("配置文件应为 JSON 对象")
    files = []
    for name in request.get("files", []):
        path = Path(name)
        if not path.is_absolute():
            path = source.parent / path
        files.append(dict(name=path.name, data=base64.b64encode(path.read_bytes()).decode("ascii")))
    request["files"] = files
    response = app.submit(request, sample=bool(request.get("sample")))
    job_id, previous = response["id"], 0
    while True:
        with app.lock:
            job = json.loads(json.dumps(app.jobs[job_id]))
        for message in job["messages"][previous:]:
            if sys.stdout:
                print(message, flush=True)
        previous = len(job["messages"])
        if job["state"] != "running":
            if sys.stdout:
                print(json.dumps(job, ensure_ascii=False, indent=2))
            return 0 if job["state"] == "done" else 1
        time.sleep(.2)


def main():
    # Windows installations may use a non-Chinese legacy console encoding.
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description="PCBA3D 本地生成工具")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--generate-config", metavar="JSON")
    parser.add_argument("--step-worker", nargs=2, metavar=("PROJECT", "DESTINATION"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.step_worker:
        from cad_worker import worker_main
        return worker_main(*args.step_worker)
    assets, ui, home = locations()
    app = Application(assets, ui, home)
    if args.self_test:
        self_test(app)
        return 0
    if args.generate_config:
        return generate_config(app, args.generate_config)
    if not 0 <= args.port <= 65535:
        raise ValueError("端口应在 0–65535 之间")
    # Prefer the requested port, then use a free port if another local app owns it.
    try:
        server = make_server(app, args.port)
    except OSError:
        if args.port == 0:
            raise
        server = make_server(app, 0)
    url = "http://127.0.0.1:" + str(server.server_port)
    if sys.stdout:
        print("PCBA3D：" + url, flush=True)
    if not args.no_browser:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        if sys.stderr:
            traceback.print_exc()
        try:
            locations()[2].joinpath("PCBA3D错误日志.txt").write_text(traceback.format_exc(), encoding="utf8")
        except OSError:
            pass
        if getattr(sys, "frozen", False):
            try:
                import ctypes
                if "--self-test" not in sys.argv and "--generate-config" not in sys.argv and "--step-worker" not in sys.argv:
                    ctypes.windll.user32.MessageBoxW(0, "工具启动失败：\n" + str(error), "PCBA3D", 0x10)
            except Exception:
                pass
        raise SystemExit(1)

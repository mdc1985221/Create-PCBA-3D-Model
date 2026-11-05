"""Keep native CAD calls outside the HTTP server's Python interpreter."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
import traceback


def worker_main(project, destination):
    project, destination = Path(project).resolve(), Path(destination).resolve()
    if destination.parent != project or destination.suffix.lower() not in {'.stp', '.step'}:
        raise ValueError('STEP 输出必须位于当前工程目录')
    result_path = project / 'STEP任务结果.json'
    try:
        with (project / 'STEP进度.jsonl').open('w', encoding='utf8') as stream:
            def progress(message):
                stream.write(json.dumps(str(message), ensure_ascii=False) + '\n')
                stream.flush()
            progress('开始导出 STP；预览可继续操作')
            import faulthandler
            diagnostic = (project / 'STEP诊断.log').open('w', encoding='utf8')
            faulthandler.dump_traceback_later(60, file=diagnostic)
            from step_export import export_step
            try:
                export_step(project, destination, progress)
            finally:
                faulthandler.cancel_dump_traceback_later()
                diagnostic.close()
        result = dict(ok=True)
    except Exception as error:
        (project / 'STEP错误日志.txt').write_text(traceback.format_exc(), encoding='utf8')
        result = dict(ok=False, error=str(error) or type(error).__name__)
    temporary = result_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf8')
    temporary.replace(result_path)
    return 0 if result['ok'] else 1


def export_isolated(project, destination, progress):
    project = Path(project).resolve()
    destination = Path(destination).resolve()
    command = [sys.executable]
    if not getattr(sys, 'frozen', False):
        command.append(str(Path(__file__).with_name('app.py')))
    command.extend(['--step-worker', str(project), str(destination)])
    progress_path = project / 'STEP进度.jsonl'
    result_path = project / 'STEP任务结果.json'
    # The application serializes export jobs. A calibrated project can be
    # exported repeatedly; retain prior diagnostics instead of reusing them.
    previous=[project/name for name in ['STEP任务结果.json','STEP进度.jsonl','STEP诊断.log','STEP运行日志.txt','STEP错误日志.txt'] if (project/name).exists()]
    if previous:
        history=project/'历史导出'/('任务_'+str(time.time_ns()));history.mkdir(parents=True)
        for old in previous:old.replace(history/old.name)
    with (project / 'STEP运行日志.txt').open('w', encoding='utf8') as log:
        worker = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        offset = 0
        try:
            while True:
                if progress_path.exists():
                    with progress_path.open('r', encoding='utf8') as stream:
                        stream.seek(offset)
                        while True:
                            position = stream.tell()
                            line = stream.readline()
                            if not line.endswith('\n'):
                                offset = position
                                break
                            progress(json.loads(line))
                            offset = stream.tell()
                if worker.poll() is not None:
                    break
                time.sleep(.2)
            result = json.loads(result_path.read_text(encoding='utf8')) if result_path.exists() else {}
            if worker.returncode != 0 or not result.get('ok'):
                raise ValueError('STP 导出失败：' + result.get('error', 'CAD 进程退出，代码 ' + str(worker.returncode)) + '；预览和输入资料已保留')
        finally:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=15)

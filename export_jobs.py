"""One-worker durable local batch exports with immutable input snapshots."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
import zipfile

FORMATS = {'JPEG': 'jpg', 'PNG': 'png', 'TIFF16': 'tif'}
INPUTS = {'.nef', '.jpg', '.jpeg', '.png'}


class ExportQueue:
    def __init__(self, root, exporter, max_jobs=20, max_bytes=1024 ** 3):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.owner_file = (self.root / '.owner.lock').open('a+b')
        if self.owner_file.tell() == 0:
            self.owner_file.write(b'0'); self.owner_file.flush()
        self.owner_file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.owner_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.owner_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.owner_file.close()
            raise ValueError('另一软件实例正在管理后台导出，请关闭多余实例') from exc
        self.exporter = exporter
        self.max_jobs, self.max_bytes = max_jobs, max_bytes
        self.lock = threading.RLock()
        self.jobs = {}
        self.readers = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='z30-export')
        self.stopped = False
        for path in self.root.glob('*/manifest.json'):
            try:
                job = json.loads(path.read_text(encoding='utf8'))
                if self._folder(job['id']) != path.parent.resolve() or job['format'] not in FORMATS:
                    continue
                if not 1 <= len(job['items']) <= 8:
                    continue
                if any(x['index'] != i or x['extension'] not in INPUTS for i, x in enumerate(job['items'])):
                    continue
                if any(x.get('output') and x['output'] != f"output-{i}.{FORMATS[job['format']]}"
                       for i, x in enumerate(job['items'])):
                    continue
                job['worker_active'] = False
                if job['state'] in ('queued', 'running'):
                    job['state'] = 'interrupted'
                    job['error'] = '上次导出被中断；可从本机快照重试'
                    for item in job['items']:
                        if item['state'] == 'running':
                            item['state'] = 'pending'
                    self._save(job)
                self.jobs[job['id']] = job
            except (OSError, ValueError, KeyError, TypeError):
                continue  # Never run an incomplete/unrecognized disk manifest.

    def _folder(self, key):
        if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{32}', key):
            raise ValueError('无效的导出任务编号')
        folder = (self.root / key).resolve()
        if folder.parent != self.root:
            raise ValueError('导出路径超出任务目录')
        return folder

    def _save(self, job):
        folder = self._folder(job['id'])
        temp = folder / 'manifest.json.tmp'
        temp.write_text(json.dumps(job, ensure_ascii=False, allow_nan=False), encoding='utf8')
        os.replace(temp, folder / 'manifest.json')

    def disk_bytes(self):
        total = 0
        for path in self.root.rglob('*'):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass  # An atomic manifest replacement may race this snapshot.
        return total

    def submit(self, sources, settings, fmt, quality, protect):
        if fmt not in FORMATS or not 1 <= len(sources) <= 8:
            raise ValueError('导出格式或批量数量无效（1–8 张）')
        total = sum(Path(x['path']).stat().st_size for x in sources)
        with self.lock:
            if self.stopped:
                raise ValueError('后台导出服务已停止')
            if len(self.jobs) >= self.max_jobs or total > 512 * 1024 ** 2 or self.disk_bytes() + total > self.max_bytes:
                raise ValueError('本机导出快照空间或任务数已达上限，请先删除旧任务')
            key = secrets.token_hex(16); folder = self._folder(key)
            folder.mkdir()
            job = dict(id=key, state='queued', created=time.time(), format=fmt, quality=quality,
                       protect=bool(protect), settings=dict(settings), items=[])
            try:
                for i, source in enumerate(sources):
                    extension = Path(source['path']).suffix.lower()
                    if extension not in INPUTS:
                        raise ValueError('不支持此快照格式')
                    copied = folder / f'input-{i}{extension}'
                    shutil.copyfile(source['path'], copied)
                    with copied.open('rb') as file:
                        digest = hashlib.file_digest(file, 'sha256').hexdigest()
                    name = source['name'].replace('\\', '/').split('/')[-1].replace('\x00', '')[:200]
                    job['items'].append(dict(index=i, name=name, extension=extension,
                                             raw=extension == '.nef', state='pending', sha256=digest))
                self._save(job)
                self.jobs[key] = job
                self.pool.submit(self._run, key)
            except Exception:
                self.jobs.pop(key, None)
                shutil.rmtree(folder)
                raise
            return key

    def get(self, key):
        with self.lock:
            if key not in self.jobs:
                raise KeyError('导出任务不存在')
            return copy.deepcopy(self.jobs[key])

    def list(self):
        with self.lock:
            return [copy.deepcopy(j) for j in sorted(self.jobs.values(), key=lambda j: j['created'], reverse=True)]

    def retry(self, key):
        with self.lock:
            job = self.jobs[key]
            if self.stopped or job['state'] in ('running', 'queued', 'done') or job.get('worker_active') or self.readers.get(key):
                raise ValueError('此任务当前不能重试')
            for item in job['items']:
                if item['state'] != 'done':
                    item['state'] = 'pending'
                    item.pop('error', None)
            (self._folder(key) / 'results.zip').unlink(missing_ok=True)
            job['state'] = 'queued'; job.pop('error', None)
            self._save(job); self.pool.submit(self._run, key)

    def cancel(self, key):
        with self.lock:
            job = self.jobs[key]
            if job['state'] in ('running', 'queued'):
                job['state'] = 'cancelled'
                job['error'] = '已取消；正在编码的单张照片会在安全点停止发布'
                self._save(job)

    def delete(self, key):
        with self.lock:
            job = self.jobs[key]
            if job['state'] in ('running', 'queued') or job.get('worker_active') or self.readers.get(key):
                raise ValueError('任务仍在处理，请取消后等待当前照片处理结束再删除')
            folder = self._folder(key)
            # Resolve and verify inside the named root before recursive deletion.
            if folder.parent != self.root:
                raise ValueError('拒绝删除任务目录之外的路径')
            shutil.rmtree(folder)
            del self.jobs[key]

    @contextmanager
    def downloads(self, key, index=None):
        """Pin completed outputs while streaming without holding the queue lock."""
        with self.lock:
            job = copy.deepcopy(self.jobs[key])
            items = [x for x in job['items'] if x['state'] == 'done' and (index is None or x['index'] == index)]
            if not items:
                raise ValueError('尚无已完成的导出文件')
            folder = self._folder(key)
            entries = []
            for item in items:
                path = folder / f"output-{item['index']}.{FORMATS[job['format']]}"
                if not path.is_file():
                    raise ValueError('导出文件已丢失，请重新导出')
                stem = Path(item['name']).stem or 'photo'
                entries.append((path, f"{item['index'] + 1:02d}-{stem}-Z30.{FORMATS[job['format']]}"))
            report = dict(settings=job['settings'], items=job['items'])
            self.readers[key] = self.readers.get(key, 0) + 1
        try:
            yield entries, report
        finally:
            with self.lock:
                self.readers[key] -= 1
                if not self.readers[key]:
                    del self.readers[key]

    def _run(self, key):
        with self.lock:
            job = self.jobs.get(key)
            if not job or job['state'] != 'queued' or self.stopped:
                return
            job['state'] = 'running'; job['worker_active'] = True; self._save(job)
        folder = self._folder(key)
        try:
            for item in job['items']:
                with self.lock:
                    if job['state'] != 'running' or self.stopped:
                        return
                    if item['state'] == 'done':
                        continue
                    item['state'] = 'running'; self._save(job)
                partial = folder / f"output-{item['index']}.part"
                try:
                    source = folder / f"input-{item['index']}{item['extension']}"
                    with source.open('rb') as file:
                        if hashlib.file_digest(file, 'sha256').hexdigest() != item['sha256']:
                            raise ValueError('导出输入快照校验失败；未处理可能已改变的文件')
                    params = dict(job['settings'])
                    if not item['raw']:
                        params['wb'] = 'camera'
                    data, applied, quality = self.exporter(source, item['raw'], params, job['format'], job['quality'], job['protect'])
                    if self.disk_bytes() + len(data) > self.max_bytes:
                        raise ValueError('本机任务空间不足，请删除旧任务后重试')
                    partial.write_bytes(data)
                    del data
                    with self.lock:
                        if job['state'] != 'running' or self.stopped:
                            return
                        output = f"output-{item['index']}.{FORMATS[job['format']]}"
                        os.replace(partial, folder / output)
                        item.update(state='done', output=output, applied_settings=applied, quality=quality)
                        item.pop('error', None); self._save(job)
                except Exception as exc:
                    with self.lock:
                        if job['state'] == 'running':
                            item.update(state='error', error=str(exc)[:300])
                            self._save(job)
                finally:
                    partial.unlink(missing_ok=True)
            with self.lock:
                if job['state'] != 'running' or self.stopped:
                    return
            completed = [x for x in job['items'] if x['state'] == 'done']
            with self.lock:
                if job['state'] == 'running':
                    job['state'] = ('done' if len(completed) == len(job['items']) else 'partial' if completed else 'error')
                    self._save(job)
        except Exception as exc:
            with self.lock:
                if job['state'] == 'running':
                    job.update(state='error', error=str(exc)[:300])
        finally:
            (folder / 'results.zip.part').unlink(missing_ok=True)
            with self.lock:
                job['worker_active'] = False
                for item in job['items']:
                    if item['state'] == 'running':
                        item['state'] = 'pending'
                self._save(job)

    def shutdown(self):
        with self.lock:
            self.stopped = True
            for job in self.jobs.values():
                if job['state'] in ('running', 'queued'):
                    job.update(state='interrupted', error='服务已关闭；可从本机快照重试')
                    self._save(job)
        self.pool.shutdown(wait=True, cancel_futures=True)
        if not self.owner_file.closed:
            self.owner_file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.owner_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.owner_file.fileno(), fcntl.LOCK_UN)
            self.owner_file.close()


def write_zip(target, entries, report):
    """ZIP_STORED streams to unseekable HTTP writers without a second disk copy."""
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path, name in entries:
            archive.write(path, name)
        archive.writestr('export-report.json', json.dumps(report, ensure_ascii=False))

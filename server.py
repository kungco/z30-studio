"""Z30 Studio: loopback-only, non-destructive RAW development."""
import argparse
import base64
import io
import json
import logging
import math
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import codex_assist
from processing import render_array
from cache import ByteLRU
import quality as quality_checks
from export_jobs import ExportQueue, FORMATS, write_zip
import shutil
import socket
import projects
import automation
import api_backend
import spatial
import zipfile
from app_runtime import APP_VERSION, resource_root, data_root
from imaging import raw_crop, raw_pixels, raster_linear, resize_linear, srgb_to_linear

os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np
import rawpy
from PIL import Image, ImageCms, ImageFilter, ImageOps

ROOT = resource_root()
TEMP = tempfile.TemporaryDirectory(prefix='z30-studio-')
SESSIONS = {}
LOCK = threading.RLock()  # Serialize interactive work; background exports have one worker.
PROJECT_TICKETS = {}
PROJECT_READERS = {}
SESSION_TTL = 30 * 60
MAX_UPLOAD = 150 * 1024 * 1024
ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
ADVISOR = codex_assist.Advisor()
AI_SETTINGS = api_backend.Settings()
CACHE = ByteLRU(128 * 1024 * 1024)
EXPORTS = None
EXPORTS_LOCK = threading.RLock()
DEFAULTS = dict(exposure=0, contrast=0, highlights=0, shadows=0,
                temperature=0, tint=0, saturation=0, vibrance=0,
                sharpen=0, rotation=0, crop='original', wb='camera') | spatial.DEFAULTS | {'lens_profile':'手动 / 未校准'}
LIMITS = dict(exposure=(-4, 4), contrast=(-100, 100), highlights=(-100, 100),
              shadows=(-100, 100), temperature=(-100, 100), tint=(-100, 100),
              saturation=(-100, 100), vibrance=(-100, 100), sharpen=(0, 100)) | spatial.LIMITS


def settings(data):
    p = DEFAULTS.copy()
    for k, (lo, hi) in LIMITS.items():
        v = float(data.get(k, p[k]))
        if not math.isfinite(v):
            raise ValueError('参数必须是有效数字')
        p[k] = max(lo, min(hi, v))
    p['rotation'] = int(data.get('rotation', 0)) % 4
    p['crop'] = data.get('crop', 'original')
    p['wb'] = data.get('wb', 'camera')
    if p['crop'] not in ('original', '3:2', '4:3', '1:1', '16:9'):
        raise ValueError('无效裁剪比例')
    if p['wb'] not in ('camera', 'auto', 'daylight'):
        raise ValueError('无效白平衡')
    p['masks'] = spatial.masks(data.get('masks', []))
    p['lens_profile'] = str(data.get('lens_profile', '手动 / 未校准'))[:100]
    return p


def decode(session, wb, preview):
    token = session.setdefault('_cache_id', session.get('meta', {}).get('id') or secrets.token_hex(16))
    cache_key = (token, 'linear', wb)
    if preview:
        cached = CACHE.get(cache_key)
        if cached is not None:
            return cached
    if session['raw']:
        with rawpy.imread(str(session['path'])) as raw:
            # postprocess mutates LibRaw sizes/flip, especially in half-size mode.
            sizes = raw.sizes
            if sizes.pixel_aspect != 1:
                raise ValueError('非方形像素 RAW 尚未验证，暂不支持')
            rgb = raw.postprocess(output_bps=16, gamma=(1, 1),
                                  no_auto_bright=True, use_camera_wb=wb == 'camera',
                                  use_auto_wb=wb == 'auto', half_size=preview,
                                  output_color=rawpy.ColorSpace.sRGB, user_flip=0)
        arr = raw_pixels(rgb, sizes, preview)
        del rgb
    else:
        with Image.open(session['path']) as source:
            arr, policy, warnings = raster_linear(source)
            session['color_policy'], session['color_warnings'] = policy, warnings
    if preview:
        h, w = arr.shape[:2]
        scale = min(1, 1600 / max(w, h))
        if scale < 1:
            size = (max(1, round(w * scale)), max(1, round(h * scale)))
            arr = resize_linear(arr, size)
        arr.setflags(write=False)
        CACHE.put(cache_key, arr)
    return arr


def geometry(arr, p):
    if p['rotation']:
        arr = np.rot90(arr, -p['rotation'])
    if p['crop'] != 'original':
        a, b = map(int, p['crop'].split(':'))
        ratio = a / b
        h, w = arr.shape[:2]
        if w / h > ratio:
            nw = max(1, round(h * ratio)); left = (w - nw) // 2
            arr = arr[:, left:left + nw]
        else:
            nh = max(1, round(w / ratio)); top = (h - nh) // 2
            arr = arr[top:top + nh]
    return arr


def develop(arr, p):
    return Image.fromarray(render_array(geometry(spatial.prepare(arr, p), p), p, bits=8))


def image_bytes(im, fmt='JPEG', quality=93):
    b = io.BytesIO()
    opts = {'quality': quality, 'subsampling': 0} if fmt == 'JPEG' else {}
    im.save(b, format=fmt, icc_profile=ICC, **opts)
    return b.getvalue()


def export_bytes(session, p, fmt='JPEG', quality=95):
    if fmt not in ('JPEG', 'PNG', 'TIFF16'):
        raise ValueError('不支持的导出格式')
    arr = decode(session, p['wb'], False)
    if fmt == 'TIFF16':
        import tifffile
        pixels = render_array(geometry(spatial.prepare(arr, p), p), p, bits=16)
        out = io.BytesIO()
        tifffile.imwrite(out, pixels, photometric='rgb', iccprofile=ICC,
                         metadata=None, compression='deflate', maxworkers=1)
        return out.getvalue()
    return image_bytes(develop(arr, p), fmt, quality)


def export_snapshot(path, is_raw, params, fmt, quality, protect):
    session = dict(path=path, raw=is_raw, cache={})
    p = settings(params)
    metrics = None
    try:
        if protect:
            base = p | {k: DEFAULTS[k] for k in codex_assist.RANGES}
            p, metrics = quality_checks.guard(decode(session, p['wb'], True), base, p, develop)
        return export_bytes(session, p, fmt, quality), p, metrics
    finally:
        if session.get('_cache_id'):
            CACHE.remove_source(session['_cache_id'])


def get_exports():
    global EXPORTS
    with EXPORTS_LOCK:
        if EXPORTS is None:
            EXPORTS = ExportQueue(data_root() / 'exports', export_snapshot)
        return EXPORTS


def data_url(im):
    return 'data:image/jpeg;base64,' + base64.b64encode(image_bytes(im, quality=90)).decode()


def preview_result(session, p, include_before=False):
    token = session.setdefault('_cache_id', session.get('meta', {}).get('id') or secrets.token_hex(16))
    cache_key = (token, 'render', json.dumps(p, sort_keys=True, allow_nan=False), bool(include_before))
    cached = CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    arr = decode(session, p['wb'], True)
    im = develop(arr, p)
    a = np.asarray(im)
    hist = [np.histogram(a[:, :, c], bins=128, range=(0, 256))[0].tolist() for c in range(3)]
    result = dict(image=data_url(im), histogram=hist, width=im.width, height=im.height)
    result['quality'] = quality_checks.measure(im)
    if include_before:
        base = DEFAULTS | {'rotation': p['rotation'], 'crop': p['crop']}
        before_key = (token, 'baseline', p['rotation'], p['crop'])
        before = CACHE.get(before_key)
        if before is None:
            before = result['image'] if p == base else data_url(develop(decode(session, 'camera', True), base))
            CACHE.put(before_key, before)
        result['before'] = before
    CACHE.put(cache_key, result)
    return result


def filter_advice(source_id, base, result):
    with LOCK:
        if source_id not in SESSIONS:
            raise ValueError('照片会话已关闭，无法检查辅助建议')
        session = SESSIONS[source_id]
        linear = decode(session, base['wb'], True)
        checked, metrics = quality_checks.guard(linear, base, result['settings'], develop)
    return {**result, 'settings': checked, 'quality': metrics}


ADVISOR.result_filter = filter_advice


def release_photo(key):
    if PROJECT_READERS.get(key):
        raise ValueError('项目正在准备或下载，请稍后移除照片')
    session = SESSIONS.pop(key)
    ADVISOR.cancel_source(key)
    CACHE.remove_source(key)
    session['cache'].clear()
    session['path'].unlink(missing_ok=True)


def reap_sessions(now=None):
    now = time.monotonic() if now is None else now
    with LOCK:
        for ticket, data in list(PROJECT_TICKETS.items()):
            if now >= data['expires']:
                release_project(ticket)
        for key, session in list(SESSIONS.items()):
            if not PROJECT_READERS.get(key) and now - session.setdefault('last_seen', now) >= SESSION_TTL:
                release_photo(key)


def release_project(ticket):
    data = PROJECT_TICKETS.pop(ticket, None)
    if data:
        for key in data['ids']:
            PROJECT_READERS[key] -= 1
            if not PROJECT_READERS[key]:
                del PROJECT_READERS[key]


def import_image(body, name, owner=None):
    ext = Path(name).suffix.lower()
    if ext not in ('.nef', '.jpg', '.jpeg', '.png'):
        raise ValueError('请选择 NEF、JPG 或 PNG 照片')
    reap_sessions()
    if sum(s.get('owner') == owner for s in SESSIONS.values()) >= 8:
        raise ValueError('当前已打开 8 张照片，请移除照片后再导入')
    if len(SESSIONS) >= 32:
        raise ValueError('本机照片会话已达上限，请关闭不用的照片或等待过期会话回收')
    key = secrets.token_hex(16)
    path = Path(TEMP.name) / (key + ext)
    path.write_bytes(body)
    session = dict(path=path, name=name, raw=ext == '.nef', cache={}, owner=owner, last_seen=time.monotonic())
    try:
        if session['raw']:
            with rawpy.imread(str(path)) as raw:
                _, _, w, h = raw_crop(raw.sizes)
                if raw.sizes.flip & 4:
                    w, h = h, w
            try:
                with Image.open(path) as im:
                    model = str(im.getexif().get(272, 'NEF RAW'))
            except Exception:
                model = 'NEF RAW'
        else:
            with Image.open(path) as im:
                model = str(im.getexif().get(272, '图像文件'))
                w, h = ImageOps.exif_transpose(im).size
                if w * h > 80_000_000:
                    raise ValueError('图像超过 8000 万像素限制')
        session['meta'] = dict(id=key, name=name, width=w, height=h, camera=model,
                               raw=session['raw'], bytes=len(body))
        result = preview_result(session, DEFAULTS, True)
        session['meta'].update(color_policy=session.get('color_policy', 'RAW → linear sRGB'),
                               warnings=session.get('color_warnings', []))
        SESSIONS[key] = session
        return result | session['meta']
    except Exception:
        path.unlink(missing_ok=True)
        raise


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def send(self, body, mime='application/json; charset=utf-8', status=200, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, status=200):
        self.send(json.dumps(obj, ensure_ascii=False).encode('utf8'), status=status)

    def stream(self, path, filename, mime):
        # Open before sending headers; deletion after open cannot serve a wrong path.
        with path.open('rb') as source:
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(filename))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            shutil.copyfileobj(source, self.wfile, length=1024 * 1024)

    def stream_zip(self, entries, report, filename):
        self.send_response(200)
        self.send_header('Content-Type', 'application/zip')
        self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(filename))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.close_connection = True
        self.end_headers()
        write_zip(self.wfile, entries, report)

    def local_request(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in allowed:
            self.json({'error': '只允许本机访问'}, 403)
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + h for h in allowed}:
            self.json({'error': '请求来源不匹配'}, 403)
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        route = urllib.parse.urlsplit(self.path).path
        if route == '/api/health':
            return self.json(dict(app='Z30 Studio', version=APP_VERSION, rawpy=rawpy.__version__,
                                  libraw='.'.join(map(str, rawpy.libraw_version)), cache=CACHE.stats()))
        if route.startswith('/api/projects/download/'):
            ticket = route.rsplit('/', 1)[-1]
            with LOCK:
                data = PROJECT_TICKETS.get(ticket)
                if not data or data['expires'] < time.monotonic():
                    release_project(ticket)
                    return self.json({'error': '项目下载已过期，请重新保存'}, 410)
                if data.get('active'):
                    return self.json({'error': '项目正在下载'}, 409)
                data['active'] = True; data['expires'] = float('inf')
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Disposition', 'attachment; filename="Z30-Project.z30proj"')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close'); self.close_connection = True
                self.end_headers()
                projects.write_project(self.wfile, data['sources'], data['manifest'])
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                with LOCK:
                    release_project(ticket)
            return
        if route == '/api/ai/config':
            return self.json(AI_SETTINGS.public())
        if route == '/api/codex/status':
            config=AI_SETTINGS.public()
            if config['provider']=='codex':return self.json(codex_assist.status() | {'provider':'codex'})
            return self.json(dict(provider=config['provider'],available=True,ready=config['has_key'],message=('接口已配置，点击分析才发送图片并使用服务商额度' if config['has_key'] else '请在模型设置中填写当前运行使用的 API Key')))
        if route.startswith('/api/codex/requests/'):
            try:
                source_id = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('source', [''])[0]
                with LOCK:
                    if source_id not in SESSIONS or SESSIONS[source_id].get('owner') != self.headers.get('X-Z30-Client'):
                        return self.json({'error': '照片会话已关闭'}, 410)
                    key = ADVISOR.find_request(source_id, route.rsplit('/', 1)[-1])
                    job = ADVISOR.get(key)
                    current = preview_result(SESSIONS[source_id], job['base_settings'])['image']
                return self.json({'job': key, 'current_image': current})
            except KeyError as exc:
                return self.json({'error': str(exc)}, 404)
            except ValueError as exc:
                return self.json({'error': str(exc)}, 400)
        if route == '/api/exports' or route.startswith('/api/exports/'):
            try:
                queue = get_exports()
                if route == '/api/exports':
                    return self.json({'jobs': queue.list(), 'storage_bytes': queue.disk_bytes(), 'limit_bytes': queue.max_bytes})
                parts = route.split('/')
                key = parts[3]
                job = queue.get(key)
                if len(parts) == 5 and parts[4] == 'download':
                    with queue.downloads(key) as (entries, report):
                        return self.stream_zip(entries, report, 'Z30-export-' + key[:8] + '.zip')
                if (len(parts) == 5 and parts[4] == 'file') or (len(parts) == 6 and parts[4] == 'files'):
                    index = int(parts[5]) if len(parts) == 6 else 0
                    with queue.downloads(key, index) as (entries, report):
                        path, name = entries[0]
                        return self.stream(path, name, {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'TIFF16': 'image/tiff'}[job['format']])
                if len(parts) != 4:
                    raise ValueError('导出接口不存在')
                return self.json(job)
            except (BrokenPipeError, ConnectionResetError):
                return
            except KeyError:
                return self.json({'error': '导出任务不存在'}, 404)
            except ValueError as exc:
                return self.json({'error': str(exc)}, 409)
            except OSError:
                return self.json({'error': '导出快照或结果无法访问，请检查本机存储'}, 500)
        if route.startswith('/api/codex/jobs/'):
            try:
                with LOCK:
                    job = ADVISOR.get(route.rsplit('/', 1)[-1])
                    if job['source_id'] not in SESSIONS or SESSIONS[job['source_id']].get('owner') != self.headers.get('X-Z30-Client'):
                        return self.json({'error': '照片会话已关闭，辅助任务已取消'}, 410)
                return self.json(job)
            except KeyError:
                return self.json({'error': '建议任务不存在，请重新生成'}, 404)
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
                 '/model-settings.js': ('model-settings.js', 'application/javascript; charset=utf-8'),
                 '/batch-grade.js': ('batch-grade.js', 'application/javascript; charset=utf-8'),
                 '/history.js': ('history.js', 'application/javascript; charset=utf-8'),
                 '/auto-grade.js': ('auto-grade.js', 'application/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8'),
                 '/assistant.css': ('assistant.css', 'text/css; charset=utf-8')}
        if route in files:
            filename, mime = files[route]
            return self.send((ROOT / 'web' / filename).read_bytes(), mime)
        return self.json({'error': '页面不存在'}, 404)

    def open_project_upload(self, size):
        owner = self.headers.get('X-Z30-Client')
        if not owner or len(owner) != 32 or any(c not in '0123456789abcdef' for c in owner):
            raise ValueError('页面会话编号无效')
        if not 0 < size <= projects.PROJECT_LIMIT:
            self.close_connection = True
            return self.json({'error': '项目为空或超过 512 MiB'}, 413)
        path = Path(TEMP.name) / (secrets.token_hex(16)+'.z30proj')
        imported = []
        try:
            with path.open('wb') as out:
                remaining = size
                while remaining:
                    chunk = self.rfile.read(min(1024*1024, remaining))
                    if not chunk:
                        raise ValueError('项目上传不完整')
                    out.write(chunk); remaining -= len(chunk)
            with projects.read_project(path, TEMP.name, settings) as document, LOCK:
                if sum(x.get('owner') == owner for x in SESSIONS.values()) + len(document['photos']) > 8:
                    raise ValueError('打开项目后超过 8 张，请先移除照片')
                photos = []
                for photo in document['photos']:
                    name = str(photo['name']).replace('\\', '/').split('/')[-1][:200]
                    result = import_image(photo['staged'].read_bytes(), name, owner)
                    key = result['id']; imported.append(key)
                    preview = preview_result(SESSIONS[key], photo['settings'], True)
                    photos.append(preview | SESSIONS[key]['meta'] | dict(settings=photo['settings'], history=photo.get('history'),
                                  preset=photo.get('preset','custom'), analysis=photo.get('analysis'), undo=photo.get('undo')))
                return self.json({'photos': photos, 'selected': max(0,min(len(photos)-1,int(document.get('selected',0))))})
        except Exception:
            with LOCK:
                for key in imported:
                    if key in SESSIONS: release_photo(key)
            raise ValueError('项目损坏、格式不支持或照片无法解码；未覆盖现有编辑')
        finally:
            path.unlink(missing_ok=True)

    def do_POST(self):
        if not self.local_request():
            return
        if self.headers.get('X-Z30-Request') != '1':
            return self.json({'error': '缺少请求标记'}, 403)
        try:
            route = urllib.parse.urlsplit(self.path).path
            size = int(self.headers.get('Content-Length', '0'))
            if route == '/api/projects/open':
                return self.open_project_upload(size)
            if not 0 < size <= MAX_UPLOAD:
                self.close_connection = True
                return self.json({'error': '文件为空或超过 150 MB'}, 413)
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError('文件传输不完整')
            route = urllib.parse.urlsplit(self.path).path
            owner = self.headers.get('X-Z30-Client')
            if owner is not None and (len(owner) != 32 or any(c not in '0123456789abcdef' for c in owner)):
                raise ValueError('无效的页面会话编号')
            if route in ('/api/ai/config','/api/ai/test'):
                data=json.loads(body)
                if route=='/api/ai/test':
                    config=AI_SETTINGS.snapshot()
                    if config is None:return self.json(codex_assist.status() | {'model_listed':True})
                    return self.json(api_backend.test_connection(config))
                with ADVISOR.lock:
                    if ADVISOR.workers:raise ValueError('正在分析或取消中，请等任务结束后更换模型配置')
                    return self.json(AI_SETTINGS.save(data))
            if route == '/api/projects/prepare':
                data = json.loads(body); ids = data.get('ids')
                if not isinstance(ids, list) or not 1 <= len(ids) <= 8 or len(set(ids)) != len(ids):
                    raise ValueError('请选择 1–8 张不同照片')
                with LOCK:
                    reap_sessions()
                    if len(PROJECT_TICKETS) >= 8:
                        raise ValueError('项目下载任务较多，请等待已有下载完成')
                    if any(k not in SESSIONS or SESSIONS[k].get('owner') != owner for k in ids):
                        raise ValueError('项目照片会话已关闭')
                    sources = [SESSIONS[k] for k in ids]
                    document = projects.manifest(sources, data.get('recipes', {}), int(data.get('selected', 0)), settings)
                    ticket = secrets.token_hex(16)
                    for key in ids:
                        PROJECT_READERS[key] = PROJECT_READERS.get(key, 0) + 1
                    PROJECT_TICKETS[ticket] = dict(ids=ids, sources=sources, manifest=document, expires=time.monotonic()+60)
                return self.json({'url': '/api/projects/download/'+ticket})
            if route in ('/api/sessions/restore', '/api/sessions/heartbeat'):
                if owner is None:
                    raise ValueError('页面会话编号缺失')
                data = json.loads(body)
                with LOCK:
                    reap_sessions()
                    owned = [(k, s) for k, s in SESSIONS.items() if s.get('owner') == owner]
                    for _, session in owned:
                        session['last_seen'] = time.monotonic()
                    if route.endswith('heartbeat'):
                        return self.json({'ids': [k for k, _ in owned], 'ttl_seconds': SESSION_TTL})
                    recipes = data.get('recipes', {})
                    if not isinstance(recipes, dict) or len(recipes) > 8:
                        raise ValueError('恢复参数格式无效')
                    photos = []
                    for key, session in owned:
                        recipe = recipes.get(key, {})
                        p = settings(recipe.get('settings', {}))
                        if not session['raw']:
                            p['wb'] = 'camera'
                        preview = preview_result(session, p, True)
                        item = preview | session['meta'] | {'settings': p, 'previewWidth': preview['width'], 'previewHeight': preview['height']}
                        item['history'] = projects.history(recipe.get('history'), settings)
                        if recipe.get('undo'):
                            item['undo'] = {'settings': settings(recipe['undo'].get('settings', {})), 'preset': recipe['undo'].get('preset', 'custom')}
                        photos.append(item)
                    return self.json({'photos': photos, 'ttl_seconds': SESSION_TTL})
            if route.startswith('/api/exports/'):
                data = json.loads(body)
                queue = get_exports()
                if route == '/api/exports/create':
                    ids = data.get('ids')
                    if (not isinstance(ids, list) or not 1 <= len(ids) <= 8 or
                            any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids)):
                        raise ValueError('请选择 1 到 8 张不同照片')
                    p = settings(data.get('settings', {}))
                    fmt = data.get('format', 'JPEG')
                    if fmt not in FORMATS:
                        raise ValueError('不支持的导出格式')
                    protect = data.get('protect', False)
                    if not isinstance(protect, bool):
                        raise ValueError('画质保护选项无效')
                    with LOCK:
                        if any(key not in SESSIONS or SESSIONS[key].get('owner') != owner for key in ids):
                            return self.json({'error': '部分照片会话已关闭，请重新选择'}, 410)
                        sources = [SESSIONS[key] for key in ids]
                        key = queue.submit(sources, p, fmt, max(60, min(100, int(data.get('quality', 95)))), protect)
                    return self.json({'job': key}, 202)
                action = {'/api/exports/retry': queue.retry, '/api/exports/cancel': queue.cancel,
                          '/api/exports/delete': queue.delete}.get(route)
                if not action:
                    raise ValueError('导出接口不存在')
                action(data.get('job'))
                return self.json({'ok': True})
            with LOCK:
                reap_sessions()
                if route == '/api/open':
                    name = urllib.parse.unquote(self.headers.get('X-Filename', 'image.nef'))
                    name = name.replace('\\', '/').split('/')[-1][:200]
                    return self.json(import_image(body, name, owner))
                data = json.loads(body)
                key = data.get('id')
                if key not in SESSIONS or SESSIONS[key].get('owner') != owner:
                    return self.json({'error': '照片会话已结束，请重新导入'}, 404)
                s = SESSIONS[key]
                s['last_seen'] = time.monotonic()
                if route == '/api/close':
                    release_photo(key)
                    return self.json({'ok': True})
                p = settings(data.get('settings', {}))
                if route == '/api/settings':
                    return self.json({'settings':p})
                if route == '/api/inspect':
                    cx, cy = float(data.get('cx', .5)), float(data.get('cy', .5))
                    if not math.isfinite(cx) or not math.isfinite(cy) or not 0 <= cx <= 1 or not 0 <= cy <= 1:
                        raise ValueError('检视位置无效')
                    token = s.setdefault('_cache_id', key)
                    cache_key = (token, 'native-rgb8', json.dumps(p, sort_keys=True, allow_nan=False))
                    pixels = CACHE.get(cache_key)
                    if pixels is None:
                        pixels = np.asarray(develop(decode(s, p['wb'], False), p))
                        pixels.setflags(write=False); CACHE.put(cache_key, pixels)
                    full_h, full_w = pixels.shape[:2]
                    w = min(full_w, max(64, min(1024, int(data.get('width', 640)))))
                    h = min(full_h, max(64, min(1024, int(data.get('height', 480)))))
                    x = max(0, min(full_w-w, round(cx*full_w-w/2)))
                    y = max(0, min(full_h-h, round(cy*full_h-h/2)))
                    tile = Image.fromarray(pixels[y:y+h, x:x+w])
                    return self.json(dict(image='data:image/png;base64,'+base64.b64encode(image_bytes(tile,'PNG')).decode(),
                        width=w,height=h,x=x,y=y,full_width=full_w,full_height=full_h,cx=(x+w/2)/full_w,cy=(y+h/2)/full_h))
                if route == '/api/codex/cancel':
                    job = ADVISOR.get(data.get('job'))
                    if job['source_id'] != key:
                        raise ValueError('任务不属于这张照片')
                    ADVISOR.cancel(data['job'])
                    return self.json({'ok': True})
                if route in ('/api/codex/suggest', '/api/codex/auto'):
                    backend=AI_SETTINGS.snapshot(data.get('backend_revision'))
                    if backend is not None and data.get('backend_revision') is None:
                        raise ValueError('请刷新页面并确认当前模型后端；旧页面不会向新接口发送照片')
                    automatic = route == '/api/codex/auto'
                    auto_options = automation.options(data.get('auto_options')) if automatic else None
                    goal = (codex_assist.AUTO_GOAL+' '+automation.goal(auto_options)) if automatic else data.get('goal', '')
                    if not isinstance(goal, str) or not goal.strip() or len(goal) > 1200:
                        raise ValueError('请输入 1 到 1200 字的调色目标')
                    base = DEFAULTS | {'rotation': p['rotation'], 'crop': p['crop']}
                    before = image_bytes(develop(decode(s, 'camera', True), base), quality=88)
                    current = image_bytes(develop(decode(s, p['wb'], True), p), quality=88)
                    job = ADVISOR.submit(key, before, current, p, goal, s['raw'],
                                         mode='auto' if automatic else 'suggest', request_id=data.get('request_id'), auto_options=auto_options, backend=backend)
                    return self.json({'job': job,
                                      'current_image': 'data:image/jpeg;base64,' + base64.b64encode(current).decode()}, 202)
                if route == '/api/codex/preview':
                    return self.json(preview_result(s, p))
                if route == '/api/preview':
                    return self.json(preview_result(s, p, data.get('before', False)))
                if route == '/api/export':
                    fmt = data.get('format', 'JPEG')
                    if fmt not in ('JPEG', 'PNG', 'TIFF16'):
                        raise ValueError('不支持的导出格式')
                    quality = max(60, min(100, int(data.get('quality', 95))))
                    content = export_bytes(s, p, fmt, quality)
                    ext = {'JPEG': 'jpg', 'PNG': 'png', 'TIFF16': 'tif'}[fmt]
                    name = urllib.parse.quote(Path(s['name']).stem + '-Z30.' + ext)
                    return self.send(content, {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'TIFF16': 'image/tiff'}[fmt],
                                     headers={'Content-Disposition': "attachment; filename*=UTF-8''" + name})
                return self.json({'error': '接口不存在'}, 404)
        except (ValueError, TypeError, KeyError) as exc:
            self.json({'error': str(exc)}, 400)
        except rawpy.LibRawError:
            self.json({'error': '无法解码此 RAW 文件，请确认文件完整且由受支持的相机拍摄'}, 422)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            print(type(exc).__name__, str(exc), flush=True)
            self.json({'error': '处理失败。请检查照片是否完整，或在启动窗口查看详情。'}, 500)


class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = os.name != 'nt'

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def create_server(port=8730):
    try:
        return LocalHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        return LocalHTTPServer(('127.0.0.1', 0), Handler)


def serve(server):
    stop_reaper = threading.Event()
    def maintain_sessions():
        while not stop_reaper.wait(60):
            reap_sessions()
    reaper = threading.Thread(target=maintain_sessions, daemon=True)
    reaper.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_started = time.monotonic()
        def note(message):
            logging.info('%s (shutdown %.2fs)', message, time.monotonic() - shutdown_started)
        note('Stopping session maintenance')
        stop_reaper.set(); reaper.join(timeout=2)
        note('Closing HTTP listener')
        server.server_close()
        note('Stopping Codex workers')
        ADVISOR.shutdown()
        if EXPORTS is not None:
            note('Stopping export workers')
            export_started = time.monotonic()
            EXPORTS.shutdown()
            waited = time.monotonic() - export_started
            if waited > 2:
                logging.warning('Export shutdown waited %.2fs for an in-flight item', waited)
        note('Removing temporary photo copies')
        TEMP.cleanup()
        note('Local service stopped')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8730)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    server = create_server(args.port)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Z30 Studio ready: {url}', flush=True)
    print('Photos stay on this computer. Close this window or press Ctrl+C to stop.', flush=True)
    if not args.no_browser:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    serve(server)


if __name__ == '__main__':
    main()

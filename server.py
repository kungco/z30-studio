"""Z30 Studio: loopback-only, non-destructive RAW development."""
import argparse
from contextlib import contextmanager,ExitStack
import hashlib
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
import diagnostics
import drafts
import darkroom
import photo_workflow as workflow
import local_vision
import lens_tools
import colour_management as colour
import export_tools
import native_cache
import ai_preflight
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
SESSION_TTL = 24 * 60 * 60
MAX_UPLOAD = 150 * 1024 * 1024
ICC = colour.profile('srgb')
ADVISOR = codex_assist.Advisor()
AI_SETTINGS = api_backend.Settings()
CACHE = ByteLRU(128 * 1024 * 1024)
EXPORTS = None
EXPORTS_LOCK = threading.RLock()
STORES_LOCK = threading.RLock()
STORES = {}
VISIONS = {}
AI_TICKETS=ai_preflight.Tickets()
DEFAULTS = dict(exposure=0, contrast=0, highlights=0, shadows=0,
                temperature=0, tint=0, saturation=0, vibrance=0,
                sharpen=0, rotation=0, crop='original', wb='camera') | spatial.DEFAULTS | darkroom.DEFAULTS | lens_tools.DEFAULTS | colour.DEFAULTS | {'lens_profile':'手动 / 未校准'}
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
    if p['crop'] not in ('original', 'free', '3:2', '4:3', '1:1', '16:9'):
        raise ValueError('无效裁剪比例')
    if p['wb'] not in ('camera', 'auto', 'daylight'):
        raise ValueError('无效白平衡')
    p['masks'] = spatial.masks(data.get('masks', []))
    p['repairs'] = spatial.repairs(data.get('repairs', []))
    p['skin_mask'] = data.get('skin_mask','')
    if not isinstance(p['skin_mask'],str):raise ValueError('人像区域格式无效')
    if p['skin_mask']:spatial.bitmap_field(p['skin_mask'])
    p['lens_profile'] = str(data.get('lens_profile', '手动 / 未校准'))[:100]
    p.update(darkroom.normalize(data))
    p.update(lens_tools.normalize(data))
    p.update(colour.normalize(data))
    return p


def source_wb(session,p):
    return 'daylight' if session.get('raw') and p.get('wb_mode','legacy')!='legacy' else p['wb']


def baseline_settings(p):
    return DEFAULTS | {k:p[k] for k in ('rotation','crop','crop_rect','straighten',*colour.DEFAULTS) if k in p}


def decode(session, wb, preview, space="srgb"):
    if session.get('asset'):
        stamp = (session['path'].stat().st_mtime_ns, session['path'].stat().st_size)
        if session.get('verified_stamp') != stamp:
            get_drafts().asset_path(session['asset'], verify=True)
            session['verified_stamp'] = stamp
    token = session.setdefault('_cache_id', session.get('meta', {}).get('id') or secrets.token_hex(16))
    cache_key = (token, 'linear-wide' if space!='srgb' else 'linear', wb)
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
                                  output_color=rawpy.ColorSpace.sRGB if space=="srgb" else rawpy.ColorSpace.ProPhoto, user_flip=0)
        arr = raw_pixels(rgb, sizes, preview)
        del rgb
        if space!="srgb":arr=colour.to_working(arr,"prophoto")
    else:
        with Image.open(session['path']) as source:
            arr, policy, warnings = raster_linear(source,wide=space!="srgb")
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


def decode_settings(session,p,preview):
    if p.get('output_space','srgb')=='srgb':return decode(session,source_wb(session,p),preview)
    return decode(session,source_wb(session,p),preview,p['output_space'])


def get_profiles():
    root=data_root()/'profiles'
    with STORES_LOCK:
        if root not in STORES:STORES[root]=colour.Profiles(root)
        return STORES[root]


def get_native_cache():
    root=data_root()/'native-cache'
    with STORES_LOCK:
        if root not in STORES:STORES[root]=native_cache.NativeCache(root)
        return STORES[root]


def source_digest(session):
    path=Path(session['path']);stat=path.stat();stamp=(stat.st_mtime_ns,stat.st_size)
    if session.get('asset') and session.get('verified_stamp')!=stamp:
        get_drafts().asset_path(session['asset'],verify=True);session['verified_stamp']=stamp
    if session.get('_digest_stamp')!=stamp:
        with path.open('rb') as file:session['_digest']=hashlib.file_digest(file,'sha256').hexdigest()
        session['_digest_stamp']=stamp
    return session['_digest']


@contextmanager
def native_pixels(session,p,progress=None):
    cache=get_native_cache();digest=source_digest(session);wide=p['output_space']!='srgb'
    linear_key=native_cache.key(['linear',digest,session['raw'],source_wb(session,p),wide])
    core={k:v for k,v in p.items() if k not in ('display_profile','proof_profile','proof_enabled','proof_intent','proof_paper','proof_gamut')}
    render_key=native_cache.key(['render',linear_key,core])
    def build_linear(path):
        if progress:progress('decode',.02)
        if session['raw']:
            from imaging import orient
            wb=source_wb(session,p)
            with rawpy.imread(str(session['path'])) as raw:
                sizes=raw.sizes
                if sizes.pixel_aspect!=1:raise ValueError('非方形像素 RAW 尚未验证')
                rgb=raw.postprocess(output_bps=16,gamma=(1,1),no_auto_bright=True,use_camera_wb=wb=='camera',
                    use_auto_wb=wb=='auto',half_size=False,output_color=rawpy.ColorSpace.ProPhoto if wide else rawpy.ColorSpace.sRGB,user_flip=0)
            if rgb.shape[:2]!=(sizes.height,sizes.width):raise ValueError('RAW 解码尺寸不一致')
            x,y,w,h=raw_crop(sizes);view=orient(rgb[y:y+h,x:x+w],int(sizes.flip))
            with native_cache.writer(path,view.shape) as write:
                for top in range(0,len(view),128):
                    bottom=min(len(view),top+128);tile=np.multiply(view[top:bottom],np.float32(1/65535),dtype=np.float32)
                    if wide:tile=colour.to_working(tile,'prophoto')
                    write(top,bottom,tile)
                    if progress:progress('decode',.02+.13*bottom/len(view))
        else:
            arr=decode_settings(session,p,False)
            with native_cache.writer(path,arr.shape) as write:
                for top in range(0,len(arr),128):write(top,min(len(arr),top+128),arr[top:top+128])
    def build_render(path):
        with ExitStack() as leases:
            linear=leases.enter_context(cache.lease(linear_key,build_linear))
            with native_cache.Scratch(cache.root,progress,linear,leases.close) as scratch:
                if progress:progress('prepare',.18)
                prepared=spatial.prepare(linear,p);native_cache.release_input(prepared)
                prepared=geometry(prepared,p);native_cache.release_input(prepared);scratch.retain(prepared)
                prepared=spatial.retouch(prepared,p);native_cache.release_input(prepared);scratch.retain(prepared)
                with native_cache.writer(path,prepared.shape) as sink:render_array(prepared,p,bits=32,sink=sink,progress=progress)
    with cache.lease(render_key,build_render,progress) as pixels:yield pixels


def geometry(arr, p):
    if p['rotation']:
        arr = np.rot90(arr, -p['rotation'])
    arr = darkroom.geometry(arr,p)
    if p['crop'] not in ('original','free'):
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
    image=Image.fromarray(render_array(spatial.retouch(geometry(spatial.prepare(arr, p), p),p), p, bits=8))
    image.info['icc_profile']=colour.profile(p.get('output_space','srgb'));return image


def image_bytes(im, fmt='JPEG', quality=93):
    b = io.BytesIO()
    opts = {'quality': quality, 'subsampling': 0} if fmt == 'JPEG' else {}
    im.save(b, format=fmt, icc_profile=im.info.get('icc_profile',ICC), **opts)
    return b.getvalue()


def export_bytes(session, p, fmt='JPEG', quality=95, options=None, progress=None):
    if fmt not in ('JPEG', 'PNG', 'TIFF16'):
        raise ValueError('不支持的导出格式')
    if session.get('path') and Path(session['path']).is_file():
        with native_pixels(session,p,progress) as pixels:
            return export_tools.encode(pixels,p['output_space'],fmt,quality,session['path'],options or {},progress)
    arr = decode_settings(session,p,False)
    if options is not None:
        pixels=render_array(spatial.retouch(geometry(spatial.prepare(arr,p),p),p),p,bits=32)
        return export_tools.encode(pixels,p['output_space'],fmt,quality,session['path'],options)
    if fmt == 'TIFF16':
        import tifffile
        pixels = render_array(spatial.retouch(geometry(spatial.prepare(arr, p), p),p), p, bits=16)
        out = io.BytesIO()
        tifffile.imwrite(out, pixels, photometric='rgb', iccprofile=colour.profile(p.get("output_space","srgb")),
                         metadata=None, compression='deflate', maxworkers=1)
        return out.getvalue()
    return image_bytes(develop(arr, p), fmt, quality)


def export_snapshot(path, is_raw, params, fmt, quality, protect, options=None, progress=None):
    session = dict(path=path, raw=is_raw, cache={})
    p = settings(params)
    metrics = None
    try:
        if protect:
            base = p | {k: DEFAULTS[k] for k in codex_assist.RANGES}
            p, metrics = quality_checks.guard(decode_settings(session,p,True), base, p, develop)
        return export_bytes(session, p, fmt, quality, options,progress), p, metrics
    finally:
        if session.get('_cache_id'):
            CACHE.remove_source(session['_cache_id'])


def get_exports():
    global EXPORTS
    with EXPORTS_LOCK:
        if EXPORTS is None:
            EXPORTS = ExportQueue(data_root() / 'exports', export_snapshot)
        return EXPORTS


def get_drafts():
    root = data_root() / 'drafts'
    with STORES_LOCK:
        if root not in STORES:
            STORES[root] = drafts.DraftStore(root)
        return STORES[root]


def get_vision():
    root=Path(os.environ.get('Z30_LOCAL_MODEL_DIR',data_root()/'models')).resolve()
    with STORES_LOCK:
        if root not in VISIONS:VISIONS[root]=local_vision.LocalVision(root)
        return VISIONS[root]


def recipe(data):
    p = settings(data.get('settings', {}))
    h = projects.history(data.get('history'), settings)
    if h and h['states'][h['index']]['settings'] != p:
        raise ValueError('草稿参数与历史不一致，未覆盖上次自动保存')
    return dict(settings=p, history=h, preset=str(data.get('preset', 'custom'))[:32],
                undo=projects.undo(data.get('undo'), settings), analysis=projects.analysis(data.get('analysis'), settings),
                catalog=workflow.catalog(data.get('catalog')))


def save_draft(data, owner):
    ids = data.get('ids', [])
    if not isinstance(ids, list) or len(ids) > projects.MAX_PHOTOS or any(not isinstance(k,str) for k in ids) or len(set(ids)) != len(ids):
        raise ValueError('草稿照片数量或编号无效')
    with LOCK:
        if any(k not in SESSIONS or SESSIONS[k].get('owner') != owner for k in ids):
            raise ValueError('草稿包含已关闭的照片；上次自动保存未改变')
        sources = [SESSIONS[k] for k in ids]
        recipes = [recipe(data.get('recipes', {}).get(k, {})) for k in ids]
        for k in ids:
            PROJECT_READERS[k] = PROJECT_READERS.get(k, 0) + 1
    try:
        store = get_drafts(); photos = []
        for source, edit in zip(sources, recipes):
            asset = source.get('asset') or store.add_asset(source['path'])
            source['asset'] = asset
            photos.append(dict(asset=asset, meta=source['meta'], thumbnail=source.get('thumbnail'), **edit))
        document = dict(photos=photos, selected=max(0, min(len(photos)-1, int(data.get('selected', 0)))))
        revision = store.save(data['draft_id'], document, data.get('expected_version', 0), data['request_id'], bool(data.get('snapshot', False)))
        return dict(draft_id=data['draft_id'], revision=revision, saved=time.time())
    finally:
        with LOCK:
            for k in ids:
                PROJECT_READERS[k] -= 1
                if not PROJECT_READERS[k]:
                    del PROJECT_READERS[k]


def restore_draft(data, owner):
    store = get_drafts(); saved = store.load(data['draft_id'], data.get('revision'))
    document = saved['document']; photos = document['photos']
    # Check every original's presence before adding any sessions. Full checksums
    # run lazily before decode; opening a 200-photo draft does not decode 200 NEFs.
    paths = [store.asset_path(photo['asset']) for photo in photos]
    edits = [recipe(photo) for photo in photos]
    with LOCK:
        if len(SESSIONS) + len(photos) > 1024 or sum(s.get('owner') == owner for s in SESSIONS.values()) + len(photos) > projects.MAX_PHOTOS:
            raise ValueError('请先关闭当前工作区，再恢复这个草稿')
        result = []
        for photo, path, edit in zip(photos, paths, edits):
            key = secrets.token_hex(16)
            meta = {k: photo['meta'][k] for k in ('name','width','height','camera','raw','bytes')}
            meta['id'] = key
            meta.update({k:photo['meta'][k] for k in ('lens_model','focal_mm','aperture','sensor_crop','focus_m') if k in photo['meta']})
            if 'lens_model' not in meta:meta.update(lens_tools.metadata(path,meta['camera']))
            session = dict(path=path, name=meta['name'], raw=meta['raw'], cache={}, owner=owner,
                           persistent=True, asset=photo['asset'], meta=meta, thumbnail=photo.get('thumbnail'), last_seen=time.monotonic())
            SESSIONS[key] = session
            result.append(meta | edit | dict(image=photo.get('thumbnail'), thumbnail=photo.get('thumbnail')))
        return dict(photos=result, selected=document['selected'], draft_id=saved['id'], revision=saved['revision'],
                    restored_revision=saved['restored_revision'])


def data_url(im):
    return 'data:image/jpeg;base64,' + base64.b64encode(image_bytes(im, quality=90)).decode()


def preview_result(session, p, include_before=False):
    token = session.setdefault('_cache_id', session.get('meta', {}).get('id') or secrets.token_hex(16))
    cache_key = (token, 'render', json.dumps(p, sort_keys=True, allow_nan=False), bool(include_before))
    cached = CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    arr = decode_settings(session,p,True)
    im = develop(arr, p)
    if not session.get('thumbnail'):
        thumb = im.copy(); thumb.thumbnail((240, 240)); session['thumbnail'] = data_url(thumb)
    a = np.asarray(im)
    hist = [np.histogram(a[:, :, c], bins=128, range=(0, 256))[0].tolist() for c in range(3)]
    display=get_profiles().preview(im,p)
    result = dict(image=data_url(display), histogram=hist, width=im.width, height=im.height, thumbnail=session['thumbnail'])
    result['quality'] = quality_checks.measure(im)
    if include_before:
        base = baseline_settings(p)
        before_key = (token, 'baseline', json.dumps(base,sort_keys=True))
        before = CACHE.get(before_key)
        if before is None:
            before = result['image'] if p == base else data_url(get_profiles().preview(develop(decode_settings(session,base,True), base),base))
            CACHE.put(before_key, before)
        result['before'] = before
    CACHE.put(cache_key, result)
    return result


def filter_advice(source_id, base, result):
    with LOCK:
        if source_id not in SESSIONS:
            raise ValueError('照片会话已关闭，无法检查辅助建议')
        session = SESSIONS[source_id]
        linear = decode_settings(session,base,True)
        checked, metrics = quality_checks.guard(linear, base, result['settings'], develop)
    return {**result, 'settings': checked, 'quality': metrics}


ADVISOR.result_filter = filter_advice


def release_photo(key):
    if PROJECT_READERS.get(key):
        raise ValueError('项目正在准备或下载，请稍后移除照片')
    session = SESSIONS.pop(key)
    ADVISOR.cancel_source(key)
    AI_TICKETS.drop_source(key)
    CACHE.remove_source(key)
    session['cache'].clear()
    if not session.get('persistent'):
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


def import_image(body, name, owner=None, make_preview=True):
    ext = Path(name).suffix.lower()
    if ext not in ('.nef', '.jpg', '.jpeg', '.png'):
        raise ValueError('请选择 NEF、JPG 或 PNG 照片')
    reap_sessions()
    if sum(s.get('owner') == owner for s in SESSIONS.values()) >= projects.MAX_PHOTOS:
        raise ValueError('当前已打开 256 张照片，请分组编辑')
    if len(SESSIONS) >= 1024:
        raise ValueError('本机照片会话已达上限，请关闭不用的照片或等待过期会话回收')
    if sum(s['meta']['bytes'] for s in SESSIONS.values() if s.get('owner') == owner) + len(body) > projects.PROJECT_LIMIT:
        raise ValueError('当前工作区原片超过 8 GiB，请分组编辑')
    if shutil.disk_usage(TEMP.name).free < len(body) + 256 * 1024 ** 2:
        raise ValueError('本机临时存储空间不足，请先释放磁盘空间')
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
        session['meta'].update(lens_tools.metadata(path,model))
        result = preview_result(session, DEFAULTS, True) if make_preview else {}
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
        logging.info(fmt, *args)

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
        if route == '/api/diagnostics':
            return self.send(diagnostics.bundle(data_root(), APP_VERSION), 'application/zip',
                             headers={'Content-Disposition': 'attachment; filename="Z30-diagnostics.zip"'})
        if route == '/api/drafts' or route.startswith('/api/drafts/'):
            try:
                store = get_drafts()
                if route == '/api/drafts':
                    return self.json(store.list())
                parts = route.split('/')
                if len(parts) == 5 and parts[4] == 'versions':
                    return self.json({'versions': store.version_list(parts[3])})
                raise ValueError('草稿接口不存在')
            except (ValueError, OSError) as exc:
                return self.json({'error': str(exc)}, 400)
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
        if route == '/api/local-models':
            return self.json(get_vision().status())
        if route == '/api/lens/profiles':
            db=lens_tools.database()
            return self.json(dict(profiles=[dict(id=p['id'],name=p['name'],crop=p['crop'],
                min_focal=min(r['focal'] for r in p['distortion']),max_focal=max(r['focal'] for r in p['distortion']),
                has_tca=bool(p['tca']),has_vignetting=bool(p['vignetting'])) for p in db['profiles']],
                source=db['source'],license=db['license'],source_sha256=db['source_sha256']))
        if route == '/api/codex/status':
            config=AI_SETTINGS.public()
            if config['provider']=='codex':return self.json(codex_assist.status() | {'provider':'codex'})
            if config['provider']=='local':return self.json(dict(provider='local',available=True,ready=True,message='本机接口已配置；请测试连接，实际视觉推理尚未验证。不使用 API Key'))
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
        if route == '/api/cache':
            return self.json(get_native_cache().stats())
        if route == '/api/colour/profiles':
            return self.json(dict(profiles=get_profiles().list(),spaces=colour.SPACES))
        if route == '/api/exports' or route.startswith('/api/exports/'):
            try:
                queue = get_exports()
                if route == '/api/exports':
                    return self.json({'jobs': queue.list(), 'storage_bytes': queue.disk_bytes(), 'limit_bytes': queue.max_bytes,
                                      'recovery_warnings': queue.recovery_warnings})
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
                 '/ai-controls.js': ('ai-controls.js','application/javascript; charset=utf-8'),
                 '/output-controls.js': ('output-controls.js','application/javascript; charset=utf-8'),
                 '/lens-controls.js': ('lens-controls.js','application/javascript; charset=utf-8'),
                 '/local-tools.js': ('local-tools.js','application/javascript; charset=utf-8'),
                 '/library-workflow.js': ('library-workflow.js','application/javascript; charset=utf-8'),
                 '/darkroom-controls.js': ('darkroom-controls.js', 'application/javascript; charset=utf-8'),
                 '/draft-save.js': ('draft-save.js', 'application/javascript; charset=utf-8'),
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
            return self.json({'error': '项目为空或超过 8 GiB'}, 413)
        if shutil.disk_usage(TEMP.name).free < size * 3 + 256 * 1024 ** 2:
            self.close_connection = True
            return self.json({'error': '打开项目需临时解包空间，当前磁盘可用空间不足'}, 413)
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
                if sum(x.get('owner') == owner for x in SESSIONS.values()) + len(document['photos']) > projects.MAX_PHOTOS:
                    raise ValueError('打开项目后超过 256 张，请先移除照片')
                photos = []
                for photo in document['photos']:
                    name = str(photo['name']).replace('\\', '/').split('/')[-1][:200]
                    eager = len(document['photos']) <= 8 or len(photos) == int(document.get('selected',0))
                    result = import_image(photo['staged'].read_bytes(), name, owner, make_preview=eager)
                    key = result['id']; imported.append(key)
                    preview = preview_result(SESSIONS[key], photo['settings'], True) if eager else {}
                    photos.append(preview | SESSIONS[key]['meta'] | dict(settings=photo['settings'], history=photo.get('history'),
                                  preset=photo.get('preset','custom'), analysis=photo.get('analysis'), undo=photo.get('undo')))
                    photos[-1]['catalog']=photo['catalog']
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
            if route.startswith('/api/drafts/'):
                if owner is None:
                    raise ValueError('页面会话编号缺失')
                data = json.loads(body)
                if route == '/api/drafts/save':
                    return self.json(save_draft(data, owner))
                if route == '/api/drafts/restore':
                    return self.json(restore_draft(data, owner))
                if route == '/api/drafts/delete':
                    with LOCK:
                        protected = [s['asset'] for s in SESSIONS.values() if s.get('asset')]
                        get_drafts().delete(data['draft_id'], protected)
                    return self.json({'ok': True})
                raise ValueError('草稿接口不存在')
            if route == '/api/local-models/install':
                return self.json(get_vision().install())
            if route in ('/api/ai/config','/api/ai/test'):
                data=json.loads(body)
                if route=='/api/ai/test':
                    config=AI_SETTINGS.snapshot()
                    if config is None:return self.json(codex_assist.status() | {'model_listed':True})
                    return self.json(api_backend.test_connection(config))
                with ADVISOR.lock:
                    if ADVISOR.workers:raise ValueError('正在分析或取消中，请等任务结束后更换模型配置')
                    return self.json(AI_SETTINGS.save(data))
            if route == '/api/edits/sync':
                if owner is None:raise ValueError('页面会话编号缺失')
                data=json.loads(body);targets=data.get('targets')
                if not isinstance(targets,list) or not 1<=len(targets)<=projects.MAX_PHOTOS:
                    raise ValueError('请选择 1–256 张同步照片')
                if len({t['id'] for t in targets})!=len(targets):raise ValueError('同步列表有重复照片')
                reference=settings(data.get('source',{}))
                with LOCK:
                    if any(t['id'] not in SESSIONS or SESSIONS[t['id']].get('owner')!=owner for t in targets):
                        raise ValueError('同步照片不属于当前页面或已经关闭')
                    result=[]
                    for target in targets:
                        p=settings(workflow.transfer(settings(target.get('settings',{})),reference,data.get('groups')))
                        if not SESSIONS[target['id']]['raw']:p['wb']='camera'
                        result.append(dict(id=target['id'],settings=p))
                return self.json({'photos':result})
            if route == '/api/projects/prepare':
                data = json.loads(body); ids = data.get('ids')
                if not isinstance(ids, list) or not 1 <= len(ids) <= projects.MAX_PHOTOS or len(set(ids)) != len(ids):
                    raise ValueError('请选择 1–256 张不同照片')
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
                    if not isinstance(recipes, dict) or len(recipes) > projects.MAX_PHOTOS:
                        raise ValueError('恢复参数格式无效')
                    photos = []
                    for key, session in owned:
                        recipe = recipes.get(key, {})
                        p = settings(recipe.get('settings', {}))
                        if not session['raw']:
                            p['wb'] = 'camera'
                        eager = len(owned) <= 8 or key == data.get('selected', owned[0][0])
                        preview = preview_result(session, p, True) if eager else dict(image=session.get('thumbnail'))
                        item = preview | session['meta'] | {'settings': p, 'thumbnail':session.get('thumbnail'),
                                'previewWidth': preview.get('width'), 'previewHeight': preview.get('height')}
                        item['history'] = projects.history(recipe.get('history'), settings)
                        item['catalog'] = workflow.catalog(recipe.get('catalog'))
                        if recipe.get('undo'):
                            item['undo'] = {'settings': settings(recipe['undo'].get('settings', {})), 'preset': recipe['undo'].get('preset', 'custom')}
                        photos.append(item)
                    return self.json({'photos': photos, 'ttl_seconds': SESSION_TTL})
            if route == '/api/cache/clear':
                get_native_cache().clear();return self.json(get_native_cache().stats())
            if route == '/api/colour/profiles':
                return self.json(get_profiles().add(body),201)
            if route.startswith('/api/exports/'):
                data = json.loads(body)
                queue = get_exports()
                if route == '/api/exports/create':
                    ids = data.get('ids')
                    if (not isinstance(ids, list) or not 1 <= len(ids) <= projects.MAX_PHOTOS or
                            any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids)):
                        raise ValueError('请选择 1 到 256 张不同照片')
                    p = settings(data.get('settings', {}))
                    recipes=data.get('recipes');per_photo=None
                    if recipes is not None:
                        if not isinstance(recipes,dict) or set(recipes)!=set(ids):raise ValueError('每张照片的导出参数不完整')
                        per_photo=[settings(recipes[key]) for key in ids]
                    options=export_tools.normalize(data['options']) if 'options' in data else None
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
                        for source_id in ids:
                            PROJECT_READERS[source_id] = PROJECT_READERS.get(source_id, 0) + 1
                    try:
                        key = queue.submit(sources, p, fmt, max(60, min(100, int(data.get('quality', 95)))), protect, options=options, per_photo=per_photo)
                    finally:
                        with LOCK:
                            for source_id in ids:
                                PROJECT_READERS[source_id] -= 1
                                if not PROJECT_READERS[source_id]:
                                    del PROJECT_READERS[source_id]
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
                if route == '/api/lens/apply':
                    update=lens_tools.select(s['meta'],data.get('options',{}))
                    return self.json({'settings':settings(p|update),'message':'已应用 Lensfun 配置；手动镜头系数已重置，请检查边缘。尚未做本机实镜网格标定。'})
                if route == '/api/lens/tca':
                    return self.json(lens_tools.estimate_tca(decode_settings(s,p,True),p))
                if route == '/api/repairs/place':
                    edit=dict(data.get('repair',{}));edit=spatial.repairs([edit])[0]
                    if data.get('auto_source',False):
                        arr=geometry(spatial.prepare(decode_settings(s,p,True),p),p)
                        edit['sx'],edit['sy']=spatial.choose_donor(arr,edit['x'],edit['y'],edit['radius'])
                    return self.json({'repair':edit})
                if route == '/api/masks/semantic':
                    im=colour.as_srgb(develop(decode_settings(s,p,True),p))
                    result=get_vision().segment(im,data.get('kind','subject'))
                    mask=spatial.masks([dict(type='semantic',bitmap=result.pop('bitmap'),label={'subject':'主体','person':'人像','sky':'天空'}[result['kind']],exposure=0)])[0]
                    return self.json(result|{'mask':mask})
                if route == '/api/masks/preview':
                    index=int(data.get('index',0))
                    if not 0<=index<len(p['masks']):raise ValueError('请选择蒙版')
                    arr=geometry(spatial.prepare(decode_settings(s,p,True),p),p);h,w=arr.shape[:2]
                    field=spatial.mask_field(p['masks'][index],w,h,0,h,arr)
                    rgba=np.zeros((h,w,4),np.uint8);rgba[...,:3]=[237,203,104];rgba[...,3]=np.rint(field*150).astype(np.uint8)
                    out=io.BytesIO();Image.fromarray(rgba).save(out,'PNG')
                    return self.json({'image':'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()})
                if route == '/api/wb/sample':
                    x,y=float(data.get('x',.5)),float(data.get('y',.5))
                    if not math.isfinite(x) or not math.isfinite(y) or not 0<=x<=1 or not 0<=y<=1:
                        raise ValueError('白平衡取样坐标无效')
                    arr=geometry(spatial.prepare(decode_settings(s,p|{'wb':'daylight' if s['raw'] else 'camera','wb_mode':'legacy'},True),p),p)
                    return self.json({'wb_gains':darkroom.sample_neutral(arr,x,y),'reference':'daylight' if s['raw'] else 'encoded image'})
                if route == '/api/settings':
                    return self.json({'settings':p})
                if route == '/api/inspect':
                    cx, cy = float(data.get('cx', .5)), float(data.get('cy', .5))
                    if not math.isfinite(cx) or not math.isfinite(cy) or not 0 <= cx <= 1 or not 0 <= cy <= 1:
                        raise ValueError('检视位置无效')
                    with native_pixels(s,p) as pixels:
                        full_h,full_w=pixels.shape[:2]
                        w=min(full_w,max(64,min(1024,int(data.get('width',640)))))
                        h=min(full_h,max(64,min(1024,int(data.get('height',480)))))
                        x=max(0,min(full_w-w,round(cx*full_w-w/2)));y=max(0,min(full_h-h,round(cy*full_h-h/2)))
                        tile=Image.fromarray(np.rint(pixels[y:y+h,x:x+w]*255).astype(np.uint8))
                    full_h, full_w = int(full_h),int(full_w)
                    w = min(full_w, max(64, min(1024, int(data.get('width', 640)))))
                    h = min(full_h, max(64, min(1024, int(data.get('height', 480)))))
                    x = max(0, min(full_w-w, round(cx*full_w-w/2)))
                    y = max(0, min(full_h-h, round(cy*full_h-h/2)))
                    tile.info['icc_profile']=colour.profile(p['output_space']);tile=get_profiles().preview(tile,p)
                    return self.json(dict(image='data:image/png;base64,'+base64.b64encode(image_bytes(tile,'PNG')).decode(),
                        width=w,height=h,x=x,y=y,full_width=full_w,full_height=full_h,cx=(x+w/2)/full_w,cy=(y+h/2)/full_h))
                if route == '/api/codex/cancel':
                    job = ADVISOR.get(data.get('job'))
                    if job['source_id'] != key:
                        raise ValueError('任务不属于这张照片')
                    ADVISOR.cancel(data['job'])
                    return self.json({'ok': True})
                if route in ('/api/codex/suggest', '/api/codex/auto','/api/ai/preflight'):
                    preview_only=route=='/api/ai/preflight'
                    config=AI_SETTINGS.public()
                    if preview_only and data.get('backend_revision')!=config['revision']:raise ValueError('模型设置已改变，请重新打开设置后再预览')
                    backend=None if preview_only else AI_SETTINGS.snapshot(data.get('backend_revision'))
                    if backend is not None and data.get('backend_revision') is None:
                        raise ValueError('请刷新页面并确认当前模型后端；旧页面不会向新接口发送照片')
                    automatic = route == '/api/codex/auto' or preview_only and data.get('mode','auto')=='auto'
                    auto_options = automation.options(data.get('auto_options')) if automatic else None
                    goal = (codex_assist.AUTO_GOAL+' '+automation.goal(auto_options)) if automatic else data.get('goal', '')
                    if not isinstance(goal, str) or not goal.strip() or len(goal) > 1200:
                        raise ValueError('请输入 1 到 1200 字的调色目标')
                    goal=goal.strip()
                    base = baseline_settings(p)
                    mode='auto' if automatic else 'suggest'
                    if data.get('preview_token') and not preview_only:
                        before,current=AI_TICKETS.get(data['preview_token'],key,owner,p,goal,mode,auto_options,config['revision'])
                    else:
                        before = image_bytes(colour.as_srgb(develop(decode_settings(s,base,True), base)), quality=88)
                        current = image_bytes(colour.as_srgb(develop(decode_settings(s,p,True), p)), quality=88)
                    if preview_only:
                        tag=api_backend.backend_tag(config)
                        with ADVISOR.lock:timings=[j['seconds'] for j in ADVISOR.jobs.values() if j.get('backend_tag')==tag and j['state']=='done' and 'seconds' in j]
                        return self.json(AI_TICKETS.issue(key,owner,p,goal,mode,auto_options,config|{'_is_raw':s['raw']},before,current,timings))
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
                    options=export_tools.normalize(data['options']) if 'options' in data else None
                    content = export_bytes(s, p, fmt, quality,options)
                    ext = {'JPEG': 'jpg', 'PNG': 'png', 'TIFF16': 'tif'}[fmt]
                    name = urllib.parse.quote(Path(s['name']).stem + '-Z30.' + ext)
                    return self.send(content, {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'TIFF16': 'image/tiff'}[fmt],
                                     headers={'Content-Disposition': "attachment; filename*=UTF-8''" + name})
                return self.json({'error': '接口不存在'}, 404)
        except drafts.Conflict as exc:
            self.json({'error': str(exc)}, 409)
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


def serve(server, shutdown_phase=None):
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
            if shutdown_phase:
                shutdown_phase(message)
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
        note('Closing full-size caches')
        for store in list(STORES.values()):
            if isinstance(store,native_cache.NativeCache):store.close()
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

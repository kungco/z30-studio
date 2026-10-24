"""Z30 Studio: loopback-only, non-destructive RAW development."""
import argparse
import base64
import io
import json
import math
import os
from pathlib import Path
import secrets
import tempfile
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import codex_assist
from imaging import raw_crop, raw_pixels, raster_linear, resize_linear, srgb_to_linear

os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np
import rawpy
from PIL import Image, ImageCms, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent
TEMP = tempfile.TemporaryDirectory(prefix='z30-studio-')
SESSIONS = {}
LOCK = threading.RLock()  # Serialize LibRaw work and cap peak export memory.
MAX_UPLOAD = 150 * 1024 * 1024
ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
ADVISOR = codex_assist.Advisor()
DEFAULTS = dict(exposure=0, contrast=0, highlights=0, shadows=0,
                temperature=0, tint=0, saturation=0, vibrance=0,
                sharpen=0, rotation=0, crop='original', wb='camera')
LIMITS = dict(exposure=(-4, 4), contrast=(-100, 100), highlights=(-100, 100),
              shadows=(-100, 100), temperature=(-100, 100), tint=(-100, 100),
              saturation=(-100, 100), vibrance=(-100, 100), sharpen=(0, 100))


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
    return p


def decode(session, wb, preview):
    if preview and wb in session['cache']:
        return session['cache'][wb]
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
        session['cache'][wb] = arr
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
    """32-bit operations, row chunks for full-resolution exports; sRGB output."""
    arr = geometry(arr, p)
    out = np.empty(arr.shape, dtype=np.uint8)
    gains = np.array([2 ** (p['temperature'] / 180),
                      2 ** (-p['tint'] / 220), 2 ** (-p['temperature'] / 180)], dtype=np.float32)
    for start in range(0, arr.shape[0], 256):
        x = arr[start:start + 256].astype(np.float32, copy=True)
        x *= (2 ** p['exposure']) * gains
        # Reduce brightness of highlight range before the display transform.
        lum = x @ np.array([.2126, .7152, .0722], dtype=np.float32)
        x *= np.exp2((p['highlights'] / 100) * np.clip(lum, 0, 1) ** 2)[..., None]
        x = np.maximum(x, 0)
        x = np.where(x <= .0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - .055)
        lum = x @ np.array([.2126, .7152, .0722], dtype=np.float32)
        x += (p['shadows'] / 100 * .32 * np.clip(1 - lum, 0, 1) ** 3)[..., None]
        x = (x - .5) * (1 + p['contrast'] / 125) + .5
        lum = x @ np.array([.2126, .7152, .0722], dtype=np.float32)
        chroma = x.max(axis=2) - x.min(axis=2)
        sat = (1 + p['saturation'] / 100) * (1 + p['vibrance'] / 100 * (1 - np.clip(chroma, 0, 1)))
        x = lum[..., None] + (x - lum[..., None]) * sat[..., None]
        out[start:start + 256] = np.rint(np.clip(x, 0, 1) * 255).astype(np.uint8)
    im = Image.fromarray(out)
    if p['sharpen']:
        im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=round(p['sharpen'] * 2), threshold=3))
    return im


def image_bytes(im, fmt='JPEG', quality=93):
    b = io.BytesIO()
    opts = {'quality': quality, 'subsampling': 0} if fmt == 'JPEG' else {}
    im.save(b, format=fmt, icc_profile=ICC, **opts)
    return b.getvalue()


def data_url(im):
    return 'data:image/jpeg;base64,' + base64.b64encode(image_bytes(im, quality=90)).decode()


def preview_result(session, p, include_before=False):
    arr = decode(session, p['wb'], True)
    im = develop(arr, p)
    a = np.asarray(im)
    hist = [np.histogram(a[:, :, c], bins=128, range=(0, 256))[0].tolist() for c in range(3)]
    result = dict(image=data_url(im), histogram=hist, width=im.width, height=im.height)
    if include_before:
        base = DEFAULTS | {'rotation': p['rotation'], 'crop': p['crop']}
        result['before'] = data_url(develop(decode(session, 'camera', True), base))
    return result


def import_image(body, name):
    ext = Path(name).suffix.lower()
    if ext not in ('.nef', '.jpg', '.jpeg', '.png'):
        raise ValueError('请选择 NEF、JPG 或 PNG 照片')
    if len(SESSIONS) >= 8:
        raise ValueError('当前已打开 8 张照片，请移除照片后再导入')
    key = secrets.token_hex(16)
    path = Path(TEMP.name) / (key + ext)
    path.write_bytes(body)
    session = dict(path=path, name=name, raw=ext == '.nef', cache={})
    try:
        if session['raw']:
            with rawpy.imread(str(path)) as raw:
                _, _, w, h = raw_crop(raw.sizes)
                if raw.sizes.flip in (5, 6):
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
            return self.json(dict(app='Z30 Studio', version='1.1', rawpy=rawpy.__version__,
                                  libraw='.'.join(map(str, rawpy.libraw_version))))
        if route == '/api/codex/status':
            return self.json(codex_assist.status())
        if route.startswith('/api/codex/jobs/'):
            try:
                return self.json(ADVISOR.get(route.rsplit('/', 1)[-1]))
            except KeyError:
                return self.json({'error': '建议任务不存在，请重新生成'}, 404)
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8'),
                 '/assistant.css': ('assistant.css', 'text/css; charset=utf-8')}
        if route in files:
            filename, mime = files[route]
            return self.send((ROOT / 'web' / filename).read_bytes(), mime)
        return self.json({'error': '页面不存在'}, 404)

    def do_POST(self):
        if not self.local_request():
            return
        if self.headers.get('X-Z30-Request') != '1':
            return self.json({'error': '缺少请求标记'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_UPLOAD:
                self.close_connection = True
                return self.json({'error': '文件为空或超过 150 MB'}, 413)
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError('文件传输不完整')
            route = urllib.parse.urlsplit(self.path).path
            with LOCK:
                if route == '/api/open':
                    name = urllib.parse.unquote(self.headers.get('X-Filename', 'image.nef'))
                    name = name.replace('\\', '/').split('/')[-1][:200]
                    return self.json(import_image(body, name))
                data = json.loads(body)
                key = data.get('id')
                if key not in SESSIONS:
                    return self.json({'error': '照片会话已结束，请重新导入'}, 404)
                s = SESSIONS[key]
                if route == '/api/close':
                    s['path'].unlink(missing_ok=True)
                    del SESSIONS[key]
                    return self.json({'ok': True})
                p = settings(data.get('settings', {}))
                if route == '/api/codex/suggest':
                    goal = data.get('goal', '')
                    if not isinstance(goal, str) or not goal.strip() or len(goal) > 1200:
                        raise ValueError('请输入 1 到 1200 字的调色目标')
                    base = DEFAULTS | {'rotation': p['rotation'], 'crop': p['crop']}
                    before = image_bytes(develop(decode(s, 'camera', True), base), quality=88)
                    current = image_bytes(develop(decode(s, p['wb'], True), p), quality=88)
                    job = ADVISOR.submit(key, before, current, p, goal, s['raw'])
                    return self.json({'job': job,
                                      'current_image': 'data:image/jpeg;base64,' + base64.b64encode(current).decode()}, 202)
                if route == '/api/codex/preview':
                    return self.json(preview_result(s, p))
                if route == '/api/preview':
                    return self.json(preview_result(s, p, data.get('before', False)))
                if route == '/api/export':
                    fmt = data.get('format', 'JPEG')
                    if fmt not in ('JPEG', 'PNG'):
                        raise ValueError('不支持的导出格式')
                    quality = max(60, min(100, int(data.get('quality', 95))))
                    arr = decode(s, p['wb'], False)
                    im = develop(arr, p)
                    content = image_bytes(im, fmt, quality)
                    ext = 'jpg' if fmt == 'JPEG' else 'png'
                    name = urllib.parse.quote(Path(s['name']).stem + '-Z30.' + ext)
                    return self.send(content, 'image/jpeg' if fmt == 'JPEG' else 'image/png',
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8730)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Z30 Studio ready: {url}', flush=True)
    print('Photos stay on this computer. Close this window or press Ctrl+C to stop.', flush=True)
    if not args.no_browser:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        TEMP.cleanup()


if __name__ == '__main__':
    main()

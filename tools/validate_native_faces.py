"""Compare the native CPU detector with an independent NumPy/ONNX reference.

Uses only the pinned public-domain NASA astronaut sample. Python/ONNX Runtime
are developer validation dependencies, never the native app's runtime.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageOps

from native_raw_benchmark import measured, digest

IMAGE_HASH = '88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5'
SIDE = 640
TOLERANCE = 2e-4  # 0.128 pixel in the 640 model canvas.


def reference_input(pixels):
    height, width = pixels.shape[:2]
    scale = SIDE / max(width, height)
    rw, rh = max(1, min(SIDE, int(np.floor(width*scale+.5)))), max(1, min(SIDE, int(np.floor(height*scale+.5))))
    xs = np.clip((np.arange(rw, dtype=np.float64)+.5)*width/rw-.5, 0, width-1)
    ys = np.clip((np.arange(rh, dtype=np.float64)+.5)*height/rh-.5, 0, height-1)
    x0, y0 = np.floor(xs).astype(int), np.floor(ys).astype(int)
    x1, y1 = np.minimum(x0+1, width-1), np.minimum(y0+1, height-1)
    dx, dy = (xs-x0).astype(np.float32)[None, :, None], (ys-y0).astype(np.float32)[:, None, None]
    top = pixels[y0[:, None], x0] + (pixels[y0[:, None], x1]-pixels[y0[:, None], x0])*dx
    bottom = pixels[y1[:, None], x0] + (pixels[y1[:, None], x1]-pixels[y1[:, None], x0])*dx
    resized = top + (bottom-top)*dy
    tensor = np.zeros((1, 3, SIDE, SIDE), dtype=np.float32)
    tensor[0, :, :rh, :rw] = np.transpose(np.clip(resized, 0, 1)[..., ::-1]*np.float32(255), (2, 0, 1))
    return tensor, rw, rh


def reference_faces(outputs, rw, rh):
    candidates = []
    for stride in (8, 16, 32):
        length = SIDE//stride
        cls = outputs[f'cls_{stride}'][0, :, 0]
        obj = outputs[f'obj_{stride}'][0, :, 0]
        scores = np.sqrt(np.clip(cls, 0, 1)*np.clip(obj, 0, 1))
        selected = np.flatnonzero(scores >= .9)
        grid = np.stack((selected % length, selected // length), axis=-1).astype(np.float32)
        box = outputs[f'bbox_{stride}'][0, selected]
        center = (grid+box[:, :2])*stride
        size = np.exp(box[:, 2:])*stride
        points = (outputs[f'kps_{stride}'][0, selected].reshape(-1, 5, 2)+grid[:, None])*stride
        for score, xy, wh, landmarks in zip(scores[selected], center, size, points):
            if not np.isfinite(wh).all() or np.any(wh < 4) or np.any(xy < 0) or xy[0] >= rw or xy[1] >= rh:
                continue
            lower = np.maximum(0, xy-wh/2)/[rw, rh]
            upper = np.minimum([rw, rh], xy+wh/2)/[rw, rh]
            if np.any(upper <= lower):
                continue
            candidates.append(dict(rect=[*lower, *(upper-lower)], confidence=float(score),
                                   landmarks=np.clip(landmarks/[rw, rh], 0, 1)))
    candidates.sort(key=lambda face: -face['confidence'])
    kept = []
    for face in candidates[:5000]:
        box = np.array(face['rect'])
        accepted = True
        for old in kept:
            other = np.array(old['rect'])
            wh = np.maximum(0, np.minimum(box[:2]+box[2:], other[:2]+other[2:])-np.maximum(box[:2], other[:2]))
            intersection = wh.prod()
            iou = intersection/(box[2:].prod()+other[2:].prod()-intersection)
            if iou >= .3:
                accepted = False
                break
        if accepted:
            kept.append(face)
            if len(kept) == 16:
                break
    return kept


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('probe', 'assets', 'image', 'out'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists() or args.out.suffix != '.json' or digest(args.image) != IMAGE_HASH:
        parser.error('Use the pinned public astronaut image and a new JSON report path')
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    model = args.assets/'face_detection_yunet_2023mar.onnx'
    session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
    root = Path(tempfile.mkdtemp(prefix='人脸验收 ', dir=Path(__file__).resolve().parents[1]/'.development')).resolve()
    original = Image.open(args.image).convert('RGB')
    wide = Image.new('RGB', (768, 512), (30, 30, 30)); wide.paste(original, (128, 0))
    tall = Image.new('RGB', (512, 768), (30, 30, 30)); tall.paste(original, (0, 128))
    cases = [('original', original), ('wide', wide), ('tall', tall), ('mirror', ImageOps.mirror(original)),
             ('blank', Image.new('RGB', (640, 480), (60, 60, 60)))]
    report = dict(stage='5A-local-face-detector-core', python_onnxruntime=ort.__version__,
                  public_image_sha256=IMAGE_HASH, probe_sha256=digest(args.probe),
                  model_sha256=digest(model), coordinate_tolerance=TOLERANCE, cases=[],
                  real_cloud_requests=0, portrait_retouch_verified=False)
    for name, image in cases:
        path = root/f'{name}.png'; image.save(path)
        dump = root/f'{name}-float'
        native = measured([str(args.probe.resolve()), str(args.assets.resolve()), str(path), str(dump)])
        pixels = np.fromfile(dump/'neutral.f32', dtype='<f4').reshape(image.height, image.width, 3)
        source_pixels = np.asarray(image, dtype=np.float32)/np.float32(255)
        input_difference = float(np.max(np.abs(pixels-source_pixels)))
        tensor, rw, rh = reference_input(pixels)
        raw = dict(zip([item.name for item in session.get_outputs()], session.run(None, {'input':tensor})))
        expected = reference_faces(raw, rw, rh)
        faces = native['first']['faces']
        if len(faces) != len(expected) or len(faces) != (0 if name == 'blank' else 1):
            raise AssertionError(f'{name}: incorrect face count')
        maximum = 0.
        for actual, wanted in zip(faces, expected):
            points = [[point['x'], point['y']] for point in actual['landmarks']]
            maximum = max(maximum, float(np.max(np.abs(np.array(actual['rect'])-wanted['rect']))),
                          float(np.max(np.abs(np.array(points)-wanted['landmarks']))),
                          abs(actual['confidence']-wanted['confidence']))
        report['cases'].append(dict(name=name, native=native, reference_max_error=maximum,
                                    neutral_vs_source_max_difference=input_difference,
                                    actual_float_input_sha256=digest(dump/'neutral.f32'),
                                    source_sha256=digest(path), success=maximum <= TOLERANCE and faces == native['repeat']['faces']))
        if not report['cases'][-1]['success']:
            report.update(success=False,error=f'{name}: geometry/score mismatch {maximum}')
            args.out.parent.mkdir(parents=True,exist_ok=True)
            args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
            raise AssertionError(report['error'])
    damaged = root/'bad-model'; damaged.mkdir()
    corrupt = bytearray(model.read_bytes()); corrupt[-1] ^= 1
    (damaged/model.name).write_bytes(corrupt)
    result = subprocess.run([str(args.probe.resolve()), str(damaged), str(root/'original.png')],
                            capture_output=True, timeout=20)
    report['corrupted_model_rejected_before_loading_runtime'] = result.returncode != 0 and b'SHA-256' in result.stderr
    damaged_runtime = root/'bad-runtime'; damaged_runtime.mkdir()
    (damaged_runtime/model.name).write_bytes(model.read_bytes())
    corrupt_runtime = bytearray((args.assets/'onnxruntime.dll').read_bytes()); corrupt_runtime[-1] ^= 1
    (damaged_runtime/'onnxruntime.dll').write_bytes(corrupt_runtime)
    result = subprocess.run([str(args.probe.resolve()), str(damaged_runtime), str(root/'original.png')],
                            capture_output=True, timeout=20)
    report['corrupted_runtime_rejected_before_loading_native_code'] = result.returncode != 0 and b'SHA-256' in result.stderr
    report['public_source_unchanged'] = digest(args.image) == IMAGE_HASH
    report['success'] = all(case['success'] for case in report['cases']) and report['corrupted_model_rejected_before_loading_runtime'] and report['corrupted_runtime_rejected_before_loading_native_code'] and report['public_source_unchanged']
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps(dict(report=str(args.out), cases=len(cases), success=report['success']), ensure_ascii=False))
    if not report['success']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

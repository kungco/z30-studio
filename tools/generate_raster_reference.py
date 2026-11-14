"""Generate small synthetic JPEG/PNG regression files with analytic references.

Only --write changes the tracked fixture. No camera photos, user metadata or
third-party profile files are included. Colour references use W3C matrices and
ICC 16.16 TRC coefficients; the production Rust CMS is not an oracle.
"""
import argparse
import io
import json
from pathlib import Path
import struct
import sys
import zlib
import numpy as np
from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.color_profiles import wide_rgb_profile, A98_TO_SRGB


def srgb(v):
    return np.where(v <= .04045, v / 12.92, ((v + .055) / 1.055) ** 2.4)


def icc_srgb(v):
    data = (ROOT / 'native/crates/core/assets/srgb.icc').read_bytes()
    g, a, b, c, d = np.array(struct.unpack_from('>5i', data, 532)) / 65536
    return np.where(v >= d, (a * v + b) ** g, c * v)


def gray_profile():
    # Original synthetic ICC v2 D50 gray/gamma profile; no vendor binary.
    xyz = b'XYZ ' + bytes(4) + struct.pack('>3i', *(round(v * 65536) for v in (.9642, 1, .8249)))
    tags = {b'wtpt': xyz, b'kTRC': b'curv' + bytes(4) + struct.pack('>IH', 1, 563)}
    header = bytearray(128)
    header[8:12] = bytes([2, 0x10, 0, 0]); header[12:24] = b'mntrGRAYXYZ '
    header[24:36] = struct.pack('>6H', 2026, 1, 1, 0, 0, 0)
    header[36:40] = b'acsp'; header[68:80] = xyz[8:]
    offset = 132 + 12 * len(tags)
    table = bytearray(struct.pack('>I', len(tags))); payload = bytearray()
    for name, data in tags.items():
        table += name + struct.pack('>II', offset + len(payload), len(data))
        payload += data + bytes((-len(data)) % 4)
    profile = header + table + payload
    struct.pack_into('>I', profile, 0, len(profile))
    return bytes(profile)


def chunk(name, data):
    return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))


def raw_png(array, colour_type=2, depth=16, ancillary=b''):
    h, w = array.shape[:2]
    data = b''.join(b'\0' + row.astype('>u2').tobytes() for row in array)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, depth, colour_type, 0, 0, 0))
            + ancillary + chunk(b'IDAT', zlib.compress(data)) + chunk(b'IEND', b''))


def oriented(array, value):
    return {1: lambda: array, 2: lambda: array[:, ::-1], 3: lambda: array[::-1, ::-1],
            4: lambda: array[::-1], 5: lambda: array.transpose(1, 0, 2),
            6: lambda: np.rot90(array, -1), 7: lambda: array.transpose(1, 0, 2)[::-1, ::-1],
            8: lambda: np.rot90(array)}[value]()


def cases():
    results = []
    original = np.array([[[24, 48, 72], [80, 112, 144], [152, 176, 200]],
                         [[192, 48, 96], [48, 192, 96], [96, 48, 192]]], np.uint8)
    image = Image.fromarray(original)
    srgb_icc = (ROOT / 'native/crates/core/assets/srgb.icc').read_bytes()
    def add(name, picture=image, fmt='PNG', transfer=srgb, tolerance=2e-6, rotation=1, warning=None, error=None, **options):
        stream = io.BytesIO()
        if fmt == 'JPEG': options = {'quality': 100, 'subsampling': 0} | options
        picture.save(stream, fmt, **options); data = stream.getvalue()
        result = dict(name=name, hex=data.hex())
        if error: result['error_contains'] = error
        else:
            # JPEG integer decoding is independently performed by Pillow/libjpeg.
            with Image.open(io.BytesIO(data)) as source:
                encoded = np.array(source.convert('RGB'), np.float64) / 255
                alpha = np.array(source.convert('RGBA').getchannel('A'), np.float64) / 255
            expected = transfer(encoded)
            if 'A' in picture.getbands() or 'transparency' in options:
                expected = expected * alpha[..., None] + 1 - alpha[..., None]
            expected = oriented(expected, rotation)
            result.update(width=expected.shape[1], height=expected.shape[0], expected=expected.reshape(-1, 3).tolist(), tolerance=tolerance)
            if warning is not None: result['warning_expected'] = warning
        results.append(result)
    add('untagged-rgb', warning=True)
    add('tagged-srgb', transfer=icc_srgb, icc_profile=srgb_icc, warning=False)
    add('wide-icc', transfer=lambda v: (v ** (563/256)) @ A98_TO_SRGB.T, tolerance=8e-5, icc_profile=wide_rgb_profile())
    add('gray-icc', Image.new('L', (3, 2), 128), transfer=lambda v: v ** (563/256), tolerance=3e-5, icc_profile=gray_profile())
    add('gray-rgb-icc', Image.new('L', (3, 2), 128), transfer=icc_srgb, icc_profile=srgb_icc)
    add('gray-alpha', Image.new('LA', (3, 2), (32, 127)), warning=True)
    add('rgba-icc', Image.new('RGBA', (3, 2), (20, 40, 80, 128)), transfer=icc_srgb, icc_profile=srgb_icc, warning=True)
    palette = Image.new('P', (3, 2)); palette.putpalette([255, 0, 0] + [0]*765)
    add('palette-transparent', palette, transparency=0, icc_profile=srgb_icc, transfer=icc_srgb)
    info = PngImagePlugin.PngInfo(); info.add(b'gAMA', struct.pack('>I', 100000))
    add('gamma-linear', pnginfo=info, transfer=lambda v: v, tolerance=3e-5)
    info = PngImagePlugin.PngInfo(); info.add(b'gAMA', struct.pack('>I', 45455))
    chroma = (.3127, .3290, .64, .33, .21, .71, .15, .06)
    info.add(b'cHRM', struct.pack('>8I', *(round(v*100000) for v in chroma)))
    add('gamma-wide-chroma', pnginfo=info, transfer=lambda v: (v ** (100000/45455)) @ A98_TO_SRGB.T, tolerance=8e-5)
    info = PngImagePlugin.PngInfo(); info.add(b'sRGB', bytes([1]))
    add('png-srgb-chunk', pnginfo=info, warning=False)
    exif = Image.Exif(); exif[34665] = {40961: 65535, 40965: {1: 'R03'}}
    add('exif-adobe-interop', exif=exif, transfer=lambda v: (v ** (563/256)) @ A98_TO_SRGB.T, tolerance=8e-5)
    for fmt in ('PNG', 'JPEG'):
        for rotation in range(1, 9):
            exif = Image.Exif(); exif[274] = rotation
            add(f'{fmt.lower()}-orientation-{rotation}', fmt=fmt, exif=exif, rotation=rotation,
                tolerance=2e-6 if fmt == 'PNG' else .009)
    add('jpeg-srgb-icc', fmt='JPEG', icc_profile=srgb_icc, transfer=icc_srgb, tolerance=.009)
    add('jpeg-gray', Image.new('L', (3, 2), 128), fmt='JPEG', tolerance=2e-6)
    xx=np.arange(5360,5376,dtype=np.uint32)[None,:]; yy=np.arange(2528,2544,dtype=np.uint32)[:,None]
    dct=np.empty((16,16,3),np.uint8)
    dct[:,:,0]=xx*255//5567;dct[:,:,1]=yy*255//3711;dct[:,:,2]=(xx+yy)*255//9278
    add('jpeg-dct-boundary',Image.fromarray(dct),fmt='JPEG',quality=95,tolerance=.009)
    xx=np.arange(37,dtype=np.uint32)[None,:];yy=np.arange(23,dtype=np.uint32)[:,None]
    subsampled=np.empty((23,37,3),np.uint8)
    subsampled[:,:,0]=(xx*7+yy*3)%256;subsampled[:,:,1]=(xx*2+yy*11)%256;subsampled[:,:,2]=(xx*5+yy*9)%256
    for progressive in (False,True):
        add('jpeg-420-'+str(progressive),Image.fromarray(subsampled),fmt='JPEG',quality=92,subsampling=2,progressive=progressive,tolerance=.009)
    add('corrupt-icc', icc_profile=b'broken', error='ICC')
    add('gray-icc-on-rgb', icc_profile=gray_profile(), error='通道')
    add('cmyk-untagged', Image.new('CMYK', (3, 2)), fmt='JPEG', error='CMYK')
    add('cmyk-rgb-icc', Image.new('CMYK', (3, 2)), fmt='JPEG', icc_profile=srgb_icc, error='CMYK')
    info = PngImagePlugin.PngInfo(); info.add(b'cHRM', struct.pack('>8I', *(round(v*100000) for v in chroma)))
    add('chroma-without-gamma', pnginfo=info, error='gAMA')
    info = PngImagePlugin.PngInfo(); info.add(b'gAMA', bytes(4))
    add('zero-gamma', pnginfo=info, error='gAMA')
    # Pillow does not forward arbitrary iCCP from PngInfo: construct these bytes
    # ourselves so the malformed-profile tests actually contain the bad chunk.
    sample = np.zeros((1, 1, 3), np.uint16)
    for name, ancillary in [
        ('empty-icc', chunk(b'iCCP', b'empty\0\0' + zlib.compress(b''))),
        ('duplicate-icc', chunk(b'iCCP', b'one\0\0'+zlib.compress(srgb_icc))*2),
        ('hdr-cicp', chunk(b'cICP', bytes([9, 16, 0, 1]))),
    ]:
        results.append(dict(name=name, hex=raw_png(sample, ancillary=ancillary).hex(), error_contains='ICC' if name != 'hdr-cicp' else 'cICP'))
    jpeg = next(v for v in results if v['name']=='jpeg-orientation-1')
    jpeg_bytes = bytes.fromhex(jpeg['hex'])
    results.append(dict(name='truncated-jpeg',hex=jpeg_bytes[:-8].hex(),error_contains='JPEG'))
    results.append(dict(name='excessive-jpeg-scans',hex=(jpeg_bytes[:2]+(b'\xff\xda\x00\x02'*101)+jpeg_bytes[2:]).hex(),error_contains='扫描上限'))
    for name, payload in [('empty-jpeg-icc', b'ICC_PROFILE\0\x01\x01'), ('partial-jpeg-icc', b'ICC_PROFILE\0\x01\x02'+srgb_icc[:40])]:
        segment = b'\xff\xe2' + struct.pack('>H',len(payload)+2) + payload
        results.append(dict(name=name,hex=(jpeg_bytes[:2]+segment+jpeg_bytes[2:]).hex(),error_contains='ICC'))
    fragments=[srgb_icc[:200],srgb_icc[200:]]
    for order in [(0,1),(1,0)]:
        segments=b''
        for index in order:
            payload=b'ICC_PROFILE\0'+bytes([index+1,2])+fragments[index]
            segments+=b'\xff\xe2'+struct.pack('>H',len(payload)+2)+payload
        with Image.open(io.BytesIO(jpeg_bytes)) as source:values=icc_srgb(np.asarray(source,dtype=np.float64)/255)
        results.append(dict(name='jpeg-icc-segment-order-'+str(order),hex=(jpeg_bytes[:2]+segments+jpeg_bytes[2:]).hex(),width=3,height=2,expected=values.reshape(-1,3).tolist(),tolerance=.009))
    exif = Image.Exif(); exif[274] = 6
    png = bytes.fromhex(results[0]['hex'])
    after = png[:-12] + chunk(b'eXIf', exif.tobytes()[6:]) + png[-12:]
    expected = oriented(srgb(original.astype(np.float64)/255),6)
    results.append(dict(name='exif-after-idat',hex=after.hex(),width=2,height=3,expected=expected.reshape(-1,3).tolist(),tolerance=2e-6))
    values = np.arange(1024, dtype=np.uint16).reshape(1, 1024)
    for mode, array, kind in [('rgb', np.stack([values, values*2, values*3], axis=-1), 2), ('gray', values, 0)]:
        expected = srgb(array.astype(np.float64)/65535)
        if kind == 0: expected = np.repeat(expected[..., None], 3, axis=-1)
        results.append(dict(name='png16-'+mode, hex=raw_png(array, kind).hex(), width=1024, height=1, expected=expected.reshape(-1, 3).tolist(), tolerance=2e-6, distinct_red=1024))
        if kind == 2:
            expected = icc_srgb(array.astype(np.float64)/65535)
            ancillary = chunk(b'iCCP', b'sRGB\0\0'+zlib.compress(srgb_icc))
            results.append(dict(name='png16-rgb-icc',hex=raw_png(array,kind,ancillary=ancillary).hex(),width=1024,height=1,expected=expected.reshape(-1,3).tolist(),tolerance=2e-6,distinct_red=1024))
    resizes = []
    source = (np.arange(11*7*3, dtype=np.float32).reshape(7,11,3) % 23) / 22
    for bounds in [(6,4),(4,10),(7,2),(1,1),(20,20)]:
        scale=min(bounds[0]/11,bounds[1]/7,1)
        width,height=max(1,round(11*scale)),max(1,round(7*scale))
        expected=np.stack([np.asarray(Image.fromarray(source[:,:,c]).resize((width,height),Image.Resampling.BILINEAR)) for c in range(3)],axis=-1)
        resizes.append(dict(bounds=list(bounds),width=width,height=height,expected=expected.reshape(-1,3).tolist()))
    for width,height,bounds in [(5,2,(10,1)),(2,5,(1,10))]:
        values=np.arange(width*height*3,dtype=np.float32).reshape(height,width,3)/30
        scale=min(bounds[0]/width,bounds[1]/height,1)
        size=(max(1,round(width*scale)),max(1,round(height*scale)))
        expected=np.stack([np.asarray(Image.fromarray(values[:,:,c]).resize(size,Image.Resampling.BILINEAR)) for c in range(3)],axis=-1)
        resizes.append(dict(source=dict(width=width,height=height,pixels=values.reshape(-1,3).tolist()),bounds=list(bounds),width=size[0],height=size[1],expected=expected.reshape(-1,3).tolist()))
    return dict(reference='Original synthetic files; analytic W3C/ICC TRC references, Pillow JPEG integer decode; no Rust CMS oracle', cases=results,
                resize_source=dict(width=11,height=7,pixels=source.reshape(-1,3).tolist()),resizes=resizes)


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--write', action='store_true'); args=parser.parse_args()
    payload=cases(); path=ROOT/'native/crates/core/tests/fixtures/raster.json'
    if args.write: path.write_text(json.dumps(payload, indent=2)+'\n', encoding='utf8')
    else:
        saved=json.loads(path.read_text(encoding='utf8'))
        if saved != payload: raise SystemExit('Raster fixture changed; review before --write')
    print(f'{len(payload["cases"])} synthetic raster cases '+('written' if args.write else 'verified'))


if __name__=='__main__': main()

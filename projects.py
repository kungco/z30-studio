"""Portable projects: validated JSON + byte-exact originals; no pickle/paths."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile
import photo_workflow as workflow

MAX_PHOTOS = 256
PROJECT_LIMIT = 8 * 1024 ** 3
MANIFEST_LIMIT = 32 * 1024 ** 2
EXTENSIONS = {'.nef', '.jpg', '.jpeg', '.png'}


def analysis(value, normalize):
    if value is None:
        return None
    result = value['result']
    text = {}
    for key in ('title', 'summary', 'limitations'):
        if not isinstance(result[key], str) or len(result[key]) > 2000:
            raise ValueError('项目分析文字无效')
        text[key] = result[key]
    observations = result['observations']
    if not isinstance(observations, list) or len(observations) > 8 or any(not isinstance(x, str) or len(x)>1000 for x in observations):
        raise ValueError('项目分析记录无效')
    quality = result.get('quality')
    if quality is not None and (not isinstance(quality, dict) or not isinstance(quality.get('message'), str)):
        raise ValueError('项目画质记录无效')
    return dict(base=normalize(value['base']), result=dict(**text, observations=observations,
                settings=normalize(result['settings']), quality=quality))


def history(value, normalize):
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get('states'), list) or not 1 <= len(value['states']) <= 50:
        raise ValueError('编辑历史格式或数量无效')
    index = value.get('index')
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(value['states']):
        raise ValueError('历史位置无效')
    states = []
    for item in value['states']:
        states.append(dict(settings=normalize(item['settings']), preset=str(item.get('preset', 'custom'))[:32],
                           label=str(item.get('label', '调整参数'))[:80]))
    return dict(states=states, index=index)


def undo(value, normalize):
    if value is None:
        return None
    return dict(settings=normalize(value['settings']), preset=str(value.get('preset','custom'))[:32])


def manifest(sources, recipes, selected, normalize):
    photos = []
    for i, source in enumerate(sources):
        path = source['path']; recipe = recipes.get(source['meta']['id'], {})
        p = normalize(recipe.get('settings', {}))
        h = history(recipe.get('history'), normalize)
        with path.open('rb') as file:
            digest = hashlib.file_digest(file, 'sha256').hexdigest()
        photos.append(dict(name=source['name'], file=f'sources/{i}{path.suffix.lower()}',
                           sha256=digest, size=path.stat().st_size, settings=p,
                           preset=str(recipe.get('preset', 'custom'))[:32], history=h, undo=undo(recipe.get('undo'),normalize),
                           analysis=analysis(recipe.get('analysis'), normalize),catalog=workflow.catalog(recipe.get('catalog'))))
    result = dict(app='Z30 Studio', version=2, selected=max(0, min(len(photos)-1, selected)), photos=photos)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf8')
    if len(encoded) > MANIFEST_LIMIT:
        raise ValueError('项目历史过大，请减少历史或照片数量')
    if sum(p['size'] for p in photos) + len(encoded) > PROJECT_LIMIT:
        raise ValueError('项目超过 8 GiB 限制，请分组保存')
    return result


def write_project(target, sources, document):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.writestr('project.json', json.dumps(document, ensure_ascii=False, allow_nan=False))
        for source, photo in zip(sources, document['photos']):
            archive.write(source['path'], photo['file'])


@contextmanager
def read_project(path, parent, normalize):
    with tempfile.TemporaryDirectory(prefix='project-', dir=parent) as staging, zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_PHOTOS + 1 or len({x.filename for x in infos}) != len(infos):
            raise ValueError('项目文件数量或重复条目无效')
        if any(x.flag_bits & 1 or stat.S_ISLNK(x.external_attr >> 16) for x in infos):
            raise ValueError('不支持加密或链接项目条目')
        if sum(x.file_size for x in infos) > PROJECT_LIMIT or archive.getinfo('project.json').file_size > MANIFEST_LIMIT:
            raise ValueError('项目解压大小超出限制')
        document = json.loads(archive.read('project.json'))
        if document.get('app') != 'Z30 Studio' or document.get('version') not in (1,2):
            raise ValueError('不支持此项目版本')
        photos = document.get('photos')
        if not isinstance(photos, list) or not 1 <= len(photos) <= MAX_PHOTOS:
            raise ValueError('项目需包含 1–256 张照片')
        expected = {'project.json'}
        for i, photo in enumerate(photos):
            suffix = Path(photo['file']).suffix.lower()
            if suffix not in EXTENSIONS or photo['file'] != f'sources/{i}{suffix}':
                raise ValueError('项目照片路径无效')
            expected.add(photo['file'])
            if not 0 < photo['size'] <= 150 * 1024 ** 2 or archive.getinfo(photo['file']).file_size != photo['size']:
                raise ValueError('项目照片大小无效')
            photo['settings'] = normalize(photo['settings'])
            photo['history'] = history(photo.get('history'), normalize)
            photo['analysis'] = analysis(photo.get('analysis'), normalize)
            photo['undo'] = undo(photo.get('undo'), normalize)
            photo['catalog'] = workflow.catalog(photo.get('catalog'))
            # The selected history node must describe the actual saved edit.
            if photo['history'] and photo['history']['states'][photo['history']['index']]['settings'] != photo['settings']:
                raise ValueError('项目参数与历史位置不一致')
            destination = Path(staging) / f'{i}{suffix}'
            with archive.open(photo['file']) as src, destination.open('wb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            with destination.open('rb') as file:
                if hashlib.file_digest(file, 'sha256').hexdigest() != photo['sha256']:
                    raise ValueError('项目原片校验失败')
            photo['staged'] = destination
        if set(archive.namelist()) != expected:
            raise ValueError('项目包含未声明文件')
        yield document

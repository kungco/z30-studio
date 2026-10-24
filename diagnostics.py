"""A deliberately narrow, bounded diagnostic bundle: logs only, never settings/photos."""
import io
import json
from pathlib import Path
import re
import zipfile


def bundle(directory, version):
    out = io.BytesIO()
    logs = Path(directory) / 'logs'
    # Do not include arbitrary files, credentials, drafts, project paths or EXIF.
    allowed = ['launcher.log', 'launcher.log.1', 'shutdown-stacks.log', 'shutdown-watchdog.log']
    allowed += [p.name for p in sorted(logs.glob('shutdown-stacks-*.log'),key=lambda p:p.stat().st_mtime)[-4:]]
    allowed += [p.name for p in sorted(logs.glob('shutdown-*.json'), key=lambda p: p.stat().st_mtime)[-8:]]
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('summary.json', json.dumps(dict(app='Z30 Studio', version=version,
                             notice='Local paths may appear in stack traces. No photos or model configuration included.')))
        for name in allowed:
            path = logs / name
            if not path.is_file() or path.is_symlink():
                continue
            with path.open('rb') as file:
                file.seek(max(0, path.stat().st_size - 512 * 1024))
                text = file.read(512 * 1024).decode('utf8', errors='replace')
            text = re.sub(r'(?i)(bearer\s+|api[_ -]?key[\s"\x27:=]+)\S+', r'\1[REDACTED]', text)
            text = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[REDACTED]', text)
            archive.writestr(name, text)
    return out.getvalue()

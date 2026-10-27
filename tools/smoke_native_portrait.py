"""Actual Slint portrait controls, source binding, restart and TIFF16 acceptance.

Only file-picker choices are injected. Inputs are copied public fixtures; no
cloud requests, user originals or physical-click acceptance are implied.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import numpy as np
from PIL import Image
import tifffile
from smoke_native import digest, memory, wait_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('exe','assets','image','out'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--bundled-vision',action='store_true')
    args=parser.parse_args()
    if args.out.exists() or args.out.suffix!='.json':parser.error('Choose a new JSON report path')
    root=Path(tempfile.mkdtemp(prefix='人像窗口 ',dir=Path(__file__).resolve().parents[1]/'.development')).resolve()
    first,second=root/'人像源图.png',root/'第二张.png';shutil.copyfile(args.image,first);Image.new('RGB',(512,320),(75,100,130)).save(second)
    originals={str(path):digest(path) for path in (args.image,first,second)}
    data=root/'data';data.mkdir()
    legacy=json.dumps(dict(version=2,photos=[dict(path=str(path),rating=0,flag=0,recipe=dict(parameters={},white_balance='camera')) for path in (first,second)],selected=0),ensure_ascii=False).encode('utf8')
    (data/'session.json').write_bytes(legacy)
    env=os.environ|{'Z30_NATIVE_DATA_DIR':str(data)};env.pop('Z30_VISION_DIR',None)
    report=dict(stage='5B-native-portrait-ui',checks=[],exe_sha256=digest(args.exe),physical_clicks_verified=False,cloud_requests=0,qa_directory=str(root))
    children=[]
    def check(label,condition):
        if not condition:raise AssertionError(label)
        report['checks'].append(label)
    def launch(number):
        controls=root/f'commands{number}';controls.mkdir();ready=root/f'ready{number}.json';stop=root/f'stop{number}.signal'
        log=(root/f'process{number}.log').open('w',encoding='utf8');startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
        process=subprocess.Popen([str(args.exe.resolve()),'--qa-ready',str(ready),'--qa-stop',str(stop),'--qa-control',str(controls)],env=env,cwd=root,stdout=log,stderr=log,startupinfo=startup)
        children.append((process,stop,log));wait_json(ready,process);sequence=0
        def command(action='snapshot',**values):
            nonlocal sequence
            sequence+=1;path=controls/f'{sequence:06}.json';pending=path.with_suffix('.pending');pending.write_text(json.dumps(dict(action=action,**values)),encoding='utf8');pending.rename(path)
            return wait_json(path.with_suffix('.result.json'),process,30)
        def until(predicate,timeout=45):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                state=command()
                if predicate(state):return state
                time.sleep(.05)
            raise TimeoutError(json.dumps(state,ensure_ascii=False))
        until(lambda s:s['preview'] and not s['busy'])
        return process,stop,command,until
    def select_assets(command,until):
        if not args.bundled_vision:command('face_assets',directory=str(args.assets.resolve()))
        return until(lambda s:s['face_assets_ready'] and not s['face_busy'])
    try:
        process,stop,command,until=launch(1)
        initial=command();baseline=initial['preview_fingerprint']
        check('v2 draft migrates to v6 with exact original backup',initial['session']['version']==6 and any(path.read_bytes()==legacy for path in data.glob('session-v2-backup-*.json')))
        select_assets(command,until);command('detect_face')
        detected=until(lambda s:s['has_face'] and not s['face_busy'] and not s['busy'])
        check('native detector binds a face without automatically applying smoothing',detected['skin_smooth']==0 and detected['preview_fingerprint']==baseline and detected['session']['photos'][0]['recipe']['portrait']['binding']['width']==512)
        command('adjust',parameter='skin_smooth',value=90.)
        smoothed=until(lambda s:not s['busy'] and s['skin_smooth']==90.)
        check('portrait slider changes actual image pixels',smoothed['preview_fingerprint']!=baseline and smoothed['neutral_fingerprint']==initial['neutral_fingerprint'])
        command('undo');undone=until(lambda s:not s['busy'])
        check('one undo reverses the whole smoothing gesture',undone['skin_smooth']==0 and undone['preview_fingerprint']==baseline)
        command('redo');redone=until(lambda s:not s['busy'])
        check('redo restores the same smoothed pixels',redone['preview_fingerprint']==smoothed['preview_fingerprint'])
        command('adjust',parameter='exposure',value=.3);current=until(lambda s:not s['busy'])
        check('global edits retain the accepted portrait recipe',current['skin_smooth']==90 and current['session']['photos'][0]['recipe']['portrait']['face']==detected['session']['photos'][0]['recipe']['portrait']['face'])
        directory=root/'batch';directory.mkdir();command('batch_start',directory=str(directory),format=2,mode=1)
        current=until(lambda s:len(s['batch']['jobs'])==2 and all(job['state']=='done' for job in s['batch']['jobs']))
        check('shared batch never transfers face coordinates to another photo',current['batch']['jobs'][0]['recipe']['portrait']['smooth']==90 and current['batch']['jobs'][1]['recipe']['portrait']['face'] is None)
        expected=Path(current['batch']['jobs'][0]['destination']);pixels=tifffile.imread(expected)
        check('portrait batch output is full-size real TIFF16',pixels.shape==(512,512,3) and pixels.dtype==np.uint16 and bool(np.any(pixels%257)))
        check('normal workflow leaves every source byte unchanged',all(digest(Path(path))==value for path,value in originals.items()))
        expected_hash=digest(expected);saved_fingerprint=current['preview_fingerprint'];report['memory_mib']=memory(process.pid)
        stop.write_text('stop');check('first portrait process closes cleanly',process.wait(timeout=30)==0)

        process,stop,command,until=launch(2);restored=command()
        check('portrait binding and pixels restore without another model request',restored['has_face'] and restored['skin_smooth']==90 and restored['preview_fingerprint']==saved_fingerprint)
        target=root/'restored.tif';command('export',destination=str(target),format=2);until(lambda s:target.exists() and not s['export_busy'])
        check('restored single export matches the earlier batch file exactly',digest(target)==expected_hash)
        # Deliberate mutation of our isolated copy, after the read-only checks.
        changed=Image.open(first).convert('RGB');changed.putpixel((0,0),(20,30,40));changed.save(first)
        command('retry_preview');stale=until(lambda s:not s['busy'] and not s['preview'])
        check('changed source refuses old portrait coordinates',('已变化' in stale['status']) or ('原片' in stale['status'] and '旧人像' in stale['status']))
        command('clear_portrait');cleared=until(lambda s:s['preview'] and not s['busy'])
        check('clearing the stale portrait restores normal editing',not cleared['has_face'] and cleared['skin_smooth']==0)
        select_assets(command,until);command('detect_face');command('select',index=1)
        switched=until(lambda s:s['selected']==1 and not s['face_busy'] and not s['busy'])
        check('switching photos never applies old detection to the new photo',not switched['has_face'] and switched['session']['photos'][1]['recipe']['portrait']['face'] is None)
        stop.write_text('stop');check('second portrait process closes cleanly',process.wait(timeout=30)==0)

        process,stop,command,until=launch(3);command('select',index=0);until(lambda s:not s['busy'] and s['preview']);select_assets(command,until);command('detect_face')
        start=time.monotonic();stop.write_text('stop');check('close during local detection exits cleanly',process.wait(timeout=30)==0)
        report['close_during_detection_seconds']=time.monotonic()-start
        check('external public fixture and untouched second source remain unchanged',digest(args.image)==originals[str(args.image)] and digest(second)==originals[str(second)])
        report.update(success=True,intentional_test_copy_mutation=True)
    except BaseException as error:
        report.update(success=False,error=f'{type(error).__name__}: {error}');raise
    finally:
        for process,stop,log in children:
            if process.poll() is None:
                stop.write_text('stop')
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            log.close()
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(json.dumps(dict(report=str(args.out),success=report.get('success'),checks=len(report['checks'])),ensure_ascii=False))


if __name__=='__main__':main()

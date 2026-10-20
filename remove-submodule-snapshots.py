#!/usr/bin/env python3
"""One-time cleanup of downloaded submodules in managed snapshots."""
import importlib.util, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location('archive', Path(__file__).with_name('update-projects.py'))
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)

def paths(source):
    config = source/'.gitmodules'
    if not config.exists(): return []
    p = subprocess.run(['git','config','--file',str(config),'--null','--get-regexp',r'^submodule\..*\.path$'],capture_output=True)
    if p.returncode not in (0,1): raise RuntimeError('Cannot read submodule paths')
    result = []
    for entry in p.stdout.decode().split('\0'):
        if not entry: continue
        rel = entry.split('\n',1)[1]
        if Path(rel).is_absolute() or '..' in Path(rel).parts or rel in ('','.'): raise RuntimeError('Unsafe submodule path')
        result.append(rel.rstrip('/'))
    return result

def clean(snapshot):
    backup = snapshot.with_name('.dependency-backup-'+snapshot.name)
    journal = snapshot.with_name('.dependency-cleanup-'+snapshot.name+'.json')
    if journal.exists():
        if backup.exists():
            if snapshot.exists():
                a.verify(snapshot); a.verify(backup); shutil.rmtree(backup)
            else: backup.rename(snapshot)
        journal.unlink()
    if backup.exists(): raise RuntimeError('Unexpected backup; leaving it untouched')
    meta = a.verify(snapshot)
    if meta.get('scope') == 'main-repository': return
    excluded = paths(snapshot/'source')
    def keep(rel):
        return Path(rel).name != '.DS_Store' and not any(rel == x or rel.startswith(x+'/') for x in excluded)
    revised = dict(meta, scope='main-repository', excluded_submodules=excluded,
                   dependency_cleanup_at=a.now())
    revised['warnings'] = [w for w in meta.get('warnings',[]) if not any(x in w for x in excluded)]
    revised['license_details'] = [x for x in meta.get('license_details',[]) if keep(x['path'])]
    if not excluded:
        temp = snapshot/'.metadata-main.tmp'
        temp.write_text(json.dumps(revised,indent=2)+'\n')
        os.replace(temp,snapshot/'metadata.json')
        print('CHECKED: '+str(snapshot),flush=True)
        return
    with tempfile.TemporaryDirectory(prefix='cleanup-',dir=snapshot.parents[2]/'.staging') as tmp:
        stage = Path(tmp)/'snapshot'
        def ignore(directory,names):
            base = Path(directory).relative_to(snapshot/'source')
            return [n for n in names if not keep((base/n).as_posix())]
        shutil.copytree(snapshot/'source',stage/'source',symlinks=True,ignore=ignore)
        revised['files'] = a.inventory(stage/'source')
        expected = {k:v for k,v in meta['files'].items() if keep(k)}
        if revised['files'] != expected: raise RuntimeError('Main-repository files do not match; original retained')
        (stage/'metadata.json').write_text(json.dumps(revised,indent=2)+'\n')
        a.verify(stage)
        journal.write_text(json.dumps({'stage':str(stage)}))
        try:
            snapshot.rename(backup)
            stage.rename(snapshot)
            a.verify(snapshot)
        except BaseException:
            if not snapshot.exists() and backup.exists(): backup.rename(snapshot)
            raise
        # The replacement is verified before deleting the dependency-heavy copy.
        a.verify(backup)
        shutil.rmtree(backup)
        journal.unlink()
    print('CLEANED: '+str(snapshot)+' ('+str(len(excluded))+' dependency repositories excluded)',flush=True)

def main():
    root = Path(sys.argv[1]).resolve()
    lock = root/'.update-lock'; lock.mkdir()
    try:
        for group in ('GitHub','Other'):
            for folder in sorted((root/group).glob('*')):
                if not folder.is_dir(): continue
                for name in ('current','previous'):
                    p = folder/name
                    if p.exists() or p.with_name('.dependency-cleanup-'+name+'.json').exists(): clean(p)
    finally: lock.rmdir()

if __name__ == '__main__': main()

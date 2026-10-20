#!/usr/bin/env python3
"""Bounded, verified source snapshots. Requires Python 3 and Git."""
import argparse, csv, datetime, hashlib, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path
from urllib.parse import urlsplit

ENV = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_LFS_SKIP_SMUDGE='1')
def git(*args, cwd=None, download_lfs=False):
    env = ENV.copy()
    if download_lfs: env.pop('GIT_LFS_SKIP_SMUDGE', None)
    p = subprocess.run(['git', '-c', 'protocol.file.allow=never', *args], cwd=cwd,
                       env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace')[-2500:].strip())
    return p.stdout.decode(errors='replace').strip()

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def inventory(root):
    result = {}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            result[p.relative_to(root).as_posix()] = 'link:' + os.readlink(p)
        elif p.is_file():
            h = hashlib.sha256()
            with p.open('rb') as f:
                for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
            result[p.relative_to(root).as_posix()] = h.hexdigest()
    return result

def verify(snapshot):
    meta = json.loads((snapshot/'metadata.json').read_text())
    actual = inventory(snapshot/'source')
    expected = meta['files']
    # Finder creates and rewrites these simply by browsing folders.
    actual = {k:v for k,v in actual.items() if Path(k).name != '.DS_Store'}
    expected = {k:v for k,v in expected.items() if Path(k).name != '.DS_Store'}
    if meta.get('format') != 1 or actual != expected:
        raise RuntimeError('Snapshot has local changes or failed verification; preserved without replacement')
    return meta

def recover(folder):
    journal = folder/'.transaction.json'
    if not journal.exists(): return
    state = json.loads(journal.read_text())
    if state.get('format') != 1: raise RuntimeError('Unrecognized transaction')
    current, previous = folder/'current', folder/'previous'
    old, older = folder/'.old-current', folder/'.old-previous'
    if Path(state['stage']).exists():
        if old.exists(): old.rename(current)
        if older.exists(): older.rename(previous)
    else:
        verify(current)
        if old.exists():
            if previous.exists(): raise RuntimeError('Ambiguous recovery; retaining all data')
            old.rename(previous)
        if older.exists():
            verify(older)
            shutil.rmtree(older)
    journal.unlink()

def promote(folder, stage):
    recover(folder)
    for name in ('current','previous'):
        if (folder/name).exists(): verify(folder/name)
    if any((folder/n).exists() for n in ('.old-current','.old-previous')):
        raise RuntimeError('Unexpected transaction folders; retaining all data')
    (folder/'.transaction.json').write_text(json.dumps({'format':1, 'stage':str(stage)}))
    try:
        if (folder/'previous').exists(): (folder/'previous').rename(folder/'.old-previous')
        if (folder/'current').exists(): (folder/'current').rename(folder/'.old-current')
        stage.rename(folder/'current')
    finally:
        recover(folder)

def identity(url):
    u = urlsplit(url)
    parts = u.path.strip('/').split('/')
    if u.scheme != 'https' or not u.hostname or u.username or u.query or u.fragment or len(parts) < 2:
        raise ValueError('Expected a plain HTTPS repository URL')
    owner, project = '/'.join(parts[:-1]), parts[-1].removesuffix('.git')
    def safe(s):
        return re.sub(r'[^A-Za-z0-9_.-]', '_', s).strip('. ') or 'unknown'
    return ('GitHub' if u.hostname.lower() == 'github.com' else 'Other', safe(owner)+' - '+safe(project), owner, project)

def archive(root, url):
    group, name, owner, project = identity(url)
    folder = root/group/name
    folder.mkdir(parents=True, exist_ok=True)
    recover(folder)
    current = folder/'current'
    if current.exists():
        old = verify(current)
        if old['source_url'] != url: raise RuntimeError('Destination belongs to another URL')
        remote = git('ls-remote', url, 'HEAD').split()
        if not remote: raise RuntimeError('Upstream default branch unavailable')
        if remote[0] == old['commit_sha'] and old.get('scope') == 'main-repository': return 'UP TO DATE', old.get('warnings', [])
    with tempfile.TemporaryDirectory(prefix='download-', dir=root/'.staging') as tmp:
        tmp = Path(tmp)
        checkout = tmp/'checkout'
        git('clone', '--depth', '1', '--single-branch', '--no-tags', '--no-recurse-submodules', '--', url, str(checkout))
        status = git('submodule', 'status', cwd=checkout)
        repositories = [checkout]
        for repo in repositories:
            git('fsck', '--connectivity-only', cwd=repo)
            if shutil.which('git-lfs'):
                git('lfs', 'pull', '--include=', '--exclude=', cwd=repo, download_lfs=True)
                git('lfs', 'fsck', cwd=repo)
            if git('status', '--porcelain', '--untracked-files=no', cwd=repo):
                raise RuntimeError('Checkout differs from committed source')
        stage = tmp/'snapshot'
        (stage/'source').mkdir(parents=True)
        # Export only files tracked directly by the main repository. Gitlinks
        # identify dependency repositories and are deliberately not expanded.
        excluded = []
        for entry in git('ls-files', '--stage', '-z', cwd=checkout).split('\0'):
            if not entry: continue
            details, rel = entry.split('\t', 1)
            if details.split()[0] == '160000':
                excluded.append(rel)
                continue
            src, dst = checkout/rel, stage/'source'/rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_symlink(): dst.symlink_to(os.readlink(src))
            else: shutil.copy2(src, dst)
        source = stage/'source'
        warnings, licenses, seen = [], [], {}
        for p in source.rglob('*'):
            rel = p.relative_to(source).as_posix()
            if p.is_symlink():
                warnings.append('Symlink may need special handling on Windows: '+rel)
                continue
            if rel.casefold() in seen: warnings.append('Case-insensitive path collision: '+rel)
            seen[rel.casefold()] = rel
            if any(re.search(r'[<>:"\\|?*]|[. ]$', part) or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)', part, re.I) for part in p.relative_to(source).parts):
                warnings.append('Windows-incompatible path: '+rel)
            if p.is_file():
                with p.open('rb') as f: prefix = f.read(200)
                if prefix.startswith(b'version https://git-lfs.github.com/spec/v1'):
                    raise RuntimeError('Unresolved Git LFS pointer: '+rel+'; install Git LFS and retry')
                if re.match(r'^(licen[sc]e|copying|notice)([._-]|$)', p.name, re.I):
                    licenses.append({'path':rel, 'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        if not licenses: warnings.append('No recognized license file found; reuse permission is unknown')
        meta = {'format':1, 'source_url':url, 'creator':owner, 'creator_note':'Repository owner/namespace; authorship remains in source notices',
                'project':project, 'commit_sha':git('rev-parse','HEAD',cwd=checkout),
                'commit_date':git('show','-s','--format=%cI','HEAD',cwd=checkout), 'archived_at':now(),
                'default_branch':git('symbolic-ref','--short','HEAD',cwd=checkout),
                'license_details':licenses, 'license_note':'Original license texts retained in source; no SPDX inference',
                'scope':'main-repository', 'excluded_submodules':excluded,
                'submodules':status, 'warnings':warnings, 'files':inventory(source)}
        (stage/'metadata.json').write_text(json.dumps(meta, indent=2)+'\n')
        verify(stage)
        promote(folder, stage)
        return 'UPDATED', warnings

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    root = args.root.resolve()
    lock = root/'.update-lock'
    try: lock.mkdir()
    except FileExistsError: sys.exit('Another run or interrupted-run lock exists: '+str(lock))
    failures = 0
    try:
        (root/'.staging').mkdir(exist_ok=True)
        urls = [s.strip() for s in (root/'projects.txt').read_text().splitlines() if s.strip()]
        if len(urls) != len(set(urls)): raise ValueError('Duplicate URLs in projects.txt')
        destinations = [identity(u)[:2] for u in urls]
        if len(destinations) != len(set((g,n.casefold()) for g,n in destinations)):
            raise ValueError('Repository folder collision')
        with (root/'warnings.log').open('w') as log:
            for url in urls[:args.limit]:
                try:
                    result, warnings = archive(root, url)
                    print(result+': '+url, flush=True)
                    for warning in warnings:
                        log.write(now()+' '+url+' '+warning+'\n')
                    if warnings: print('WARNING: '+str(len(warnings))+' details in warnings.log', flush=True)
                except Exception as e:
                    failures += 1
                    print('WARNING: '+url+' — '+str(e), flush=True)
                    log.write(now()+' '+url+' '+str(e)+'\n')
        target = root/'archive-index.csv'
        with (root/'.archive-index.tmp').open('w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['source_url','creator','project','commit_sha','archived_at','license_files','snapshot','warnings'])
            for group in ('GitHub','Other'):
                for p in sorted((root/group).glob('*/current/metadata.json')):
                    m = json.loads(p.read_text())
                    writer.writerow([m[k] for k in ('source_url','creator','project','commit_sha','archived_at')]+['; '.join(x['path'] for x in m['license_details']),p.parent.relative_to(root).as_posix(),'; '.join(m['warnings'])])
        os.replace(root/'.archive-index.tmp', target)
    finally: lock.rmdir()
    return 1 if failures else 0

if __name__ == '__main__': sys.exit(main())

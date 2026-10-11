#!/usr/bin/env python3
"""GTA L-Hook: stable-Proton Greyhound Racing compatibility diagnostic.

No third-party binaries are redistributed. This script discovers the user's
own three uploaded/downloaded archives, and obtains NativeUI from Guad/NativeUI
unless an existing NativeUI.dll or local archive is provided.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

APP_ID = '271590'
STATE = Path.home() / '.local' / 'share' / 'greyhound-proton-test'
PROTON_TOOL_NAME = 'proton_10'  # Steam's Proton 10.0 compatibility-tool identifier
PINNED_SHVDN_SHA256 = 'b6b4b61bce0437e295ebab065ca437c95ed938e0169197726150047861478211'
# The previously selected, non-nightly stable SHVDN archive; reject all substitutions.
REQUIRED = {
    'greyhound': {'GreyhoundRacing.dll'},
    'dotnet': {'ScriptHookVDotNet.asi', 'ScriptHookVDotNet2.dll', 'ScriptHookVDotNet3.dll'},
    'hook': {'bin/ScriptHookV.dll', 'bin/dinput8.dll'},
}
ROOT_FILES = ['ScriptHookV.dll', 'dinput8.dll', 'ScriptHookVDotNet.asi',
              'ScriptHookVDotNet2.dll', 'ScriptHookVDotNet3.dll',
              'ScriptHookVDotNet.ini']
SCRIPT_FILES = ['GreyhoundRacing.dll', 'NativeUI.dll']
LAST_LOG_NAMES = ['ScriptHookVDotNet.log', 'ScriptHookV.log', 'asiloader.log',
                  'ScriptHookVDotNet2.log', 'ScriptHookVDotNet3.log']


def log(s: str) -> None:
    print(s, flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for buf in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(buf)
    return h.hexdigest()


def runid() -> str:
    return datetime.now().strftime('%Y%m%d-%H%M%S')


def steam_roots() -> list[Path]:
    homes = [Path.home() / p for p in ('.steam/debian-installation', '.steam/steam', '.steam/root',
                                        '.local/share/Steam',
                                        '.var/app/com.valvesoftware.Steam/.local/share/Steam')]
    # Steam's VDF file lists additional game libraries.
    libraries = []
    for root in homes:
        libraries.append(root)
        vdf = root / 'steamapps' / 'libraryfolders.vdf'
        if vdf.is_file():
            try:
                text = vdf.read_text(errors='replace')
                for entry in re.findall(r'"path"\s+"([^"]+)"', text):
                    libraries.append(Path(entry.replace('\\\\', '/')))
            except OSError:
                pass
    unique = []
    seen = set()
    for lib in libraries:
        key = str(lib.expanduser().resolve(strict=False))
        if key not in seen:
            unique.append(lib.expanduser())
            seen.add(key)
    return unique


def discover_game() -> tuple[Path | None, Path | None]:
    for root in steam_roots():
        steamapps = root / 'steamapps'
        manifest = steamapps / ('appmanifest_' + APP_ID + '.acf')
        candidates = []
        if manifest.is_file():
            try:
                m = re.search(r'"installdir"\s+"([^"]+)"', manifest.read_text(errors='replace'))
                if m:
                    candidates.append(steamapps / 'common' / m.group(1))
            except OSError:
                pass
        candidates.extend((steamapps / 'common' / 'Grand Theft Auto V',
                           steamapps / 'common' / 'Grand Theft Auto V Legacy'))
        for folder in candidates:
            if (folder / 'GTA5.exe').is_file():
                # The default compatdata for this library is preferred.
                prefix = steamapps / 'compatdata' / APP_ID / 'pfx'
                if not prefix.is_dir():
                    prefix = next((p / 'steamapps' / 'compatdata' / APP_ID / 'pfx'
                                   for p in steam_roots()
                                   if (p / 'steamapps' / 'compatdata' / APP_ID / 'pfx').is_dir()), None)
                return folder.resolve(), prefix.resolve() if prefix else None
    return None, None


def pick_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    game, prefix = discover_game()
    if args.game_root:
        game = Path(args.game_root).expanduser().resolve(strict=False)
    if args.prefix:
        prefix = Path(args.prefix).expanduser().resolve(strict=False)
    if not game or not (game / 'GTA5.exe').is_file():
        raise RuntimeError('GTA V LEGACY not found: use --game-root "/path/to/Grand Theft Auto V" (must contain GTA5.exe).')
    if not prefix or not prefix.is_dir() or not (prefix / 'system.reg').exists():
        raise RuntimeError('Active Proton prefix not found: use --prefix /path/to/steamapps/compatdata/271590/pfx.')
    return game, prefix


def steam_is_running() -> bool:
    """Never rewrite Steam's config while Steam can overwrite it on exit."""
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            name = (process / 'comm').read_text(errors='replace').strip().lower()
            if name in ('steam', 'steam.exe'):
                return True
        except (OSError, PermissionError):
            pass
    return False


def stable_proton10() -> Path:
    """Only a real installed stable Proton 10.0 directory is acceptable."""
    for root in steam_roots():
        folder = root / 'steamapps' / 'common' / 'Proton 10.0'
        if (folder / 'proton').is_file() and (folder / 'files' / 'bin' / 'wine').is_file():
            return folder.resolve()
    raise RuntimeError('Stable Proton 10.0 is not detected in Steam libraries. Refusing Experimental or an unverified compatibility tool.')


def matching_brace_end(content: str, open_index: int) -> int:
    """Locate a VDF block end, respecting quoted strings and escaped quotes."""
    depth = 0
    quoted = False
    escaped = False
    for i in range(open_index, len(content)):
        c = content[i]
        if quoted:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
    raise RuntimeError('Malformed Steam configuration; refusing to modify it.')


def pin_stable_mapping(vdf: str) -> str:
    """Modify only GTA V's CompatToolMapping entry; preserve all other config."""
    outer = re.search(r'"CompatToolMapping"\s*\{', vdf, re.I)
    if not outer:
        raise RuntimeError('Steam config has no CompatToolMapping block. Refusing to guess its structure.')
    start = vdf.index('{', outer.start())
    end = matching_brace_end(vdf, start)
    body = vdf[start+1:end]
    app = re.search(r'"' + re.escape(APP_ID) + r'"\s*\{', body)
    stanza = '\n\t\t\t"' + APP_ID + '"\n\t\t\t{\n\t\t\t\t"name"\t\t"' + PROTON_TOOL_NAME + '"\n\t\t\t\t"config"\t\t""\n\t\t\t\t"priority"\t\t"250"\n\t\t\t}\n'
    if app:
        opening = body.index('{', app.start())
        closing = matching_brace_end(body, opening)
        prior = body[opening+1:closing]
        value = re.search(r'"name"\s*"([^"]*)"', prior)
        if value and value.group(1) == PROTON_TOOL_NAME:
            return vdf
        body = body[:app.start()] + stanza + body[closing+1:]
    else:
        body = stanza + body
    return vdf[:start+1] + body + vdf[end:]


def find_steam_config() -> Path:
    configs = []
    for root in steam_roots():
        candidate = root / 'config' / 'config.vdf'
        if candidate.is_file():
            canonical = candidate.resolve()
            if canonical not in configs:
                configs.append(canonical)
    if len(configs) != 1:
        raise RuntimeError(f'Expected one Steam config.vdf, found {len(configs)} distinct copies. Refusing to guess which Steam uses.')
    return configs[0]


def verify_or_pin_proton10(backup: Path) -> tuple[Path, Path]:
    """Use Steam's game-specific stable mapping, with a reversible file backup."""
    proton = stable_proton10()
    config = find_steam_config()
    old = config.read_text(encoding='utf-8')
    updated = pin_stable_mapping(old)
    if updated != old:
        if steam_is_running():
            raise RuntimeError('Close Steam completely before changing GTA V from Experimental to stable Proton 10.0. No Steam settings were changed.')
        saved = backup / 'steam-config-before-proton10.vdf'
        saved.write_text(old, encoding='utf-8')
        temp = config.with_name(config.name + '.gtalhook-tmp')
        try:
            temp.write_text(updated, encoding='utf-8')
            temp.replace(config)
        finally:
            if temp.exists():
                temp.unlink()
        if pin_stable_mapping(config.read_text(encoding='utf-8')) != config.read_text(encoding='utf-8'):
            raise RuntimeError('Failed to verify GTA V Steam compatibility-tool mapping.')
        log('GTA V Steam compatibility tool set to Proton 10.0 (stable); prior Steam config backed up at ' + str(saved))
    else:
        log('GTA V Steam compatibility tool already pinned to Proton 10.0 (stable).')
    log('Verified stable Proton binary: ' + str(proton / 'proton'))
    return proton, config


def find_archives(args: argparse.Namespace) -> dict[str, tuple[Path, dict]]:
    dirs = [Path.cwd(), Path(__file__).resolve().parent, Path.home() / 'Downloads']
    if args.archives_dir:
        dirs.insert(0, Path(args.archives_dir).expanduser())
    seen = set()
    found = {}
    for folder in dirs:
        if not folder.is_dir():
            continue
        for archive in sorted(folder.glob('*.zip')):
            if archive.resolve() in seen:
                continue
            seen.add(archive.resolve())
            try:
                with zipfile.ZipFile(archive) as z:
                    names = set(z.namelist())
                    for kind, required in REQUIRED.items():
                        # Never select a nightly even if its filename sorts before
                        # the stable ZIP (e.g. ...-v3.7.0-nightly.193.zip vs
                        # ScriptHookVDotNet.zip). Search for the next candidate.
                        if kind == 'dotnet' and 'nightly' in archive.name.lower():
                            continue
                        if required <= names and kind not in found:
                            found[kind] = (archive, {name: z.getinfo(name) for name in required})
            except (OSError, zipfile.BadZipFile):
                continue
    return found


def find_nativeui(args: argparse.Namespace, game: Path) -> tuple[bytes, str]:
    if args.nativeui:
        p = Path(args.nativeui).expanduser()
        if not p.is_file():
            raise RuntimeError(f'--nativeui file missing: {p}')
        return parse_nativeui(p), str(p)
    existing = game / 'scripts' / 'NativeUI.dll'
    if existing.is_file():
        return existing.read_bytes(), str(existing)
    locations = [Path.cwd(), Path(__file__).resolve().parent, Path.home() / 'Downloads']
    if args.archives_dir:
        locations.insert(0, Path(args.archives_dir).expanduser())
    for folder in locations:
        if not folder.is_dir():
            continue
        for p in sorted(folder.iterdir()):
            if p.is_file() and ('nativeui' in p.name.lower()) and p.suffix.lower() in ('.zip', '.dll'):
                try:
                    return parse_nativeui(p), str(p)
                except (zipfile.BadZipFile, RuntimeError):
                    pass
    if args.no_download:
        raise RuntimeError('NativeUI.dll missing. Download NativeUI 1.9.1 from https://github.com/Guad/NativeUI/releases and provide --nativeui /path/to/file.zip')
    log('NativeUI not local. Fetching the original author’s v1.9.1 release metadata from GitHub...')
    req = urllib.request.Request('https://api.github.com/repos/Guad/NativeUI/releases/tags/1.9.1',
                                 headers={'User-Agent': 'Greyhound-Proton-Test/1.0', 'Accept':'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            release = json.load(r)
        assets = release.get('assets', [])
        ranked = sorted((a for a in assets if a.get('name', '').lower().endswith(('.zip', '.dll'))),
                        key=lambda a: (not a['name'].lower().endswith('.zip'), len(a['name'])))
        for a in ranked:
            url = a.get('browser_download_url', '')
            if not url.startswith('https://github.com/Guad/NativeUI/releases/download/'):
                continue
            log('Downloading '+a['name']+' from '+url)
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Greyhound-Proton-Test/1.0'}), timeout=60) as r:
                data = r.read(20 * 1024 * 1024 + 1)
            if len(data) > 20 * 1024 * 1024:
                raise RuntimeError('NativeUI download unexpectedly large.')
            if a['name'].lower().endswith('.dll'):
                return require_pe(data), url
            import io
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                dlls = [n for n in z.namelist() if Path(n).name.lower() == 'nativeui.dll']
                if dlls:
                    return require_pe(z.read(sorted(dlls, key=len)[0])), url
    except Exception as exc:
        raise RuntimeError('Cannot fetch official NativeUI release: '+str(exc)+
                           '. Download the asset yourself from https://github.com/Guad/NativeUI/releases/tag/1.9.1 and use --nativeui FILE.') from exc
    raise RuntimeError('Official NativeUI 1.9.1 release did not contain NativeUI.dll. Supply it via --nativeui FILE.')


def parse_nativeui(p: Path) -> bytes:
    if p.suffix.lower() == '.dll':
        return require_pe(p.read_bytes())
    with zipfile.ZipFile(p) as z:
        matches = [n for n in z.namelist() if Path(n).name.lower() == 'nativeui.dll']
        if not matches:
            raise RuntimeError('No NativeUI.dll inside '+str(p))
        return require_pe(z.read(sorted(matches, key=len)[0]))


def require_pe(blob: bytes) -> bytes:
    if not blob.startswith(b'MZ'):
        raise RuntimeError('Downloaded NativeUI.dll is not a Windows DLL (missing MZ header)')
    return blob


def get_bytes(found: dict, kind: str, member: str) -> bytes:
    with zipfile.ZipFile(found[kind][0]) as z:
        return z.read(member)


def is_game_running() -> bool:
    proc = Path('/proc')
    needles = {'gta5.exe', 'gtavlauncher.exe', 'gta5_enhanced.exe', 'playgtav.exe'}
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            if (d / 'comm').read_text(errors='replace').strip().lower() in needles:
                return True
        except (OSError, PermissionError):
            pass
    return False


def size_bytes(folder: Path) -> int:
    total = 0
    for path, _, names in os.walk(folder, followlinks=False):
        for name in names:
            f = Path(path) / name
            if not f.is_symlink():
                try: total += f.stat().st_size
                except OSError: pass
    return total


def backup_prefix(prefix: Path, target: Path) -> None:
    bytes_needed = size_bytes(prefix)
    free = shutil.disk_usage(target.parent).free
    minimum = bytes_needed + 800 * 1024 * 1024  # .NET installers need room too
    if free < minimum:
        raise RuntimeError(f'Not enough space for full prefix backup + dependency install: need ~{minimum/2**30:.2f} GiB, free {free/2**30:.2f} GiB.')
    log(f'Backing up full Proton prefix ({bytes_needed/2**30:.2f} GiB) to {target} ...')
    subprocess.run(['cp', '-a', '--reflink=auto', '--', str(prefix), str(target)], check=True)
    if not (target / 'system.reg').is_file():
        raise RuntimeError('Prefix backup verification failed.')


def existing_net48(prefix: Path) -> bool:
    try:
        s = (prefix / 'system.reg').read_text(encoding='utf-8', errors='replace')
        for section in re.finditer(r'^\[([^\]]+)\]([^\[]*)', s, re.M | re.S):
            key, values = section.group(1), section.group(2)
            if 'NET Framework Setup' in key and 'NDP' in key and 'v4' in key and 'Full' in key:
                m = re.search(r'"Release"=dword:([0-9a-fA-F]+)', values)
                if m and int(m.group(1), 16) >= 528040:
                    return True
    except OSError:
        pass
    return False


def do_command(cmd: list[str], logfile: Path) -> None:
    log('Running: '+' '.join(cmd))
    with logfile.open('w', encoding='utf-8') as f:
        f.write('COMMAND: '+' '.join(cmd)+'\nSTART: '+datetime.now().isoformat()+'\n\n')
        f.flush()
        p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
        code = p.wait()
        f.write(f'\nEXIT CODE: {code}\n')
    if code:
        raise RuntimeError(f'Command failed (exit {code}); see {logfile}. Stop here. Do not assume the dependency installed.')


def snapshot_game(game: Path, backup: Path, pending: dict[Path, bytes]) -> list[dict]:
    target = backup / 'game-files'
    target.mkdir(parents=True, exist_ok=True)
    manifest = []
    for dest, blob in pending.items():
        rel = dest.relative_to(game).as_posix()
        record = {'path': rel, 'existed': dest.exists(), 'new_sha256': hashlib.sha256(blob).hexdigest()}
        if dest.exists():
            if not dest.is_file() or dest.is_symlink():
                raise RuntimeError(f'Refusing to overwrite non-regular file: {dest}')
            copy_path = target / rel
            copy_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, copy_path)
            record['old_sha256'] = sha256(copy_path)
        manifest.append(record)
    return manifest


def write_files(pending: dict[Path, bytes]) -> None:
    for dest, blob in pending.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp = dest.with_name(dest.name+'.gh-test-tmp')
        try:
            temp.write_bytes(blob)
            temp.replace(dest)
        finally:
            if temp.exists(): temp.unlink()
        log('Installed '+str(dest))


def collect(game: Path, prefix: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    lines = [f'Collected: {datetime.now().isoformat()}', f'Game: {game}', f'Prefix: {prefix}',
             'AppID: '+APP_ID, f'.NET Framework 4.8 detected in prefix: {existing_net48(prefix)}',
             'NOTE: Stable ScriptHookVDotNet 3.6.0 has known issues with GTA V 3258+.',
             'NOTE: Missing SHVDN log may mean ASI or Windows DLL override never loaded.', '']
    for base in [game, game/'scripts']:
        for file in sorted(base.iterdir()):
            if file.is_file() and file.suffix.lower() in ('.asi', '.dll') and (base == game or file.name in SCRIPT_FILES):
                lines.append(f'{file.relative_to(game)} size={file.stat().st_size} sha256={sha256(file)}')
    logs = []
    for base in [game, game/'scripts']:
        for name in LAST_LOG_NAMES:
            f = base / name
            if f.is_file() and f.stat().st_size <= 25 * 1024 * 1024:
                logs.append(f)
    for f in game.glob('*ScriptHook*log*'):
        if f.is_file() and f not in logs and f.stat().st_size <= 25 * 1024 * 1024:
            logs.append(f)
    for f in logs:
        shutil.copy2(f, out / (f.parent.name+'-'+f.name))
        lines.append('Captured game log: '+str(f))
    steamlog = Path.home() / ('steam-'+APP_ID+'.log')
    if steamlog.is_file():
        # Steam's PROTON_LOG can be huge. Only retain the tail.
        with steamlog.open('rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell()-5*1024*1024))
            (out/'proton-log-tail.txt').write_bytes(f.read())
        lines.append('Captured last 5 MiB of '+str(steamlog))
    # Include dependency-install logs and a manifest for troubleshooting.
    backup_base = STATE/'backups'
    backups = sorted([p for p in backup_base.iterdir() if (p/'manifest.json').exists()], reverse=True) if backup_base.is_dir() else []
    if backups:
        newest = backups[0]
        for f in newest.glob('*.log'):
            if f.stat().st_size <= 25*1024*1024:
                shutil.copy2(f, out/('install-'+f.name))
        shutil.copy2(newest/'manifest.json', out/'install-manifest.json')
        lines.append('Install backup ID: '+newest.name)
    (out/'report.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    log('Report saved to '+str(out/'report.txt'))
    zip_name = shutil.make_archive(str(out), 'zip', root_dir=out)
    log('Diagnostic ZIP ready for sharing: '+zip_name)
    if not logs:
        log('No ScriptHookVDotNet log found. Confirm Steam launch options: PROTON_LOG=1 WINEDLLOVERRIDES="dinput8=n,b" %command%')


def do_install(args: argparse.Namespace) -> None:
    game, prefix = pick_paths(args)
    if is_game_running():
        raise RuntimeError('GTA V appears to be running. Close the game before installation.')
    found = find_archives(args)
    log('\nGTA Legacy: '+str(game)+'\nProton prefix: '+str(prefix))
    missing = sorted(set(REQUIRED)-set(found))
    if missing:
        raise RuntimeError('Missing archives '+', '.join(missing)+'. Put Greyhound Racing 1.1, ScriptHookV 3889, and stable ScriptHookVDotNet ZIPs in ~/Downloads or specify --archives-dir.')
    for name in ('greyhound', 'hook', 'dotnet'):
        f = found[name][0]
        log(f'{name}: {f}\n    SHA-256: {sha256(f)}')
    # Pin the exact stable archive already selected for this test; no nightlies.
    if sha256(found['dotnet'][0]) != PINNED_SHVDN_SHA256:
        raise RuntimeError('SHVDN archive hash differs from the pinned stable build. Refusing unverified or nightly versions.')
    # Verify supplied stable archive, not a nightly, with its expected content.
    if 'nightly' in found['dotnet'][0].name.lower():
        raise RuntimeError('Nightly refused. Use stable ScriptHookVDotNet archive.')
    with zipfile.ZipFile(found['dotnet'][0]) as z:
        if 'README.txt' not in z.namelist() or 'Framework' not in z.read('README.txt').decode(errors='replace'):
            raise RuntimeError('Unrecognized SHVDN package; refusing to install.')
    nativeui, nativeui_source = find_nativeui(args, game)
    log('NativeUI source: '+nativeui_source)
    if not shutil.which('protontricks'):
        raise RuntimeError('protontricks not installed; refusing dependency installation.')
    if not shutil.which('cp'):
        raise RuntimeError('Missing GNU cp for reversible prefix backup.')
    if not (game/'ScriptHookV.dll').exists():
        log('ScriptHookV.dll missing: will install from your supplied archive.')
    else:
        log('Existing ScriptHookV.dll detected: KEEPING IT, because it already works on your setup.')
    pending = {}
    for name in ('ScriptHookV.dll', 'dinput8.dll'):
        dest = game/name
        if not dest.exists():
            pending[dest] = get_bytes(found, 'hook', 'bin/'+name)
    for name in ('ScriptHookVDotNet.asi', 'ScriptHookVDotNet2.dll', 'ScriptHookVDotNet3.dll'):
        pending[game/name] = get_bytes(found, 'dotnet', name)
    # Stable 3.6.0 may crash when F4 invokes its console on GTA >= 3258.
    # Disable the optional SHVDN console/reload hotkeys without changing other settings.
    ini_path = game/'ScriptHookVDotNet.ini'
    ini_text = ini_path.read_text(errors='replace') if ini_path.is_file() else ''
    for key in ('ReloadKey', 'ConsoleKey'):
        pat = r'(?im)^\s*'+key+r'\s*=.*$'
        if re.search(pat, ini_text):
            ini_text = re.sub(pat, key+'=None', ini_text)
        else:
            ini_text += ('\n' if ini_text and not ini_text.endswith('\n') else '')+key+'=None\n'
    new_ini = ini_text.encode('utf-8')
    if not ini_path.is_file() or ini_path.read_bytes() != new_ini:
        pending[ini_path] = new_ini
        log('SHVDN console/reload hotkeys disabled for the stable 3.6.0 test (existing settings backed up).')
    pending[game/'scripts'/'GreyhoundRacing.dll'] = get_bytes(found, 'greyhound', 'GreyhoundRacing.dll')
    if not (game/'scripts'/'NativeUI.dll').is_file():
        pending[game/'scripts'/'NativeUI.dll'] = nativeui
    if args.plan:
        stable_proton10()
        log('\nPLAN ONLY. Will copy:')
        for dest in pending: log('  '+str(dest))
        log('Will pin GTA V to stable Proton 10.0 with config backup; verify Proton 10.0 is installed, then back up the prefix, install verified dependencies and mod files.')
        return
    log('\nIMPORTANT: Stable SHVDN v3.6.0 has a documented GTA V 3258+ problem EVEN ON WINDOWS.')
    log('This is a compatibility diagnostic, not a promise the mod will run.')
    log('No nightlies and no GTA downgrade will be installed. Your existing base Script Hook V is preserved.')
    log('A full prefix snapshot may be large, and .NET installation may open GUI windows and take time.')
    log('Explicit --install received. No confirmation prompts.')
    job = runid()
    dest = STATE / 'backups' / job
    dest.mkdir(parents=True, exist_ok=False)
    manifest = {'id':job, 'game':str(game), 'prefix':str(prefix), 'files':[], 'status':'preparing',
                'source_sha256':{k:sha256(v[0]) for k,v in found.items()},'nativeui_source':nativeui_source}
    manifest_path = dest / 'manifest.json'
    def save(): manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    save()
    try:
        proton, config = verify_or_pin_proton10(dest)
        manifest['proton'] = str(proton)
        manifest['steam_config'] = str(config)
        manifest['steam_mapping'] = PROTON_TOOL_NAME
        save()
        backup_prefix(prefix, dest / 'pfx')
        manifest['files'] = snapshot_game(game, dest, pending)
        manifest['status']='snapshot_complete'
        save()
        if pin_stable_mapping(config.read_text(encoding='utf-8')) != config.read_text(encoding='utf-8'):
            raise RuntimeError('GTA V is not pinned to Proton 10.0: stopping before running Protontricks.')
        if existing_net48(prefix):
            log('.NET 4.8 release registry key detected; skipping reinstall.')
        else:
            do_command(['protontricks', APP_ID, 'dotnet48'], dest/'dotnet48-install.log')
        do_command(['protontricks', APP_ID, 'vcrun2019'], dest/'vcrun2019-install.log')
        # dotnet48's winetricks recipe can leave the prefix presenting an old OS.
        do_command(['protontricks', APP_ID, 'win10'], dest/'win10-restore.log')
        manifest['status']='dependencies_attempted'
        save()
        write_files(pending)
        manifest['status']='installed'
        save()
    except Exception:
        manifest['status']='failed'
        save()
        log('\nINSTALL INCOMPLETE. Full prefix backup and original game files are preserved in '+str(dest))
        log('After closing Steam and GTA, use --rollback to restore them if needed.')
        raise
    log('\nINSTALLED. Backup ID: '+job)
    log('GTA startup logging: in Steam launch options use PROTON_LOG=1 WINEDLLOVERRIDES="dinput8=n,b" %command%')
    log('Start STORY MODE only; go to Vinewood Racetrack (beside the casino) and look for greyhound marker.')
    log('After exiting game run: python3 '+Path(__file__).name+' --collect')
    log('To revert: python3 '+Path(__file__).name+' --rollback '+job)
    log('Install log and recovery manifest: '+str(dest))


def do_rollback(args: argparse.Namespace) -> None:
    where = STATE/'backups'
    if args.rollback == 'latest':
        backups = sorted([p for p in where.iterdir() if (p/'manifest.json').exists()], reverse=True) if where.is_dir() else []
        if not backups: raise RuntimeError('No backups found.')
        backup = backups[0]
    else:
        backup = where/args.rollback
    if not (backup/'manifest.json').exists():
        raise RuntimeError('Backup manifest not found: '+str(backup))
    data = json.loads((backup/'manifest.json').read_text())
    game = Path(data['game'])
    prefix = Path(data['prefix'])
    snapshot = backup/'pfx'
    if is_game_running():
        raise RuntimeError('Close GTA before rollback.')
    if not snapshot.is_dir() or not (snapshot/'system.reg').exists():
        raise RuntimeError('No complete prefix snapshot; cannot safely roll back.')
    # Reject if game root was moved or mismatches.
    if not (game/'GTA5.exe').is_file():
        raise RuntimeError('Original GTA game root no longer found: '+str(game))
    log('Rollback backup: '+str(backup))
    log('Game: '+str(game)+'\nPrefix: '+str(prefix))
    log('Explicit --rollback received. Restoring backup without an extra confirmation prompt.')
    # Preserve saves/settings written after the snapshot, before restoring pfx.
    save_stash = backup/'user-documents-preserved-on-rollback'
    if prefix.is_dir():
        users = prefix/'drive_c'/'users'
        if users.is_dir():
            for user in users.iterdir():
                current = user/'Documents'/'Rockstar Games'
                if current.is_dir() and not current.is_symlink():
                    destination = save_stash/user.name/'Rockstar Games'
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(current, destination, dirs_exist_ok=True)
                    log('Preserved current Rockstar saves/settings: '+str(current))
    failed_snapshot = prefix.with_name(prefix.name+'.before-greyhound-restore-'+runid())
    if prefix.exists():
        prefix.rename(failed_snapshot)
    try:
        subprocess.run(['cp', '-a', '--reflink=auto', '--', str(snapshot), str(prefix)], check=True)
        if not (prefix/'system.reg').is_file():
            raise RuntimeError('Restored prefix was incomplete.')
    except Exception:
        if prefix.exists():
            shutil.rmtree(prefix)
        if failed_snapshot.exists():
            failed_snapshot.rename(prefix)
        raise
    if save_stash.is_dir():
        for user in save_stash.iterdir():
            saved = user/'Rockstar Games'
            if saved.is_dir():
                destination = prefix/'drive_c'/'users'/user.name/'Documents'/'Rockstar Games'
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(saved, destination, dirs_exist_ok=True)
                log('Reapplied saves/settings from before rollback: '+str(destination))
    for r in data.get('files', []):
        target = game/r['path']
        if r['existed']:
            original = backup/'game-files'/r['path']
            if original.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, target)
                log('Restored '+str(target))
        elif target.is_file() and sha256(target) == r['new_sha256']:
            target.unlink()
            log('Removed test file '+str(target))
        elif target.exists():
            log('LEFT modified file alone (since install): '+str(target))
    log('Rollback finished. Kept pre-restore prefix at '+str(failed_snapshot) if failed_snapshot.exists() else 'Rollback finished.')


def main() -> None:
    ap = argparse.ArgumentParser(description='Reversible Greyhound Racing .NET 4.8 test for GTA V Legacy on Steam/Proton.')
    action = ap.add_mutually_exclusive_group()
    action.add_argument('--install', action='store_true', help='Backup prefix; install .NET48 / VC++ 2019 and game mod DLLs.')
    action.add_argument('--collect', action='store_true', help='Collect runtime/game logs after a story-mode test.')
    action.add_argument('--rollback', nargs='?', const='latest', metavar='BACKUP_ID', help='Restore prefix and game DLLs from backup.')
    ap.add_argument('--plan', action='store_true', help='With --install, show intended file changes but do not modify.')
    ap.add_argument('--game-root', help='Absolute path to GTA5.exe parent folder.')
    ap.add_argument('--prefix', help='Absolute path to GTA V Proton pfx folder (if auto detection fails).')
    ap.add_argument('--archives-dir', help='Folder containing the three supplied ZIPs.')
    ap.add_argument('--nativeui', help='Local official NativeUI 1.9.1 DLL or zip file.')
    ap.add_argument('--no-download', action='store_true', help='Never download NativeUI from GitHub.')
    ap.add_argument('--yes', action='store_true', help=argparse.SUPPRESS)  # Legacy compatibility; no longer required
    args = ap.parse_args()
    try:
        if args.rollback:
            do_rollback(args)
        elif args.install:
            do_install(args)
        else:
            game, prefix = pick_paths(args)
            log('GTA V Legacy: '+str(game))
            log('Proton prefix: '+str(prefix))
            log('.NET 4.8 detected via registry: '+str(existing_net48(prefix)))
            archives = find_archives(args)
            for kind in REQUIRED:
                log(f'{kind}: {archives[kind][0]}' if kind in archives else f'{kind}: MISSING (place ZIP in ~/Downloads)')
            if args.collect:
                collect(game, prefix, STATE/'reports'/runid())
            else:
                log('Use --install --plan to preview, then --install to back up and install.')
    except (RuntimeError, OSError, subprocess.CalledProcessError, EOFError) as e:
        log('\nERROR: '+str(e))
        sys.exit(1)


if __name__ == '__main__':
    main()

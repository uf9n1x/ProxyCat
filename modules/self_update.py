"""
模块名称：modules.self_update
功能描述：一键自更新：从 PROJECT_REPO_URL 的 GitHub Release 取最新版本信息，
          下载源码包（zipball）、按排除规则覆盖本地文件、然后重启进程。
          重启方式自适应：Linux 上若 systemd 用户服务 proxycat.service 在跑则走 systemctl restart，
          其余环境（Windows 裸进程 / nohup）用分离子进程拉起新进程后本进程退出。
职责边界：负责：Release 查询（带 5 分钟缓存）、更新包下载与覆盖、进度状态、重启编排。
          不负责：版本号比对口径（复用 version_check 的解析规则）、HTTP 路由（app.py）。
关键依赖：modules.modules（CURRENT_VERSION、PROJECT_REPO_URL、logger）、httpx、packaging。
已知限制：
  1. 只做「覆盖式」更新：Release 里删掉的旧文件会留在磁盘上（配置/日志/数据目录本来就不动），
     大版本清理旧文件请手动处理。
  2. 覆盖在运行中的 .py 文件：Windows 上文本文件可覆盖，但正在运行的解释器已把代码读进内存，
     新代码在重启后才生效——这正是更新后必须重启的原因。
  3. config/、logs/、modules/proxypool/data/（含归属地库与数据库）以及 backups/ 永不覆盖，
     部署机各自的凭据与运行数据在更新后原样保留。
  4. 公开仓库无需 token；若仓库转私有，GitHub API 会返回 404，本模块按「查询失败」上报。
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

import httpx

from modules.modules import CURRENT_VERSION, PROJECT_REPO_URL
from modules.version_check import _parse_version

logger = logging.getLogger(__name__)

RELEASE_API_TTL_SECONDS = 300

DOWNLOAD_TIMEOUT_SECONDS = 120.0

# 这些顶层目录/文件永不被更新包覆盖：各部署机的凭据与运行数据只属于自己
_EXCLUDE_TOP = {
    'config', 'logs', 'backups', '.git', '.github', '.workbuddy', '.scratch',
    '.venv', 'venv', '__pycache__', '.idea', '.vscode', 'node_modules',
}
# 二级目录排除：modules/proxypool/data 下是数据库与归属地库
_EXCLUDE_NESTED = {('modules', 'proxypool', 'data')}

_state_lock = threading.Lock()
_state = {'phase': 'idle', 'message': '', 'started_at': None, 'updated_at': None}
_release_cache = {'at': 0.0, 'data': None}


def _repo_parts():
    m = re.match(r'^https://github\.com/([^/]+)/([^/]+)/?$', PROJECT_REPO_URL.strip().rstrip('/'))
    if not m or m.group(1).lower() == 'placeholder':
        return None
    return m.group(1), m.group(2)


def _headers():
    return {'User-Agent': 'ProxyCat-SelfUpdate', 'Accept': 'application/vnd.github+json'}


def _set_state(phase, message=''):
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    with _state_lock:
        if phase == 'idle':
            _state.clear()
            _state.update({'phase': 'idle', 'message': '', 'started_at': None, 'updated_at': None})
            return
        if _state['phase'] == 'idle':
            _state['started_at'] = now
        _state.update({'phase': phase, 'message': message, 'updated_at': now})


def state():
    with _state_lock:
        return dict(_state)


def latest_release(force=False):
    """查最新 Release；5 分钟内复用缓存。返回 dict 或 None（查询失败/仓库未配置）。"""
    parts = _repo_parts()
    if not parts:
        return None
    owner, repo = parts
    with _state_lock:
        cache_ok = (not force) and _release_cache['data'] and \
                   (time.time() - _release_cache['at'] < RELEASE_API_TTL_SECONDS)
        if cache_ok:
            return dict(_release_cache['data'])
    try:
        resp = httpx.get(
            f'https://api.github.com/repos/{owner}/{repo}/releases/latest',
            headers=_headers(), timeout=15.0, follow_redirects=True,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.warning(f"查询最新 Release 失败: {e}")
        return None
    tag = str(payload.get('tag_name') or '')
    info = {
        'tag': tag,
        'version': tag if _parse_version(tag) else None,
        'html_url': payload.get('html_url') or '',
        'zipball_url': payload.get('zipball_url') or '',
        'notes': (payload.get('body') or '')[:2000],
        'published_at': payload.get('published_at') or '',
    }
    with _state_lock:
        _release_cache['at'] = time.time()
        _release_cache['data'] = dict(info)
    return info


def has_update(release=None):
    """release 为 latest_release() 的结果；版本比较口径与 version_check 一致。"""
    release = release or latest_release()
    if not release or not release.get('tag'):
        return False, None
    remote = _parse_version(release['tag'])
    current = _parse_version(CURRENT_VERSION)
    if remote is None or current is None:
        return False, release
    return remote > current, release


def _relaunch_detached():
    """以同样命令行在分离会话里重新拉起本程序。返回 True 表示已交由新进程。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [sys.executable] + [a for a in sys.argv]
    log_path = os.path.join(root, 'logs', 'relaunch.log')
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    kwargs = {'cwd': root, 'stdin': subprocess.DEVNULL}
    try:
        log_fp = open(log_path, 'ab')
    except OSError:
        log_fp = None
    if log_fp is not None:
        kwargs['stdout'] = log_fp
        kwargs['stderr'] = log_fp
    if os.name == 'nt':
        kwargs['creationflags'] = getattr(subprocess, 'DETACHED_PROCESS', 0x00000008) | \
                                  getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
    else:
        kwargs['start_new_session'] = True
    try:
        subprocess.Popen(cmd, **kwargs)
        return True
    except Exception as e:
        logger.error(f"重启进程拉起失败: {e}")
        return False


def _restart_via_systemd():
    """Linux 且 proxycat.service 用户服务在跑：走 systemd 重启（最干净）。"""
    if os.name != 'nt' and shutil.which('systemctl'):
        try:
            probe = subprocess.run(
                ['systemctl', '--user', 'is-active', 'proxycat.service'],
                capture_output=True, text=True, timeout=6,
            )
            if probe.stdout.strip() == 'active':
                subprocess.Popen(
                    ['systemctl', '--user', 'restart', 'proxycat.service'],
                    start_new_session=True,
                )
                return True
        except Exception as e:
            logger.warning(f"systemd 重启探测失败，回退到进程自拉起: {e}")
    return False


def _restart():
    if _restart_via_systemd():
        time.sleep(1.5)
        os._exit(0)
    if _relaunch_detached():
        time.sleep(1.5)
        os._exit(0)
    raise RuntimeError('restart failed: both systemd and detached relaunch unavailable')


def _download_zipball(url, dest):
    with httpx.stream('GET', url, headers=_headers(), timeout=DOWNLOAD_TIMEOUT_SECONDS,
                      follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, 'wb') as fp:
            for chunk in resp.iter_bytes(64 * 1024):
                fp.write(chunk)


def _apply_update():
    try:
        release = latest_release(force=True)
        if not release:
            _set_state('error', '无法获取最新版本信息（网络或仓库配置问题）')
            return
        has, _ = has_update(release)
        if not has:
            _set_state('error', f'远端 {release["tag"]} 不比当前 {CURRENT_VERSION} 新，无需更新')
            return

        _set_state('downloading', f'正在下载 {release["tag"]} …')
        tmp = tempfile.NamedTemporaryFile(prefix='proxycat-update-', suffix='.zip', delete=False)
        tmp.close()
        try:
            _download_zipball(release['zipball_url'], tmp.name)
            size_mb = os.path.getsize(tmp.name) / 1048576

            _set_state('extracting', f'下载完成（{size_mb:.1f} MB），正在覆盖文件 …')
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            applied = 0
            with zipfile.ZipFile(tmp.name) as zf:
                names = zf.namelist()
                if not names:
                    raise RuntimeError('更新包为空')
                prefix = names[0].split('/')[0] + '/'
                for name in names:
                    rel = name[len(prefix):] if name.startswith(prefix) else name
                    if not rel or rel.endswith('/'):
                        continue
                    segs = rel.replace('\\', '/').split('/')
                    if segs[0] in _EXCLUDE_TOP:
                        continue
                    if tuple(segs[:3]) in _EXCLUDE_NESTED:
                        continue
                    if any(seg == '__pycache__' for seg in segs):
                        continue
                    target = os.path.join(root, *segs)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(name) as src, open(target, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    applied += 1
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        logger.info(f"自更新覆盖完成：{applied} 个文件 → {release['tag']}，准备重启")
        _set_state('restarting', f'更新完成（{applied} 个文件），正在重启 …')
        time.sleep(1.0)
        _restart()
    except Exception as e:
        logger.error(f"自更新失败: {e}", exc_info=True)
        _set_state('error', f'更新失败: {e}')


def start_update():
    """启动后台更新线程；已有更新在跑时返回 False。"""
    with _state_lock:
        if _state['phase'] not in ('idle', 'error', 'done'):
            return False
    threading.Thread(target=_apply_update, daemon=True, name='self-update').start()
    return True

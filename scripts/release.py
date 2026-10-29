"""
发版脚本：一条命令完成 版本号提升 → 提交 → 打 tag → 推送 → 创建 GitHub Release。

用法：
    .venv/Scripts/python.exe scripts/release.py 1.0.1 -m "修复了 xxx；新增 yyy"
    .venv/Scripts/python.exe scripts/release.py 1.1.0 -m "..." --dry-run

前置条件：
  1. git remote origin 已配置且可推送（首次推送后即长期有效）。
  2. 环境变量 GITHUB_TOKEN 指向一个对目标仓库有 repo 权限的 Personal Access Token
     （仅本机发版用；同事的部署机不需要任何 token——公开仓库拉更新包不走鉴权）。

流程说明：
  - 版本号写入 modules/modules.py 的 CURRENT_VERSION（格式 ProxyCat-V<版本>）；
  - GitHub 收到 tag 后自动生成 Source code (zip)，面板的一键更新就是下载这个包；
  - Release 说明默认取 -m 文本；留空则用最近一次提交的说明。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES_PY = os.path.join(ROOT, 'modules', 'modules.py')
VERSION_LINE = re.compile(r'^(CURRENT_VERSION = )"([^"]+)"', re.M)


def sh(*args, **kw):
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"命令失败: {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout.strip()


def repo_slug():
    url = sh('git', 'remote', 'get-url', 'origin')
    m = re.search(r'github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$', url)
    if not m:
        sys.exit(f"无法从 origin 解析 GitHub 仓库: {url}")
    return m.group(1)


def set_version(new):
    src = open(MODULES_PY, encoding='utf-8').read()
    updated, n = VERSION_LINE.subn(lambda m: f'{m.group(1)}"ProxyCat-V{new}"', src, count=1)
    if n != 1:
        sys.exit('未在 modules/modules.py 找到 CURRENT_VERSION 行')
    open(MODULES_PY, 'w', encoding='utf-8', newline='').write(updated)


def create_release(slug, tag, token, notes):
    api = f'https://api.github.com/repos/{slug}/releases'
    payload = json.dumps({
        'tag_name': tag,
        'target_commitish': 'main',
        'name': f'ProxyCat-{tag}' if not tag.startswith('ProxyCat-') else tag,
        'body': notes or '',
        'draft': False,
        'prerelease': False,
    }).encode()
    req = urllib.request.Request(api, data=payload, method='POST', headers={
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'ProxyCat-Release',
        'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return data.get('html_url')


def main():
    ap = argparse.ArgumentParser(description='ProxyCat 发版：改版本号、打 tag、推 GitHub、建 Release')
    ap.add_argument('version', help='新版本号，如 1.0.1（内部格式 ProxyCat-V1.0.1）')
    ap.add_argument('-m', '--message', default='', help='Release 说明（留空用最近一次提交说明）')
    ap.add_argument('--dry-run', action='store_true', help='只打印将执行的动作，不实际执行')
    args = ap.parse_args()

    ver = args.version.lstrip('vV')
    if not re.fullmatch(r'\d+\.\d+(\.\d+)?', ver):
        sys.exit(f'版本号格式应为 x.y 或 x.y.z，收到: {args.version}')
    tag = f'ProxyCat-V{ver}'

    slug = repo_slug()
    token = os.environ.get('GITHUB_TOKEN', '').strip()
    notes = args.message or sh('git', 'log', '-1', '--pretty=%B')

    print(f'仓库: {slug}')
    print(f'版本: ProxyCat-V{CURRENT_VERSION_NOW()} -> {tag}')
    print(f'说明: {notes[:80]}{"..." if len(notes) > 80 else ""}')
    if args.dry_run:
        print('(dry-run，未执行任何动作)')
        return
    if not token:
        sys.exit('缺少 GITHUB_TOKEN 环境变量（创建 Release 需要）。设置方法：\n'
                 '  PowerShell: $env:GITHUB_TOKEN = "ghp_xxx" 后重新运行')

    set_version(ver)
    sh('git', 'add', 'modules/modules.py')
    if sh('git', 'status', '--porcelain', '--', 'modules/modules.py'):
        sh('git', 'commit', '-m', f'chore(release): bump version to {tag}')
    if tag in sh('git', 'tag', '--list'):
        sys.exit(f'标签 {tag} 已存在，如需重发请先删除：git tag -d {tag} && git push origin :refs/tags/{tag}')
    sh('git', 'tag', tag)
    sh('git', 'push', 'origin', 'main')
    sh('git', 'push', 'origin', tag)
    url = create_release(slug, tag, token, notes)
    print(f'\n✅ Release 已创建: {url}')
    print('同事的面板会在 24 小时内的例行检查（或手动点“立即检查更新”）中看到新版本，'
          '点「一键更新」即可完成下载、覆盖与重启。')


def CURRENT_VERSION_NOW():
    src = open(MODULES_PY, encoding='utf-8').read()
    m = VERSION_LINE.search(src)
    return m.group(2) if m else '?'


if __name__ == '__main__':
    main()

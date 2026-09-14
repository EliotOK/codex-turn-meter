"""Install the packaged plugin into this user's personal Codex marketplace."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    if sys.version_info < (3,10):
        raise SystemExit('Python 3.10+ is required.')
    home=Path.home()
    source=Path(__file__).resolve().parent/'codex-turn-meter'
    target=home/'plugins'/'codex-turn-meter'
    helpers=home/'.codex/skills/.system/plugin-creator/scripts'
    create=helpers/'create_basic_plugin.py'
    if not create.is_file():
        raise SystemExit('Codex plugin-creator skill is required for personal marketplace registration.')
    bundled=Path(os.environ.get('LOCALAPPDATA',str(home/'AppData/Local')))/'OpenAI/Codex/bin'
    candidates=[p for p in bundled.glob('*/codex.exe') if p.is_file() and p.stat().st_size > 1024*1024] if os.name=='nt' else []
    codex=str(max(candidates,key=lambda p:p.stat().st_mtime)) if candidates else (
        shutil.which('codex.cmd') if os.name=='nt' else shutil.which('codex'))
    if not codex:
        raise SystemExit('Codex CLI not found in PATH.')
    marketplace=home/'.agents/plugins/marketplace.json'
    existing=marketplace.exists()
    if existing:
        market=subprocess.check_output([sys.executable,str(helpers/'read_marketplace_name.py')],text=True).strip()
    else:
        market='personal'
    # A second installation is explicit and updates only this plugin's own tree.
    if target.exists():
        manifest=json.loads((target/'.codex-plugin/plugin.json').read_text(encoding='utf-8'))
        if manifest.get('name')!='codex-turn-meter':
            raise SystemExit('Target is not the expected plugin.')
        entries=json.loads(marketplace.read_text(encoding='utf-8'))['plugins'] if existing else []
        if not any(p.get('name')=='codex-turn-meter' and p.get('source')=={'source':'local','path':'./plugins/codex-turn-meter'} for p in entries):
            raise SystemExit('Existing target has no matching personal marketplace entry.')
    else:
        subprocess.run([sys.executable,str(create),'codex-turn-meter','--with-marketplace'],check=True)
    shutil.copytree(source,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    config=json.loads((target/'.mcp.json').read_text(encoding='utf-8'))
    config['mcpServers']['meter']['command']=sys.executable
    (target/'.mcp.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    subprocess.run([sys.executable,str(helpers/'update_plugin_cachebuster.py'),str(target)],check=True)
    subprocess.run([sys.executable,str(helpers/'validate_plugin.py'),str(target)],check=True)
    subprocess.run([codex,'plugin','add',f'codex-turn-meter@{market}'],check=True)
    print('Installed. Open a new task and ask: 打开当前任务的用量面板')


if __name__=='__main__':
    main()

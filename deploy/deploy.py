import json
import os
import subprocess
import sys
import argparse
import time
import tempfile
import atexit
import shlex
import re
from pathlib import Path

from scraping_stack import (
    SCRAPING_COMPOSE_PROJECT,
    build_scraping_compose,
    scraper_image_source_hash,
)

# Web アプリ本体の deploy 手順をまとめたスクリプト。
# 共有データは remote 側に残しつつ、コードと設定だけを安全に更新する。

# Store temp key paths for cleanup
_temp_key_paths = []
_cleanup_registered = False
_RUNTIME_MUNICIPALITY_FILES = (
    'municipality_master.tsv',
    'assembly_minutes_system_urls.tsv',
    'reiki_system_urls.tsv',
    'municipality_homepages.csv',
)
DEFAULT_SCRAPER_IMAGE_NAME = "miyabe-tools-scraper"
DEFAULT_GIJIROKU_LOOP_SECONDS = 21600
DEFAULT_REIKI_LOOP_SECONDS = 21600
DEFAULT_SCRAPER_FAIL_SLEEP_SECONDS = 900
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]

def _current_windows_user_sid():
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    sid = result.stdout.strip()
    if not sid:
        raise RuntimeError("Could not determine current Windows user SID.")
    return sid


def _restrict_private_key_permissions(path):
    if os.name != 'nt':
        os.chmod(path, 0o600)
        return

    sid = _current_windows_user_sid()
    subprocess.run(
        [
            "icacls",
            path,
            "/inheritance:r",
            "/grant:r",
            f"*{sid}:F",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _register_temp_key_for_cleanup(temp_path):
    global _cleanup_registered
    _temp_key_paths.append(temp_path)
    if not _cleanup_registered:
        atexit.register(cleanup_temp_keys)
        _cleanup_registered = True


def _write_temp_ssh_key(key_bytes, *, source_label):
    fd, temp_path = tempfile.mkstemp(prefix='ssh_key_')
    os.close(fd)
    with open(temp_path, 'wb') as handle:
        handle.write(key_bytes)
    _restrict_private_key_permissions(temp_path)
    _register_temp_key_for_cleanup(temp_path)
    print(f"SSH key prepared from {source_label}: {temp_path}")
    return temp_path


def prepare_ssh_key(key_path):
    """Copy a local SSH key to a temp file with stable permissions."""
    with open(key_path, 'rb') as handle:
        key_bytes = handle.read()
    return _write_temp_ssh_key(key_bytes, source_label=key_path)


def wsl_mount_path_to_windows_path(path):
    """Convert /mnt/i/path used in WSL to I:\\path for Windows-side fallback reads."""
    match = re.match(r'^/mnt/([A-Za-z])/(.+)$', str(path).strip())
    if not match:
        return ''
    drive = match.group(1).upper()
    rest = match.group(2).replace('/', '\\')
    return f"{drive}:\\{rest}"


def read_windows_file_bytes_from_wsl(windows_path):
    """Read a Windows file from WSL even when the drive is not mounted under /mnt."""
    script = (
        "$path = [Console]::In.ReadToEnd();"
        "$bytes = [System.IO.File]::ReadAllBytes($path.Trim());"
        "[Console]::OpenStandardOutput().Write($bytes, 0, $bytes.Length)"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            input=windows_path.encode('utf-8'),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
    except FileNotFoundError:
        print("Error: powershell.exe was not found; cannot read Windows SSH key from WSL.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode('utf-8', errors='replace')
        print(f"Error reading SSH key from Windows path: {windows_path}")
        print(f"Stderr: {stderr}")
        sys.exit(1)

    return result.stdout


def prepare_ssh_key_from_wsl_path(wsl_key_path):
    """Read an SSH key via WSL and copy it to a local temp file."""
    if os.name != 'nt':
        try:
            return prepare_ssh_key(wsl_key_path)
        except OSError as e:
            windows_path = wsl_mount_path_to_windows_path(wsl_key_path)
            if not windows_path:
                raise
            print(f"WSL path is not directly readable ({e}); trying Windows path: {windows_path}")
            key_bytes = read_windows_file_bytes_from_wsl(windows_path)
            if not key_bytes:
                print(f"Error: SSH key at Windows path is empty: {windows_path}")
                sys.exit(1)
            return _write_temp_ssh_key(key_bytes, source_label=windows_path)

    windows_path = wsl_mount_path_to_windows_path(wsl_key_path)
    if windows_path and os.path.exists(windows_path):
        return prepare_ssh_key(windows_path)

    try:
        result = subprocess.run(
            ["wsl.exe", "sh", "-lc", f"cat {shlex.quote(wsl_key_path)}"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print("Error: wsl.exe was not found, but deploy.json specifies wsl_key_path.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        if windows_path:
            print(f"WSL key read failed; trying Windows path: {windows_path}")
            key_bytes = read_windows_file_bytes_from_wsl(windows_path)
            if key_bytes:
                return _write_temp_ssh_key(key_bytes, source_label=windows_path)
        stderr = e.stderr.decode('utf-8', errors='replace')
        print(f"Error reading SSH key from WSL path: {wsl_key_path}")
        print(f"Stderr: {stderr}")
        sys.exit(1)

    if not result.stdout:
        print(f"Error: SSH key at WSL path is empty: {wsl_key_path}")
        sys.exit(1)

    return _write_temp_ssh_key(result.stdout, source_label=wsl_key_path)


def prepare_ssh_key_from_config(config):
    """Resolve and stage the SSH key defined in deploy config."""
    wsl_key_path = str(config.get('wsl_key_path', '')).strip()
    key_path = str(config.get('key_path', '')).strip()

    if wsl_key_path:
        config['key_path'] = prepare_ssh_key_from_wsl_path(wsl_key_path)
        return config['key_path']

    if key_path:
        config['key_path'] = prepare_ssh_key(key_path)
        return config['key_path']

    print("Error: deploy config must contain either key_path or wsl_key_path")
    sys.exit(1)


def cleanup_temp_keys():
    """Remove temporary SSH key files."""
    while _temp_key_paths:
        temp_path = _temp_key_paths.pop()
        if os.path.exists(temp_path):
            os.remove(temp_path)
            print(f"Cleaned up temp SSH key: {temp_path}")

def prepare_runtime_municipality_data():
    """Publishes municipality metadata for the web runtime under data/municipalities."""
    # 自治体マスタは data/municipalities を git 管理された正本として扱う。
    source_dir = os.path.join('data', 'municipalities')
    os.makedirs(source_dir, exist_ok=True)
    available = 0
    for filename in _RUNTIME_MUNICIPALITY_FILES:
        if os.path.exists(os.path.join(source_dir, filename)):
            available += 1
    print(f"Using tracked runtime municipality data: {available} files")

def load_config(config_path):
    with open(config_path, 'r') as f:
        return json.load(f)

def resolve_remote_dest_dir(path):
    remote_dir = str(path).strip()
    if not remote_dir:
        print("Error: dest_dir must not be empty")
        sys.exit(1)
    if remote_dir.startswith('/'):
        return remote_dir
    return f"~/{remote_dir.lstrip('~/')}"

def resolve_remote_shared_data_dir(config):
    shared_data_dir = str(config.get('shared_data_dir', '/mnt/big/miyabe-tools')).strip()
    if not shared_data_dir:
        print("Error: shared_data_dir must not be empty")
        sys.exit(1)
    if not shared_data_dir.startswith('/'):
        print("Error: shared_data_dir must be an absolute path on the remote host")
        sys.exit(1)
    return shared_data_dir

def run_command(cmd, capture_output=True, ignore_error=False):
    """Executes a shell command."""
    print(f"Running: {cmd}")
    try:
        if capture_output:
            result = subprocess.run(cmd, shell=True, check=not ignore_error, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return result.stdout.strip()
        else:
            subprocess.run(cmd, shell=True, check=not ignore_error)
            return None
    except subprocess.CalledProcessError as e:
        if ignore_error:
            return None
        print(f"Error executing command: {cmd}")
        print(f"Stdout: {e.stdout}")
        print(f"Stderr: {e.stderr}")
        sys.exit(1)

def ssh_exec(config, command, *, stream=False):
    """Executes a command on the remote server via SSH."""
    command_bytes = command.replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')
    ssh_args = [
        "ssh",
        "-i",
        config['key_path'],
        "-p",
        str(config.get('port', 22)),
        "-o",
        "StrictHostKeyChecking=no",
        f"{config['user']}@{config['host']}",
        "sh",
        "-s",
    ]
    debug_cmd = " ".join(shlex.quote(part) for part in ssh_args)
    print(f"Running: {debug_cmd}")
    if stream:
        process = subprocess.Popen(
            ssh_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        assert process.stdin is not None
        process.stdin.write(command_bytes)
        process.stdin.close()
        if process.stdout is not None:
            for line in process.stdout:
                print(line.decode('utf-8', errors='replace'), end="")
        returncode = process.wait()
        if returncode != 0:
            print(f"Error executing remote command via SSH: {debug_cmd}")
            print(f"Remote script:\n{command}")
            sys.exit(returncode)
        return ""

    try:
        result = subprocess.run(
            ssh_args,
            input=command_bytes,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout.decode('utf-8', errors='replace').strip()
    except subprocess.CalledProcessError as e:
        print(f"Error executing remote command via SSH: {debug_cmd}")
        print(f"Remote script:\n{command}")
        print(f"Stdout: {e.stdout.decode('utf-8', errors='replace') if isinstance(e.stdout, bytes) else e.stdout}")
        print(f"Stderr: {e.stderr.decode('utf-8', errors='replace') if isinstance(e.stderr, bytes) else e.stderr}")
        sys.exit(1)

def ssh_copy_content(config, content, remote_path):
    """Copies string content to a remote file via SSH."""
    ssh_base = f"ssh -i {config['key_path']} -p {config.get('port', 22)} -o StrictHostKeyChecking=no {config['user']}@{config['host']}"
    full_cmd = f"{ssh_base} \"cat > {remote_path}\""
    
    try:
        process = subprocess.Popen(full_cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate(input=content)
        
        if process.returncode != 0:
            print(f"Error copying content to {remote_path}")
            print(f"Stderr: {stderr}")
            sys.exit(1)
    except Exception as e:
        print(f"Error in ssh_copy_content: {e}")
        sys.exit(1)


def remote_scraping_compose_cmd(dest_dir: str, compose_args: str) -> str:
    """Builds a docker compose command for the scraper stack with its own project name."""
    # Web と scraper が同じ compose project だと、scraper 側の --remove-orphans が web/php を巻き込む。
    return (
        f"cd {dest_dir} && "
        f"docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml {compose_args}"
    )


def remote_scraper_cleanup_cmd(scraper_image_name: str = DEFAULT_SCRAPER_IMAGE_NAME) -> str:
    """Builds a shell snippet that removes stray standalone scraper containers."""
    # 旧 deploy や手動調査で `docker run miyabe-tools-scraper ...` を残すと、
    # その単発コンテナが scrape_state.json を上書きして live progress を壊す。
    # さらに host 側に残った legacy wrapper / 直実行プロセスも random 名 container を増やすので、先に止める。
    return f"""
pkill -f 'tools/remote/run_gijiroku_remote.sh' >/dev/null 2>&1 || true
pkill -f 'tools/remote/run_reiki_remote.sh' >/dev/null 2>&1 || true
pkill -f 'tools/gijiroku/scrape_all_minutes.py' >/dev/null 2>&1 || true
pkill -f 'tools/reiki/scrape_all_reiki.py' >/dev/null 2>&1 || true
docker ps -a --filter ancestor={shlex.quote(scraper_image_name)} --format '{{{{.Names}}}}' | while IFS= read -r name; do
  case "$name" in
    {SCRAPING_COMPOSE_PROJECT}-*) ;;
    miyabe-tools-scraper-gijiroku-1|miyabe-tools-scraper-reiki-1) docker rm -f "$name" >/dev/null 2>&1 || true ;;
    "") ;;
    *) docker rm -f "$name" >/dev/null 2>&1 || true ;;
  esac
done
""".strip()


def remote_user_ids(config: dict) -> tuple[str, str]:
    uid = ssh_exec(config, "id -u").strip()
    gid = ssh_exec(config, "id -g").strip()
    if uid == "" or gid == "":
        raise RuntimeError("Could not determine remote uid/gid.")
    return uid, gid


def ensure_remote_scraping_compose(
    config,
    dest_dir: str,
    shared_data_dir: str,
    scraper_image_name: str = DEFAULT_SCRAPER_IMAGE_NAME,
    *,
    gijiroku_loop_seconds: int = DEFAULT_GIJIROKU_LOOP_SECONDS,
    reiki_loop_seconds: int = DEFAULT_REIKI_LOOP_SECONDS,
    fail_sleep_seconds: int = DEFAULT_SCRAPER_FAIL_SLEEP_SECONDS,
):
    uid, gid = remote_user_ids(config)
    compose_text = build_scraping_compose(
        image_name=scraper_image_name,
        shared_data_dir=shared_data_dir,
        uid=uid,
        gid=gid,
        gijiroku_loop_seconds=gijiroku_loop_seconds,
        reiki_loop_seconds=reiki_loop_seconds,
        fail_sleep_seconds=fail_sleep_seconds,
    )
    ssh_copy_content(config, compose_text + "\n", f"{dest_dir}/docker-compose.scraping.yml")


def remote_scraper_image_stamp_path(dest_dir: str) -> str:
    return f"{dest_dir}/work/celery/scraper-image.sha256"


def remote_file_text(config, remote_path: str) -> str:
    quoted = shlex.quote(remote_path)
    return ssh_exec(config, f"if [ -f {quoted} ]; then cat {quoted}; fi")


def ensure_scraper_image(config, dest_dir: str, image_name: str = DEFAULT_SCRAPER_IMAGE_NAME) -> None:
    expected_hash = scraper_image_source_hash(WORKSPACE_ROOT)
    stamp_path = remote_scraper_image_stamp_path(dest_dir)
    image_present = ssh_exec(
        config,
        f"docker image inspect {image_name} >/dev/null 2>&1 && echo present || echo missing",
    ).strip()
    current_hash = remote_file_text(config, stamp_path).strip()
    if image_present == "present" and current_hash == expected_hash:
        return
    print(f"=== Building scraper image ({image_name}) ===")
    ssh_exec(config, f"cd {dest_dir} && SCRAPER_IMAGE_NAME={image_name} sh ./tools/remote/build_scraper_image.sh")
    ssh_exec(config, f"mkdir -p {dest_dir}/work/celery")
    ssh_copy_content(config, expected_hash + "\n", stamp_path)


def verify_scraping_services_running(config, dest_dir: str) -> str:
    verify_cmd = f"""
set -eu
cd {dest_dir}
running="$(docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml ps --status running --services)"
printf '%s\n' "$running"
echo "$running" | grep -qx 'scraper-redis'
echo "$running" | grep -qx 'scraper-gijiroku'
echo "$running" | grep -qx 'scraper-reiki'
echo "$running" | grep -qx 'scraper-beat'
"""
    return ssh_exec(config, verify_cmd)

def ensure_remote_shared_data_permissions(config, shared_data_dir):
    """Ensures shared non-boards directories support app writes."""
    web_group = str(config.get('web_group', 'www-data')).strip() or 'www-data'
    permission_cmd = f"""
mkdir -p {shared_data_dir}
mkdir -p {shared_data_dir}/reiki {shared_data_dir}/gijiroku
chgrp {web_group} {shared_data_dir}
chgrp {web_group} {shared_data_dir}/reiki {shared_data_dir}/gijiroku
chmod 2775 {shared_data_dir}
chmod 2775 {shared_data_dir}/reiki {shared_data_dir}/gijiroku
if [ -d {shared_data_dir}/reiki ]; then find {shared_data_dir}/reiki -type d -exec chgrp {web_group} {{}} + -exec chmod 2775 {{}} +; fi
if [ -d {shared_data_dir}/gijiroku ]; then find {shared_data_dir}/gijiroku -type d -exec chgrp {web_group} {{}} + -exec chmod 2775 {{}} +; fi
if [ -d {shared_data_dir}/reiki ]; then find {shared_data_dir}/reiki -type f -name '*.sqlite' -exec chgrp {web_group} {{}} + -exec chmod 664 {{}} +; fi
if [ -d {shared_data_dir}/gijiroku ]; then find {shared_data_dir}/gijiroku -type f -name '*.sqlite' -exec chgrp {web_group} {{}} + -exec chmod 664 {{}} +; fi
"""
    ssh_exec(config, permission_cmd)

def ensure_remote_service_data_permissions(config, dest_dir):
    """Ensures service-local boards data and shared user DB remain writable."""
    web_group = str(config.get('web_group', 'www-data')).strip() or 'www-data'
    permission_cmd = f"""
mkdir -p {dest_dir}/data {dest_dir}/data/boards
chgrp {web_group} {dest_dir}/data {dest_dir}/data/boards
chmod 2775 {dest_dir}/data {dest_dir}/data/boards
if [ -d {dest_dir}/data/boards ]; then find {dest_dir}/data/boards -type d -exec chgrp {web_group} {{}} + -exec chmod 2775 {{}} +; fi
if [ -f {dest_dir}/data/users.sqlite ]; then chgrp {web_group} {dest_dir}/data/users.sqlite && chmod 664 {dest_dir}/data/users.sqlite; fi
if [ -f {dest_dir}/data/config.json ]; then chgrp {web_group} {dest_dir}/data/config.json && chmod 664 {dest_dir}/data/config.json; fi
"""
    ssh_exec(config, permission_cmd)

def migrate_remote_data_layout(config, dest_dir, shared_data_dir):
    """Copies existing remote non-boards data to the shared data directory once."""
    print("=== Migrating Existing Remote Data Layout ===")
    migration_cmd = f"""
mkdir -p {dest_dir}/data {dest_dir}/data/boards {shared_data_dir} {shared_data_dir}/reiki {shared_data_dir}/gijiroku
if [ -f {shared_data_dir}/config.json ] && [ ! -f {dest_dir}/data/config.json ]; then cp -a {shared_data_dir}/config.json {dest_dir}/data/config.json; fi
if [ -f {shared_data_dir}/users.sqlite ] && [ ! -f {dest_dir}/data/users.sqlite ]; then cp -a {shared_data_dir}/users.sqlite {dest_dir}/data/users.sqlite; fi
if [ -d {dest_dir}/data/reiki ]; then rsync -a --ignore-existing {dest_dir}/data/reiki/ {shared_data_dir}/reiki/; fi
if [ -d {dest_dir}/data/gijiroku ]; then rsync -a --ignore-existing {dest_dir}/data/gijiroku/ {shared_data_dir}/gijiroku/; fi
"""
    ssh_exec(config, migration_cmd)

def normalize_remote_municipality_storage(config, dest_dir, shared_data_dir):
    """Normalizes remote municipality storage names and rebuilds task snapshots."""
    # 旧 slug の残骸があると一覧表示や task 集計がぶれるので、deploy 時に一度揃える。
    # /workspace/data 配下には boards と、shared volume が重なった reiki/gijiroku の両方が見える。
    print("=== Normalizing Remote Municipality Storage ===")
    normalization_cmd = f"""
set -eu
echo '[deploy] prepare municipality storage'
mkdir -p {dest_dir}/tools {dest_dir}/data/background_tasks {dest_dir}/data/municipalities {shared_data_dir}/municipalities {shared_data_dir}/reiki {shared_data_dir}/gijiroku
echo '[deploy] sync municipality metadata to shared data'
rsync -a {dest_dir}/data/municipalities/ {shared_data_dir}/municipalities/
if [ -f {dest_dir}/docker-compose.scraping.yml ]; then
  echo '[deploy] stop scraper workers for normalization'
  cd {dest_dir}
  docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml stop scraper-gijiroku scraper-reiki scraper-beat >/dev/null 2>&1 || true
  echo '[deploy] run municipality normalization container'
  docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml run --rm -T --no-deps -v {shared_data_dir}/reiki:/workspace/data/reiki --entrypoint sh scraper-gijiroku -lc '
set -eu
python3 /workspace/tools/normalize_municipality_storage.py --workspace-root /workspace --data-root /workspace/data --work-root /workspace/work --background-task-dir /workspace/data/background_tasks
python3 /workspace/tools/backfill_background_tasks.py --fast --workspace-root /workspace --data-root /workspace/data --work-root /workspace/work
'
else
  printf '%s\n' 'SKIP: remote municipality normalization requires docker-compose.scraping.yml'
fi
"""
    output = ssh_exec(config, normalization_cmd, stream=True)
    if output:
        print(output)

def restart_scraping_services_if_present(
    config,
    dest_dir,
    shared_data_dir,
    scraper_image_name=DEFAULT_SCRAPER_IMAGE_NAME,
):
    """Ensures the scraper stack exists and restarts it."""
    # deploy 単体でもスクレイパを確実に再開できるよう、compose と image をここで self-heal する。
    ensure_remote_scraping_compose(config, dest_dir, shared_data_dir, scraper_image_name)
    ensure_scraper_image(config, dest_dir, scraper_image_name)
    restart_cmd = f"""
set -eu
{remote_scraper_cleanup_cmd(scraper_image_name)}
{remote_scraping_compose_cmd(dest_dir, 'up -d --force-recreate --remove-orphans')}
"""
    output = ssh_exec(config, restart_cmd)
    verify_output = verify_scraping_services_running(config, dest_dir)
    if output and verify_output:
        return output + "\n" + verify_output
    return output or verify_output

def prewarm_runtime_caches(config, dest_dir):
    """Builds homepage / cross-search caches inside the php container after deploy."""
    prewarm_cmd = f"""
cd {dest_dir}
docker compose exec -T php php /var/www/lib/prewarm_runtime_caches.php
"""
    return ssh_exec(config, prewarm_cmd)

def sync_single_file(config, ssh_base, local_path, remote_path, dry_run=False, required=True, ignore_existing_remote=False):
    """Syncs a single file to the remote server using rsync."""
    if not os.path.exists(local_path):
        if required:
            print(f"Error: required file not found: {local_path}")
            sys.exit(1)
        print(f"Skipping {local_path} (not found locally)")
        return

    print(f"Syncing {local_path}...")
    dry_flag = " --dry-run" if dry_run else ""
    ignore_flag = " --ignore-existing" if ignore_existing_remote else ""
    rsync_cmd = (
        f"rsync -avz --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r{ignore_flag}{dry_flag} "
        f"-e \"{ssh_base}\" {local_path} {config['user']}@{config['host']}:{remote_path}"
    )
    run_command(rsync_cmd, capture_output=False)

def sync_files(config, dest_dir, shared_data_dir, dry_run=False):
    """Syncs app/runtime files to remote, but leaves scraped shared data in place."""
    print("=== Syncing Code and Config Files ===")
    prepare_runtime_municipality_data()
    
    # Ensure remote directories exist
    ssh_exec(
        config,
        f"mkdir -p {dest_dir}/app {dest_dir}/lib {dest_dir}/src {dest_dir}/nginx {dest_dir}/docker/php {dest_dir}/tools {dest_dir}/data {dest_dir}/data/boards {dest_dir}/data/background_tasks {dest_dir}/data/municipalities {dest_dir}/work/celery {shared_data_dir} {shared_data_dir}/reiki {shared_data_dir}/gijiroku"
    )

    # Use rsync for better handling of large number of files
    # Note: rsync over ssh is more reliable for large directories
    rsync_key_path = str(config['key_path']).replace('\\', '/')
    if os.name == 'nt' and len(rsync_key_path) >= 3 and rsync_key_path[1:3] == ':/':
        rsync_key_path = f"/cygdrive/{rsync_key_path[0].lower()}/{rsync_key_path[3:]}"
    ssh_binary = "/usr/bin/ssh" if os.name == "nt" else "ssh"
    ssh_base = f"{ssh_binary} -i {rsync_key_path} -p {config.get('port', 22)} -o StrictHostKeyChecking=no"
    
    # Sync each directory separately for better error handling and progress tracking.
    # Scraped gijiroku/reiki data lives in shared_data_dir and is populated on the remote host,
    # so deploy must not mirror local development copies into those directories.
    # We still sync tools and municipality metadata so the remote host can normalize
    # existing shared data and rebuild task snapshots against the current slug rules.
    dirs_to_sync = [
        ("app/", f"{dest_dir}/app/"),
        ("lib/", f"{dest_dir}/lib/"),
        ("nginx/", f"{dest_dir}/nginx/"),
        ("docker/", f"{dest_dir}/docker/"),
        ("tools/", f"{dest_dir}/tools/"),
        ("data/municipalities/", f"{dest_dir}/data/municipalities/"),
        ("data/boards/", f"{dest_dir}/data/boards/"),
    ]
    
    # Rsync filters per sync directory.
    # - "protect:<pattern>": protect from --delete on remote
    # - "exclude:<pattern>": never transfer, never delete
    rsync_filters = {
        "data/boards/": [
            "exclude:tasks.sqlite",      # server-created: task progress
            "protect:boards.sqlite",     # prevent --delete; normal transfer/overwrite OK
        ],
    }

    # Sync root data files separately (rsync only handles directories above)
    sync_single_file(config, ssh_base, "data/config.json", f"{dest_dir}/data/config.json", dry_run=dry_run, required=True)
    sync_single_file(config, ssh_base, ".dockerignore", f"{dest_dir}/.dockerignore", dry_run=dry_run, required=False)
    sync_single_file(
        config,
        ssh_base,
        "data/users.sqlite",
        f"{dest_dir}/data/users.sqlite",
        dry_run=dry_run,
        required=False,
        ignore_existing_remote=True,
    )
    for local_path, remote_path in dirs_to_sync:
        print(f"Syncing {local_path}...")
        # -a: archive mode (preserves permissions, times, etc.)
        # -v: verbose
        # -z: compress during transfer
        # --delete: remove files on remote that don't exist locally
        filter_opts = " --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.pyo' --exclude='*.tmp'"
        for rule in rsync_filters.get(local_path, []):
            kind, pattern = rule.split(":", 1)
            if kind == "protect":
                # Protect from --delete, but still transfer if exists locally
                filter_opts += f" --filter='P {pattern}'"
            elif kind == "exclude":
                # Never transfer, never delete (dev-only files)
                filter_opts += f" --exclude='{pattern}'"
        dry_flag = " --dry-run" if dry_run else ""
        rsync_cmd = (
            f"rsync -avz --delete --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r{dry_flag}{filter_opts} "
            f"-e \"{ssh_base}\" {local_path} {config['user']}@{config['host']}:{remote_path}"
        )
        run_command(rsync_cmd, capture_output=False)
    
    print("Sync complete.")


def cleanup_legacy_search_artifacts(config, dest_dir, shared_data_dir):
    quoted_dest = shlex.quote(dest_dir)
    quoted_shared_gijiroku = shlex.quote(f"{shared_data_dir}/gijiroku")
    quoted_shared_reiki = shlex.quote(f"{shared_data_dir}/reiki")
    script = f"""
set -eu
dest_dir={quoted_dest}
shared_gijiroku={quoted_shared_gijiroku}
shared_reiki={quoted_shared_reiki}
case "$dest_dir" in
  "~") dest_dir="$HOME" ;;
  "~/"*) dest_dir="$HOME/${{dest_dir#~/}}" ;;
esac
rm -rf "$dest_dir/src"
rm -f "$dest_dir"/data/gijiroku/*/minutes.sqlite*
rm -f "$dest_dir"/data/reiki/*/ordinances.sqlite*
rm -f "$dest_dir"/work/gijiroku/*/minutes.sqlite*
rm -f "$dest_dir"/work/reiki/*/ordinances.sqlite*
rm -f "$shared_gijiroku"/*/minutes.sqlite*
rm -f "$shared_reiki"/*/ordinances.sqlite*
"""
    ssh_exec(config, script)

def main():
    parser = argparse.ArgumentParser(description='Deploy script.')
    parser.add_argument('config_file', help='Path to configuration JSON file')
    parser.add_argument('--full', action='store_true', help='Perform full deployment including Docker build and push. Default is code-only sync.')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be synced without actually transferring files.')
    parser.add_argument(
        '--skip-normalize',
        action='store_true',
        help='Skip remote municipality storage normalization; useful for code-only scraper restarts.',
    )
    parser.add_argument(
        '--skip-data-maintenance',
        action='store_true',
        help='Skip remote shared-data permission and migration passes for fast code-only deploys.',
    )
    
    args = parser.parse_args()

    config = load_config(args.config_file)
    
    # Prepare SSH key (copy to temp with restrictive permissions).
    prepare_ssh_key_from_config(config)
    
    registry = config['registry_domain']
    # Image names
    img_web = f"{registry}/miyabe-tools-web:latest"
    img_php = f"{registry}/miyabe-tools-php:latest"

    dest_dir = resolve_remote_dest_dir(config['dest_dir'])
    shared_data_dir = resolve_remote_shared_data_dir(config)

    if args.full:
        print("=== 1. Docker Login ===")
        login_cmd = f"echo {config['registry_pass']} | docker login {registry} -u {config['registry_user']} --password-stdin"
        run_command(login_cmd)

        print("=== 2. Build & Push Images ===")
        run_command(f"docker build -t {img_web} -f docker/nginx/Dockerfile .", capture_output=False)
        run_command(f"docker build -t {img_php} -f docker/php/Dockerfile .", capture_output=False)
        
        run_command(f"docker push {img_web}", capture_output=False)
        run_command(f"docker push {img_php}", capture_output=False)

    print("=== 3. Prepare Remote Environment ===")
    ssh_exec(config, f"mkdir -p {dest_dir}/data {dest_dir}/data/boards {shared_data_dir}")
    if args.dry_run:
        print("Skipping remote data migration in dry-run mode.")
    elif args.skip_data_maintenance:
        print("=== Skipping Remote Shared-Data Maintenance ===")
    else:
        ensure_remote_shared_data_permissions(config, shared_data_dir)
        ensure_remote_service_data_permissions(config, dest_dir)
        migrate_remote_data_layout(config, dest_dir, shared_data_dir)
    
    # Always sync code now, to support volume mounts
    sync_files(config, dest_dir, shared_data_dir, dry_run=args.dry_run)
    if args.dry_run:
        print("=== Dry-run complete; skipping docker-compose update and service restart ===")
        return

    cleanup_legacy_search_artifacts(config, dest_dir, shared_data_dir)

    if args.skip_normalize:
        print("=== Skipping Remote Municipality Storage Normalization ===")
    else:
        normalize_remote_municipality_storage(config, dest_dir, shared_data_dir)
    if args.skip_data_maintenance:
        print("=== Skipping Remote Permission Refresh ===")
    else:
        ensure_remote_shared_data_permissions(config, shared_data_dir)
        ensure_remote_service_data_permissions(config, dest_dir)

    # Generate docker-compose.prod.yml
    # Keep the service's data directory mounted as before,
    # then overlay only large non-boards datasets from external storage.
    docker_compose_prod = f"""version: '3'
services:
  web:
    image: {img_web}
    restart: "no"
    ports:
      - "{config.get('app_port', 8301)}:80"
    volumes:
      - ./data:/var/www/data
      - {shared_data_dir}/reiki:/var/www/data/reiki
      - {shared_data_dir}/gijiroku:/var/www/data/gijiroku
      - ./app:/var/www/html
      - ./lib:/var/www/lib
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf
    depends_on:
      - php

  php:
    image: {img_php}
    restart: "no"
    environment:
      OPENSEARCH_URL: ${{OPENSEARCH_URL:-http://opensearch:9200}}
      OPENSEARCH_USER: ${{OPENSEARCH_USER:-}}
      OPENSEARCH_PASSWORD: ${{OPENSEARCH_PASSWORD:-}}
      OPENSEARCH_INSECURE_DEV: ${{OPENSEARCH_INSECURE_DEV:-true}}
      MIYABE_SEARCH_ALIAS: ${{MIYABE_SEARCH_ALIAS:-miyabe-documents-current}}
      MIYABE_MINUTES_ALIAS: ${{MIYABE_MINUTES_ALIAS:-miyabe-minutes-current}}
      MIYABE_REIKI_ALIAS: ${{MIYABE_REIKI_ALIAS:-miyabe-reiki-current}}
    volumes:
      - ./data:/var/www/data
      - {shared_data_dir}/reiki:/var/www/data/reiki
      - {shared_data_dir}/gijiroku:/var/www/data/gijiroku
      - ./app:/var/www/html
      - ./lib:/var/www/lib
      - ./docker/php/zz-www-overrides.conf:/usr/local/etc/php-fpm.d/zz-www-overrides.conf:ro
    depends_on:
      opensearch:
        condition: service_healthy

  opensearch:
    image: opensearchproject/opensearch:2.15.0
    restart: "no"
    environment:
      discovery.type: single-node
      DISABLE_SECURITY_PLUGIN: "true"
      OPENSEARCH_JAVA_OPTS: "-Xms512m -Xmx512m"
    ports:
      - "{config.get('opensearch_port', 9200)}:9200"
    volumes:
      - opensearch-data:/usr/share/opensearch/data
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:9200/_cluster/health >/dev/null || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 30

volumes:
  opensearch-data:
"""
    
    print("=== 4. Deploy to Remote ===")
    ssh_copy_content(config, docker_compose_prod, f"{dest_dir}/docker-compose.yml")
    
    if args.full:
        # Remote login and pull only if we pushed new images
        remote_login = f"echo {config['registry_pass']} | docker login {registry} -u {config['registry_user']} --password-stdin"
        ssh_exec(config, remote_login)
        ssh_exec(config, f"cd {dest_dir} && docker compose pull && docker compose up -d")
    else:
        print("=== Restarting services to pick up code changes ===")
        # Explicit restart to ensure PHP/Nginx reload
        ssh_exec(config, f"cd {dest_dir} && docker compose up -d && docker compose restart")

    print("=== Prewarming runtime caches ===")
    prewarm_output = prewarm_runtime_caches(config, dest_dir)
    if prewarm_output:
        print(prewarm_output)

    print("=== Restarting scraping services if configured ===")
    restart_output = restart_scraping_services_if_present(config, dest_dir, shared_data_dir)
    if restart_output:
        print(restart_output)

    print("=== Deployment Complete ===")

if __name__ == "__main__":
    main()

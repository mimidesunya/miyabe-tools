import json
import os
import subprocess
import sys
import argparse
import time
import tempfile
import shutil
import atexit
import shlex

# Web アプリ本体の deploy 手順をまとめたスクリプト。
# 共有データは remote 側に残しつつ、コードと設定だけを安全に更新する。

# Store temp key path for cleanup
_temp_key_path = None
_RUNTIME_MUNICIPALITY_FILES = (
    'municipality_master.tsv',
    'assembly_minutes_system_urls.tsv',
    'reiki_system_urls.tsv',
    'municipality_homepages.csv',
)
SCRAPING_COMPOSE_PROJECT = "miyabe-tools-scraping"
DEFAULT_SCRAPER_IMAGE_NAME = "miyabe-tools-scraper"
DEFAULT_GIJIROKU_LOOP_SECONDS = 21600
DEFAULT_REIKI_LOOP_SECONDS = 21600
DEFAULT_SCRAPER_FAIL_SLEEP_SECONDS = 900

def prepare_ssh_key(key_path):
    """Copy SSH key to a temp file with correct permissions for WSL."""
    global _temp_key_path
    
    # Create a temp file
    fd, temp_path = tempfile.mkstemp(prefix='ssh_key_')
    os.close(fd)
    
    # Copy the key content
    shutil.copy2(key_path, temp_path)
    
    # Set correct permissions (600)
    os.chmod(temp_path, 0o600)
    
    _temp_key_path = temp_path
    
    # Register cleanup
    atexit.register(cleanup_temp_key)
    
    print(f"SSH key prepared at: {temp_path}")
    return temp_path

def cleanup_temp_key():
    """Remove the temporary SSH key file."""
    global _temp_key_path
    if _temp_key_path and os.path.exists(_temp_key_path):
        os.remove(_temp_key_path)
        print(f"Cleaned up temp SSH key: {_temp_key_path}")

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

def ssh_exec(config, command):
    """Executes a command on the remote server via SSH."""
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
    try:
        result = subprocess.run(
            ssh_args,
            input=command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error executing remote command via SSH: {debug_cmd}")
        print(f"Remote script:\n{command}")
        print(f"Stdout: {e.stdout}")
        print(f"Stderr: {e.stderr}")
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
    # さらに host 側の旧 run_*_remote.sh も random 名 container を増やすので、先に止める。
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


def yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    # compose の command/env は数値風の文字列も string として解釈させたい。
    if isinstance(value, str):
        escaped = text.replace("\\", "\\\\").replace("\"", "\\\"")
        return f"\"{escaped}\""
    if text == "" or any(ch in text for ch in ":#[]{}&,>*!|%@`'\" \t"):
        escaped = text.replace("\\", "\\\\").replace("\"", "\\\"")
        return f"\"{escaped}\""
    return text


def yaml_dump(value: object, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(yaml_dump(item, indent + 2))
            else:
                lines.append(f"{pad}{key}: {yaml_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(yaml_dump(item, indent + 2))
            else:
                lines.append(f"{pad}- {yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{yaml_scalar(value)}"


def build_scraping_compose(
    *,
    image_name: str,
    shared_data_dir: str,
    uid: str,
    gid: str,
    gijiroku_loop_seconds: int,
    reiki_loop_seconds: int,
    fail_sleep_seconds: int,
) -> str:
    compose = {
        "name": SCRAPING_COMPOSE_PROJECT,
        "services": {
            "scraper-gijiroku": {
                "image": image_name,
                "restart": "unless-stopped",
                "init": True,
                "user": f"{uid}:{gid}",
                "working_dir": "/workspace",
                "environment": {
                    "HOME": "/tmp",
                    "PYTHONUNBUFFERED": "1",
                    "SCRAPER_LOOP_SECONDS": str(gijiroku_loop_seconds),
                    "SCRAPER_FAIL_SLEEP_SECONDS": str(fail_sleep_seconds),
                },
                "volumes": [
                    ".:/workspace",
                    f"{shared_data_dir}/gijiroku:/workspace/data/gijiroku",
                ],
                "command": [
                    "sh",
                    "./tools/remote/run_gijiroku_service.sh",
                    "--ack-robots",
                    "--parallel",
                    "8",
                    "--per-host-parallel",
                    "1",
                    "--per-host-start-interval",
                    "2",
                ],
            },
            "scraper-reiki": {
                "image": image_name,
                "restart": "unless-stopped",
                "init": True,
                "user": f"{uid}:{gid}",
                "working_dir": "/workspace",
                "environment": {
                    "HOME": "/tmp",
                    "PYTHONUNBUFFERED": "1",
                    "SCRAPER_LOOP_SECONDS": str(reiki_loop_seconds),
                    "SCRAPER_FAIL_SLEEP_SECONDS": str(fail_sleep_seconds),
                },
                "volumes": [
                    ".:/workspace",
                    f"{shared_data_dir}/reiki:/workspace/data/reiki",
                ],
                "command": [
                    "sh",
                    "./tools/remote/run_reiki_service.sh",
                    "--parallel",
                    "8",
                    "--per-host-parallel",
                    "1",
                    "--per-host-start-interval",
                    "2",
                    "--check-updates",
                ],
            },
        }
    }
    return yaml_dump(compose)


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
    compose_present = ssh_exec(
        config,
        f"[ -f {dest_dir}/docker-compose.scraping.yml ] && echo present || echo missing",
    ).strip()
    if compose_present == "present":
        return

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


def ensure_scraper_image(config, dest_dir: str, image_name: str = DEFAULT_SCRAPER_IMAGE_NAME) -> None:
    image_present = ssh_exec(
        config,
        f"docker image inspect {image_name} >/dev/null 2>&1 && echo present || echo missing",
    ).strip()
    if image_present == "present":
        return
    print(f"=== Building scraper image ({image_name}) ===")
    ssh_exec(config, f"cd {dest_dir} && SCRAPER_IMAGE_NAME={image_name} sh ./tools/remote/build_scraper_image.sh")


def verify_scraping_services_running(config, dest_dir: str) -> str:
    verify_cmd = f"""
set -eu
cd {dest_dir}
running="$(docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml ps --status running --services)"
printf '%s\n' "$running"
echo "$running" | grep -qx 'scraper-gijiroku'
echo "$running" | grep -qx 'scraper-reiki'
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
mkdir -p {dest_dir}/tools {dest_dir}/data/background_tasks {dest_dir}/data/municipalities {shared_data_dir}/municipalities {shared_data_dir}/reiki {shared_data_dir}/gijiroku
rsync -a {dest_dir}/data/municipalities/ {shared_data_dir}/municipalities/
if [ -f {dest_dir}/docker-compose.scraping.yml ]; then
  cd {dest_dir}
  docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml stop scraper-gijiroku scraper-reiki >/dev/null 2>&1 || true
  docker compose -p {SCRAPING_COMPOSE_PROJECT} -f docker-compose.scraping.yml run --rm -T -v {shared_data_dir}/reiki:/workspace/data/reiki --entrypoint sh scraper-gijiroku -lc '
set -eu
python3 /workspace/tools/normalize_municipality_storage.py --workspace-root /workspace --data-root /workspace/data --work-root /workspace/work --background-task-dir /workspace/data/background_tasks
python3 /workspace/tools/backfill_background_tasks.py --workspace-root /workspace --data-root /workspace/data --work-root /workspace/work
'
else
  printf '%s\n' 'SKIP: remote municipality normalization requires docker-compose.scraping.yml'
fi
"""
    output = ssh_exec(config, normalization_cmd)
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
    rsync_cmd = f"rsync -avz{ignore_flag}{dry_flag} -e '{ssh_base}' {local_path} {config['user']}@{config['host']}:{remote_path}"
    run_command(rsync_cmd, capture_output=False)

def sync_files(config, dest_dir, shared_data_dir, dry_run=False):
    """Syncs app/runtime files to remote, but leaves scraped shared data in place."""
    print("=== Syncing Code and Config Files ===")
    prepare_runtime_municipality_data()
    
    # Ensure remote directories exist
    ssh_exec(
        config,
        f"mkdir -p {dest_dir}/app {dest_dir}/lib {dest_dir}/src {dest_dir}/nginx {dest_dir}/docker/php {dest_dir}/tools {dest_dir}/data {dest_dir}/data/boards {dest_dir}/data/background_tasks {dest_dir}/data/municipalities {shared_data_dir} {shared_data_dir}/reiki {shared_data_dir}/gijiroku"
    )

    # Use rsync for better handling of large number of files
    # Note: rsync over ssh is more reliable for large directories
    ssh_base = f"ssh -i {config['key_path']} -p {config.get('port', 22)} -o StrictHostKeyChecking=no"
    
    # Sync each directory separately for better error handling and progress tracking.
    # Scraped gijiroku/reiki data lives in shared_data_dir and is populated on the remote host,
    # so deploy must not mirror local development copies into those directories.
    # We still sync tools and municipality metadata so the remote host can normalize
    # existing shared data and rebuild task snapshots against the current slug rules.
    dirs_to_sync = [
        ("app/", f"{dest_dir}/app/"),
        ("lib/", f"{dest_dir}/lib/"),
        ("src/", f"{dest_dir}/src/"),
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
    sync_single_file(
        config,
        ssh_base,
        "data/users.sqlite",
        f"{dest_dir}/data/users.sqlite",
        dry_run=dry_run,
        required=False,
        ignore_existing_remote=True,
    )
    sync_single_file(
        config,
        ssh_base,
        "data/background_tasks/gijiroku.json",
        f"{dest_dir}/data/background_tasks/gijiroku.json",
        dry_run=dry_run,
        required=False,
    )
    sync_single_file(
        config,
        ssh_base,
        "data/background_tasks/reiki.json",
        f"{dest_dir}/data/background_tasks/reiki.json",
        dry_run=dry_run,
        required=False,
    )
    sync_single_file(
        config,
        ssh_base,
        "data/background_tasks/gijiroku_snapshot.json",
        f"{dest_dir}/data/background_tasks/gijiroku_snapshot.json",
        dry_run=dry_run,
        required=False,
    )
    sync_single_file(
        config,
        ssh_base,
        "data/background_tasks/reiki_snapshot.json",
        f"{dest_dir}/data/background_tasks/reiki_snapshot.json",
        dry_run=dry_run,
        required=False,
    )

    for local_path, remote_path in dirs_to_sync:
        print(f"Syncing {local_path}...")
        # -a: archive mode (preserves permissions, times, etc.)
        # -v: verbose
        # -z: compress during transfer
        # --delete: remove files on remote that don't exist locally
        filter_opts = ""
        for rule in rsync_filters.get(local_path, []):
            kind, pattern = rule.split(":", 1)
            if kind == "protect":
                # Protect from --delete, but still transfer if exists locally
                filter_opts += f" --filter='P {pattern}'"
            elif kind == "exclude":
                # Never transfer, never delete (dev-only files)
                filter_opts += f" --exclude='{pattern}'"
        dry_flag = " --dry-run" if dry_run else ""
        rsync_cmd = f"rsync -avz --delete{dry_flag}{filter_opts} -e '{ssh_base}' {local_path} {config['user']}@{config['host']}:{remote_path}"
        run_command(rsync_cmd, capture_output=False)
    
    print("Sync complete.")

def main():
    parser = argparse.ArgumentParser(description='Deploy script.')
    parser.add_argument('config_file', help='Path to configuration JSON file')
    parser.add_argument('--full', action='store_true', help='Perform full deployment including Docker build and push. Default is code-only sync.')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be synced without actually transferring files.')
    
    args = parser.parse_args()

    config = load_config(args.config_file)
    
    # Prepare SSH key (copy to temp with correct permissions for WSL)
    original_key_path = config['key_path']
    config['key_path'] = prepare_ssh_key(original_key_path)
    
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
    else:
        ensure_remote_shared_data_permissions(config, shared_data_dir)
        ensure_remote_service_data_permissions(config, dest_dir)
        migrate_remote_data_layout(config, dest_dir, shared_data_dir)
    
    # Always sync code now, to support volume mounts
    sync_files(config, dest_dir, shared_data_dir, dry_run=args.dry_run)
    if args.dry_run:
        print("=== Dry-run complete; skipping docker-compose update and service restart ===")
        return

    normalize_remote_municipality_storage(config, dest_dir, shared_data_dir)
    ensure_remote_shared_data_permissions(config, shared_data_dir)
    ensure_remote_service_data_permissions(config, dest_dir)

    # Generate docker-compose.prod.yml
    # Keep the service's data directory mounted as before,
    # then overlay only large non-boards datasets from external storage.
    docker_compose_prod = f"""version: '3'
services:
  web:
    image: {img_web}
    restart: always
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
    restart: always
    volumes:
      - ./data:/var/www/data
      - {shared_data_dir}/reiki:/var/www/data/reiki
      - {shared_data_dir}/gijiroku:/var/www/data/gijiroku
      - ./app:/var/www/html
      - ./lib:/var/www/lib
      - ./src:/var/www/src
      - ./docker/php/zz-www-overrides.conf:/usr/local/etc/php-fpm.d/zz-www-overrides.conf:ro
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

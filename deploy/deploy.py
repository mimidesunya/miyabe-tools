import json
import os
import subprocess
import sys
import argparse
import time
import tempfile
import shutil
import atexit

# Store temp key path for cleanup
_temp_key_path = None

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
    ssh_cmd = f"ssh -i {config['key_path']} -p {config.get('port', 22)} -o StrictHostKeyChecking=no {config['user']}@{config['host']} \"{command}\""
    return run_command(ssh_cmd)

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
    """Syncs app, lib, nginx, and data directories to remote."""
    print("=== Syncing Code and Config Files ===")
    
    # Ensure remote directories exist
    ssh_exec(
        config,
        f"mkdir -p {dest_dir}/app {dest_dir}/lib {dest_dir}/nginx {dest_dir}/data {dest_dir}/data/boards {shared_data_dir} {shared_data_dir}/reiki {shared_data_dir}/gijiroku"
    )

    # Use rsync for better handling of large number of files
    # Note: rsync over ssh is more reliable for large directories
    ssh_base = f"ssh -i {config['key_path']} -p {config.get('port', 22)} -o StrictHostKeyChecking=no"
    
    # Sync each directory separately for better error handling and progress tracking
    dirs_to_sync = [
        ("app/", f"{dest_dir}/app/"),
        ("lib/", f"{dest_dir}/lib/"),
        ("nginx/", f"{dest_dir}/nginx/"),
        ("data/reiki/", f"{shared_data_dir}/reiki/"),
        ("data/gijiroku/", f"{shared_data_dir}/gijiroku/"),
        ("data/boards/", f"{dest_dir}/data/boards/"),
    ]
    
    # Rsync filters per sync directory.
    # - "protect:<pattern>": protect from --delete on remote
    # - "exclude:<pattern>": never transfer, never delete
    rsync_filters = {
        "data/reiki/": [
            "protect:feedback.sqlite",   # server-created: user votes
        ],
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

    print("=== Deployment Complete ===")

if __name__ == "__main__":
    main()

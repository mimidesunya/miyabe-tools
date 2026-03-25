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

def sync_files(config, dest_dir, dry_run=False):
    """Syncs app, lib, nginx, and data directories to remote."""
    print("=== Syncing Code and Config Files ===")
    
    # Ensure remote directories exist
    ssh_exec(config, f"mkdir -p {dest_dir}/app {dest_dir}/lib {dest_dir}/nginx {dest_dir}/data/reiki {dest_dir}/data/gijiroku {dest_dir}/data/boards")

    # Use rsync for better handling of large number of files
    # Note: rsync over ssh is more reliable for large directories
    ssh_base = f"ssh -i {config['key_path']} -p {config.get('port', 22)} -o StrictHostKeyChecking=no"
    
    # Sync each directory separately for better error handling and progress tracking
    dirs_to_sync = [
        ("app/", f"{dest_dir}/app/"),
        ("lib/", f"{dest_dir}/lib/"),
        ("nginx/", f"{dest_dir}/nginx/"),
        ("data/reiki/", f"{dest_dir}/data/reiki/"),
        ("data/gijiroku/", f"{dest_dir}/data/gijiroku/"),
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

    # Sync data/config.json separately (rsync only handles directories above)
    print("Syncing data/config.json...")
    ssh_base_scp = f"scp -i {config['key_path']} -P {config.get('port', 22)} -o StrictHostKeyChecking=no"
    run_command(f"{ssh_base_scp} data/config.json {config['user']}@{config['host']}:{dest_dir}/data/config.json", capture_output=False)

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

    dest_dir = config['dest_dir']
    if not dest_dir.startswith('/'):
        dest_dir = f"~/{dest_dir}"

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
    ssh_exec(config, f"mkdir -p {dest_dir}/data")
    
    # Always sync code now, to support volume mounts
    sync_files(config, dest_dir, dry_run=args.dry_run)

    # Generate docker-compose.prod.yml
    # We mount app and lib to allow code updates without image rebuild
    docker_compose_prod = f"""version: '3'
services:
  web:
    image: {img_web}
    restart: always
    ports:
      - "{config.get('app_port', 8301)}:80"
    volumes:
      - ./data:/var/www/data
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

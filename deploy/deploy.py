import json
import os
import subprocess
import sys
import time

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

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 deploy/deploy.py <config_file>")
        sys.exit(1)

    config_path = sys.argv[1]
    config = load_config(config_path)
    
    registry = config['registry_domain']
    # Image names
    img_web = f"{registry}/miyabe-tools-web:latest"
    img_php = f"{registry}/miyabe-tools-php:latest"

    print("=== 1. Docker Login ===")
    # Note: Login might be needed on local if pushing provided we have access.
    # Assuming user is logged in or we can pass password.
    # For CI-like scripts, echo password to stdin is safer.
    login_cmd = f"echo {config['registry_pass']} | docker login {registry} -u {config['registry_user']} --password-stdin"
    run_command(login_cmd)

    print("=== 2. Build & Push Images ===")
    run_command(f"docker build -t {img_web} -f docker/nginx/Dockerfile .", capture_output=False)
    run_command(f"docker build -t {img_php} -f docker/php/Dockerfile .", capture_output=False)
    
    run_command(f"docker push {img_web}", capture_output=False)
    run_command(f"docker push {img_php}", capture_output=False)

    print("=== 3. Prepare Remote Environment ===")
    dest_dir = config['dest_dir']
    if not dest_dir.startswith('/'):
        dest_dir = f"~/{dest_dir}"
    
    ssh_exec(config, f"mkdir -p {dest_dir}/data")

    # Generate docker-compose.prod.yml
    # We remove build contexts and volumes that mount code, keeping data volumes.
    docker_compose_prod = f"""version: '3'
services:
  web:
    image: {img_web}
    ports:
      - "{config.get('app_port', 8301)}:80"
    volumes:
      - ./data:/var/www/data
    depends_on:
      - php

  php:
    image: {img_php}
    volumes:
      - ./data:/var/www/data
"""
    
    print("=== 4. Deploy to Remote ===")
    ssh_copy_content(config, docker_compose_prod, f"{dest_dir}/docker-compose.yml")
    
    # We need to ensure the remote server can pull from the registry.
    # We'll run docker login on the remote too.
    remote_login = f"echo {config['registry_pass']} | docker login {registry} -u {config['registry_user']} --password-stdin"
    ssh_exec(config, remote_login)
    
    # Pull and Up
    ssh_exec(config, f"cd {dest_dir} && docker compose pull && docker compose up -d")

    print("=== Deployment Complete ===")

if __name__ == "__main__":
    main()

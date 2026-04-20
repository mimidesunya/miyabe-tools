#!/usr/bin/env python3
"""Helper script to check files on remote server."""
import sys
sys.path.insert(0, 'deploy')
from deploy import (
    load_config,
    prepare_ssh_key_from_config,
    resolve_remote_dest_dir,
    resolve_remote_shared_data_dir,
    ssh_exec,
)

config = load_config('deploy.json')
prepare_ssh_key_from_config(config)
compose_dir = resolve_remote_dest_dir(config['dest_dir'])
shared_data_dir = resolve_remote_shared_data_dir(config)
host_image_dir = f'{shared_data_dir}/reiki/14130-kawasaki-shi/images'
container_image_dir = '/var/www/data/reiki/14130-kawasaki-shi/images'
host_source_html = f'{shared_data_dir}/reiki/14130-kawasaki-shi/html/H309999991001A_j.html'
public_image_url = 'http://localhost:8301/data/reiki/14130-kawasaki-shi/images/S-20100304-011Z0001.gif'
public_source_html_url = 'http://localhost:8301/data/reiki/14130-kawasaki-shi/html/H309999991001A_j.html'

# Check if image file exists
print("=== Checking image file on server ===")
result = ssh_exec(config, f'ls -lh {host_image_dir}/S-20100304-011Z0001.gif 2>&1')
print(result)

print("\n=== Checking image directory ===")
result = ssh_exec(config, f'ls -lh {host_image_dir}/ | head -20')
print(result)

print("\n=== Checking if directory exists ===")
result = ssh_exec(config, f'test -d {host_image_dir} && echo "Directory exists" || echo "Directory NOT found"')
print(result)

print("\n=== Count images in directory ===")
result = ssh_exec(config, f'ls {host_image_dir}/*.gif 2>/dev/null | wc -l')
print(f"Image count: {result}")

print("\n=== Check Docker container mount ===")
result = ssh_exec(config, f'cd {compose_dir} && docker compose exec web ls -lh {container_image_dir}/S-20100304-011Z0001.gif 2>&1')
print(result)

print("\n=== Count images in Docker container ===")
result = ssh_exec(config, f'cd {compose_dir} && docker compose exec web sh -c "ls {container_image_dir}/*.gif 2>/dev/null | wc -l"')
print(f"Images in container: {result}")

print("\n=== Restart Docker containers ===")
result = ssh_exec(config, f'cd {compose_dir} && docker compose down && docker compose up -d')
print(result)

print("\n=== Re-check images in Docker container ===")
result = ssh_exec(config, f'cd {compose_dir} && docker compose exec web sh -c "ls {container_image_dir}/*.gif 2>/dev/null | wc -l"')
print(f"Images in container after restart: {result}")

print("\n=== Check boards data directory on host ===")
result = ssh_exec(config, f'ls -la {compose_dir}/data/ 2>&1')
print(result)

print("\n=== Check shared data directory on host ===")
result = ssh_exec(config, f'ls -la {shared_data_dir}/ 2>&1 | head -20')
print(result)

print("\n=== Check docker-compose.yml volumes ===")
result = ssh_exec(config, f'cd {compose_dir} && grep -A 6 "volumes:" docker-compose.yml')
print(result)

print("\n=== Count files in container with find ===")
result = ssh_exec(config, f'cd {compose_dir} && docker compose exec web find {container_image_dir} -name "*.gif" | wc -l')
print(f"Images found with find: {result}")

print("\n=== Test direct HTTP access to image ===")
result = ssh_exec(config, f'curl -I {public_image_url} 2>&1 | head -5')
print(result)

print("\n=== Check nginx error logs ===")
result = ssh_exec(config, f'cd {compose_dir} && docker compose logs web 2>&1 | grep -i error | tail -10')
print(result)

print("\n=== Test file access inside container ===")
result = ssh_exec(config, f'cd {compose_dir} && docker compose exec web ls -lh {container_image_dir}/S-20100304-011Z0001.gif')
print(result)

print("\n=== Test nginx config ===")
result = ssh_exec(config, f'cd {compose_dir} && docker compose exec web nginx -t 2>&1')
print(result)

print("\n=== Check actual request in nginx ===")
result = ssh_exec(config, f'curl {public_image_url} -o /tmp/test.gif 2>&1 && file /tmp/test.gif')
print(result)

print("\n=== Test cat file inside nginx container ===")
result = ssh_exec(config, f'cd {compose_dir} && docker compose exec web cat {container_image_dir}/S-20100304-011Z0001.gif | wc -c')
print(f"File size in container: {result} bytes")

print("\n=== Check nginx location blocks ===")
result = ssh_exec(config, f'cd {compose_dir} && docker compose exec web cat /etc/nginx/conf.d/default.conf | grep -A 3 "location /data"')
print(result)

print("\n=== Test with different path ===")
result = ssh_exec(config, f'ls -lh {host_source_html} 2>&1 && curl -I {public_source_html_url} 2>&1 | grep HTTP')
print(result)

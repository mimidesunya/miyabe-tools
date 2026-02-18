#!/usr/bin/env python3
"""Helper script to check files on remote server."""
import sys
sys.path.insert(0, 'deploy')
from deploy import load_config, prepare_ssh_key, ssh_exec

config = load_config('deploy.json')
config['key_path'] = prepare_ssh_key(config['key_path'])

# Check if image file exists
print("=== Checking image file on server ===")
result = ssh_exec(config, 'ls -lh ~/services/miyabe-tools/data/reiki/kawasaki_images/S-20100304-011Z0001.gif 2>&1')
print(result)

print("\n=== Checking image directory ===")
result = ssh_exec(config, 'ls -lh ~/services/miyabe-tools/data/reiki/kawasaki_images/ | head -20')
print(result)

print("\n=== Checking if directory exists ===")
result = ssh_exec(config, 'test -d ~/services/miyabe-tools/data/reiki/kawasaki_images && echo "Directory exists" || echo "Directory NOT found"')
print(result)

print("\n=== Count images in directory ===")
result = ssh_exec(config, 'ls ~/services/miyabe-tools/data/reiki/kawasaki_images/*.gif 2>/dev/null | wc -l')
print(f"Image count: {result}")

print("\n=== Check Docker container mount ===")
result = ssh_exec(config, 'cd ~/services/miyabe-tools && docker compose exec web ls -lh /var/www/data/reiki/kawasaki_images/S-20100304-011Z0001.gif 2>&1')
print(result)

print("\n=== Count images in Docker container ===")
result = ssh_exec(config, 'cd ~/services/miyabe-tools && docker compose exec web sh -c "ls /var/www/data/reiki/kawasaki_images/*.gif 2>/dev/null | wc -l"')
print(f"Images in container: {result}")

print("\n=== Restart Docker containers ===")
result = ssh_exec(config, 'cd ~/services/miyabe-tools && docker compose down && docker compose up -d')
print(result)

print("\n=== Re-check images in Docker container ===")
result = ssh_exec(config, 'cd ~/services/miyabe-tools && docker compose exec web sh -c "ls /var/www/data/reiki/kawasaki_images/*.gif 2>/dev/null | wc -l"')
print(f"Images in container after restart: {result}")

print("\n=== Check host directory structure ===")
result = ssh_exec(config, 'ls -la ~/services/miyabe-tools/data/ 2>&1')
print(result)

print("\n=== Check if data/reiki exists on host ===")
result = ssh_exec(config, 'ls -la ~/services/miyabe-tools/data/reiki/ 2>&1 | head -20')
print(result)

print("\n=== Check docker-compose.yml volumes ===")
result = ssh_exec(config, 'cd ~/services/miyabe-tools && grep -A 5 "volumes:" docker-compose.yml')
print(result)

print("\n=== Count files in container with find ===")
result = ssh_exec(config, 'cd ~/services/miyabe-tools && docker compose exec web find /var/www/data/reiki/kawasaki_images -name "*.gif" | wc -l')
print(f"Images found with find: {result}")

print("\n=== Test direct HTTP access to image ===")
result = ssh_exec(config, 'curl -I http://localhost:8301/data/reiki/kawasaki_images/S-20100304-011Z0001.gif 2>&1 | head -5')
print(result)

print("\n=== Check nginx error logs ===")
result = ssh_exec(config, 'cd ~/services/miyabe-tools && docker compose logs web 2>&1 | grep -i error | tail -10')
print(result)

print("\n=== Test file access inside container ===")
result = ssh_exec(config, 'cd ~/services/miyabe-tools && docker compose exec web ls -lh /var/www/data/reiki/kawasaki_images/S-20100304-011Z0001.gif')
print(result)

print("\n=== Test nginx config ===")
result = ssh_exec(config, 'cd ~/services/miyabe-tools && docker compose exec web nginx -t 2>&1')
print(result)

print("\n=== Check actual request in nginx ===")
result = ssh_exec(config, 'curl http://localhost:8301/data/reiki/kawasaki_images/S-20100304-011Z0001.gif -o /tmp/test.gif 2>&1 && file /tmp/test.gif')
print(result)

print("\n=== Test cat file inside nginx container ===")
result = ssh_exec(config, 'cd ~/services/miyabe-tools && docker compose exec web cat /var/www/data/reiki/kawasaki_images/S-20100304-011Z0001.gif | wc -c')
print(f"File size in container: {result} bytes")

print("\n=== Check nginx location blocks ===")
result = ssh_exec(config, 'cd ~/services/miyabe-tools && docker compose exec web cat /etc/nginx/conf.d/default.conf | grep -A 3 "location /data"')
print(result)

print("\n=== Test with different path ===")
result = ssh_exec(config, 'curl -I http://localhost:8301/data/reiki/kawasaki/H309999991001A_j.html 2>&1 | grep HTTP')
print(result)

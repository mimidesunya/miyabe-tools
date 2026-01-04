#!/bin/bash

# Ensure we are in the project root
cd "$(dirname "$0")"

# Execute the python deployment script
python3 deploy/deploy.py deploy.json

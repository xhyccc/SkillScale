#!/bin/bash
set -e
cd /Users/haoyi/Desktop/SkillScale
echo "Building..."
docker build -f docker/Dockerfile.rust -t skillscale-gateway:latest .
echo "Running Demo..."
python gateway/demo_mcp_client.py

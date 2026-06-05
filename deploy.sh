#!/bin/bash
cd /home/ubuntu/Sir-Penguin || exit 1
git pull origin main
/home/ubuntu/Sir-Penguin/venv/bin/pip install -r requirements.txt --quiet
sudo systemctl restart sir-penguin
echo "Deploy gata: $(date)"

#!/bin/bash
# deploy.sh — rulat automat de GitHub Actions la fiecare push pe main.
# Trage ultimul cod, instaleaza dependentele DOAR daca s-au schimbat, reporneste botul.

set -e
cd ~/Sir-Penguin

echo "==> Salvez hash-ul requirements dinainte"
OLD_REQ=$(sha1sum requirements.txt 2>/dev/null | cut -d' ' -f1 || echo "none")

echo "==> Trag ultimul cod de pe GitHub"
git fetch origin main
git reset --hard origin/main

echo "==> Verific daca requirements.txt s-a schimbat"
NEW_REQ=$(sha1sum requirements.txt 2>/dev/null | cut -d' ' -f1 || echo "none")
if [ "$OLD_REQ" != "$NEW_REQ" ]; then
  echo "    -> requirements s-au schimbat, instalez dependentele"
  venv/bin/pip install -q -r requirements.txt
else
  echo "    -> requirements neschimbate, sar peste pip install (mai rapid)"
fi

echo "==> Repornesc botul"
sudo systemctl restart sir-penguin

echo "==> Gata! Status:"
sudo systemctl is-active sir-penguin

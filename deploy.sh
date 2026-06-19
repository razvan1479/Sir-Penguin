#!/bin/bash
# deploy.sh — rulat automat de GitHub Actions la fiecare push pe main.
# Trage ultimul cod, instaleaza dependentele si reporneste botul.

set -e
cd ~/Sir-Penguin

echo "==> Trag ultimul cod de pe GitHub"
git fetch origin main
git reset --hard origin/main

echo "==> Instalez dependentele (daca s-au schimbat)"
venv/bin/pip install -q -r requirements.txt

echo "==> Repornesc botul"
sudo systemctl restart sir-penguin

echo "==> Gata! Status:"
sudo systemctl is-active sir-penguin

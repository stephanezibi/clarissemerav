#!/bin/bash
# Lancement local sur Mac (test) — double-cliquer
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
(sleep 3 && open "http://localhost:8000") &
.venv/bin/python run.py

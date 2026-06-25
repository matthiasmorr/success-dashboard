#!/bin/bash
# Erfolgs-Dashboard lokal starten -> öffnet http://localhost:8501
cd "$(dirname "$0")"
exec ./venv/bin/streamlit run app.py

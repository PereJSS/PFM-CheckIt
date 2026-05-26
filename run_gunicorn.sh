#!/bin/bash

# Script para ejecutar Django con gunicorn en producción
# Uso: ./run_gunicorn.sh [número de workers]

WORKERS=${1:-4}
BIND="0.0.0.0:8000"
TIMEOUT=120

cd "$(dirname "$0")/checkIt"

../.env/bin/gunicorn \
    --workers $WORKERS \
    --bind $BIND \
    --timeout $TIMEOUT \
    --access-logfile - \
    --error-logfile - \
    core.wsgi:application

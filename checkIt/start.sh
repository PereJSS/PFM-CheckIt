#!/usr/bin/env bash
# Start Command de Render: `bash start.sh` (rootDir checkIt).
#
# Celery (worker con el beat dentro) y gunicorn comparten instancia. Antes se lanzaban con
# `celery ... & gunicorn ...`: si Celery moría, la web seguía y Render decía «live» (4 nov).
#
# 1. Arranca Celery y espera a que avise de que está listo (señal worker_ready, en
#    core/celery.py). Si muere antes, sale con error SIN abrir el puerto: en un deploy,
#    Render lo da por fallido y mantiene la versión anterior. Si solo tarda (p. ej. Redis
#    caído y Celery reintentando), tras CHECKIT_ESPERA_CELERY_SEG arranca la web igualmente
#    y lo avisa: /healthz/celery dará la alarma.
# 2. Arranca gunicorn. Si cualquiera de los dos termina, para el otro y sale con error:
#    Render reinicia la instancia.
# 3. Con SIGTERM/SIGINT (deploy nuevo, reinicio) reenvía la señal a los dos y sale bien.
#
# El beat va dentro del worker (-B): si muriera solo él, este script no lo vería; lo cubre
# el latido de /healthz/celery.
# CHECKIT_CMD_CELERY / CHECKIT_CMD_WEB solo existen para los tests (core/tests.py).
set -u

CMD_CELERY=${CHECKIT_CMD_CELERY:-celery -A core worker -B --pool=solo --loglevel=info}
CMD_WEB=${CHECKIT_CMD_WEB:-gunicorn core.wsgi}
ESPERA=${CHECKIT_ESPERA_CELERY_SEG:-60}

celery_pid=
web_pid=
directorio=$(mktemp -d)
export CHECKIT_CELERY_LISTO="$directorio/celery-listo"

limpiar_marca() {
    rm -f "$CHECKIT_CELERY_LISTO"
    rmdir "$directorio" 2>/dev/null
}

parar() {
    kill -TERM ${celery_pid:+"$celery_pid"} ${web_pid:+"$web_pid"} 2>/dev/null
    wait
    limpiar_marca
}

salir_con_error() {
    # Aunque un proceso saliera con 0, la instancia no sirve sin él: siempre con error.
    exit $(( $1 == 0 ? 1 : $1 ))
}

trap 'parar; exit 0' TERM INT

# Separación por palabras intencionada: los comandos no llevan comillas.
$CMD_CELERY &
celery_pid=$!

limite=$(( SECONDS + ESPERA ))
until [ -e "$CHECKIT_CELERY_LISTO" ]; do
    if ! kill -0 "$celery_pid" 2>/dev/null; then
        wait "$celery_pid"
        codigo=$?
        echo "start.sh: celery terminó con código $codigo antes de estar listo; no se arranca gunicorn" >&2
        limpiar_marca
        salir_con_error "$codigo"
    fi
    if (( SECONDS >= limite )); then
        echo "start.sh: celery lleva ${ESPERA}s sin avisar de que está listo; se arranca gunicorn igualmente (ver /healthz/celery)" >&2
        break
    fi
    # sleep en segundo plano + wait: así una señal no espera a que termine el sleep
    sleep 0.2 &
    wait $!
done

$CMD_WEB &
web_pid=$!

wait -n "$celery_pid" "$web_pid"
codigo=$?
if kill -0 "$celery_pid" 2>/dev/null; then quien='gunicorn'; else quien='celery'; fi
echo "start.sh: $quien terminó con código $codigo; se para el otro proceso y se sale con error" >&2
parar
salir_con_error "$codigo"

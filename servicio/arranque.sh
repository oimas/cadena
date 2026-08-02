#!/bin/bash
# Arranca los DOS procesos del contenedor: el servicio de render y nginx.
# bash y no sh: `wait -n` no existe en dash, que es el /bin/sh de Debian.
#
# Van juntos en una sola imagen a propósito. Separarlos en dos servicios de
# EasyPanel obligaría a crear y cablear el segundo a mano, y a que nginx supiera
# su hostname interno; así el despliegue sigue siendo "Build = Dockerfile,
# Puerto = 80", exactamente como estaba.
set -e

# El render escucha SOLO en loopback: nginx lo proxea desde dentro del mismo
# contenedor, así que no hay motivo para exponerlo a la red.
python /app/exportar_mapa.py --servidor --host 127.0.0.1 --puerto 8765 &
RENDER_PID=$!

# Si el render se cae, que el contenedor se caiga con él: EasyPanel lo reinicia
# y no queda un sitio a medias donde previsualizar falla en silencio.
term() { kill -TERM "$RENDER_PID" 2>/dev/null; exit 0; }
trap term TERM INT

nginx -g 'daemon off;' &
NGINX_PID=$!

wait -n "$RENDER_PID" "$NGINX_PID"
exit $?

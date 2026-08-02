# ─────────────────────────────────────────────────────────────
# MDEA · Visualizador de Cadenas de Valor
# nginx (sitio estático) + servicio de render de mapas de imprenta.
# Deploy en EasyPanel → mdea.intismart.com
#
# Los DOS procesos van en una sola imagen a propósito: separarlos en dos
# servicios de EasyPanel obligaría a crearlos y cablearlos a mano. Así el
# despliegue sigue siendo "Build = Dockerfile, Puerto = 80".
#
# Por qué el render vive acá y no en la máquina de quien exporta: los
# navegadores BLOQUEAN que una página de un origen público llame a 127.0.0.1
# (Private Network Access). Desde mdea.intismart.com el servidor local es
# inalcanzable por diseño, así que el render tiene que estar del mismo lado
# que el HTML y servirse por el mismo origen (nginx proxea /exportador).
#
# Runtime: el HTML necesita indices_data.js (mismo dir). Leaflet, los tiles del
# mapa base y los que baja contextily para el render vienen de CDNs externas
# → el contenedor necesita salida a internet, que EasyPanel da por defecto.
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Debian y no Alpine: geopandas/matplotlib/pyproj tienen wheels manylinux
# listas, mientras que en musl habría que compilarlas.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx \
 && rm -rf /var/lib/apt/lists/*

COPY servicio/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Debian trae su propio sitio por defecto en sites-enabled: si queda, compite
# con el nuestro por el server_name y gana el suyo.
COPY default.conf /etc/nginx/conf.d/default.conf
RUN rm -f /etc/nginx/sites-enabled/default

# index.html (redirector) va a la raíz web; el visor y sus datos quedan planos
# junto al index.
COPY index.html                      /usr/share/nginx/html/
COPY logo/logo.jpg                   /usr/share/nginx/html/logo.jpg
COPY CADENAS/5.CACAO/mapa.html       /usr/share/nginx/html/
COPY CADENAS/5.CACAO/indices_data.js /usr/share/nginx/html/

COPY servicio/exportar_mapa.py /app/exportar_mapa.py
COPY servicio/arranque.sh      /app/arranque.sh
# El sed es cinturón y tirantes junto al .gitattributes: si el script llegara
# con CRLF (se trabaja desde Windows), el shebang sería "#!/bin/bash\r" y el
# contenedor moriría con "bad interpreter". Cuesta nada y evita ese fallo.
RUN sed -i 's/\r$//' /app/arranque.sh && chmod +x /app/arranque.sh

# matplotlib y contextily escriben caché; sin HOME escribible se quejan.
ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mpl \
    XDG_CACHE_HOME=/tmp/cache

EXPOSE 80

CMD ["/app/arranque.sh"]

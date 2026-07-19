# ─────────────────────────────────────────────────────────────
# MDEA · Visualizador de Cadenas de Valor de Cacao — v10.6
# Sitio estático servido con nginx. Repo rooteado en MAPA MDEA.
# Deploy pensado para EasyPanel → mdea.intismart.com
#
# Runtime: el HTML solo necesita indices_data.js (mismo dir en la
# imagen). Leaflet y los tiles de mapa base vienen de CDNs externas
# (unpkg / cartocdn / arcgisonline / opentopomap) → el contenedor
# necesita salida a internet, que EasyPanel da por defecto.
# ─────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# Config del servidor: home = index.html (redirector), gzip activado.
COPY default.conf /etc/nginx/conf.d/default.conf

# index.html (redirector) va a la raíz web; el visor y sus datos se
# copian desde CADENAS/5.CACAO y quedan planos junto al index.
COPY index.html                                  /usr/share/nginx/html/
COPY logo/logo.jpg                               /usr/share/nginx/html/logo.jpg
COPY CADENAS/5.CACAO/mapa.html                   /usr/share/nginx/html/
COPY CADENAS/5.CACAO/indices_data.js             /usr/share/nginx/html/

EXPOSE 80

# nginx:alpine ya arranca nginx en primer plano (no hace falta CMD).

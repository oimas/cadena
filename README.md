# MDEA · Visualizador de Cadenas de Valor

Visor web interactivo de las cadenas de valor agrarias del Perú (MDEA — Mapa
de Desarrollo Económico Agrario). Muestra rutas, nodos, centros poblados,
índices territoriales (IS → PPE) y el Potencial Productivo Efectivo sobre un
mapa Leaflet.

**En línea:** https://mdea.intismart.com

Cultivos incluidos (multi-cultivo aditivo): **Cacao, Café, Madera, Mango,
Palta, Quinua**.

---

## Qué es este repo

Es el **sitio ya construido** (salida del build) más el **servicio de render**
de mapas de imprenta. No contiene los scripts de build (viven fuera del repo,
en `PUBLICAR/scripts/`).

```
.
├── index.html                     # redirector: home → /mapa
├── CADENAS/5.CACAO/
│   ├── mapa.html                  # el visor (≈83 MB, datos embebidos)
│   ├── mapa.UNIFICADO.html        # copia de seguridad del unificado
│   └── indices_data.js            # capas de índices (≈11 MB)
├── logo/logo.jpg
├── servicio/                      # renderer que corre junto a nginx
│   ├── exportar_mapa.py           # COPIA; la fuente vive en PUBLICAR/scripts/
│   ├── requirements.txt
│   └── arranque.sh                # levanta render + nginx
├── Dockerfile                     # python:3.12-slim + nginx
├── default.conf                   # nginx: /mapa, gzip y proxy /exportador
├── .dockerignore
├── .gitattributes                 # fuerza LF (el contenedor es Linux)
└── .gitignore
```

---

## Stack / lenguajes

- **HTML + CSS** — interfaz.
- **JavaScript** — lógica del mapa (librería **Leaflet**, vía CDN unpkg).
- **Datos** — JSON, GeoJSON y PNG en base64, **embebidos** en el HTML/JS.
- **nginx** — sirve el sitio y proxea el renderer.
- **Python** — dos roles distintos:
  - *fuera del repo*: genera los HTML (build local).
  - *dentro del contenedor*: el **servicio de render** (matplotlib + geopandas +
    contextily) que convierte una selección del visor en PNG 300 dpi + PDF.

> No usa base de datos. El contenedor **necesita salida a internet**: Leaflet,
> los tiles del mapa base del visor y las teselas que baja el renderer son CDNs.

---

## Cómo funciona en runtime

1. `mdea.intismart.com` → `index.html` redirige a `/mapa`.
2. nginx sirve `mapa.html` en la ruta limpia `/mapa` (ver `default.conf`).
3. `mapa.html` carga `indices_data.js` (relativo) y Leaflet desde la CDN.
4. Toda la data (rutas, nodos, CCPP, cubo PPE) ya está dentro de los archivos.
5. Al **exportar un mapa**, el visor hace `POST /exportador/render` con la
   selección; nginx lo proxea al renderer en `127.0.0.1:8765`, que devuelve una
   previsualización y deja el PNG/PDF listo en `/exportador/descargar/<id>.png`.

### Por qué el render corre acá y no en la máquina de quien exporta

Los navegadores **bloquean** que una página de origen público llame a
`127.0.0.1` (*Private Network Access*): `Permission was denied for this request
to access the loopback address space`. No se puede sortear desde la página, así
que el renderer tiene que vivir del mismo lado que el HTML y responder por el
mismo origen. El visor detecta dónde se abrió y elige a dónde pedir: al
`127.0.0.1:8765` local si es `file://`, o a `/exportador` si está publicado.

---

## Deploy

Flujo actual: **build local → git push → EasyPanel construye la imagen Docker → publica.**

```
BUILD (Python, local)
  → python PUBLICAR/scripts/unificar_cultivos.py
  → copiar mapa.UNIFICADO.html → CADENAS/5.CACAO/mapa.html
  → git add / commit / push (rama main)
  → EasyPanel reconstruye el contenedor y publica en mdea.intismart.com
```

**EasyPanel:** Build = Dockerfile · Puerto = `80`. No cambió al sumar el
renderer, pero el **primer build tarda bastante más**: instala Python y el stack
geoespacial (~1 GB). Los siguientes reusan caché.

Probar la imagen localmente:

```bash
docker build -t mdea .
docker run -p 8080:80 mdea
# abrir http://localhost:8080
# y comprobar el renderer:  curl http://localhost:8080/exportador/ping
```

### Si el mapa exportado sale sin fondo

El renderer devuelve `basemap_ok` en cada render y el visor lo muestra en
pantalla. En `false` significa que el contenedor no alcanzó
`basemaps.cartocdn.com` — política de red o DNS del servidor. El resto del mapa
es correcto; el fondo "Blanco" del visor no necesita teselas.

### Notas del build

- **`rasterio` es obligatorio** en `servicio/requirements.txt` aunque el servidor
  no lea GeoTIFF: es dependencia dura de `contextily`. Sin él los mapas salen sin
  mapa base **sin avisar en el build**.
- **`.gitattributes` fuerza LF.** `arranque.sh` con CRLF deja el shebang como
  `#!/bin/bash\r` y el contenedor no arranca (*bad interpreter*). El Dockerfile
  además le pasa un `sed` por las dudas.
- **`.gitignore` ignora todo salvo una lista blanca.** Al agregar archivos al
  deploy hay que permitirlos explícitamente o el `COPY` del Dockerfile falla.

---

## Cultivos integrados

| Cultivo | Estado pipeline  | CCPP | MDEA indices |
|---------|------------------|------|--------------|
| Cacao   | Completo (1→9)   | Sí   | Sí           |
| Café    | Completo (1→9)   | Sí   | Sí           |
| Quinua  | Completo (1→9)   | Sí   | Sí           |
| Palta   | Completo (1→9)   | Sí   | Sí           |
| Mango   | Paso 4 (sin 3)   | Sí (526 CCPP) | No  |
| Madera  | Paso 4 (sin 3)   | No (data incompleta) | No |

- **Madera**: paso 3 (CCPP) no ejecutado — falta shapefile `CCPP_IGN100K.shp`
  completo (el actual está truncado a 128 bytes). El visor funciona sin
  conexiones CCPP para Madera.
- **Mango**: paso 3 ejecutado con éxito (526 CCPP conectados). Sin índices
  MDEA (no se corrió `preprocesar_indices.py` para la zona Mango).

---

## Modelo de etapas (acopios)

Las etapas de acopio son **3, solo a nivel visual** (campo `E` de cada nodo).
El nombre interno del nodo, el índice nodal (`id_nodo3`) y las conexiones de
los centros poblados (CCPP, que casan por nombre de nodo) quedan intactos.

| Etiqueta visible  | Cultivos                     |
|-------------------|------------------------------|
| Acopio            | Palta, Mango, Madera         |
| Acopio en baba    | Cacao                        |
| Acopio en grano   | Café, Quinua, Cacao          |
| Reacopio          | Todos                        |
| Procesamiento     | Todos                        |
| Exportación       | Todos                        |

Cascada de índices por punto (clic sobre el cubo): IS → ICPH → IHR → PPBAgro
→ PPBAgri → IAA → **PPE** (paso 09).

---

## Cambios recientes

### v16 — Río ponderado: una vía, una línea (Jul 2026)

El río ponderado ya se lee como un río: el flujo que comparte carretera se suma
en una sola línea, y el valor se codifica en **grosor + tono**.

- **Fusión de trazas paralelas.** OSRM devuelve trazados ligeramente distintos
  para la **misma** carretera, así que dos sub-rutas que comparten camino salían
  como **dos líneas finas paralelas** (medido: 23–70 m de separación; el 16 % de
  los tramos tenía una paralela dentro de 80 m). Ahora cada tramo cede sus
  sub-rutas a un "gemelo" cercano (perpendicular < 80 m, ángulo < 25°) y deja de
  dibujarse. **No se mueve ninguna coordenada**: sobrevive la traza real de OSRM.
  Resultado: 182.397 → 55.486 tramos y **0 paralelas** dentro de 80 m.
- **Intensidad por valor.** El río va de un tinte claro en los capilares al color
  de la dimensión **oscurecido** en el troncal. Antes solo variaba el grosor y dos
  tramos de peso parecido se confundían.
- **Cada sub-ruta cuenta una sola vez.** Una sub-ruta que recorre dos veces el
  mismo tramo (ida y vuelta) se sumaba dos veces: el número mostrado llegó a
  inflarse **383 puntos** (zona marcaba 766 % donde correspondía 523 %).
- **Departamento redondea a 100.** Los % del FORMATO no cierran exacto (99.8,
  100.2): el grupo que llega al total se muestra como `100.0`, para que el número
  no contradiga al rótulo "100 % COMPLETO". Solo presentación, y solo cerca de
  100 — un tramo donde confluyen dos departamentos (161.8 %) se muestra tal cual.
  Zona y corredor van sin redondeo.
- **Desglose por clic** sobre el río: agrupa por el denominador contra el que está
  normalizado cada % (zona = ruta base; dpto/corredor = su producción), y cada
  grupo cierra en su propio 100 %. La **marca del 100 %** (punteada roja) señala
  por dónde converge una zona / un departamento / un corredor **completo**.
- Nota: la red de tramos es compartida, así que el **caudal por conteo** (1er
  botón) también se benefició de la fusión — su máximo pasó de ~33 a 41
  recorridos, porque las rutas que van por la misma carretera ya caen en el
  mismo tramo.
- **Apilado de capas corregido.** Las conexiones **CCPP → nodo** se traían al
  frente y tapaban el río, las rutas y hasta los propios centros poblados: con
  el checkbox activo el mapa se volvía una maraña azul. Ahora son contexto y van
  **debajo**. El orden de `orden()` quedó, de abajo hacia arriba: zona agrícola /
  departamentos → conexiones CCPP → río → rutas → centros poblados → cuellos de
  botella.

Verificado headless (Playwright + Edge) sobre el unificado: sin errores de
consola, río dibujado en las 3 dimensiones y capturas antes/después del caso
reportado (AM1/R1 vs AM1/R2, Amazonas).

### v15 — Caudal ponderado por % (Jul 2026)

- **Segundo botón de "río"** bajo el de conteo: dibuja el flujo sobre las vías
  acumulando el **% de la sub-ruta** (por **zona / departamento / corredor**) en
  vez del número de recorridos. La dimensión se elige con **3 chips**, y cada río
  tiene su color: conteo **azul**, zona **naranja**, departamento **verde**,
  corredor **violeta**.
- **Excluyente** con el caudal de conteo; se **re-normaliza** al filtrar rutas o
  cambiar de dimensión. Los chips Dpto/Corredor **se grisan** (con fallback a Zona)
  cuando solo queda activo un cultivo sin esas columnas.
- **Datos**: las 3 dimensiones existen en Cacao/Café/Palta/Quinua; Mango/Madera
  solo tienen "% en zona".

### v10.7 — Multi-cultivo unificado (Jul 2026)

- **+Madera y Mango**: integrados al visor unificado con `unificar_cultivos.py`.
  Madera (4 sub-rutas, 57 nodos, 17 flechas) y Mango (12 sub-rutas, 88 nodos,
  526 CCPP, 25 flechas) se suman a Cacao, Café, Palta y Quinua.
- **Fix CCPP remap en `nodos_metricas`**: el remap de etapa (`o.E='Acopio'`
  para Madera/Mango/Palta) ahora se aplica tanto a `NODOS` (panel sidebar) como
  a `AN.nodos_metricas` (función `nodosVisiblesNom()` que filtra CCPP). Antes
  las líneas CCPP de Mango/Madera se controlaban con el checkbox de "Acopio en
  baba" (Cacao) en vez de "Acopio".
- **ETAPAS_CROPS actualizado**: `"Acopio":["PALTA","MANGO","MADERA"]` — el
  panel de etapas greyea correctamente las etapas incompatibles con el cultivo
  activo.
- **Leyenda original**: `etLabel()` muestra nombres de etapa sin combinar
  ("Acopio", "Acopio en baba", "Acopio en grano", "Exportación / Mercado").
- **Normalización por cultivo en `recalcularCaudal()`**: el radio de los nodos
  se calcula dentro del rango de cada cultivo (0..1 por crop), para que nodos de
  Quinua y Café con la misma etapa tengan tamaño comparable.
- **Radio Quinua**: mínimo 6.4 px (vs 1.8 px general) para mejor visibilidad.
- **R_MIN=1.8, R_MAX=13**: nodos más pequeños que el original (3.4→13) pero
  con Quinua elevado.
- **`correr_paso4.py` parcheado**: el paso 3 (CCPP) ya no es obligatorio — si
  falta, el visor funciona sin conexiones CCPP para ese cultivo.

### v10.6 — 3 acopios + CCPP + fixes

- **3 acopios** (Acopio / Acopio en baba / Acopio en grano) — renombrado visual
  del campo de etapa; funcionalidad interna, índice nodal y conexiones CCPP
  intactos.
- **Fix PPE**: guard `null` en `getElementById('sub-titulo').textContent` que
  rompía el script antes de registrar el clic del cubo.
- **CCPP**: visibilidad sincronizada con el marcador; la línea CCPP→nodo va a la
  par de rutas y por debajo de nodos y centros poblados; centro poblado amarillo
  con borde celeste, más chico.
- **Leyenda**: Acopio / Acopio en baba / Acopio en grano (sin prefijo repetido).
- Antes: vistas en vivo, CCPP por nodos y alta de Palta; visor multi-cultivo
  (Cacao + Quinua + Café); botón de caudal; índice nodal clasificado.

---

## Archivos clave del build

| Archivo | Descripción |
|---------|-------------|
| `PUBLICAR/scripts/correr_paso4.py` | Orquestador del paso 4 — genera el HTML por cultivo |
| `PUBLICAR/scripts/unificar_cultivos.py` | Fusiona todos los cultivos en un solo HTML unificado |
| `PUBLICAR/CADENAS/5.CACAO/mapa.html` | Plantilla base (solo Cacao) |
| `PUBLICAR/CADENAS/5.CACAO/mapa.UNIFICADO.html` | HTML unificado generado (salida final) |
| `PUBLICAR/FLUJO.md` | Runbook completo de build + verificación |

---

## Notas

- `mapa.html` pesa ≈82 MB por la data embebida (GitHub avisa que supera los
  50 MB recomendados). gzip está activado en nginx para acelerar la 1ª carga.
- Para una plataforma real / integración institucional (MIDAGRI) conviene
  separar datos de la presentación (PostGIS + teselas/API + servicios OGC).

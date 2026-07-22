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

Es el **sitio ya construido** (salida del build), servido como estático con
nginx en un contenedor Docker. No contiene los scripts de build (esos viven
fuera del repo, en `PUBLICAR/scripts/`).

```
.
├── index.html                     # redirector: home → /mapa
├── CADENAS/5.CACAO/
│   ├── mapa.html                  # el visor (≈82 MB, datos embebidos)
│   ├── mapa.UNIFICADO.html        # copia de seguridad del unificado
│   └── indices_data.js            # capas de índices (≈11 MB)
├── logo/logo.jpg
├── Dockerfile                     # imagen nginx:alpine
├── default.conf                   # config nginx (ruta /mapa + gzip)
├── .dockerignore
└── .gitignore
```

---

## Stack / lenguajes

- **HTML + CSS** — interfaz.
- **JavaScript** — lógica del mapa (librería **Leaflet**, vía CDN unpkg).
- **Python** — solo para *generar* los HTML (build local, fuera del repo).
  No corre en el servidor.
- **Datos** — JSON, GeoJSON y PNG en base64, **embebidos** en el HTML/JS.
- **nginx (Docker)** — servidor web.

> No usa base de datos ni backend: es un **sitio estático**. El contenedor sí
> necesita salida a internet (Leaflet y los tiles del mapa base son CDNs).

---

## Cómo funciona en runtime

1. `mdea.intismart.com` → `index.html` redirige a `/mapa`.
2. nginx sirve `mapa.html` en la ruta limpia `/mapa` (ver `default.conf`).
3. `mapa.html` carga `indices_data.js` (relativo) y Leaflet desde la CDN.
4. Toda la data (rutas, nodos, CCPP, cubo PPE) ya está dentro de los archivos.

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

Probar la imagen localmente:

```bash
docker build -t mdea .
docker run -p 8080:80 mdea
# abrir http://localhost:8080
```

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

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
exportar_mapa.py — de la SELECCIÓN del visor a un mapa de imprenta.

    python exportar_mapa.py seleccion_20260801_1432.json

Lee el JSON que descarga el botón "Exportar mapa" del visor
(`PUBLICAR/CADENAS/5.CACAO/mapa.html`) y produce un PNG a 300 dpi + un PDF
vectorial con el marco cartográfico completo: título, leyenda, norte, barra de
escala, grilla de coordenadas y créditos.

═══ Por qué el trabajo se parte en dos ═══

El navegador NO rasteriza el mapa. Solo captura QUÉ está seleccionado y CÓMO se
ve (color y grosor ya resueltos); el dibujo lo hace este script. Las razones:

 1. Un `html2canvas` sobre Leaflet es una captura de pantalla escalada:
    tipografía estirada y el ráster del índice ya viene downsampleado a base64.
 2. Acá el índice se lee del **GeoTIFF original** de `MDEA/indices/` — esa es la
    diferencia entre 300 dpi reales y una ampliación.
 3. El basemap lo baja `contextily` a la resolución del papel, no a la de la
    pantalla.

═══ Diseñado para hacerse público después ═══

El contrato es el JSON (`schema: "mdea.export/1"`), no el sistema de archivos.
Este script funciona en dos modos, sin cambiar el JSON:

 - LOCAL (hoy): si encuentra `MDEA/indices/`, lee el GeoTIFF → máxima calidad.
 - AUTÓNOMO: si no lo encuentra, usa el PNG base64 que el propio JSON trae
   embebido. Sirve tal cual del lado de un servidor, sin acceso a los .tif.

El día que esto se exponga al público, lo único que cambia es quién ejecuta el
script (un worker en vez de vos); el navegador sigue emitiendo el mismo JSON.

═══ WYSIWYG ═══

El JSON trae lo VISIBLE, con el estilo ya resuelto por el visor. Este script no
vuelve a filtrar ni a recalcular: si en pantalla hay 12 sub-rutas, imprime 12.
El río de caudal (v16) llega ya fusionado y agrupado por grosor — reimplementar
`fusionarParalelas()` acá duplicaría lógica delicada y arriesgaría que el
impreso no coincida con la pantalla.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import sys
from datetime import datetime

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Polygon as MplPolygon

import geopandas as gpd
from shapely.geometry import shape as shp_shape
import pyproj


# ════════════════════════════════════════════════════════════════════════════
# CONSTANTES DEL PROYECTO (espejo del visor · no inventar valores acá)
# ════════════════════════════════════════════════════════════════════════════

SCHEMA = "mdea.export/1"          # contrato con el visor (ver cabecera)

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PUBLICAR_DIR = os.path.dirname(_SCRIPTS_DIR)
BASE = os.path.dirname(_PUBLICAR_DIR)
INDICES_DIR = os.path.join(BASE, "MDEA", "indices")

# Las 5 clases del MDEA. Misma paleta que los QML y que preprocesar_indices.py.
PALETA = {
    1: ("Muy Bajo", "#d73027"),
    2: ("Bajo",     "#fc8d59"),
    3: ("Medio",    "#fee08b"),
    4: ("Alto",     "#a6d96a"),
    5: ("Muy Alto", "#1a9850"),
}

# id del índice → GeoTIFF de clases. Copiado de preprocesar_indices.py::RASTERS,
# que es la fuente de verdad: si allá cambia un archivo, actualizar acá.
TIF_POR_INDICE = {
    "01_IS":       "01_aptitud_biofisica/MDEA_01_IS_fao_AGRI_v1.tif",
    "02_ICPH":     "02_capacidad_hidrica/MDEA_02_ICPH_nativa_AGRI_v1.tif",
    "03_IHR":      "03_habilitacion_rural/MDEA_03_IHR_clases_AGRI_v1.tif",
    "04_PPBAgro":  "04_ppb_agropecuario/MDEA_04_PPBAGRO_clases_AGRI_v1.tif",
    "05_PPBAGRI":  "05_ppb_agroindustrial/MDEA_05_PPBAGRI_clases_AGRI_v1.tif",
    "08_IAA":      "08_articulacion_territorial/MDEA_08_IAA_clases_AGRI_v1.tif",
    "09_PPE":      "09_ppe/MDEA_09_PPE_clases_AGRI_v1.tif",
}

# Nombre del basemap en el visor (BASES de mapa.html) → proveedor de contextily.
# 'Blanco' es el gridLayer blanco del visor: sin teselas.
BASEMAPS = {
    # CARTO cerro sus basemaps publicos (2026-09): sin API key las teselas
    # llegan estampadas "API KEY REQUIRED". Se pasan a Esri, que sigue abierto
    # y ya servia el satelital.
    "Claro":    ("Esri", "WorldGrayCanvas",  "© Esri © OpenStreetMap contributors"),
    "Calles":   ("Esri", "WorldStreetMap",   "© Esri © OpenStreetMap contributors"),
    # Fondos suaves: la red vial sin que le gane al dato. 'Calles tenue' es la
    # misma tesela que 'Calles' pero gris y al 55 %; 'Calles claras' es el
    # canvas gris SIN la capa de rótulos (cero topónimos).
    "Calles tenue":  ("Esri", "WorldStreetMap",  "© Esri © OpenStreetMap contributors"),
    "Calles claras": ("Esri", "WorldGrayCanvas", "© Esri © OpenStreetMap contributors"),
    "Relieve":  ("OpenTopoMap", None,        "© OpenTopoMap (CC-BY-SA) © OpenStreetMap contributors"),
    "Satélite": ("Esri", "WorldImagery",     "© Esri, Maxar, Earthstar Geographics"),
    "Blanco":   (None, None,                 ""),
}

# Variante SIN topónimos de cada proveedor. A los zooms de papel el basemap
# rotula cientos de caseríos que compiten con los nombres de nodo y de
# departamento. OpenTopoMap y Esri no tienen variante limpia: quedan como están.
# Ojo: desde que el gris lo pone Esri (CARTO pide API key), "limpio" ya no quita
# topónimos en Claro/Calles — las entradas CartoDB de abajo quedan por si alguna
# vez se configura una clave.
BASEMAPS_LIMPIOS = {
    ("CartoDB", "Positron"): "PositronNoLabels",
    ("CartoDB", "Voyager"): "VoyagerNoLabels",
}

# Los fondos "tenues" del visor son la MISMA tesela con un filtro CSS
# (grayscale + opacidad). Acá se replica —gris + alpha— o el impreso saldría a
# color y no se parecería a lo que la persona eligió en pantalla.
BASEMAPS_TENUES = {"Calles tenue": 0.55}

# Papel en mm (ancho, alto) en VERTICAL. La orientación se aplica después.
PAPEL_MM = {"A5": (148, 210), "A4": (210, 297), "A3": (297, 420), "A2": (420, 594)}

# Tipografía del marco. Están juntas a propósito: la legibilidad de un mapa
# impreso se ajusta como un sistema, no retocando un `fontsize` suelto.
FS_COORD = 12      # números de la grilla de coordenadas
FS_LEG_TIT = 15    # "CULTIVO CAFÉ" — el rótulo que más se lee
FS_LEG_SEC = 12    # "RUTAS", "ETAPAS"
FS_LEG = 11.5      # ítems de la leyenda
FS_ESCALA = 10     # rótulos de la barra de escala

# Orden de la cadena (espejo de ORDEN en mapa.html). Sirve para desempatar el
# apilado cuando dos etapas coinciden en tamaño.
ORDEN_ETAPAS = ["Acopio", "Acopio en baba", "Acopio en grano",
                "Reacopio", "Procesamiento", "Exportación"]

# Rótulo de la leyenda. Espejo de `etLabel()` en mapa.html — el mapa impreso y
# el visor tienen que nombrar la misma cosa igual.
ETIQUETA_ETAPA = {"Exportación": "Exportación / Mercado"}

# Realce de tamaño por etapa, sobre el radio que ya salió del caudal. La
# Exportación se dibuja con estrella y una estrella tiene mucha menos "mancha"
# que un círculo de la misma área nominal (sus puntas son cóncavas), así que a
# igual caudal se lee más chica. Es el nodo que cierra la cadena: conviene que
# se vea. No afecta a las demás etapas ni a la comparación entre mercados.
REALCE_ETAPA = {"Exportación": 1.6}

# Etapas exentas de la regla general "grande abajo, chico encima" y del achique
# por solape. La Exportación va SIEMPRE arriba y sin reducir:
#   · Es el nodo que cierra la cadena — lo que el mapa tiene que mostrar.
#   · Al realzarla pasaba a ser la más grande y la regla la mandaba al fondo,
#     donde el cuadrado de Procesamiento la tapaba entera. Exento del achique
#     tampoco alcanzaba: seguía abajo.
#   · Una estrella cubre poca superficie real (sus brazos son finos), así que
#     ponerla encima no esconde lo que tiene debajo — se ve entre las puntas.
ENCIMA = {"Exportación"}
Z_ENCIMA = 9.6                       # por sobre los 12 niveles (9.0 … 9.44)

# Tope de teselas a bajar. Un mapa nacional a zoom alto son miles de pedidos a
# un servidor gratuito: se acota y se avisa, en vez de abusar en silencio.
MAX_TESELAS = 600


# ════════════════════════════════════════════════════════════════════════════
# PROYECCIÓN
# ════════════════════════════════════════════════════════════════════════════

def elegir_crs(bbox, forzado=None):
    """CRS de trabajo + su etiqueta para los créditos.

    Perú cruza tres husos UTM (17, 18, 19), así que un mapa NACIONAL en UTM
    deforma los extremos. La regla: si la selección cabe holgada en un huso
    (≲5° de longitud) se usa ese UTM — métrico de verdad, como corresponde a un
    mapa regional; si no, Web Mercator, que es además lo que muestra el visor.
    """
    if forzado:
        crs = forzado if str(forzado).upper().startswith("EPSG") else f"EPSG:{forzado}"
        return crs, crs

    w, s, e, n = bbox
    if (e - w) <= 5.0:
        lon_c = (w + e) / 2.0
        zona = int((lon_c + 180) // 6) + 1
        epsg = 32700 + zona          # 327xx = hemisferio sur
        return f"EPSG:{epsg}", f"UTM {zona}S (EPSG:{epsg})"
    return "EPSG:3857", "Web Mercator (EPSG:3857)"


def metros_a_unidades(crs, lon0, lat0, metros):
    """Cuántas unidades del CRS proyectado equivalen a `metros` en el terreno.

    Se resuelve con un cálculo geodésico (elipsoide WGS84), no con el factor de
    escala nominal: sirve igual en UTM (donde casi coinciden) que en Mercator
    (donde a −10° de latitud la diferencia es del orden del 1.5 %). Sin esto la
    barra de escala miente, que es el error clásico de un mapa web impreso.
    """
    geod = pyproj.Geod(ellps="WGS84")
    lon1, lat1, _ = geod.fwd(lon0, lat0, 90, metros)
    tr = pyproj.Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x0, _ = tr.transform(lon0, lat0)
    x1, _ = tr.transform(lon1, lat1)
    return abs(x1 - x0)


def paso_lindo(v):
    """Redondea a 1 / 2 / 5 × 10ⁿ — los pasos que un ojo lee sin esfuerzo."""
    if v <= 0:
        return 1
    exp = math.floor(math.log10(v))
    m = v / (10 ** exp)
    m = 1 if m < 1.5 else 2 if m < 3.5 else 5 if m < 7.5 else 10
    return m * (10 ** exp)


# ════════════════════════════════════════════════════════════════════════════
# GEOMETRÍA · GeoJSON del JSON → GeoSeries proyectada
# ════════════════════════════════════════════════════════════════════════════

def proyectar(geoms, crs):
    """Lista de dicts GeoJSON → GeoSeries en `crs`. Ignora los nulos."""
    limpio = [shp_shape(g) for g in geoms if g]
    if not limpio:
        return None
    return gpd.GeoSeries(limpio, crs="EPSG:4326").to_crs(crs)


def proyectar_puntos(xs, ys, crs):
    tr = pyproj.Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    return tr.transform(np.asarray(xs, float), np.asarray(ys, float))


def latlng_a_xy(segs, crs):
    """Segmentos [[lat,lng],[lat,lng]] de Leaflet → [[(x,y),(x,y)], ...].

    Ojo con el orden: Leaflet trabaja en [lat,lng] y todo lo demás en [lng,lat].
    """
    if not segs:
        return []
    arr = np.asarray(segs, float)                     # (n, 2, 2) = [lat, lng]
    tr = pyproj.Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x, y = tr.transform(arr[:, :, 1].ravel(), arr[:, :, 0].ravel())
    return np.stack([x.reshape(-1, 2), y.reshape(-1, 2)], axis=-1)


# ════════════════════════════════════════════════════════════════════════════
# ÍNDICE MDEA · GeoTIFF original (preferido) o el PNG del JSON (fallback)
# ════════════════════════════════════════════════════════════════════════════

def _rgba_desde_clases(arr, nodata, alpha):
    rgba = np.zeros(arr.shape + (4,), dtype=np.uint8)
    a = int(round(255 * alpha))
    for val, (_, hexc) in PALETA.items():
        r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
        rgba[arr == val] = (r, g, b, a)
    return rgba


def indice_desde_tif(idx, bbox, crs, alpha):
    """Lee el GeoTIFF de clases, recorta al bbox y lo reproyecta al CRS del mapa.

    Devuelve (rgba, extent) o None si el archivo no está. `extent` va en
    unidades del CRS destino, listo para `ax.imshow`.
    """
    rel = TIF_POR_INDICE.get(idx.get("id"))
    if not rel:
        return None
    ruta = os.path.join(INDICES_DIR, rel)
    if not os.path.exists(ruta):
        return None

    import rasterio
    from rasterio.warp import reproject, Resampling, transform_bounds
    from rasterio.transform import from_bounds as tf_from_bounds
    from rasterio.windows import from_bounds as win_from_bounds

    w, s, e, n = bbox
    with rasterio.open(ruta) as src:
        nodata = src.nodata if src.nodata is not None else -9999
        # Ventana en coordenadas del origen, con un margen para que el remuestreo
        # no coma el borde.
        wb = transform_bounds("EPSG:4326", src.crs, w, s, e, n)
        mx, my = (wb[2] - wb[0]) * .02, (wb[3] - wb[1]) * .02
        try:
            win = win_from_bounds(wb[0] - mx, wb[1] - my, wb[2] + mx, wb[3] + my,
                                  src.transform).round_offsets().round_lengths()
            win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
        except Exception:
            return None
        if win.width < 1 or win.height < 1:
            return None
        # Techo de 5000 px (~42 cm a 300 dpi): cubre hasta A2 sin remuestrear y
        # evita cargar el país entero a resolución nativa.
        esc = min(1.0, 5000.0 / max(win.width, win.height))
        oh, ow = max(1, int(win.height * esc)), max(1, int(win.width * esc))
        arr = src.read(1, window=win, out_shape=(oh, ow),
                       resampling=Resampling.nearest)
        src_t = src.window_transform(win) * rasterio.Affine.scale(
            win.width / ow, win.height / oh)

        db = transform_bounds("EPSG:4326", crs, w, s, e, n)
        dw, dh = ow, oh
        dst_t = tf_from_bounds(db[0], db[1], db[2], db[3], dw, dh)
        dst = np.full((dh, dw), nodata, dtype=arr.dtype)
        reproject(arr, dst, src_transform=src_t, src_crs=src.crs,
                  dst_transform=dst_t, dst_crs=crs,
                  resampling=Resampling.nearest,
                  src_nodata=nodata, dst_nodata=nodata)

    return _rgba_desde_clases(dst, nodata, alpha), (db[0], db[2], db[1], db[3])


def indice_desde_b64(idx, crs, alpha):
    """Fallback autónomo: el PNG que el visor embebió en el JSON.

    Ese PNG ya está en Web Mercator (así lo dejó preprocesar_indices.py) y sus
    `bounds` vienen en lat/lon. Solo se reproyecta el rectángulo. Menos resolución
    que el .tif, pero permite correr sin `MDEA/indices/` — que es lo que hará un
    servidor el día que esto sea público.
    """
    if not idx.get("b64"):
        return None
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(idx["b64"]))).convert("RGBA")
    rgba = np.array(img)
    if alpha < 1.0:
        rgba[..., 3] = (rgba[..., 3] * alpha).astype(np.uint8)
    w, s, e, n = idx["bounds"]
    # Se reproyecta con pyproj y no con rasterio a propósito: este camino es el
    # que corre en el servidor, donde no hay GeoTIFF que leer, y rasterio es la
    # dependencia más pesada del script. Así la imagen del contenedor no la
    # necesita. El PNG ya está en Mercator, así que se pasa por 3857.
    def _bounds(src, dst, w, s, e, n):
        if src == dst:
            return w, s, e, n
        tr = pyproj.Transformer.from_crs(src, dst, always_xy=True)
        xs, ys = tr.transform([w, e, w, e], [s, s, n, n])
        return min(xs), min(ys), max(xs), max(ys)

    b3857 = _bounds("EPSG:4326", "EPSG:3857", w, s, e, n)
    db = b3857 if crs == "EPSG:3857" else _bounds("EPSG:3857", crs, *b3857)
    return rgba, (db[0], db[2], db[1], db[3])


# ════════════════════════════════════════════════════════════════════════════
# BASEMAP
# ════════════════════════════════════════════════════════════════════════════

def poner_basemap(ax, crs, nombre, zoom=None, limpio=False):
    """Teselas del mismo proveedor que el usuario tenía en el visor.

    `limpio` pide la variante SIN etiquetas del proveedor: a los zooms de papel
    el basemap escupe cientos de topónimos que compiten con los rótulos del
    mapa (nodos, departamentos). Donde el proveedor no tiene variante limpia
    (OpenTopoMap) se usa la normal.

    El zoom NO depende de si es previsualización: preview y descarga tienen que
    bajar EXACTAMENTE las mismas teselas o el impreso no se parece a lo que se
    vio. Antes la preview usaba un nivel menos y salía con muchas menos
    etiquetas que el PNG final. Lo único que cambia entre una y otra es el dpi.
    """
    fam, var, credito = BASEMAPS.get(nombre, BASEMAPS["Claro"])
    if fam is None:
        return "", False                      # fondo "Blanco": sin teselas a propósito
    if limpio and var:
        var = BASEMAPS_LIMPIOS.get((fam, var), var)
    try:
        import contextily as ctx
    except ImportError:
        print("  ! contextily no está instalado -> mapa sin basemap "
              "(pip install contextily)")
        return "", False

    # Caché de teselas EN DISCO. Sin esto contextily re-descarga todo en cada
    # render (medido: 94 s el mismo mapa que ya se había previsualizado) — con
    # el caché, preview y descarga comparten teselas, y repetir una zona en
    # cualquier sesión futura sale casi gratis.
    try:
        cache = os.path.join(os.path.expanduser("~"), ".cache", "mdea_teselas")
        os.makedirs(cache, exist_ok=True)
        ctx.set_cache_dir(cache)
    except Exception:
        pass

    prov = getattr(ctx.providers, fam)
    if var:
        prov = getattr(prov, var)

    # El zoom 'auto' de contextily lo calcula para la resolución de PANTALLA. En
    # papel el mismo mapa ocupa 3-4× más píxeles, así que se suben DOS niveles
    # (con +1 las etiquetas del basemap salían blandas a 300 dpi). Cada nivel
    # cuadruplica las teselas → se acota con MAX_TESELAS.
    if zoom is None:
        try:
            zoom = ctx.tile._calculate_zoom(*_bbox_lonlat(ax, crs)) + 2
        except Exception:
            zoom = "auto"
    if isinstance(zoom, int):
        zoom = min(zoom, _zoom_max(_bbox_lonlat(ax, crs), MAX_TESELAS))

    n_antes = len(ax.images)
    try:
        ctx.add_basemap(ax, crs=crs, source=prov, zoom=zoom, attribution=False,
                        zorder=0, alpha=BASEMAPS_TENUES.get(nombre, 1.0))
    except Exception as ex:
        print(f"  ! NO se pudo bajar el basemap ({type(ex).__name__}: {ex})")
        return credito, False

    if nombre in BASEMAPS_TENUES and len(ax.images) > n_antes:
        # A gris, igual que el filtro del visor. Se hace sobre la imagen ya
        # colocada porque contextily no expone las teselas antes de dibujarlas.
        img = ax.images[n_antes]
        arr = img.get_array()
        if arr is not None and getattr(arr, "ndim", 0) == 3 and arr.shape[2] >= 3:
            luz = (arr[..., :3].astype("float32")
                   * np.array([0.299, 0.587, 0.114], dtype="float32")).sum(axis=2)
            gris = np.repeat(luz[..., None], 3, axis=2).astype(arr.dtype)
            if arr.shape[2] == 4:
                gris = np.dstack([gris, arr[..., 3]])
            img.set_data(gris)
    return credito, True


def _bbox_lonlat(ax, crs):
    """(w, s, e, n) en lat/lon a partir de los límites actuales del eje."""
    x0, x1 = ax.get_xlim(); y0, y1 = ax.get_ylim()
    tr = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    (w, e), (s, n) = tr.transform([x0, x1], [y0, y1])
    return w, s, e, n


def _zoom_max(bbox, tope=None):
    """Mayor zoom que no supere `tope` teselas para ese bbox."""
    tope = tope or MAX_TESELAS
    w, s, e, n = bbox
    for z in range(19, 2, -1):
        k = 2 ** z
        nx = abs(((e + 180) / 360 * k) - ((w + 180) / 360 * k)) + 1
        f = lambda la: (1 - math.log(math.tan(math.radians(la)) +
                                     1 / math.cos(math.radians(la))) / math.pi) / 2 * k
        ny = abs(f(max(-85, min(85, s))) - f(max(-85, min(85, n)))) + 1
        if nx * ny <= tope:
            return z
    return 3


# ════════════════════════════════════════════════════════════════════════════
# CAPAS
# ════════════════════════════════════════════════════════════════════════════

# Ancho típico del visor en CSS px. Es el denominador del factor de trazo: no
# hay que medirlo exacto, solo mantener la MISMA referencia que usó el ojo al
# elegir los grosores del visor (G_MAX, RC_MAX, R_MAX de mapa.html).
ANCHO_VISOR_PX = 1600.0


def escala_trazo(fig):
    """Cuántos puntos de papel equivalen a 1 px de línea del visor.

    El visor dibuja en px CSS sobre ~1600 px de ancho; matplotlib en PUNTOS
    (1/72"). Para que el impreso se vea como la pantalla, un trazo de w px debe
    ocupar la misma FRACCIÓN del ancho del mapa:

        w_pt = w_px · (ancho_fig_pulgadas · 72) / ANCHO_VISOR_PX

    De ahí sale ~0.74 en A3 horizontal. Se calcula en vez de fijarlo porque el
    factor cambia con el papel: en A5 el mismo grosor taparía el mapa.
    """
    return fig.get_size_inches()[0] * 72.0 / ANCHO_VISOR_PX


def dibujar_capas(ax, d, crs, op, ft=1.0, con_tiles=True):
    """Dibuja lo que el visor tenía visible. `leyenda` acumula lo que se usó.

    `ft` es el factor de escala_trazo(): convierte los grosores y radios que el
    visor resolvió en px a las unidades de papel. `con_tiles` avisa si hay
    basemap debajo: sin él, la tierra se rellena para el look de atlas.
    """
    capas = d.get("capas", {})
    leyenda = {"etapas": [], "indice": None, "caudal": None, "extras": []}

    # ── Tierra (solo sin basemap): junto al océano del facecolor convierte el
    # fondo "Blanco" en tierra crema / mar celeste, en vez de un lienzo gris.
    # Va DEBAJO del índice raster (zorder 2) para no taparlo.
    deptos_pre = capas.get("deptos") or []
    if deptos_pre and not con_tiles:
        gs = proyectar([x["G"] for x in deptos_pre], crs)
        if gs is not None:
            gs.plot(ax=ax, color="#F7F4EC", edgecolor="none", zorder=1.2)

    # ── Área agrícola (contexto de fondo) ──────────────────────────────────
    if capas.get("agricola"):
        gs = proyectar([capas["agricola"]], crs)
        if gs is not None:
            gs.plot(ax=ax, color="#8FBF7F", alpha=.30, edgecolor="none", zorder=3)
            leyenda["extras"].append(Patch(facecolor="#8FBF7F", alpha=.45,
                                           edgecolor="none", label="Área agrícola"))

    # ── Contorno del Perú ──────────────────────────────────────────────────
    # Mismo estilo que el fondo "Perú" del visor (gris punteado, sin relleno):
    # un relleno taparía el ráster del índice que va encima.
    if op.get("peru", True) and capas.get("peru"):
        gs = proyectar(capas["peru"], crs)
        if gs is not None:
            gs.boundary.plot(ax=ax, color="#5B6670", linewidth=1.5,
                             linestyle=(0, (5, 3)), alpha=.95, zorder=4.8)

    # ── Departamentos ──────────────────────────────────────────────────────
    # Dos regímenes distintos, según si el usuario pidió la capa en el visor:
    #
    #  · ACTIVA  → trazo marcado y punteado. Es información que se pidió ver.
    #  · APAGADA → trazo sutil de fondo. Con el basemap sin topónimos el mapa
    #    perdía toda referencia territorial; una línea muy tenue la devuelve sin
    #    competir con rutas ni nodos. Por eso los departamentos viajan siempre
    #    en el JSON, no solo cuando la capa está prendida.
    deptos = capas.get("deptos") or []
    if deptos:
        activos = bool(capas.get("deptos_activos"))
        sel = [x for x in deptos if x.get("sel")]
        nosel = [x for x in deptos if not x.get("sel")]
        if not activos:
            gs = proyectar([x["G"] for x in deptos], crs)
            if gs is not None:
                gs.boundary.plot(ax=ax, color="#98A3AE", linewidth=.45,
                                 linestyle=(0, (1, 3)), alpha=.45, zorder=4)
        else:
            # Los no seleccionados quedan tenues: el mapa dice qué se está
            # mirando sin recortar el territorio.
            if nosel and len(sel) != len(deptos):
                gs = proyectar([x["G"] for x in nosel], crs)
                if gs is not None:
                    gs.boundary.plot(ax=ax, color="#8A97A3", linewidth=.7,
                                     linestyle=(0, (2, 3)), alpha=.6, zorder=4)
            gs = proyectar([x["G"] for x in (sel or deptos)], crs)
            if gs is not None:
                gs.boundary.plot(ax=ax, color="#2E3742", linewidth=1.7,
                                 linestyle=(0, (7, 3)), alpha=.95, zorder=4.5)

    # ── Conexiones CCPP → nodo (contexto, va DEBAJO del río; ver orden() ) ──
    ccl = capas.get("ccpp_l") or []
    if ccl:
        gs = proyectar([x["G"] for x in ccl], crs)
        if gs is not None:
            gs.plot(ax=ax, color="#4FC3F7", linewidth=.35, alpha=.5, zorder=5)
            leyenda["extras"].append(Line2D([], [], color="#4FC3F7", lw=.9,
                                            label=f"Conexión CCPP → nodo ({len(ccl)})"))

    # ── Río de caudal (llega ya fusionado y agrupado por grosor) ────────────
    caudal = d.get("caudal")
    if caudal and caudal.get("grupos"):
        from matplotlib.collections import LineCollection
        for g in sorted(caudal["grupos"], key=lambda g: g.get("w", 1)):
            segs = latlng_a_xy(g.get("segs") or [], crs)
            if len(segs) == 0:
                continue
            ax.add_collection(LineCollection(
                segs, colors=g.get("color", "#0d6ba8"),
                linewidths=max(.3, g.get("w", 1) * ft), alpha=.9,
                capstyle="round", joinstyle="round", zorder=6))
        if caudal.get("cien"):
            segs = latlng_a_xy(caudal["cien"], crs)
            if len(segs):
                ax.add_collection(LineCollection(
                    segs, colors="#E53935", linewidths=1.0, alpha=.9,
                    linestyles=(0, (5, 5)), zorder=6.6))
                leyenda["extras"].append(Line2D([], [], color="#E53935", lw=1.2,
                                                ls=(0, (4, 4)), label="Convergencia del 100 %"))
        et = caudal.get("etiqueta") or "recorridos"
        leyenda["caudal"] = (caudal.get("color", "#0d6ba8"),
                             f"Caudal por {et}" if caudal.get("modo") == "pct"
                             else "Caudal (nº de recorridos)")

    # ── Rutas (modo normal, excluyente con el río) ─────────────────────────
    for r in (capas.get("rutas") or []):
        gs = proyectar([r["G"]], crs)
        if gs is None:
            continue
        # capstyle/joinstyle (no solid_*): geopandas arma un LineCollection, y
        # los `solid_*` son propiedades de Line2D.
        gs.plot(ax=ax, color=r.get("color", "#3D6B49"),
                linewidth=max(.3, r.get("w", 1.5) * ft),
                alpha=r.get("opacidad", .95), zorder=7,
                capstyle="round", joinstyle="round")

    # ── Nodos por etapa ────────────────────────────────────────────────────
    # El zorder lo decide el TAMAÑO, no la etapa: el símbolo más grande va
    # abajo y el más chico encima. Varios nodos comparten coordenada exacta con
    # etapas distintas (p.ej. Acopio y Acopio en grano en CHINCHEROS, o
    # Procesamiento y Exportación en ICA); dibujando por etapa con un zorder
    # único, la última tapaba por completo a la anterior y en el papel se veía
    # una sola. Ordenando por tamaño, el de abajo siempre asoma como un anillo.
    # No se mueve ni se redimensiona nada: solo cambia el orden de dibujo.
    FORMA = {"circle": "o", "diamond": "D", "square": "s", "star": "*"}
    # matplotlib normaliza los marcadores por ÁREA, no por radio: con el mismo
    # `s`, un rombo llega 1.371× más lejos del centro que un círculo y un
    # cuadrado 1.391× (medido rasterizando y midiendo la distancia máxima al
    # centro). Se probó dividir por este factor para dejarlos inscriptos en el
    # mismo radio, y quedó PEOR: la "cintura" del rombo pasa a medir lo mismo
    # que el círculo de adentro y el anidado desaparece. La normalización por
    # área es además la perceptualmente correcta para símbolos proporcionales
    # (lo que se compara es la mancha, no el radio). El factor se conserva para
    # DETECTAR solapes: ahí sí importa hasta dónde llega el símbolo dibujado.
    EXTENT = {"o": 1.000, "D": 1.371, "s": 1.391, "*": 1.058}
    nodos = capas.get("nodos") or []
    if nodos:
        # ── Tamaño por CAUDAL, con achique local donde se tapan ───────────
        # El radio vuelve a salir del caudal (como en el visor): un mercado con
        # mucho flujo se ve grande, que es la lectura que importa. Se probó y se
        # DESCARTÓ una escalera de tamaño por etapa (Acopio grande → Exportación
        # chica): garantizaba el anidado pero achicaba los mercados justo donde
        # más flujo confluye.
        #
        # El solape se resuelve puntualmente: se dibuja de mayor a menor y, si un
        # símbolo fuera a tapar del todo a otro ya colocado, se lo ACHICA lo justo
        # para dejar un anillo visible. Se evaluó desplazarlos y se descartó —
        # mover un nodo miente sobre dónde está el acopio y además cascadea (al
        # correr uno se lo empuja sobre un tercero). La posición no se toca: la
        # distorsión es solo de tamaño, acotada, y solo donde el símbolo de abajo
        # sería invisible de todos modos.
        dpi_fig = ax.figure.dpi
        xs_t, ys_t = proyectar_puntos([n["x"] for n in nodos],
                                      [n["y"] for n in nodos], crs)
        P = ax.transData.transform(np.column_stack([xs_t, ys_t]))   # píxeles
        # El realce va ANTES del achique por solape, para que el algoritmo
        # proteja a los vecinos del tamaño con el que la Exportación se dibuja
        # de verdad, no del que tenía sin realzar.
        R0 = np.array([max(1.5, n.get("r", 5)) * REALCE_ETAPA.get(n["E"], 1.0)
                       for n in nodos]) * ft * .85
        Rpx = R0 * dpi_fig / 72.0                    # radio nominal, en píxeles
        # Hasta dónde llega REALMENTE cada símbolo dibujado (un rombo sobresale
        # 1.371× su radio nominal). Es lo que hay que comparar para saber si dos
        # se tapan.
        EXT = np.array([EXTENT.get(FORMA.get(n.get("forma", "circle"), "o"), 1.0)
                        for n in nodos])
        MARGEN = 2.4 * dpi_fig / 72.0                # anillo mínimo visible
        PISO = 0.45                                  # nunca por debajo del 45 %

        colocados = []
        for i in np.argsort(-Rpx):
            if nodos[i]["E"] in ENCIMA:
                colocados.append(i)                   # participa, pero no se achica
                continue
            for j in colocados:
                d = float(np.hypot(*(P[i] - P[j])))
                if d >= Rpx[j] * EXT[j] + Rpx[i] * EXT[i]:
                    continue                          # no se tocan
                # Para que j asome, su borde debe quedar fuera del de i:
                #   (d + Rpx[j]·EXT[j]) - Rpx[i]·EXT[i] >= MARGEN
                lim = (d + Rpx[j] * EXT[j] - MARGEN) / EXT[i]
                if Rpx[i] > lim:
                    Rpx[i] = max(R0[i] * dpi_fig / 72.0 * PISO, lim)
            colocados.append(i)
        R = Rpx * 72.0 / dpi_fig                      # de vuelta a puntos

        n_ach = int((Rpx < R0 * dpi_fig / 72.0 - .5).sum())
        if n_ach:
            print(f"  · {n_ach} de {len(nodos)} nodos achicados por solape")

        # Apilado: el grande abajo, el chico encima, según el tamaño FINAL.
        # 12 niveles discretos para no hacer un scatter por nodo, y desempate
        # determinista por etapa (si no, dos etapas del mismo tamaño se turnaban
        # al azar cuál tapaba a cuál entre corridas).
        rmin, rmax = float(R.min()), float(R.max())
        NIV = 12

        def nivel(r):
            t = 0.0 if rmax <= rmin else (r - rmin) / (rmax - rmin)
            return round((1 - t) * (NIV - 1))          # 0 = el más grande

        grupos = {}
        for idx, nd in enumerate(nodos):
            grupos.setdefault((nd["E"], nivel(R[idx])), []).append((idx, nd))

        vistas = {}
        for (etapa, niv), lista in sorted(
                grupos.items(),
                key=lambda kv: (kv[0][1],
                                -ORDEN_ETAPAS.index(kv[0][0])
                                if kv[0][0] in ORDEN_ETAPAS else 0)):
            col = lista[0][1].get("color", "#666")
            mk = FORMA.get(lista[0][1].get("forma", "circle"), "o")
            idxs = [i for i, _ in lista]
            # matplotlib toma el tamaño del marcador como ÁREA en pt²: se pasa
            # el RADIO a diámetro en puntos y se eleva al cuadrado. El divisor
            # por forma deja TODOS los marcadores inscriptos en el mismo radio,
            # sea círculo, rombo, cuadrado o estrella.
            # Normalización por ÁREA (la de matplotlib): ver EXTENT arriba.
            s = (R[idxs] * 2) ** 2
            # Se dibuja en coordenadas de DATOS, no de display: los píxeles solo
            # sirvieron para medir el solape. Con `transform=None` el PDF —que es
            # vectorial y usa 72 dpi internos— habría colocado los nodos en
            # cualquier lado.
            z = Z_ENCIMA if etapa in ENCIMA else 9 + niv * .04
            ax.scatter(xs_t[idxs], ys_t[idxs], s=s, marker=mk, c=col,
                       edgecolors="white", linewidths=max(.7, 1.3 * ft),
                       zorder=z)
            v = vistas.setdefault(etapa, [col, mk, 0])
            v[2] += len(idxs)
        for etapa, (col, mk, n) in vistas.items():
            leyenda["etapas"].append((etapa, col, mk, n))

    # ── Centros poblados ───────────────────────────────────────────────────
    ccp = capas.get("ccpp_p") or []
    if ccp:
        xs, ys = proyectar_puntos([c["x"] for c in ccp], [c["y"] for c in ccp], crs)
        ax.scatter(xs, ys, s=7, marker="o", c="#FFD54F", edgecolors="#4FC3F7",
                   linewidths=.35, alpha=.9, zorder=10)
        leyenda["extras"].append(Line2D([], [], marker="o", ls="", color="#FFD54F",
                                        markeredgecolor="#4FC3F7", markersize=5,
                                        label=f"Centro poblado ({len(ccp)})"))

    # ── Etiquetas ──────────────────────────────────────────────────────────
    if op.get("etiquetas_nodos"):
        vis = [n for n in nodos if n.get("lbl")]
        for n in vis:
            x, y = proyectar_puntos([n["x"]], [n["y"]], crs)
            ax.annotate(n["nom"], (x[0], y[0]), fontsize=6.5, ha="left",
                        va="center", xytext=(4, 0), textcoords="offset points",
                        color="#1A1A1A", zorder=13,
                        path_effects=[pe.withStroke(linewidth=2, foreground="white")])

    if op.get("etiquetas_dptos"):
        cand = [(dp["nombre"], dp["lbl"]) for dp in deptos
                if dp.get("sel") and dp.get("lbl")]
        if cand:
            xs, ys = proyectar_puntos([c[1][0] for c in cand],
                                      [c[1][1] for c in cand], crs)
            # Rótulo plano: gris, sin negrita, sin halo ni recuadro, y DEBAJO de
            # rutas y nodos (zorder 4.9, apenas por encima de los límites y del
            # ráster del índice). Antes iban en blanco con contorno oscuro y a
            # zorder 14: se leían como una etiqueta pegada encima del mapa y le
            # robaban protagonismo a la cadena, que es el tema. Así son
            # tipografía de fondo — se leen, no compiten, y una ruta que pasa
            # por arriba las tapa, que es lo correcto.
            for nom, x, y in sin_solapar(ax, [(c[0], x, y) for c, x, y
                                              in zip(cand, xs, ys)], 12):
                ax.annotate(nom, (x, y), fontsize=12, ha="center", va="center",
                            color="#8A97A3", zorder=4.9)

    return leyenda


def sin_solapar(ax, items, fontsize):
    """Filtra etiquetas que se pisan, quedándose con la primera de cada choque.

    Los rótulos de departamento vienen de un punto fijo por polígono (`d.lbl`),
    así que en la costa norte —donde Piura, Lambayeque y Tumbes se apretujan—
    salían encimados e ilegibles. Se estima la caja de cada texto en píxeles de
    salida y se descarta la que invade una ya colocada. Es deliberadamente
    simple: no reubica, solo omite, que en un mapa es preferible a dos nombres
    superpuestos.
    """
    dpi = ax.figure.dpi
    ancho_car = fontsize * .58 * dpi / 72.0     # ancho medio de carácter, en px
    alto = fontsize * 1.25 * dpi / 72.0
    puestos, salida = [], []
    for nom, x, y in items:
        px, py = ax.transData.transform((x, y))
        w = len(nom) * ancho_car
        caja = (px - w / 2, py - alto / 2, px + w / 2, py + alto / 2)
        if any(not (caja[2] < o[0] or caja[0] > o[2] or
                    caja[3] < o[1] or caja[1] > o[3]) for o in puestos):
            continue
        puestos.append(caja)
        salida.append((nom, x, y))
    return salida


# ════════════════════════════════════════════════════════════════════════════
# MARCO CARTOGRÁFICO
# ════════════════════════════════════════════════════════════════════════════

def dibujar_grilla(ax, crs, bbox):
    """Grilla de coordenadas geográficas sobre el mapa proyectado."""
    w, s, e, n = bbox
    tr = pyproj.Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x0, x1 = ax.get_xlim(); y0, y1 = ax.get_ylim()

    plon = paso_lindo((e - w) / 5.0)
    plat = paso_lindo((n - s) / 5.0)
    lons = np.arange(math.ceil(w / plon) * plon, e + 1e-9, plon)
    lats = np.arange(math.ceil(s / plat) * plat, n + 1e-9, plat)

    def fmt(v, pos):
        """Grados con minutos cuando el paso es menor a 1° (formato cartopy)."""
        g = abs(v)
        # El ecuador y Greenwich no llevan hemisferio: "0°", no "0°N".
        h = "" if g < 1e-9 else (pos[0] if v > 0 else pos[1])
        if min(plon, plat) >= 1:
            return f"{g:.0f}°{h}"
        ent = int(g)
        mins = (g - ent) * 60
        return f"{ent}°{mins:04.1f}'{h}" if mins >= .05 else f"{ent}°{h}"

    # Etiquetas FUERA del marco (izquierda y abajo) con una marca corta sobre el
    # borde, que es el patrón del gridliner de cartopy. Se hace a mano porque
    # cartopy no tiene wheel para esta versión de Python y arrastrarlo con
    # compilación de GEOS/PROJ no se justifica por un graticule.
    # La grilla va POR ENCIMA de las capas del mapa (zorder 18: sobre índice,
    # rutas, nodos y etiquetas; debajo de brújula/escala/leyendas). Es la
    # convención del graticule: una referencia que cruza el mapa entero, no un
    # fondo que las capas van tapando a pedazos.
    tick = min(x1 - x0, y1 - y0) * .008
    for lon in lons:
        x, _ = tr.transform(lon, (s + n) / 2)
        if not (x0 < x < x1):
            continue
        ax.axvline(x, color="#6C7885", lw=.55, ls=(0, (1, 3)), alpha=.8, zorder=18)
        ax.plot([x, x], [y0, y0 + tick], color="#1F2933", lw=1.0,
                zorder=24, clip_on=False)
        ax.annotate(fmt(lon, "EO"), (x, y0), fontsize=FS_COORD, ha="center",
                    va="top", xytext=(0, -7), textcoords="offset points",
                    color="#1F2933", annotation_clip=False)
    for lat in lats:
        _, y = tr.transform((w + e) / 2, lat)
        if not (y0 < y < y1):
            continue
        ax.axhline(y, color="#6C7885", lw=.55, ls=(0, (1, 3)), alpha=.8, zorder=18)
        ax.plot([x0, x0 + tick], [y, y], color="#1F2933", lw=1.0,
                zorder=24, clip_on=False)
        # Horizontales, no rotadas: giradas 90° obligaban a torcer la cabeza.
        ax.annotate(fmt(lat, "NS"), (x0, y), fontsize=FS_COORD, ha="right",
                    va="center", xytext=(-7, 0), textcoords="offset points",
                    color="#1F2933", annotation_clip=False)


def dibujar_norte(ax):
    """Flecha de norte, arriba a la derecha.

    Va arriba y no abajo porque las dos leyendas ocupan las esquinas inferiores;
    la barra de escala, por lo mismo, va al centro del borde inferior.
    """
    x0, x1 = ax.get_xlim(); y0, y1 = ax.get_ylim()
    dx, dy = x1 - x0, y1 - y0
    # Aguja bipartida clásica (mitad oscura, mitad clara) con halo blanco: se
    # lee sobre cualquier fondo SIN recuadro — la caja blanca de antes tapaba
    # el mapa base tanto como lo que pretendía proteger.
    cx = x1 - dx * .052
    h = dy * .085
    w = h * .36
    cy = y1 - dy * .105
    tip = (cx, cy + h / 2)
    notch = (cx, cy - h / 2 + h * .28)      # muesca inferior: silueta de dardo
    izq = [tip, (cx - w, cy - h / 2), notch]
    der = [tip, notch, (cx + w, cy - h / 2)]
    halo = [pe.withStroke(linewidth=3.5, foreground="white")]
    ax.add_patch(MplPolygon(izq, closed=True, facecolor="#1F2933",
                            edgecolor="#1F2933", linewidth=.8, zorder=25,
                            path_effects=halo))
    ax.add_patch(MplPolygon(der, closed=True, facecolor="white",
                            edgecolor="#1F2933", linewidth=.8, zorder=25.1))
    ax.annotate("N", xy=(cx, cy + h / 2 + dy * .008), fontsize=20,
                fontweight="bold", ha="center", va="bottom", color="#1F2933",
                zorder=25.2,
                path_effects=[pe.withStroke(linewidth=3.5, foreground="white")])


def dibujar_escala(ax, crs, bbox):
    """Barra de escala con la longitud medida geodésicamente (ver metros_a_unidades)."""
    x0, x1 = ax.get_xlim(); y0, y1 = ax.get_ylim()
    dx, dy = x1 - x0, y1 - y0
    lon_c, lat_c = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2

    u_por_km = metros_a_unidades(crs, lon_c, lat_c, 1000.0)
    km = paso_lindo((dx * .18) / u_por_km)
    largo = km * u_por_km
    txt = f"{km:g} km" if km >= 1 else f"{km * 1000:g} m"

    sx = x0 + dx / 2 - largo / 2      # centrada: las esquinas son de las leyendas
    sy = y0 + dy * .038
    alto = dy * .006
    # Barra a dos tramos (claro/oscuro), la convención cartográfica clásica.
    for i in range(2):
        a = sx + largo / 2 * i
        ax.add_patch(plt.Rectangle((a, sy), largo / 2, alto, zorder=25,
                                   facecolor="#1F2933" if i == 0 else "white",
                                   edgecolor="#1F2933", linewidth=.8))
    for frac, lab in ((0, "0"), (.5, f"{km / 2:g}"), (1, txt)):
        ax.annotate(lab, (sx + largo * frac, sy + alto * 1.6), fontsize=FS_ESCALA,
                    ha="center", va="bottom", color="#1F2933", zorder=25,
                    fontweight="bold" if frac == 1 else "normal",
                    path_effects=[pe.withStroke(linewidth=2.5, foreground="white")])


def _sep(label):
    """Entrada de leyenda sin símbolo: sirve de encabezado de sección."""
    return Line2D([], [], ls="", marker="", label=label)


def dibujar_leyenda(ax, leyenda, d):
    """Leyenda de la cadena: CULTIVO → RUTAS → ETAPAS, más la del índice.

    Estructurada en secciones, no como una lista plana: primero qué cultivo se
    está mirando, después por qué rutas va y recién ahí los símbolos. Las etapas
    van SIN el conteo — en un mapa impreso el número no aporta (se ven los
    nodos) y competía con el nombre.
    """
    sel = d.get("seleccion", {})
    cultivos = [c for c in (sel.get("cultivos") or []) if c]
    rutas = sel.get("rutas") or []

    handles = []

    # ── RUTAS: las visibles, con el color con que se dibujan ───────────────
    if rutas:
        handles.append(_sep("RUTAS"))
        # Tope para que la leyenda no se coma el mapa cuando hay muchas.
        MAX = 14
        for r in rutas[:MAX]:
            handles.append(Line2D([], [], color=r.get("color", "#555"), lw=3.2,
                                  label=r.get("R", "")))
        if len(rutas) > MAX:
            handles.append(_sep(f"… y {len(rutas) - MAX} rutas más"))

    # ── ETAPAS: sin conteo ─────────────────────────────────────────────────
    if leyenda["etapas"]:
        handles.append(_sep(""))
        handles.append(_sep("ETAPAS"))
        # Tamaño uniforme: en el mapa el radio codifica el CAUDAL, no la etapa,
        # así que un símbolo más grande acá sugeriría una jerarquía que no existe.
        for etapa, col, mk, _n in leyenda["etapas"]:
            # El mismo realce que en el mapa: si la Exportación se dibuja más
            # grande allá, el recuadro tiene que mostrarla igual.
            handles.append(Line2D([], [], marker=mk, ls="", color=col,
                                  markersize=10 * REALCE_ETAPA.get(etapa, 1.0),
                                  markeredgecolor="white", markeredgewidth=1.0,
                                  label=ETIQUETA_ETAPA.get(etapa, etapa)))

    if leyenda["caudal"] or leyenda["extras"]:
        handles.append(_sep(""))
    if leyenda["caudal"]:
        col, lab = leyenda["caudal"]
        handles.append(Line2D([], [], color=col, lw=4, label=lab))
    handles += leyenda["extras"]

    if handles:
        # Título: "CULTIVO CAFÉ" / "CULTIVOS CACAO · CAFÉ · …"
        if len(cultivos) == 1:
            titulo = f"CULTIVO {cultivos[0].upper()}"
        elif cultivos:
            titulo = "CULTIVOS " + " · ".join(c.upper() for c in cultivos)
        else:
            titulo = "CADENA DE VALOR"

        leg = ax.legend(handles=handles, loc="lower left", fontsize=FS_LEG,
                        framealpha=.96, borderpad=1.0, labelspacing=.55,
                        handlelength=1.9, title=titulo,
                        title_fontsize=FS_LEG_TIT, edgecolor="#3A4550")
        leg.set_zorder(26)
        leg.get_frame().set_linewidth(1.0)
        leg.get_title().set_fontweight("bold")
        # Los encabezados de sección van en negrita y con el mismo cuerpo que
        # el título de la leyenda del índice, para que se lean como tales.
        for t in leg.get_texts():
            if t.get_text() in ("RUTAS", "ETAPAS"):
                t.set_fontweight("bold")
                t.set_fontsize(FS_LEG_SEC)
        ax.add_artist(leg)

    idx = d.get("indice")
    if idx:
        h = [Patch(facecolor=c, edgecolor="none", label=f"{k} · {nom}")
             for k, (nom, c) in sorted(PALETA.items(), reverse=True)]
        leg2 = ax.legend(handles=h, loc="lower right", fontsize=FS_LEG,
                         framealpha=.96, borderpad=.9, labelspacing=.45,
                         edgecolor="#3A4550",
                         title=f"{idx.get('sigla', '')} · {idx.get('nombre', '')}",
                         title_fontsize=FS_LEG_SEC)
        leg2.set_zorder(26)
        leg2.get_frame().set_linewidth(1.0)
        leg2.get_title().set_fontweight("bold")


# ════════════════════════════════════════════════════════════════════════════
# COMPOSICIÓN
# ════════════════════════════════════════════════════════════════════════════

def componer(d, salida_base, crs_forzado=None, zoom=None, sin_basemap=False,
             formatos=("png", "pdf"), info=None):
    papel = d.get("papel", {})
    fmt = papel.get("formato", "A3").upper()
    dpi = int(papel.get("dpi", 300))
    mm_w, mm_h = PAPEL_MM.get(fmt, PAPEL_MM["A3"])
    if papel.get("orientacion", "horizontal").lower().startswith("h"):
        mm_w, mm_h = mm_h, mm_w

    bbox = d["vista"]["bbox"]
    crs, crs_lbl = elegir_crs(bbox, crs_forzado)
    print(f"  · papel {fmt} {papel.get('orientacion', 'horizontal')} @ {dpi} dpi")
    print(f"  · proyección: {crs_lbl}")

    fig = plt.figure(figsize=(mm_w / 25.4, mm_h / 25.4), dpi=dpi, facecolor="white")
    # geopandas dispara un draw_idle por cada .plot(), y con backend Agg eso
    # RE-RENDERIZA la figura entera — rasters incluidos — una vez por capa.
    # Medido con cProfile: 76 de 89 s eran esos redibujos intermedios que nadie
    # ve. Se anula draw_idle durante la composición; el único draw que importa
    # es el de savefig.
    _draw_idle = fig.canvas.draw_idle
    fig.canvas.draw_idle = lambda *a, **k: None
    # Márgenes: izquierda y abajo cargan las etiquetas de la grilla (van FUERA
    # del marco). Sin pie, el mapa se estira hacia abajo en vez de dejar una
    # banda blanca.
    con_pie = bool(d.get("opciones", {}).get("creditos", True))
    _b, _h = (.062, .858) if con_pie else (.042, .878)
    ax = fig.add_axes([.068, _b, .872, _h])
    # Fondo NEUTRO. Se probó un celeste de océano (#D8E6F0) para dar aire de
    # atlas cuando no hay teselas, y se descartó: si el basemap no llega a
    # dibujarse —por elección o porque falló la descarga— el mapa entero queda
    # bañado en azul, que es justo lo que no se quiere. El gris claro es
    # invisible bajo las teselas y discreto cuando no las hay.
    ax.set_facecolor("#F2F5F7")

    # Extensión primero: contextily y el marco leen los límites del eje.
    tr = pyproj.Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    (ex0, ex1), (ey0, ey1) = tr.transform([bbox[0], bbox[2]], [bbox[1], bbox[3]])

    # El encuadre del visor casi nunca tiene la proporción de la hoja: con
    # aspect='equal' matplotlib encoge el mapa y deja franjas blancas enormes.
    # Se EXPANDE el lado corto hasta llenar el marco — nunca se recorta, así que
    # el impreso siempre contiene todo lo que estaba en pantalla y un poco más,
    # que es como compone un mapa de imprenta.
    px, py, pw, ph = ax.get_position().bounds
    aspecto_marco = (pw * mm_w) / (ph * mm_h)
    aspecto_dato = (ex1 - ex0) / (ey1 - ey0)
    if aspecto_dato < aspecto_marco:                 # falta ancho
        extra = ((ey1 - ey0) * aspecto_marco - (ex1 - ex0)) / 2
        ex0, ex1 = ex0 - extra, ex1 + extra
    else:                                            # falta alto
        extra = ((ex1 - ex0) / aspecto_marco - (ey1 - ey0)) / 2
        ey0, ey1 = ey0 - extra, ey1 + extra
    # bbox geográfico real del impreso: la grilla y la escala deben leer ESTO,
    # no el encuadre original, o las coordenadas quedarían corridas.
    tinv = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    (bw, be), (bs, bn) = tinv.transform([ex0, ex1], [ey0, ey1])
    bbox = [bw, bs, be, bn]

    ax.set_xlim(ex0, ex1)
    ax.set_ylim(ey0, ey1)
    ax.set_aspect("equal")

    credito_base, base_ok = "", False
    if not sin_basemap:
        credito_base, base_ok = poner_basemap(
            ax, crs, d["vista"].get("basemap", "Claro"), zoom,
            d.get("opciones", {}).get("base_limpio", True))
        ax.set_xlim(ex0, ex1); ax.set_ylim(ey0, ey1)   # contextily toca los límites
    if info is not None:
        info["basemap_ok"] = base_ok

    # Índice MDEA: el .tif original si está, si no el PNG del propio JSON.
    idx = d.get("indice")
    fuente_idx = None
    if idx:
        alpha = float(idx.get("opacidad", .8))
        r = indice_desde_tif(idx, bbox, crs, alpha)
        fuente_idx = "GeoTIFF original"
        if r is None:
            r = indice_desde_b64(idx, crs, alpha)
            fuente_idx = "PNG embebido en la selección"
        if r is not None:
            rgba, extent = r
            ax.imshow(rgba, extent=extent, origin="upper", zorder=2,
                      interpolation="nearest")
            print(f"  · índice {idx.get('sigla')}: {fuente_idx}")
        else:
            print(f"  ! índice {idx.get('sigla')}: sin fuente disponible")
        ax.set_xlim(ex0, ex1); ax.set_ylim(ey0, ey1)

    # Sin teselas dibujadas —por elección o porque la descarga falló— los
    # departamentos se rellenan como tierra, para que el mapa no quede flotando
    # sobre un fondo plano. Se mira el resultado REAL, no la intención: si se
    # pidió basemap y no llegó, igual conviene el relleno.
    con_tiles = base_ok

    ft = escala_trazo(fig)
    leyenda = dibujar_capas(ax, d, crs, d.get("opciones", {}), ft, con_tiles)

    op = d.get("opciones", {})
    if op.get("grilla", True):
        dibujar_grilla(ax, crs, bbox)
    if op.get("norte", True):
        dibujar_norte(ax)
    if op.get("escala", True):
        dibujar_escala(ax, crs, bbox)
    if op.get("leyenda", True):
        dibujar_leyenda(ax, leyenda, d)

    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_edgecolor("#1F2933"); s.set_linewidth(1.2)

    # ── Título y créditos ──────────────────────────────────────────────────
    titulo = d.get("titulo") or "MDEA · Cadenas de valor"
    fig.text(.5, .962, titulo, ha="center", va="center", fontsize=19,
             fontweight="bold", color="#16202A")
    if d.get("subtitulo"):
        fig.text(.5, .934, d["subtitulo"], ha="center", va="center",
                 fontsize=11.5, color="#48535E")

    # Pie: se quitó la línea de "Fuente / Límites / Generado desde…" — el rótulo
    # de cultivo ya vive en la leyenda y la referencia institucional satura el
    # mapa. Queda solo el CRS (dato cartográfico, no crédito) y la ATRIBUCIÓN de
    # las teselas, que NO es opcional: las licencias de CARTO/OSM/Esri la exigen
    # en cualquier mapa que las use. Va en cuerpo chico y gris.
    if op.get("creditos", True):
        pie = f"CRS: {crs_lbl}"
        if credito_base:
            pie += f"   ·   {credito_base}"
        fig.text(.5, .028, pie, ha="center", va="center",
                 fontsize=8, color="#6B7680")

    fig.canvas.draw_idle = _draw_idle
    png = pdf = None
    if "png" in formatos:
        png = salida_base + ".png"
        fig.savefig(png, dpi=dpi, facecolor="white")
    if "pdf" in formatos:
        pdf = salida_base + ".pdf"
        fig.savefig(pdf, facecolor="white")  # PDF: vectorial salvo el ráster
    plt.close(fig)
    return png, pdf


# ════════════════════════════════════════════════════════════════════════════
# SERVIDOR DE RENDER · previsualizar y descargar sin salir del visor
# ════════════════════════════════════════════════════════════════════════════
#
# Con `--servidor`, el visor deja de bajar el JSON: se lo manda acá, recibe una
# previsualización y ofrece PNG/PDF con un clic. Es, además, EXACTAMENTE la
# arquitectura que va a correr el día que esto se ofrezca al público — el mismo
# POST, contra un worker en vez de contra tu máquina. Si el servidor no está
# levantado el visor lo detecta y vuelve solo a descargar el JSON.

def servir(puerto=8765, host="127.0.0.1"):
    import threading, uuid, tempfile
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    trabajos = {}                 # id -> {"png": ruta, "pdf": ruta, "nombre": str}
    tmp = tempfile.mkdtemp(prefix="mdea_export_")
    # matplotlib con backend Agg NO es reentrante: dos renders simultáneos se
    # pisan la figura. El servidor es multi-hilo por comodidad, así que el
    # render va serializado.
    candado = threading.Lock()

    def diagnostico_red():
        """Prueba UNA tesela de cada proveedor y devuelve el error exacto.

        Existe porque cuando el impreso sale sin fondo, desde afuera no hay
        forma de saber por qué: el visor solo dice «no pudo descargar las
        teselas» y el motivo real queda en los logs del contenedor. Medido en
        producción (2026-09-03): fallaban los CUATRO proveedores a la vez —tres
        dominios distintos—, que es la firma de un contenedor sin salida a
        internet, no de un proveedor que cambió de política. Esto lo distingue:
        si el DNS resuelve pero la descarga corta, es egress; si no resuelve,
        es DNS; si vuelve 403, es el proveedor.

        Se consulta con GET /exportador/diagnostico en el sitio publicado.
        """
        import socket, urllib.parse, urllib.request
        out = {"teselas": {}, "dns": {}}
        try:
            import contextily as ctx
            out["contextily"] = getattr(ctx, "__version__", "?")
        except Exception as ex:
            return {"contextily": f"NO SE PUEDE IMPORTAR: {type(ex).__name__}: {ex}",
                    "teselas": {}, "dns": {}}
        z, x, y = 6, 17, 34               # una tesela sobre Perú
        for nombre, (fam, var, _cred) in BASEMAPS.items():
            if fam is None:
                continue                  # 'Blanco' no usa teselas
            try:
                prov = getattr(ctx.providers, fam)
                if var:
                    prov = getattr(prov, var)
                url = prov.build_url(z=z, x=x, y=y)
            except Exception as ex:
                out["teselas"][nombre] = f"no se pudo armar la URL: {type(ex).__name__}: {ex}"
                continue
            host = urllib.parse.urlsplit(url).hostname
            if host not in out["dns"]:
                try:
                    out["dns"][host] = socket.gethostbyname(host)
                except Exception as ex:
                    out["dns"][host] = f"{type(ex).__name__}: {ex}"
            try:
                pedido = urllib.request.Request(url, headers={"User-Agent": "mdea-export"})
                with urllib.request.urlopen(pedido, timeout=12) as r:
                    out["teselas"][nombre] = f"HTTP {r.status} · {len(r.read())} bytes"
            except Exception as ex:
                out["teselas"][nombre] = f"{type(ex).__name__}: {ex}"
        out["ok"] = all(str(v).startswith("HTTP 200") for v in out["teselas"].values())
        return out

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass                  # el servidor imprime lo suyo, no el access log

        def _cors(self):
            # El visor se abre con file:// → Origin "null". Se permite todo:
            # esto escucha solo en localhost y sirve para uso local.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def _json(self, obj, code=200):
            cuerpo = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self._cors()
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            if self.path == "/ping":
                return self._json({"ok": True, "servicio": "mdea-export", "schema": SCHEMA})
            if self.path == "/diagnostico":
                return self._json(diagnostico_red())
            if self.path.startswith("/descargar/"):
                nombre = self.path.rsplit("/", 1)[-1]
                jid, _, ext = nombre.rpartition(".")
                t = trabajos.get(jid)
                if not t or ext not in ("png", "pdf"):
                    return self._json({"error": "no encontrado"}, 404)
                # Render completo diferido: la primera descarga lo genera (PNG y
                # PDF juntos, comparten la figura); las siguientes salen del caché.
                if not t.get(ext):
                    print(f"  → render completo {jid}")
                    try:
                        with candado:
                            if not t.get("png"):
                                t["png"], t["pdf"] = componer(t["sel"], t["base"])
                    except Exception as ex:
                        import traceback; traceback.print_exc()
                        return self._json({"error": f"{type(ex).__name__}: {ex}"}, 500)
                datos = open(t[ext], "rb").read()
                self.send_response(200)
                self.send_header("Content-Type",
                                 "image/png" if ext == "png" else "application/pdf")
                self.send_header("Content-Length", str(len(datos)))
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{t["nombre"]}.{ext}"')
                self._cors()
                self.end_headers()
                self.wfile.write(datos)
                return
            self._json({"error": "ruta desconocida"}, 404)

        def do_POST(self):
            if self.path != "/render":
                return self._json({"error": "ruta desconocida"}, 404)
            try:
                n = int(self.headers.get("Content-Length", 0))
                d = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception as ex:
                return self._json({"error": f"JSON inválido: {ex}"}, 400)
            if not str(d.get("schema", "")).startswith("mdea.export/"):
                return self._json({"error": "schema no reconocido"}, 400)

            jid = uuid.uuid4().hex[:12]
            sello = datetime.now().strftime("%Y%m%d_%H%M")
            base = os.path.join(tmp, jid)
            print(f"  → preview {jid} · {d.get('papel', {}).get('formato')} "
                  f"{d.get('papel', {}).get('orientacion')}")
            # La PREVIEW se renderiza a 140 dpi y solo PNG: ~4.6× menos píxeles
            # que 300 dpi y sin el costo del PDF. El render completo se difiere
            # a la descarga. Lo ÚNICO que cambia es el dpi — las teselas son las
            # mismas (mismo zoom) y quedan cacheadas, así que la descarga
            # muestra exactamente lo que se previsualizó.
            prev = dict(d)
            prev["papel"] = dict(d.get("papel") or {})
            prev["papel"]["dpi"] = min(140, int(prev["papel"].get("dpi", 300)))
            info = {}
            try:
                with candado:
                    png_prev, _ = componer(prev, base + "_prev",
                                           formatos=("png",), info=info)
            except Exception as ex:
                import traceback; traceback.print_exc()
                return self._json({"error": f"{type(ex).__name__}: {ex}"}, 500)

            # Se guarda la SELECCIÓN, no el resultado: el png/pdf a resolución
            # completa se renderizan recién si alguien los pide.
            trabajos[jid] = {"sel": d, "base": base, "png": None, "pdf": None,
                             "nombre": f"mapa_{sello}"}
            from PIL import Image
            im = Image.open(png_prev).convert("RGB")
            im.thumbnail((1400, 1400))
            buf = io.BytesIO(); im.save(buf, format="JPEG", quality=88)
            return self._json({
                "id": jid,
                "preview": "data:image/jpeg;base64," +
                           base64.b64encode(buf.getvalue()).decode(),
                "nombre": trabajos[jid]["nombre"],
                # Se informa al visor si el mapa base llegó a dibujarse. Sin
                # esto, un contenedor sin salida a internet devuelve mapas con
                # el fondo pelado y no hay forma de saber por qué sin mirar los
                # logs del servidor.
                "basemap_ok": bool(info.get("basemap_ok")),
                "basemap": d.get("vista", {}).get("basemap"),
            })

    srv = ThreadingHTTPServer((host, puerto), H)
    print(f"╭─ Servidor de mapas MDEA")
    print(f"│  escuchando en  http://{host}:{puerto}")
    print(f"│  temporales en  {tmp}")
    print(f"│  Abrí el visor y usá «Exportar mapa» → Previsualizar.")
    print(f"╰─ Ctrl+C para parar")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  servidor detenido")


# ════════════════════════════════════════════════════════════════════════════

def main():
    # La consola de Windows arranca en cp1252 y revienta con '→' o '·'. Se fuerza
    # UTF-8 acá para no depender de `chcp 65001` ni de PYTHONIOENCODING.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        description="Convierte la selección del visor MDEA en un mapa de imprenta.")
    ap.add_argument("json", nargs="?", help="seleccion_*.json que descargó el visor")
    ap.add_argument("--servidor", action="store_true",
                    help="modo servicio: el visor previsualiza y descarga sin bajar el JSON")
    ap.add_argument("--puerto", type=int, default=8765, help="puerto del servidor (8765)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="interfaz donde escuchar. 127.0.0.1 por defecto: en el "
                         "contenedor nginx proxea desde adentro, no hace falta exponerlo")
    ap.add_argument("-o", "--salida", help="ruta de salida sin extensión")
    ap.add_argument("--crs", help="forzar CRS (p.ej. EPSG:32718). Por defecto: automático")
    ap.add_argument("--zoom", type=int, help="zoom de teselas del basemap")
    ap.add_argument("--sin-basemap", action="store_true", help="no bajar teselas")
    a = ap.parse_args()

    if a.servidor:
        return servir(a.puerto, a.host)
    if not a.json:
        sys.exit("Falta el JSON. Usá `--servidor` para el modo servicio "
                 "(previsualizar y descargar desde el visor).")
    if not os.path.exists(a.json):
        sys.exit(f"No existe: {a.json}")
    with open(a.json, encoding="utf-8") as f:
        d = json.load(f)

    esquema = d.get("schema", "")
    if not esquema.startswith("mdea.export/"):
        sys.exit(f"JSON no reconocido (schema='{esquema}'). ¿Es una selección del visor?")

    base = a.salida or os.path.splitext(a.json)[0].replace("seleccion", "mapa")
    print(f"→ {os.path.basename(a.json)}")
    png, pdf = componer(d, base, a.crs, a.zoom, a.sin_basemap)
    for p in (png, pdf):
        print(f"  ✓ {p}  ({os.path.getsize(p) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

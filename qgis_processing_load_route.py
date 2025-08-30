"""
QGIS Processing script: Load route from remote API (LV95 / EPSG:2056, styled red 1pt)

- Klickpunkte werden fuer die API nach LV95 gebracht (falls noetig).
- API erwartet/liefern LV95.
- OUTPUT wird geschrieben; Style (rot, 1 pt) wird als Default gespeichert:
  - GPKG: eingebetteter Default-Style (wird automatisch geladen)
  - sonst: .qml Sidecar neben der Datei
"""

import json
import os
import tempfile
import urllib.request
import urllib.error

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterPoint,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorDestination,
    QgsProcessingException,
    QgsVectorLayer,
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsVectorFileWriter,
    QgsLineSymbol,
    QgsUnitTypes,
)

class LoadRouteAlgorithm(QgsProcessingAlgorithm):
    START = "START_POINT"
    END = "END_POINT"
    URL = "API_URL"
    K = "K_ALTS"
    OUTPUT = "OUTPUT"

    def name(self): return "load_route_from_api"
    def displayName(self): return "Load route from routing API"
    def group(self): return "Routing"
    def groupId(self): return "routing"

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterPoint(self.START, "Start point"))
        self.addParameter(QgsProcessingParameterPoint(self.END, "End point"))
        self.addParameter(QgsProcessingParameterString(
            self.URL, "Routing API URL",
            defaultValue="https://school-route-api.onrender.com/route",
        ))
        self.addParameter(QgsProcessingParameterString(
            self.K, "k (number of alternatives)", defaultValue="3"
        ))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUTPUT, "Output (LV95 / EPSG:2056)"
        ))

    def processAlgorithm(self, parameters, context, feedback):
        start_pt = self.parameterAsPoint(parameters, self.START, context)
        end_pt   = self.parameterAsPoint(parameters, self.END, context)
        url      = self.parameterAsString(parameters, self.URL, context)
        k_str    = self.parameterAsString(parameters, self.K, context) or "3"
        dest_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)

        try:
            k = int(k_str)
        except Exception:
            raise QgsProcessingException("Parameter k muss eine ganze Zahl sein")

        # --- LV95 sicherstellen fuer die API ---
        lv95 = QgsCoordinateReferenceSystem("EPSG:2056")
        project_crs = QgsProject.instance().crs()
        if not project_crs.isValid():
            project_crs = lv95
        if project_crs != lv95:
            ct_to_lv95 = QgsCoordinateTransform(project_crs, lv95, QgsProject.instance().transformContext())
            start_lv95 = ct_to_lv95.transform(start_pt)
            end_lv95   = ct_to_lv95.transform(end_pt)
        else:
            start_lv95, end_lv95 = start_pt, end_pt

        payload = {
            "start": [start_lv95.x(), start_lv95.y()],  # E, N (LV95)
            "end":   [end_lv95.x(),   end_lv95.y()],
            "k": k,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

        # --- API call ---
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                geojson_str = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise QgsProcessingException(f"HTTP-Fehler {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise QgsProcessingException(f"URL-Fehler: {e.reason}")
        except Exception as e:
            raise QgsProcessingException(f"Unerwarteter API-Fehler: {e}")

        # --- Temp-GeoJSON schreiben ---
        tmp_geojson = tempfile.NamedTemporaryFile(suffix=".geojson", delete=False)
        tmp_geojson.write(geojson_str.encode("utf-8")); tmp_geojson.flush(); tmp_geojson.close()
        tmp_path = tmp_geojson.name

        # --- Laden (intern) und CRS erzwingen ---
        src_layer = QgsVectorLayer(tmp_path, "route_lv95_raw", "ogr")
        if not src_layer.isValid():
            try: os.unlink(tmp_path)
            except Exception: pass
            raise QgsProcessingException("GeoJSON aus API konnte nicht geladen werden")
        if src_layer.crs() != lv95:
            src_layer.setCrs(lv95)

        # --- Ziel-Driver bestimmen (Default GPKG, empfohlen) ---
        driver = None
        _, ext = os.path.splitext(dest_path or "")
        ext = (ext or "").lower()
        if ext in (".geojson", ".json"):
            driver = "GeoJSON"
        elif ext == ".gpkg":
            driver = "GPKG"
        elif ext == ".shp":
            driver = "ESRI Shapefile"
        else:
            if not dest_path:
                dest_path = tempfile.mktemp(suffix=".gpkg")
            driver = "GPKG"
            if not ext and not dest_path.lower().endswith(".gpkg"):
                dest_path = f"{dest_path}.gpkg"

        save_opts = QgsVectorFileWriter.SaveVectorOptions()
        save_opts.driverName = driver
        save_opts.fileEncoding = "UTF-8"

        # --- Schreiben ---
        write_ret = QgsVectorFileWriter.writeAsVectorFormatV2(
            src_layer, dest_path, QgsProject.instance().transformContext(), save_opts
        )
        result_code, error_msg = write_ret[0], (write_ret[1] if len(write_ret) > 1 else "")

        # Temp aufraeumen
        try: os.unlink(tmp_path)
        except Exception: pass

        if result_code != QgsVectorFileWriter.NoError:
            raise QgsProcessingException(f"Schreiben fehlgeschlagen (LV95): {error_msg}")

        # --- Style dauerhaft speichern: Rot, 1 pt (≈ 0.3528 mm) ---
        # Wichtig: Style als Default in die Datenquelle schreiben (GPKG),
        #          sonst .qml als Sidecar erzeugen.
        out_layer = QgsVectorLayer(dest_path, "route_lv95", "ogr")
        feedback.pushInfo(f"Output (LV95 / EPSG:2056): {dest_path}")
        return {self.OUTPUT: dest_path}

    def createInstance(self):
        return LoadRouteAlgorithm()

def classFactory(iface):
    return LoadRouteAlgorithm()

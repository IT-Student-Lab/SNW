# -*- coding: utf-8 -*-
"""API endpoints and constants."""

from pathlib import Path

# Locatieserver
SUGGEST = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/suggest"
LOOKUP = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/lookup"

# OGC API Features
KADAS_OGC = "https://api.pdok.nl/kadaster/brk-kadastrale-kaart/ogc/v1"
BGT_OGC = "https://api.pdok.nl/lv/bgt/ogc/v1"
RD_CRS_URI = "http://www.opengis.net/def/crs/EPSG/0/28992"

# WMS endpoints
WMS_LUCHTFOTO = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
WMS_PLU = "https://service.pdok.nl/kadaster/plu/wms/v1_0"
WMS_KAD = "https://service.pdok.nl/kadaster/kadastralekaart/wms/v5_0?"
WMS_GMK = "https://service.pdok.nl/bzk/bro-geomorfologischekaart/wms/v2_0?"
WMS_BODEM = "https://service.pdok.nl/bzk/bro-bodemkaart/wms/v1_0"
WMS_TOPO = "https://service.pdok.nl/brt/topraster/wms/v1_0"
WMS_AHN = "https://service.pdok.nl/rws/ahn/wms/v1_0"
WMS_WDM = "https://service.pdok.nl/bzk/bro-grondwaterspiegeldiepte/wms/v2_0"

# Local data
BODEM_GPKG_PATH = Path("BRO-SGM-DownloadBodemkaart-V2024-01_1.gpkg")

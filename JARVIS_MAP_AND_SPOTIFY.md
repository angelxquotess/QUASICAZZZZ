# 🛰️ Mark XXXIX-OR — JARVIS MAP & SPOTIFY UPDATE

Questo update integra in **Mark-XXXIX-OR** due novità prese (e adattate)
dal progetto `ieieeee`:

1. **Spotify Desktop control** — riproduzione reale del client Spotify
   (anche con account FREE) via `WM_APPCOMMAND` + matching intelligente
   delle canzoni.
2. **JARVIS Tactical Map** — un nuovo tool che apre una mappa 2D stile
   JARVIS HUD con descrizione live della destinazione.

---

## 🎵 Spotify

File aggiunti dalla repo `ieieeee` (root del progetto):

```
spotify_api.py
spotify_setup.py
spotify_credentials.txt   # contiene client_id + client_secret
```

Wrapper aggiunto in `actions/spotify_control.py` e registrato come tool
in `main.py`.

### Comandi vocali esempio

```
Jarvis, play Bohemian Rhapsody
Jarvis, metti Imagine Dragons
Jarvis, pausa
Jarvis, riprendi
Jarvis, prossima canzone
Jarvis, canzone precedente
Jarvis, cosa stai suonando?
```

> Premium **non richiesto**. Funziona con account Spotify FREE.
> Su macOS/Linux i comandi rispondono ma il controllo WM_APPCOMMAND è
> Windows-only (degrado grazioso).

---

## 🗺️ JARVIS Map

Nuovo tool in `actions/jarvis_map.py`. Apre nel browser un HTML
auto-contenuto con:

- mappa **Leaflet + OpenStreetMap** filtrata in stile **dark JARVIS-blue**
- overlay HUD con **angoli, griglia, scanline animata, corners luminosi**
- pannello sinistro: **lat/lon, meteo live, popolazione, breve storia**
- pannello destro: **POI vicini** (Wikipedia geosearch) cliccabili —
  la mappa zooma sul punto e apre il popup

### Fonti dati (tutte gratis, nessuna API key)

| Cosa | Servizio |
|---|---|
| Geocoding | Nominatim (OpenStreetMap) |
| Meteo live | Open-Meteo (`current_weather`) |
| Storia/Abitanti | Wikipedia REST `page/summary` |
| POI nei dintorni | Wikipedia `geosearch` |

### Comandi vocali esempio

```
Jarvis, show me New York
Jarvis, mostrami Roma
Jarvis, visualizza Tokyo sulla mappa
Jarvis, open the map of Paris
```

JARVIS apre la mappa **E** descrive brevemente la destinazione a voce
(meteo + estratto storia/abitanti).

---

## File toccati

```
main.py                          → import + tool_decl + dispatcher
actions/spotify_control.py       → NEW
actions/jarvis_map.py            → NEW
spotify_api.py                   → NEW (copiato da ieieeee)
spotify_setup.py                 → NEW (copiato da ieieeee)
spotify_credentials.txt          → NEW (copiato da ieieeee)
JARVIS_MAP_AND_SPOTIFY.md        → NEW (questo file)
```

Nessuna libreria aggiunta a `requirements.txt`: `requests` era già
presente, Leaflet viene caricato via CDN nel browser, tutto il resto
usa la stdlib.

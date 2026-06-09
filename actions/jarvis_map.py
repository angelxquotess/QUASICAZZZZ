# actions/jarvis_map.py
# MARK XXXIX-OR - JARVIS HUD MAP (Native Window Edition)
#
# Apre una finestra Python NATIVA in stile Jarvis (Iron Man) che mostra
# una mappa interattiva centrata sulla citta' richiesta. NIENTE BROWSER.
#
# - Mappa reale interattiva (tkintermapview) con tile dark CartoDB
# - HUD overlay animato stile Jarvis: corner brackets, grid, scan-line,
#   reticolo centrale, anello rotante, blocchi laterali con dati live
# - POI da Wikipedia, meteo da Open-Meteo, riepilogo Wikipedia
# - 100% gratis: nessuna chiave API
#
# Comando vocale tipico:
#   "Jarvis, show me Naples"
#   "Jarvis, mostrami Napoli"

import json
import math
import threading
import urllib.parse
import urllib.request
from datetime import datetime


# ---------------------------------------------------------------
# Module-level state: handle to the currently open JARVIS map
# window so it can be closed remotely when the user issues an
# unrelated command or explicitly says "torna alla schermata
# iniziale".
# ---------------------------------------------------------------
_ACTIVE_WINDOW = {"root": None, "alive": False}


def close_jarvis_map() -> bool:
    """
    Close the currently open JARVIS map window, if any.
    Safe to call from any thread. Returns True if a window was closed.
    """
    info = _ACTIVE_WINDOW
    root = info.get("root")
    if not root or not info.get("alive"):
        return False
    try:
        # Schedule destroy on the Tk thread.
        root.after(0, root.destroy)
    except Exception:
        try:
            root.destroy()
        except Exception:
            pass
    info["alive"] = False
    info["root"] = None
    return True


def is_jarvis_map_open() -> bool:
    """Return True if a JARVIS map window is currently displayed."""
    return bool(_ACTIVE_WINDOW.get("alive"))


# ---------------------------------------------------------------
# HTTP helper (stdlib only)
# ---------------------------------------------------------------
def _http_get_json(url: str, timeout: float = 6.0):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MarkXXXIX-OR-JarvisMap/2.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        return json.loads(data)
    except Exception as e:
        print(f"[jarvis_map] HTTP error: {url[:80]}... -> {e}")
        return None


# ---------------------------------------------------------------
# Geocoding (Nominatim)
# ---------------------------------------------------------------
def _geocode(city: str):
    url = (
        "https://nominatim.openstreetmap.org/search?"
        + urllib.parse.urlencode({"q": city, "format": "json", "limit": 1})
    )
    data = _http_get_json(url) or []
    if not data:
        return None
    item = data[0]
    return {
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
        "display_name": item.get("display_name", city),
    }


# ---------------------------------------------------------------
# Wikipedia summary
# ---------------------------------------------------------------
def _wiki_summary(title: str, lang: str = "en"):
    enc = urllib.parse.quote(title.replace(" ", "_"))
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{enc}"
    data = _http_get_json(url)
    if not data or data.get("type", "").endswith("not_found"):
        return None
    return {
        "title": data.get("title", title),
        "extract": (data.get("extract") or "").strip(),
    }


# ---------------------------------------------------------------
# Wikipedia geosearch - punti di interesse nei dintorni
# ---------------------------------------------------------------
def _wiki_pois(lat: float, lon: float, radius_m: int = 10000, limit: int = 10, lang: str = "en"):
    url = (
        f"https://{lang}.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode({
            "action": "query",
            "list": "geosearch",
            "gscoord": f"{lat}|{lon}",
            "gsradius": radius_m,
            "gslimit": limit,
            "format": "json",
        })
    )
    data = _http_get_json(url) or {}
    items = ((data.get("query") or {}).get("geosearch")) or []
    return [
        {"title": it.get("title"), "lat": it.get("lat"), "lon": it.get("lon")}
        for it in items
    ]


# ---------------------------------------------------------------
# Open-Meteo current weather (no key)
# ---------------------------------------------------------------
def _weather(lat: float, lon: float):
    url = (
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "current_weather": "true",
        })
    )
    data = _http_get_json(url) or {}
    cw = data.get("current_weather") or {}
    if not cw:
        return None
    return {
        "temperature": cw.get("temperature"),
        "windspeed": cw.get("windspeed"),
        "weathercode": cw.get("weathercode"),
    }


def _short_extract(text: str, max_chars: int = 280) -> str:
    if not text:
        return "No data available for this target."
    text = text.replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(".", 1)[0]
    return cut + "..."


# ---------------------------------------------------------------
# JARVIS HUD WINDOW
# ---------------------------------------------------------------
def _open_jarvis_window(city: str, geo: dict, weather, wiki, pois: list):
    """
    Build a native Tk window with a real interactive map + animated
    Jarvis-style HUD overlay drawn on top via a transparent Canvas.
    """
    import tkinter as tk
    from tkinter import font as tkfont

    try:
        import tkintermapview
    except ImportError:
        print("[jarvis_map] tkintermapview not installed. Run: pip install tkintermapview")
        return False

    # ---- Palette JARVIS ----
    HUD       = "#4cf0ff"
    HUD_DIM   = "#1f8a99"
    HUD_DEEP  = "#0a3a44"
    AMBER     = "#ffb84a"
    BG        = "#02060a"
    PANEL_BG  = "#031018"
    TEXT      = "#cdeeff"

    root = tk.Tk()
    root.title(f"JARVIS // TACTICAL MAP - {city.upper()}")
    root.configure(bg=BG)
    W, H = 1280, 780
    root.geometry(f"{W}x{H}")
    root.minsize(900, 600)

    # Register this window as the active JARVIS map window so it
    # can be closed remotely when the user issues an unrelated
    # command.
    # If a previous window is still alive, close it first.
    prev = _ACTIVE_WINDOW.get("root")
    if prev is not None:
        try:
            prev.after(0, prev.destroy)
        except Exception:
            pass
    _ACTIVE_WINDOW["root"] = root
    _ACTIVE_WINDOW["alive"] = True

    # Fonts
    mono_big   = tkfont.Font(family="Consolas", size=14, weight="bold")
    mono_mid   = tkfont.Font(family="Consolas", size=10, weight="bold")
    mono_small = tkfont.Font(family="Consolas", size=9)
    mono_tiny  = tkfont.Font(family="Consolas", size=8)

    # ---- Map widget (dark tiles, free) ----
    map_widget = tkintermapview.TkinterMapView(
        root, width=W, height=H, corner_radius=0
    )
    map_widget.place(x=0, y=0, relwidth=1, relheight=1)
    # CartoDB Dark Matter - free dark tiles, perfect for HUD aesthetic
    map_widget.set_tile_server(
        "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
        max_zoom=20,
    )
    map_widget.set_position(geo["lat"], geo["lon"])
    map_widget.set_zoom(12)

    # Primary target marker
    map_widget.set_marker(
        geo["lat"], geo["lon"],
        text=city.upper(),
        marker_color_circle=AMBER,
        marker_color_outside=AMBER,
        text_color=AMBER,
        font=("Consolas", 10, "bold"),
    )
    # POI markers
    for p in (pois or [])[:10]:
        try:
            map_widget.set_marker(
                p["lat"], p["lon"],
                text=(p["title"] or "")[:24],
                marker_color_circle=HUD,
                marker_color_outside=HUD_DIM,
                text_color=HUD,
                font=("Consolas", 8),
            )
        except Exception:
            pass

    # ---- HUD overlay canvas (transparent to mouse via wm_attributes? not portable)
    # Instead we draw HUD only in the borders/corners so the map stays clickable.
    hud = tk.Canvas(root, bg=BG, highlightthickness=0, bd=0)
    hud.place(x=0, y=0, relwidth=1, relheight=1)
    # Make canvas not block map: lower it then bring only frame elements above.
    # Simpler approach: keep HUD over the map but only paint borders/panels.
    # We delete center area by drawing panels only.

    # We'll keep HUD on top but only draw panels at edges; map needs to receive
    # clicks in the middle area. Trick: put map ABOVE hud for middle, but we
    # need HUD visible. Compromise: place HUD frames as separate widgets.
    hud.destroy()

    # ---- Edge HUD frames (drawn as tk widgets so map remains interactive) ----

    # Top bar
    topbar = tk.Frame(root, bg=BG, height=48)
    topbar.place(x=0, y=0, relwidth=1)
    top_canvas = tk.Canvas(topbar, bg=BG, height=48, highlightthickness=0, bd=0)
    top_canvas.pack(fill="both", expand=True)
    top_canvas.create_line(0, 47, 9999, 47, fill=HUD, width=1)
    top_canvas.create_text(
        24, 24, anchor="w", text="J . A . R . V . I . S",
        fill=HUD, font=mono_big,
    )
    top_canvas.create_text(
        180, 28, anchor="w", text="TACTICAL CARTOGRAPHY MODULE",
        fill=HUD_DIM, font=mono_tiny,
    )
    # right side: clock
    clock_text_id = top_canvas.create_text(
        W - 24, 18, anchor="e", text="--:--:--",
        fill=HUD, font=mono_mid,
    )
    date_text_id = top_canvas.create_text(
        W - 24, 36, anchor="e", text="",
        fill=HUD_DIM, font=mono_tiny,
    )

    # Bottom bar
    botbar = tk.Frame(root, bg=BG, height=32)
    botbar.place(x=0, rely=1.0, y=-32, relwidth=1)
    bot_canvas = tk.Canvas(botbar, bg=BG, height=32, highlightthickness=0, bd=0)
    bot_canvas.pack(fill="both", expand=True)
    bot_canvas.create_line(0, 0, 9999, 0, fill=HUD, width=1)
    bot_canvas.create_text(
        24, 16, anchor="w",
        text="MARK . XXXIX . OR   //   STATUS: ONLINE",
        fill=HUD_DIM, font=mono_tiny,
    )
    scan_x_id = bot_canvas.create_line(
        0, 16, 60, 16, fill=AMBER, width=2,
    )

    # ---- Left INFO panel ----
    INFO_W = 300
    info_frame = tk.Frame(root, bg=PANEL_BG, highlightthickness=1,
                          highlightbackground=HUD, highlightcolor=HUD)
    info_frame.place(x=16, y=64, width=INFO_W, height=H - 130)

    info_canvas = tk.Canvas(info_frame, bg=PANEL_BG, highlightthickness=0, bd=0)
    info_canvas.pack(fill="both", expand=True, padx=14, pady=12)

    title = (wiki or {}).get("title") or city
    info_canvas.create_text(
        0, 0, anchor="nw", text=title.upper(),
        fill=HUD, font=mono_big,
    )
    info_canvas.create_text(
        0, 28, anchor="nw", text="PRIMARY TARGET // SCANNING",
        fill=HUD_DIM, font=mono_tiny,
    )

    def _row(canvas, y, label, value):
        canvas.create_text(0, y, anchor="nw", text=label, fill=HUD_DIM, font=mono_small)
        canvas.create_text(INFO_W - 32, y, anchor="ne", text=str(value), fill=HUD, font=mono_small)
        canvas.create_line(0, y + 18, INFO_W - 32, y + 18, fill=HUD_DEEP, dash=(2, 3))

    y = 60
    _row(info_canvas, y, "LAT", f"{geo['lat']:.5f}")
    y += 24
    _row(info_canvas, y, "LON", f"{geo['lon']:.5f}")
    y += 24
    if weather and weather.get("temperature") is not None:
        _row(info_canvas, y, "TEMP", f"{weather['temperature']} C")
        y += 24
    if weather and weather.get("windspeed") is not None:
        _row(info_canvas, y, "WIND", f"{weather['windspeed']} km/h")
        y += 24
    _row(info_canvas, y, "POI", f"{len(pois or [])}")
    y += 24
    _row(info_canvas, y, "STATUS", "LOCKED")
    y += 32

    extract = _short_extract((wiki or {}).get("extract") or "")
    # word-wrap manually
    info_canvas.create_text(
        0, y, anchor="nw", text="INTEL", fill=HUD_DIM, font=mono_tiny,
    )
    y += 16
    info_canvas.create_text(
        0, y, anchor="nw", text=extract,
        fill=TEXT, font=mono_small, width=INFO_W - 32,
    )

    # ---- Right POI panel ----
    POI_W = 260
    poi_frame = tk.Frame(root, bg=PANEL_BG, highlightthickness=1,
                         highlightbackground=HUD, highlightcolor=HUD)
    poi_frame.place(x=W - POI_W - 16, y=64, width=POI_W, height=H - 130)

    poi_header = tk.Canvas(poi_frame, bg=PANEL_BG, height=28, highlightthickness=0, bd=0)
    poi_header.pack(fill="x", padx=12, pady=(10, 4))
    poi_header.create_text(
        0, 6, anchor="nw", text="POI . NEARBY",
        fill=HUD_DIM, font=mono_tiny,
    )
    poi_header.create_line(0, 24, 9999, 24, fill=HUD, width=1)

    poi_list = tk.Listbox(
        poi_frame, bg=PANEL_BG, fg=TEXT,
        font=("Consolas", 9),
        selectbackground=HUD, selectforeground=BG,
        highlightthickness=0, bd=0, activestyle="none",
    )
    poi_list.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    poi_data = (pois or [])[:10]
    for p in poi_data:
        poi_list.insert("end", f"> {p['title']}")
    if not poi_data:
        poi_list.insert("end", "  -- no POI in radius --")

    def _on_poi_click(_evt):
        sel = poi_list.curselection()
        if not sel or sel[0] >= len(poi_data):
            return
        p = poi_data[sel[0]]
        map_widget.set_position(p["lat"], p["lon"])
        map_widget.set_zoom(15)
    poi_list.bind("<<ListboxSelect>>", _on_poi_click)

    # ---- Corner brackets (4 corner overlays as small canvases) ----
    def _corner(cx, cy, anchor):
        size = 36
        c = tk.Canvas(root, bg=BG, width=size, height=size, highlightthickness=0, bd=0)
        if anchor == "tl":
            c.place(x=cx, y=cy)
            c.create_line(0, 0, size, 0, fill=HUD, width=2)
            c.create_line(0, 0, 0, size, fill=HUD, width=2)
        elif anchor == "tr":
            c.place(x=cx - size, y=cy)
            c.create_line(0, 0, size, 0, fill=HUD, width=2)
            c.create_line(size - 1, 0, size - 1, size, fill=HUD, width=2)
        elif anchor == "bl":
            c.place(x=cx, y=cy - size)
            c.create_line(0, size - 1, size, size - 1, fill=HUD, width=2)
            c.create_line(0, 0, 0, size, fill=HUD, width=2)
        elif anchor == "br":
            c.place(x=cx - size, y=cy - size)
            c.create_line(0, size - 1, size, size - 1, fill=HUD, width=2)
            c.create_line(size - 1, 0, size - 1, size, fill=HUD, width=2)
        return c

    corners = []
    def _layout_corners():
        for c in corners:
            c.destroy()
        corners.clear()
        rw = root.winfo_width()
        rh = root.winfo_height()
        corners.append(_corner(8, 56, "tl"))
        corners.append(_corner(rw - 8, 56, "tr"))
        corners.append(_corner(8, rh - 40, "bl"))
        corners.append(_corner(rw - 8, rh - 40, "br"))
    root.after(80, _layout_corners)
    root.bind("<Configure>", lambda e: root.after(50, _layout_corners))

    # ---- Center HUD reticle (small canvas placed centrally; non-blocking) ----
    RET = 140
    reticle = tk.Canvas(root, bg=BG, width=RET, height=RET,
                        highlightthickness=0, bd=0)
    # Important: make it semi-transparent feel by drawing only thin lines.
    # We can't have true alpha, so we keep it small and put it bottom-center
    # so it doesn't block clicks on the map center.
    def _place_reticle():
        rw = root.winfo_width()
        rh = root.winfo_height()
        reticle.place(x=(rw // 2) - (RET // 2), y=rh - 40 - RET - 8)
    root.after(80, _place_reticle)
    root.bind("<Configure>", lambda e: (root.after(50, _layout_corners),
                                        root.after(50, _place_reticle)))

    # Static reticle decoration
    cx, cy = RET // 2, RET // 2
    reticle.create_oval(cx - 60, cy - 60, cx + 60, cy + 60, outline=HUD, width=1)
    reticle.create_oval(cx - 40, cy - 40, cx + 40, cy + 40, outline=HUD_DIM, width=1)
    reticle.create_line(cx, cy - 60, cx, cy - 70, fill=HUD, width=2)
    reticle.create_line(cx, cy + 60, cx, cy + 70, fill=HUD, width=2)
    reticle.create_line(cx - 60, cy, cx - 70, cy, fill=HUD, width=2)
    reticle.create_line(cx + 60, cy, cx + 70, cy, fill=HUD, width=2)
    reticle.create_text(cx, cy + 56, text="LOCK", fill=AMBER, font=mono_tiny)

    # Rotating arc
    arc_id = reticle.create_arc(
        cx - 50, cy - 50, cx + 50, cy + 50,
        start=0, extent=80, style="arc", outline=AMBER, width=2,
    )

    # ---- Animation loop ----
    state = {"angle": 0, "scan_x": 0, "alive": True}

    def _tick():
        if not state["alive"]:
            return
        # rotating arc
        state["angle"] = (state["angle"] + 4) % 360
        reticle.itemconfig(arc_id, start=state["angle"])

        # bottom scan line
        rw = root.winfo_width() or W
        state["scan_x"] = (state["scan_x"] + 6) % rw
        bot_canvas.coords(scan_x_id,
                          state["scan_x"], 16,
                          state["scan_x"] + 60, 16)

        # clock
        now = datetime.now()
        top_canvas.itemconfig(clock_text_id, text=now.strftime("%H:%M:%S"))
        top_canvas.itemconfig(date_text_id,  text=now.strftime("%Y-%m-%d  UTC%z").strip())

        root.after(50, _tick)
    root.after(50, _tick)

    def _on_close():
        state["alive"] = False
        _ACTIVE_WINDOW["alive"] = False
        _ACTIVE_WINDOW["root"] = None
        try:
            root.destroy()
        except Exception:
            pass
    root.protocol("WM_DELETE_WINDOW", _on_close)

    try:
        root.mainloop()
    finally:
        # Ensure the global state is cleared even if mainloop exits
        # because of an external destroy() call.
        state["alive"] = False
        _ACTIVE_WINDOW["alive"] = False
        _ACTIVE_WINDOW["root"] = None
    return True


# ---------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------
def jarvis_map(parameters=None, response=None, player=None, session_memory=None) -> str:
    params = parameters or {}
    city = (params.get("city") or params.get("location") or "").strip()
    if not city:
        return "Sir, you must tell me which city to display on the map."

    def log(m):
        print(f"[jarvis_map] {m}")
        if player:
            try:
                player.write_log(f"[map] {m}")
            except Exception:
                pass

    log(f"target acquired: {city}")
    geo = _geocode(city)
    if not geo:
        return f"Sir, I cannot locate '{city}' on the map."

    weather = _weather(geo["lat"], geo["lon"])
    wiki    = _wiki_summary(city, lang="en") or _wiki_summary(city, lang="it")
    pois    = _wiki_pois(geo["lat"], geo["lon"], radius_m=10000, limit=10, lang="en")

    # Open the JARVIS HUD window in a background thread so the assistant
    # main loop is not blocked by Tk's mainloop.
    def _runner():
        try:
            _open_jarvis_window(city, geo, weather, wiki, pois)
        except Exception as e:
            log(f"window error: {e}")

    t = threading.Thread(target=_runner, name="JarvisMapWindow", daemon=True)
    t.start()

    # Spoken reply
    pieces = [f"Displaying {city} on the tactical map, sir."]
    if weather and weather.get("temperature") is not None:
        pieces.append(f"Current temperature is {weather['temperature']} degrees.")
    if wiki and wiki.get("extract"):
        pieces.append(_short_extract(wiki["extract"], 200))
    return " ".join(pieces)

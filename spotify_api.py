"""Spotify Control - Search SENZA Premium + Controlli via WM_APPCOMMAND

[FIX v6 - 2026-01]
- search_and_play(): ora la nuova canzone PARTE SEMPRE, anche se ne stava già
  suonando un'altra. Strategia: PAUSE deterministico → apri URI → riapri URI
  → PLAY. In questo modo lo stato è prevedibile e il toggle di Spotify non
  lascia mai il brano in pausa.
- Matching canzoni MOLTO più preciso: tra tutti i risultati Spotify (limit=15)
  scegliamo quello con (1) nome più simile a quello chiesto e (2) maggior
  popolarità. Risolve i casi "diversi" → Shiva, "marylean" → Sfera, ecc.
- Riconoscimento dell'artista nella query: se scrivi "diversi shiva", filtra
  i risultati per quell'artista prima del ranking.
- Mantenuti fallback HTML (DDG/Bing/Mojeek/Startpage/Spotify HTML) e tutta
  la logica di gestione finestra (closed-to-tray / minimized / normal).

[FIX v5 - 2026-01]
- Rimosso User-Agent browser dalle chiamate API ufficiali (causava 403)
- Logging dettagliato + fallback senza market=IT + retry su 401
- diagnose() pubblica per debug rapido

OK con account Spotify FREE (Premium NON richiesto).
"""
import os
import re
import time
import base64
import ctypes
import difflib
import logging
import subprocess
import unicodedata
from ctypes import wintypes
from pathlib import Path
from urllib.parse import unquote

import requests

log = logging.getLogger(__name__)
if not log.handlers:
    logging.basicConfig(
        level=os.environ.get("SPOTIFY_LOG_LEVEL", "INFO"),
        format="[spotify] %(levelname)s %(message)s",
    )
log.setLevel(os.environ.get("SPOTIFY_LOG_LEVEL", "INFO"))

# ------------------------------------------------------------------
# COSTANTI WINDOWS
# ------------------------------------------------------------------
WM_APPCOMMAND = 0x0319
APPCOMMAND_MEDIA_PLAY = 46
APPCOMMAND_MEDIA_PAUSE = 47
APPCOMMAND_MEDIA_PLAY_PAUSE = 14
APPCOMMAND_MEDIA_NEXTTRACK = 11
APPCOMMAND_MEDIA_PREVIOUSTRACK = 12

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWNOACTIVATE = 4
SW_MINIMIZE = 6
SW_SHOWNA = 8
SW_FORCEMINIMIZE = 11

_UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_TRACK_ID_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([a-zA-Z0-9]{22})")

# ------------------------------------------------------------------
# CREDENZIALI (Client Credentials Flow)
# ------------------------------------------------------------------
_APP_TOKEN_CACHE = {"token": None, "expires_at": 0}
_CREDS_FILE_NAMES = ["spotify_credentials.txt", ".spotify_credentials"]
_API_BLOCKED_PREMIUM = False


def _load_credentials():
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    csec = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if cid and csec:
        return cid, csec

    search_dirs = [Path(__file__).resolve().parent, Path.cwd()]
    for d in search_dirs:
        for name in _CREDS_FILE_NAMES:
            p = d / name
            if not p.exists():
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except Exception:
                continue
            pairs = {}
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    pairs[k.strip().lower()] = v.strip().strip('"').strip("'")
            cid = pairs.get("client_id") or pairs.get("spotify_client_id")
            csec = pairs.get("client_secret") or pairs.get("spotify_client_secret")
            if cid and csec:
                return cid, csec
    return None, None


def _short_body(r):
    try:
        t = r.text
    except Exception:
        return ""
    if not t:
        return ""
    t = t.replace("\n", " ").strip()
    return t[:300] + ("…" if len(t) > 300 else "")


def _get_app_token(force_refresh=False):
    now = time.time()
    if (not force_refresh
            and _APP_TOKEN_CACHE["token"]
            and now < _APP_TOKEN_CACHE["expires_at"] - 60):
        return _APP_TOKEN_CACHE["token"]

    cid, csec = _load_credentials()
    if not cid or not csec:
        log.warning("Nessuna credenziale Spotify trovata.")
        return None

    try:
        auth = base64.b64encode(f"{cid}:{csec}".encode("utf-8")).decode("ascii")
        r = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=10,
        )
        if r.status_code != 200:
            log.error("Token POST HTTP %s - body: %s", r.status_code, _short_body(r))
            return None
        data = r.json()
        token = data.get("access_token")
        if not token:
            return None
        _APP_TOKEN_CACHE["token"] = token
        _APP_TOKEN_CACHE["expires_at"] = now + int(data.get("expires_in", 3500))
        log.info("Token App OK (expires_in=%ss)", data.get("expires_in"))
        return token
    except Exception as e:
        log.exception("Eccezione durante richiesta token: %s", e)
        return None


def has_credentials():
    cid, csec = _load_credentials()
    return bool(cid and csec)


# ------------------------------------------------------------------
# LEGACY anon token (fallback)
# ------------------------------------------------------------------
_TOKEN_CACHE = {"token": None, "expires_at": 0}


def _get_anon_token():
    now = time.time()
    if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expires_at"] - 60:
        return _TOKEN_CACHE["token"]
    try:
        s = requests.Session()
        s.get("https://open.spotify.com/", headers={"User-Agent": _UA_BROWSER}, timeout=10)
        r = s.get(
            "https://open.spotify.com/get_access_token",
            params={"reason": "transport", "productType": "web_player"},
            headers={
                "User-Agent": _UA_BROWSER,
                "Accept": "application/json",
                "App-Platform": "WebPlayer",
                "Referer": "https://open.spotify.com/",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        token = data.get("accessToken")
        if not token:
            return None
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["expires_at"] = (
            (data.get("accessTokenExpirationTimestampMs", 0) / 1000.0) or (now + 3000)
        )
        return token
    except Exception:
        return None


# ------------------------------------------------------------------
# STRING NORMALIZATION + MATCHING
# ------------------------------------------------------------------
def _normalize(s: str) -> str:
    """Toglie accenti, lowercase, comprime spazi/punteggiatura."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Stopwords / filler che vanno tolti dalla query per il matching titolo
_FILLER_WORDS = {
    "di", "del", "della", "dei", "delle", "da", "dal", "il", "la", "lo",
    "le", "gli", "i", "un", "una", "uno", "by", "of", "the",
    "feat", "ft", "featuring", "con", "and",
}


def _strip_filler(text: str) -> str:
    toks = [t for t in _normalize(text).split() if t not in _FILLER_WORDS]
    return " ".join(toks)


def _try_split_song_artist(query: str):
    """Se la query è 'titolo artista' o 'titolo di artista', prova a separare.
    Ritorna (titolo_guess, artista_guess_or_None).
    """
    q = query.strip()
    # pattern: " di <artista>" / " by <artista>"
    m = re.search(r"\s+(?:di|by|of)\s+(.+)$", q, flags=re.IGNORECASE)
    if m:
        artist = m.group(1).strip()
        title = q[:m.start()].strip()
        if title and artist:
            return title, artist
    return q, None


def _name_similarity(query_norm: str, track_name_norm: str) -> float:
    if not query_norm or not track_name_norm:
        return 0.0
    return difflib.SequenceMatcher(None, query_norm, track_name_norm).ratio()


def _artist_match(query_artist_norm: str, track_artists_norm: str) -> float:
    if not query_artist_norm or not track_artists_norm:
        return 0.0
    if query_artist_norm in track_artists_norm:
        return 1.0
    return difflib.SequenceMatcher(None, query_artist_norm, track_artists_norm).ratio()


def _pick_best_match(items: list, original_query: str):
    """Sceglie la traccia migliore tra i risultati Spotify.

    Algoritmo:
    1. Se la query contiene "titolo di artista" → filtra per quell'artista.
    2. Score = bonus_match_esatto + bonus_contiene + similarity_nome + popolarità.
       In pratica: prima viene la somiglianza del NOME, poi la popolarità.
    """
    if not items:
        return None

    title_guess, artist_guess = _try_split_song_artist(original_query)
    qt = _normalize(title_guess)
    qt_stripped = _strip_filler(title_guess)
    qa = _normalize(artist_guess) if artist_guess else None

    # Filtra per artista se specificato (soft filter: se nessuno matcha, non filtrare)
    candidates = items
    if qa:
        filtered = []
        for t in items:
            artists_norm = _normalize(", ".join(a["name"] for a in t.get("artists", [])))
            if qa and (qa in artists_norm or artists_norm in qa
                       or _artist_match(qa, artists_norm) >= 0.7):
                filtered.append(t)
        if filtered:
            candidates = filtered

    best = None
    best_score = -1.0
    for t in candidates:
        name_norm = _normalize(t.get("name", ""))
        name_stripped = _strip_filler(t.get("name", ""))
        artists_norm = _normalize(", ".join(a["name"] for a in t.get("artists", [])))
        popularity = float(t.get("popularity", 0))  # 0-100

        sim_full = _name_similarity(qt, name_norm)
        sim_stripped = _name_similarity(qt_stripped, name_stripped)
        sim = max(sim_full, sim_stripped)

        exact_bonus = 200.0 if (qt == name_norm or qt_stripped == name_stripped) else 0.0
        contains_bonus = 60.0 if (qt and (qt in name_norm or name_norm in qt)) else 0.0
        artist_bonus = 0.0
        if qa:
            am = _artist_match(qa, artists_norm)
            artist_bonus = am * 80.0

        # Penalità per remix/edit/sped up se la query non li chiedeva
        penalty = 0.0
        suspicious = ("remix", "sped up", "slowed", "remaster", "live",
                      "acoustic", "instrumental", "karaoke", "cover")
        qfull = _normalize(original_query)
        for word in suspicious:
            if word in name_norm and word not in qfull:
                penalty += 25.0
                break

        # Composizione finale: la somiglianza del nome pesa molto (×150),
        # poi popolarità (×1, range 0-100), poi bonus.
        score = (sim * 150.0) + exact_bonus + contains_bonus + artist_bonus + popularity - penalty

        log.debug("  candidate=%r artist=%r pop=%.0f sim=%.2f score=%.1f",
                  t.get("name"), ", ".join(a["name"] for a in t.get("artists", [])),
                  popularity, sim, score)

        if score > best_score:
            best_score = score
            best = t

    if best:
        log.info("Best match: %r - %r (pop=%s, score=%.1f)",
                 best.get("name"),
                 ", ".join(a["name"] for a in best.get("artists", [])),
                 best.get("popularity"), best_score)
    return best


# ------------------------------------------------------------------
# /v1/search
# ------------------------------------------------------------------
def _do_search_request(token, song, market=None, limit=15):
    """Ritorna (items, status_code, body_snippet)."""
    global _API_BLOCKED_PREMIUM
    params = {"q": song, "type": "track", "limit": limit}
    if market:
        params["market"] = market
    try:
        r = requests.get(
            "https://api.spotify.com/v1/search",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=10,
        )
    except Exception as e:
        log.error("Eccezione di rete in /v1/search: %s", e)
        return None, -1, str(e)

    if r.status_code != 200:
        body = _short_body(r)
        log.error("/v1/search HTTP %s (market=%s) - body: %s",
                  r.status_code, market, body)
        if r.status_code == 403 and "premium" in body.lower() and "owner" in body.lower():
            _API_BLOCKED_PREMIUM = True
            log.warning("API Spotify bloccata: owner FREE. Useremo i fallback.")
        return None, r.status_code, body

    try:
        items = (r.json().get("tracks") or {}).get("items") or []
    except Exception as e:
        log.error("/v1/search JSON parse error: %s", e)
        return None, r.status_code, _short_body(r)
    return items, r.status_code, None


def _search_via_api(song):
    """Cerca via API ufficiale e applica matching intelligente."""
    if _API_BLOCKED_PREMIUM:
        return None

    token = _get_app_token()
    if not token:
        token = _get_anon_token()
    if not token:
        return None

    items, status, _ = _do_search_request(token, song, market="IT")

    if _API_BLOCKED_PREMIUM:
        return None

    if status == 401:
        log.info("Token scaduto (401), refresh e retry...")
        token = _get_app_token(force_refresh=True) or _get_anon_token()
        if token:
            items, status, _ = _do_search_request(token, song, market="IT")

    if (items is None and status in (400, 404)) or (items is not None and len(items) == 0):
        log.info("Retry senza market...")
        items, status, _ = _do_search_request(token, song, market=None)

    if _API_BLOCKED_PREMIUM:
        return None

    if items is None or len(items) == 0:
        anon = _get_anon_token()
        if anon and anon != token:
            log.info("Fallback con token anonimo web player...")
            items, status, _ = _do_search_request(anon, song, market=None)

    if not items:
        return None

    # MATCHING INTELLIGENTE: scegli il miglior risultato in base a
    # somiglianza nome + popolarità + artista (se specificato).
    t = _pick_best_match(items, song)
    if not t:
        t = items[0]
    return {
        "uri": t["uri"],
        "id": t["id"],
        "name": t["name"],
        "artist": ", ".join(a["name"] for a in t["artists"]),
        "popularity": t.get("popularity", 0),
    }


# ------------------------------------------------------------------
# Fallback HTML (oEmbed + DDG / Bing / Mojeek / Startpage / Spotify HTML)
# ------------------------------------------------------------------
def _oembed_meta(track_id):
    try:
        r = requests.get(
            "https://open.spotify.com/oembed",
            params={"url": f"https://open.spotify.com/track/{track_id}"},
            headers={"User-Agent": _UA_BROWSER},
            timeout=10,
        )
        if r.status_code != 200:
            return ("", "")
        data = r.json()
        title = (data.get("title") or "").strip()
        if " - " in title:
            name, artist = title.split(" - ", 1)
            return (name.strip(), artist.strip())
        return (title, "")
    except Exception:
        return ("", "")


def _extract_track_id(html):
    if not html:
        return None
    ids = _TRACK_ID_RE.findall(html)
    if ids:
        return ids[0]
    ids = _TRACK_ID_RE.findall(unquote(html))
    return ids[0] if ids else None


def _search_via_ddg(song):
    q = f'site:open.spotify.com/track {song}'
    headers = {"User-Agent": _UA_BROWSER, "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"}
    try:
        r = requests.post("https://html.duckduckgo.com/html/",
                          data={"q": q}, headers=headers, timeout=12)
        if r.status_code == 200:
            tid = _extract_track_id(r.text)
            if tid:
                return tid
    except Exception:
        pass
    try:
        r = requests.get("https://lite.duckduckgo.com/lite/",
                         params={"q": q}, headers=headers, timeout=12)
        if r.status_code == 200:
            return _extract_track_id(r.text)
    except Exception:
        pass
    return None


def _search_via_bing(song):
    q = f'site:open.spotify.com/track {song}'
    try:
        r = requests.get("https://www.bing.com/search", params={"q": q},
                         headers={"User-Agent": _UA_BROWSER,
                                  "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"},
                         timeout=12)
        if r.status_code != 200:
            return None
        return _extract_track_id(r.text)
    except Exception:
        return None


def _search_via_mojeek(song):
    q = f'site:open.spotify.com/track {song}'
    try:
        r = requests.get("https://www.mojeek.com/search", params={"q": q},
                         headers={"User-Agent": _UA_BROWSER,
                                  "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"},
                         timeout=12)
        if r.status_code != 200:
            return None
        return _extract_track_id(r.text)
    except Exception:
        return None


def _search_via_startpage(song):
    q = f'site:open.spotify.com/track {song}'
    try:
        r = requests.post("https://www.startpage.com/sp/search",
                          data={"query": q, "cat": "web"},
                          headers={"User-Agent": _UA_BROWSER,
                                   "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"},
                          timeout=12)
        if r.status_code != 200:
            return None
        return _extract_track_id(r.text)
    except Exception:
        return None


def _search_via_spotify_html(song):
    from urllib.parse import quote_plus
    url = f"https://open.spotify.com/search/{quote_plus(song)}"
    try:
        r = requests.get(url, headers={"User-Agent": _UA_BROWSER,
                                       "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"},
                         timeout=12)
        if r.status_code != 200:
            return None
        return _extract_track_id(r.text)
    except Exception:
        return None


# ------------------------------------------------------------------
# SEARCH PUBBLICA (cache 5 min)
# ------------------------------------------------------------------
_SEARCH_CACHE = {}
_SEARCH_CACHE_TTL = 300.0


def _cache_get(key):
    entry = _SEARCH_CACHE.get(key)
    if not entry:
        return None
    ts, track = entry
    if (time.time() - ts) > _SEARCH_CACHE_TTL:
        _SEARCH_CACHE.pop(key, None)
        return None
    return track


def _cache_set(key, track):
    if track:
        _SEARCH_CACHE[key] = (time.time(), track)


def search_track(song):
    if not song or not song.strip():
        return None
    song = song.strip()
    key = song.lower()

    cached = _cache_get(key)
    if cached:
        return cached

    t = _search_via_api(song)
    if t:
        _cache_set(key, t)
        return t

    fallbacks = (_search_via_ddg, _search_via_bing,
                 _search_via_mojeek, _search_via_startpage,
                 _search_via_spotify_html)
    track_id = None
    for fn in fallbacks:
        track_id = fn(song)
        if track_id:
            log.info("Fallback OK con %s → %s", fn.__name__, track_id)
            break
    if not track_id:
        return None

    name, artist = _oembed_meta(track_id)
    track = {
        "uri": f"spotify:track:{track_id}",
        "id": track_id,
        "name": name or song,
        "artist": artist,
        "popularity": 0,
    }
    _cache_set(key, track)
    return track


# ------------------------------------------------------------------
# DIAGNOSE
# ------------------------------------------------------------------
def diagnose(song="Imagine John Lennon"):
    out = {
        "has_credentials": has_credentials(),
        "app_token": None,
        "app_token_len": 0,
        "anon_token_len": 0,
        "search_status_it": None,
        "search_status_nomarket": None,
        "api_blocked_premium": False,
        "first_result": None,
        "best_match": None,
        "all_candidates": [],
        "fallback_results": {},
        "fallback_track_resolved": None,
        "errors": [],
    }
    tok = _get_app_token(force_refresh=True)
    items = None
    if tok:
        out["app_token"] = "OK"
        out["app_token_len"] = len(tok)
        items, status, body = _do_search_request(tok, song, market="IT")
        out["search_status_it"] = status
        if items is None:
            out["errors"].append(f"market=IT → HTTP {status}: {body}")
            if not _API_BLOCKED_PREMIUM:
                items, status, body = _do_search_request(tok, song, market=None)
                out["search_status_nomarket"] = status
                if items is None:
                    out["errors"].append(f"no-market → HTTP {status}: {body}")
        if items:
            t = items[0]
            out["first_result"] = {"name": t["name"], "uri": t["uri"],
                                   "artist": ", ".join(a["name"] for a in t["artists"]),
                                   "popularity": t.get("popularity", 0)}
            out["all_candidates"] = [
                {"name": x["name"],
                 "artist": ", ".join(a["name"] for a in x["artists"]),
                 "popularity": x.get("popularity", 0)}
                for x in items[:10]
            ]
            best = _pick_best_match(items, song)
            if best:
                out["best_match"] = {"name": best["name"], "uri": best["uri"],
                                     "artist": ", ".join(a["name"] for a in best["artists"]),
                                     "popularity": best.get("popularity", 0)}
    out["api_blocked_premium"] = _API_BLOCKED_PREMIUM
    anon = _get_anon_token()
    if anon:
        out["anon_token_len"] = len(anon)

    engines = [
        ("ddg", _search_via_ddg),
        ("bing", _search_via_bing),
        ("mojeek", _search_via_mojeek),
        ("startpage", _search_via_startpage),
        ("spotify_html", _search_via_spotify_html),
    ]
    fallback_results = {}
    tid = None
    for name, fn in engines:
        r = fn(song)
        fallback_results[name] = r
        if r and not tid:
            tid = r
    out["fallback_results"] = fallback_results

    if tid:
        name, artist = _oembed_meta(tid)
        out["fallback_track_resolved"] = {
            "id": tid, "uri": f"spotify:track:{tid}",
            "name": name or song, "artist": artist,
        }
    return out


# ------------------------------------------------------------------
# SPOTIFY WINDOW HANDLES (Windows only)
# ------------------------------------------------------------------
def _get_spotify_pids():
    pids = []
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq Spotify.exe", "/FO", "CSV", "/NH"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).decode(errors="ignore")
        for line in out.splitlines():
            if "spotify.exe" in line.lower():
                parts = [p.strip().strip('"') for p in line.split(",")]
                if len(parts) >= 2:
                    try:
                        pids.append(int(parts[1]))
                    except ValueError:
                        pass
    except Exception:
        pass
    return pids


def _find_all_spotify_hwnds(include_invisible=True):
    pids = _get_spotify_pids()
    if not pids:
        return []
    hwnds = []
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            if include_invisible or user32.IsWindowVisible(hwnd):
                hwnds.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return hwnds


def _ensure_spotify_running():
    if _get_spotify_pids():
        return True
    paths = [
        os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe"),
        "spotify.exe",
    ]
    for sp in paths:
        try:
            subprocess.Popen([sp])
            for _ in range(20):
                time.sleep(0.3)
                if _get_spotify_pids():
                    return True
            return True
        except Exception:
            continue
    return False


def _send_appcommand(cmd):
    hwnds = _find_all_spotify_hwnds()
    if not hwnds:
        return False
    user32 = ctypes.windll.user32
    SendMessageW = user32.SendMessageW
    SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    SendMessageW.restype = ctypes.c_long
    PostMessageW = user32.PostMessageW
    PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    PostMessageW.restype = wintypes.BOOL

    ok = False
    for hwnd in hwnds:
        try:
            SendMessageW(hwnd, WM_APPCOMMAND, hwnd, cmd << 16)
            PostMessageW(hwnd, WM_APPCOMMAND, hwnd, cmd << 16)
            ok = True
        except Exception:
            pass
    return ok


# ------------------------------------------------------------------
# WINDOW STATE CAPTURE / RESTORE
# ------------------------------------------------------------------
def _capture_window_states():
    user32 = ctypes.windll.user32
    snapshot = {"target": "normal", "foreground": 0}
    try:
        snapshot["foreground"] = int(user32.GetForegroundWindow() or 0)
    except Exception:
        pass

    hwnds = _find_all_spotify_hwnds(include_invisible=True)
    any_visible = False
    any_visible_normal = False
    for hwnd in hwnds:
        try:
            vis = bool(user32.IsWindowVisible(hwnd))
            ico = bool(user32.IsIconic(hwnd))
            if vis:
                any_visible = True
                if not ico:
                    any_visible_normal = True
        except Exception:
            pass

    if not any_visible:
        snapshot["target"] = "hidden"
    elif not any_visible_normal:
        snapshot["target"] = "minimized"
    else:
        snapshot["target"] = "normal"
    return snapshot


def _apply_target_once(target, prev_fg):
    user32 = ctypes.windll.user32
    try:
        hwnds = _find_all_spotify_hwnds(include_invisible=True)
    except Exception:
        hwnds = []
    for hwnd in hwnds:
        try:
            if target == "hidden":
                if user32.IsWindowVisible(hwnd):
                    user32.ShowWindow(hwnd, SW_MINIMIZE)
                    user32.ShowWindow(hwnd, SW_HIDE)
                    user32.ShowWindow(hwnd, SW_HIDE)
            elif target == "minimized":
                if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, SW_MINIMIZE)
        except Exception:
            pass
    if prev_fg:
        try:
            user32.SetForegroundWindow(prev_fg)
        except Exception:
            pass


# ------------------------------------------------------------------
# PLAY
# ------------------------------------------------------------------
def search_and_play(song, track=None):
    """Cerca e fa partire una canzone.

    FIX v6 - parte SEMPRE, anche se ne stava già suonando un'altra.

    Strategia:
      1. Cattura lo stato della finestra Spotify (hidden/min/normal).
      2. Mandiamo APPCOMMAND_MEDIA_PAUSE → mette Spotify in stato DETERMINISTICO
         (pausa se stava suonando, no-op se era già fermo). Dopo questo
         passo siamo sicuri che lo stato è "non in riproduzione".
      3. Apriamo l'URI: Spotify seleziona/carica la traccia. In stato di
         pausa NON parte da solo.
      4. Aspettiamo che la traccia si carichi, poi riapriamo l'URI: questo
         è un trick affidabile per forzare la riproduzione su Spotify
         desktop quando il client ha appena caricato la traccia.
      5. Come garanzia ulteriore mandiamo APPCOMMAND_MEDIA_PLAY: dato che
         siamo partiti da stato "paused", il toggle ci porta a "playing".
      6. Ripristiniamo lo stato della finestra (hidden/min/normal).
    """
    if track is None:
        if not song or not song.strip():
            return False, "Canzone non specificata.", None
        track = search_track(song)
        if not track:
            return False, f"'{song}' non trovato.", None

    was_running = bool(_get_spotify_pids())
    _ensure_spotify_running()
    if not was_running:
        time.sleep(2.0)

    # 1) Cattura stato finestra
    snapshot = _capture_window_states()
    target = snapshot["target"]
    prev_fg = snapshot["foreground"]

    # 2) Stato deterministico: PAUSA. Se stava già suonando, ora è in pausa.
    #    Se non stava suonando, nessun effetto. APPCOMMAND_MEDIA_PAUSE su
    #    Spotify è dedicato (non toggle), quindi è sicuro mandarlo sempre.
    if was_running:
        _send_appcommand(APPCOMMAND_MEDIA_PAUSE)
        time.sleep(0.2)

    # 3) Apri URI → Spotify seleziona/carica la traccia (non parte perché paused)
    try:
        os.startfile(track["uri"])
    except Exception as e:
        return False, f"Errore apertura traccia: {e}", track["uri"]

    # 4) Aspetta che il client carichi, poi riapri (forza play su Spotify)
    time.sleep(1.0)
    try:
        os.startfile(track["uri"])
    except Exception:
        pass

    # 5) Garanzia: invia PLAY. Dato che lo stato di partenza era "paused"
    #    (passo 2), il toggle ci porta SEMPRE a "playing".
    time.sleep(0.6)
    _send_appcommand(APPCOMMAND_MEDIA_PLAY)

    # 6) Ripristina finestra per 3 secondi (Spotify a volte la rimostra)
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if was_running and target != "normal":
            _apply_target_once(target, prev_fg)
        time.sleep(0.2)

    if was_running and target != "normal":
        _apply_target_once(target, prev_fg)

    label = track["name"]
    if track.get("artist"):
        label += f" - {track['artist']}"
    return True, f"▶️ {label}", track["uri"]


# ------------------------------------------------------------------
# CONTROLLI
# ------------------------------------------------------------------
def pause():
    if _send_appcommand(APPCOMMAND_MEDIA_PAUSE):
        return True, "⏸️ Pausa"
    return False, "Errore pausa: Spotify non in esecuzione"


def resume():
    if _send_appcommand(APPCOMMAND_MEDIA_PLAY):
        return True, "▶️ Play"
    return False, "Errore play: Spotify non in esecuzione"


def next_track():
    if _send_appcommand(APPCOMMAND_MEDIA_NEXTTRACK):
        return True, "⏭️ Next"
    return False, "Errore next: Spotify non in esecuzione"


def previous_track():
    if _send_appcommand(APPCOMMAND_MEDIA_PREVIOUSTRACK):
        return True, "⏮️ Previous"
    return False, "Errore previous: Spotify non in esecuzione"


def get_current_track():
    return None


if __name__ == "__main__":
    print("\n🎵 Spotify Control (FREE) - search API + WM_APPCOMMAND\n")
    song = input("Che canzone vuoi? ").strip()
    if song:
        track = search_track(song)
        if track:
            print(f"\n✅ Trovato: {track['name']}"
                  + (f" - {track['artist']}" if track['artist'] else ""))
            print(f"   URI: {track['uri']}\n")
            ok, msg, _ = search_and_play(song, track=track)
            print(msg)
        else:
            print(f"\n❌ '{song}' non trovato.\n")
            print("💡 Esegui: python -c \"import spotify_api,json;print(json.dumps(spotify_api.diagnose(),indent=2))\"")

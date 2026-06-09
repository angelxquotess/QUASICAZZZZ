# actions/spotify_control.py
# Wrapper per spotify_api.py — integra la riproduzione Spotify Desktop
# nel sistema di tool-calling di MARK XXXIX-OR.
#
# Importa spotify_api dalla root del repo. Funzionalita':
#   - search_and_play(song)
#   - pause(), resume()
#   - next_track(), previous_track()
#   - current track info
#
# Su Windows usa WM_APPCOMMAND verso il client Spotify Desktop.
# Su macOS/Linux degrada con grazia (i comandi rispondono comunque
# con messaggio informativo).

import sys
import platform
from pathlib import Path

# spotify_api.py vive nella root del repo Mark-XXXIX-OR
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    import spotify_api  # noqa: E402
    _SPOTIFY_OK = True
    _SPOTIFY_ERR = None
except Exception as e:
    spotify_api = None
    _SPOTIFY_OK = False
    _SPOTIFY_ERR = str(e)


def _log(player, msg: str):
    if player:
        try:
            player.write_log(f"[spotify] {msg}")
        except Exception:
            pass
    print(f"[spotify] {msg}")


def spotify_control(parameters=None, response=None, player=None, session_memory=None) -> str:
    """
    parameters:
        action: play | pause | resume | next | previous | current
        song:   (solo per play) titolo/artista
    """
    params = parameters or {}
    action = (params.get("action") or "play").lower().strip()
    song = (params.get("song") or "").strip()

    if not _SPOTIFY_OK:
        msg = f"Sir, the Spotify module is not available: {_SPOTIFY_ERR}"
        _log(player, msg)
        return msg

    system = platform.system()
    if system != "Windows" and action in {"play", "pause", "resume", "next", "previous"}:
        # Comandi multimediali nativi su mac/linux
        _log(player, f"non-Windows ({system}): degrado a fallback nativo")

    try:
        if action == "play":
            if not song:
                return "Sir, please specify the song or artist to play."
            _log(player, f"play '{song}'")
            ok = spotify_api.search_and_play(song)
            if ok:
                track = spotify_api.get_current_track() if hasattr(spotify_api, "get_current_track") else None
                if track and isinstance(track, dict) and track.get("name"):
                    name = track.get("name")
                    artist = track.get("artist") or ""
                    label = f"{name} - {artist}".strip(" -")
                    return f"Now playing {label} on Spotify, sir."
                return f"Now playing '{song}' on Spotify, sir."
            return f"Sir, I couldn't start '{song}' on Spotify."

        if action == "pause":
            _log(player, "pause")
            spotify_api.pause()
            return "Music paused, sir."

        if action == "resume":
            _log(player, "resume")
            spotify_api.resume()
            return "Resuming playback, sir."

        if action in ("next", "skip"):
            _log(player, "next")
            spotify_api.next_track()
            return "Skipping to the next track, sir."

        if action in ("previous", "prev", "back"):
            _log(player, "previous")
            spotify_api.previous_track()
            return "Going back to the previous track, sir."

        if action == "current":
            track = spotify_api.get_current_track() if hasattr(spotify_api, "get_current_track") else None
            if track and isinstance(track, dict) and track.get("name"):
                return f"Currently playing {track.get('name')} - {track.get('artist','')}".strip(" -")
            return "Sir, I cannot identify the current track."

        return f"Unknown spotify action: {action}"

    except Exception as e:
        msg = f"Sir, the Spotify command failed: {e}"
        _log(player, msg)
        return msg

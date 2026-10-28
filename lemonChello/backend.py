import os
import threading
import requests
from flask import Flask, jsonify, request, render_template
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import yt_dlp
from mutagen.id3 import ID3, TIT2, TPE1, TALB, USLT, APIC, error

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Environment configuration with defaults matching your server setup
BASE_OUTPUT_DIR = os.getenv('MUSIC_DIR', '/music')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID', 'YOUR_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET', 'YOUR_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI', 'http://127.0.0.1:8888/callback')
NAVIDROME_URL = os.getenv('NAVIDROME_URL', 'http://navidrome:4533')

download_status = {"status": "Idle", "progress": 0, "current_track": ""}

class MyLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): print(f"[Error] {msg}")

def fetch_lyrics(artist, title):
    """Fetch lyrics from a public API endpoint."""
    try:
        url = f"https://api.lyrics.ovh/v1/{requests.utils.quote(artist)}/{requests.utils.quote(title)}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return response.json().get('lyrics', None)
    except Exception as e:
        print(f"[-] Could not fetch lyrics: {e}")
    return None

def apply_metadata(mp3_path, track_info, cover_path=None, lyrics=None):
    """Embed ID3 tags, cover art, and lyrics into the MP3 file."""
    try:
        try:
            audio = ID3(mp3_path)
        except error:
            audio = ID3()

        audio.add(TIT2(encoding=3, text=track_info.get('title', 'Unknown Title')))
        audio.add(TPE1(encoding=3, text=track_info.get('artist', 'Unknown Artist')))
        audio.add(TALB(encoding=3, text=track_info.get('album', 'Singles')))

        if lyrics:
            audio.add(USLT(encoding=3, lang='eng', desc='Lyrics', text=lyrics))

        if cover_path and os.path.exists(cover_path):
            with open(cover_path, 'rb') as album_art:
                audio.add(APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,
                    desc='Cover',
                    data=album_art.read()
                ))

        audio.save(mp3_path)
    except Exception as e:
        print(f"[-] Error applying metadata: {e}")

def trigger_navidrome_scan():
    """Trigger an automated library scan in Navidrome."""
    try:
        scan_url = f"{NAVIDROME_URL}/rest/startScan?u=admin&t=94e1861f65e9e738d8b529c7aa232ab6&s=lemonchello_salt&v=1.16.1&c=LemonChello&f=json"
        requests.get(scan_url, timeout=5)
        print("[+] Triggered Navidrome library scan.")
    except Exception as e:
        print(f"[-] Failed to trigger Navidrome scan: {e}")

def run_download_job(url):
    global download_status
    try:
        download_status = {"status": "Authenticating & Fetching Metadata...", "progress": 5, "current_track": ""}
        
        auth_manager = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="playlist-read-private playlist-read-collaborative",
            cache_path="/app/.cache",
            open_browser=False
        )
        sp = spotipy.Spotify(auth_manager=auth_manager)
        
        # Handle individual track vs playlist
        tracks = []
        if 'track' in url:
            track_id = url.split('/')[-1].split('?')[0]
            t = sp.track(track_id)
            name = t['name']
            artists = ", ".join([artist['name'] for artist in t['artists']])
            album = t['album']['name'] if t.get('album') else "Singles"
            duration_ms = t.get('duration_ms', 0)
            images = t['album']['images'] if t.get('album') else []
            tracks.append({'name': name, 'artist': artists, 'album': album, 'duration_ms': duration_ms, 'images': images})
            playlist_name = "Singles"
        else:
            playlist_info = sp.playlist(url, fields="name")
            playlist_name = "".join([c if c.isalnum() or c in (' ', '-', '_') else '_' for c in playlist_info['name']])
            
            results = sp.playlist_tracks(url)
            def process_items(items):
                for item in items:
                    if item:
                        track = item.get('item') or item.get('track')
                        if track and track.get('name') and track.get('artists'):
                            name = track['name']
                            artists = ", ".join([artist['name'] for artist in track['artists']])
                            album = track['album']['name'] if track.get('album') else "Unknown Album"
                            duration_ms = track.get('duration_ms', 0)
                            images = track['album']['images'] if track.get('album') else []
                            tracks.append({'name': name, 'artist': artists, 'album': album, 'duration_ms': duration_ms, 'images': images})

            process_items(results['items'])
            while results['next']:
                results = sp.next(results)
                process_items(results['items'])

        total_tracks = len(tracks)
        output_dir = os.path.join(BASE_OUTPUT_DIR, playlist_name)
        os.makedirs(output_dir, exist_ok=True)
        
        for i, track in enumerate(tracks, 1):
            artist = track['artist']
            name = track['name']
            album = track['album']
            q_str = f"{artist} - {name}"
            
            download_status = {
                "status": f"Downloading ({i}/{total_tracks})",
                "progress": int((i / total_tracks) * 100),
                "current_track": q_str
            }
            
            safe_artist = "".join(c for c in artist if c.isalnum() or c in (' ', '-', '_')).strip()
            safe_album = "".join(c for c in album if c.isalnum() or c in (' ', '-', '_')).strip()
            track_dir = os.path.join(output_dir, safe_artist, safe_album)
            os.makedirs(track_dir, exist_ok=True)

            cover_path = os.path.join(track_dir, "cover.jpg")
            if track['images']:
                try:
                    img_data = requests.get(track['images'][0]['url']).content
                    with open(cover_path, 'wb') as handler:
                        handler.write(img_data)
                except Exception:
                    pass

            spotify_duration_sec = track['duration_ms'] / 1000
            search_query = f"ytsearch5:{q_str} official audio"
            
            ydl_search_opts = {'format': 'bestaudio/best', 'quiet': True, 'logger': MyLogger()}
            best_entry = None
            
            try:
                with yt_dlp.YoutubeDL(ydl_search_opts) as ydl:
                    search_results = ydl.extract_info(search_query, download=False)
                    if 'entries' in search_results and search_results['entries']:
                        for entry in search_results['entries']:
                            duration = entry.get('duration', 0)
                            if spotify_duration_sec > 0 and abs(duration - spotify_duration_sec) <= 5:
                                best_entry = entry
                                break
                        if not best_entry:
                            best_entry = search_results['entries'][0]
            except Exception as e:
                print(f"Search failed for {q_str}: {e}")
                continue

            if not best_entry:
                continue

            ydl_download_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(track_dir, '%(title)s.%(ext)s'),
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}],
                'logger': MyLogger(),
                'quiet': True,
            }
            
            try:
                with yt_dlp.YoutubeDL(ydl_download_opts) as ydl:
                    ydl.download([best_entry['webpage_url']])
                    filename = ydl.prepare_filename(best_entry)
                    mp3_path = os.path.splitext(filename)[0] + ".mp3"

                lyrics = fetch_lyrics(artist, name)
                track_info = {'artist': artist, 'title': name, 'album': album}
                apply_metadata(mp3_path, track_info, cover_path, lyrics)
            except Exception as e:
                print(f"Failed download {q_str}: {e}")
                
        download_status = {"status": "Complete", "progress": 100, "current_track": "All tracks downloaded successfully!"}
        trigger_navidrome_scan()
    except Exception as e:
        download_status = {"status": f"Error: {str(e)}", "progress": 0, "current_track": ""}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/download', methods=['POST'])
def start_download():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({"error": "No playlist URL provided"}), 400
        
    if download_status["status"].startswith("Downloading") or download_status["status"].startswith("Authenticating"):
        return jsonify({"message": "Download already in progress!"}), 400
        
    thread = threading.Thread(target=run_download_job, args=(url,))
    thread.start()
    return jsonify({"message": "Download started in background!"})

@app.route('/api/status', methods=['GET'])
def get_status():
    return jsonify(download_status)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

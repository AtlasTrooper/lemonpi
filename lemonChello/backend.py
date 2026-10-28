import os
import threading
from flask import Flask, jsonify, request, render_template
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import yt_dlp

app = Flask(__name__)

# Environment configuration with defaults matching your server setup
BASE_OUTPUT_DIR = os.getenv('MUSIC_DIR', '/music')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID', 'YOUR_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET', 'YOUR_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI', 'http://localhost:8888/callback')

download_status = {"status": "Idle", "progress": 0, "current_track": ""}

class MyLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): print(f"[Error] {msg}")

def run_download_job(playlist_url):
    global download_status
    try:
        download_status = {"status": "Authenticating & Fetching Metadata...", "progress": 5, "current_track": ""}
        
        # Uses cache path mounted from the host
        auth_manager = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="playlist-read-private playlist-read-collaborative",
            cache_path="/app/.cache"
        )
        sp = spotipy.Spotify(auth_manager=auth_manager)
        
        playlist_info = sp.playlist(playlist_url, fields="name")
        playlist_name = "".join([c if c.isalnum() or c in (' ', '-', '_') else '_' for c in playlist_info['name']])
        
        results = sp.playlist_tracks(playlist_url)
        tracks = []
        
        def process_items(items):
            for item in items:
                if item:
                    track = item.get('item') or item.get('track')
                    if track and track.get('name') and track.get('artists'):
                        name = track['name']
                        artists = ", ".join([artist['name'] for artist in track['artists']])
                        album = track['album']['name'] if track.get('album') else "Unknown Album"
                        tracks.append({'name': name, 'artist': artists, 'album': album})

        process_items(results['items'])
        while results['next']:
            results = sp.next(results)
            process_items(results['items'])
            
        total_tracks = len(tracks)
        output_dir = os.path.join(BASE_OUTPUT_DIR, playlist_name)
        os.makedirs(output_dir, exist_ok=True)
        
        for i, track in enumerate(tracks, 1):
            q_str = f"{track['artist']} - {track['name']}"
            download_status = {
                "status": f"Downloading ({i}/{total_tracks})",
                "progress": int((i / total_tracks) * 100),
                "current_track": q_str
            }
            
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(output_dir, '%(artist)s/%(album)s/%(title)s.%(ext)s'),
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}],
                'logger': MyLogger(),
                'silent': True,
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([f"ytsearch1:{q_str}"])
            except Exception as e:
                print(f"Failed {q_str}: {e}")
                
        download_status = {"status": "Complete", "progress": 100, "current_track": "All tracks downloaded successfully!"}
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

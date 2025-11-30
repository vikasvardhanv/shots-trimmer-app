import base64
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

import moviepy.editor as mp
import requests
import yt_dlp
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

# Fix for Pillow compatibility
try:
    from PIL import Image
    # For newer Pillow versions, use LANCZOS instead of ANTIALIAS
    if hasattr(Image, 'LANCZOS'):
        Image.ANTIALIAS = Image.LANCZOS
    elif hasattr(Image, 'Resampling'):
        Image.ANTIALIAS = Image.Resampling.LANCZOS
except ImportError:
    pass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')


def _normalise_database_uri(uri: str | None) -> str:
    if not uri:
        instance_path = BASE_DIR / 'instance'
        instance_path.mkdir(parents=True, exist_ok=True)
        return f'sqlite:///{instance_path / "app.db"}'
    if uri.startswith('postgres://'):
        return uri.replace('postgres://', 'postgresql://', 1)
    return uri


app = Flask(__name__)

# Production-ready configuration
secure_default_secret = secrets.token_hex(16)
app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY', secure_default_secret),
    MAX_CONTENT_LENGTH=int(os.environ.get('MAX_CONTENT_LENGTH', 100 * 1024 * 1024)),
    SEND_FILE_MAX_AGE_DEFAULT=31_536_000,  # Cache static files for 1 year
    API_KEY=os.environ.get('API_KEY', ''),
    SQLALCHEMY_DATABASE_URI=_normalise_database_uri(os.environ.get('DATABASE_URL')),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={'pool_pre_ping': True},
    JSON_SORT_KEYS=False,
    JSONIFY_PRETTYPRINT_REGULAR=False,
)

render_external_url = os.environ.get('RENDER_EXTERNAL_URL', '').strip()
render_env_flag = os.environ.get('RENDER', '').strip().lower()
is_production_env = os.environ.get('FLASK_ENV', '').lower()
is_production = (
    is_production_env == 'production'
    or bool(render_external_url)
    or render_env_flag in ('true', '1', 'yes')
)

preferred_url_scheme = os.environ.get('PREFERRED_URL_SCHEME')
if not preferred_url_scheme:
    preferred_url_scheme = 'https' if is_production else 'http'

app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)
app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')
app.config.setdefault('REMEMBER_COOKIE_HTTPONLY', True)
app.config.setdefault('SESSION_COOKIE_SECURE', is_production)
app.config.setdefault('PREFERRED_URL_SCHEME', preferred_url_scheme)

if not app.config['API_KEY']:
    logger.warning("No ADMIN API_KEY configured; relying on user-generated keys only.")

# Ensure runtime directories exist
for runtime_dir in ('downloads', 'uploads', 'logs'):
    target = BASE_DIR / runtime_dir
    target.mkdir(parents=True, exist_ok=True)

# Initialize extensions
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
oauth = OAuth(app)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# OAuth providers - Only register if credentials are provided
google = None
github = None

# Google OAuth setup with better error handling
google_client_id = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
google_client_secret = os.environ.get('GOOGLE_CLIENT_SECRET', '').strip()

if google_client_id and google_client_secret:
    try:
        google = oauth.register(
            name='google',
            client_id=google_client_id,
            client_secret=google_client_secret,
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={
                'scope': 'openid email profile https://www.googleapis.com/auth/youtube.readonly'
            }
        )
        logger.info("✅ Google OAuth configured successfully with YouTube access")
    except Exception as e:
        logger.warning(f"❌ Failed to register Google OAuth: {e}")
else:
    logger.info("ℹ️ Google OAuth not configured (missing credentials)")

# GitHub OAuth setup with better error handling  
github_client_id = os.environ.get('GITHUB_CLIENT_ID', '').strip()
github_client_secret = os.environ.get('GITHUB_CLIENT_SECRET', '').strip()

if github_client_id and github_client_secret:
    try:
        github = oauth.register(
            name='github',
            client_id=github_client_id,
            client_secret=github_client_secret,
            access_token_url='https://github.com/login/oauth/access_token',
            authorize_url='https://github.com/login/oauth/authorize',
            api_base_url='https://api.github.com/',
            client_kwargs={'scope': 'user:email'}
        )
        logger.info("✅ GitHub OAuth configured successfully")
    except Exception as e:
        logger.warning(f"❌ Failed to register GitHub OAuth: {e}")
else:
    logger.info("ℹ️ GitHub OAuth not configured (missing credentials)")

# User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    avatar_url = db.Column(db.String(200))
    provider = db.Column(db.String(50), nullable=False)
    provider_id = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    api_usages = db.relationship('ApiUsage', backref='user', lazy=True)

class ApiUsage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    api_key = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    usage_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_used = db.Column(db.DateTime)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Security headers
@app.after_request
def after_request(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

# Thread-safe dictionary for job progress tracking


class ThreadSafeDict(dict):
    """Simple thread-safe dictionary wrapper for storing job progress."""

    def __init__(self):
        super().__init__()
        self._lock = threading.RLock()

    def __getitem__(self, key: Any) -> Any:
        with self._lock:
            return super().__getitem__(key)

    def __setitem__(self, key: Any, value: Any) -> None:
        with self._lock:
            super().__setitem__(key, value)

    def __delitem__(self, key: Any) -> None:
        with self._lock:
            super().__delitem__(key)

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            return super().get(key, default)

    def pop(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            return super().pop(key, default)

    def items(self) -> list[tuple[Any, Any]]:
        with self._lock:
            return list(super().items())

    def keys(self) -> list[Any]:
        with self._lock:
            return list(super().keys())

    def values(self) -> list[Any]:
        with self._lock:
            return list(super().values())

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return super().__contains__(key)


conversion_progress: ThreadSafeDict = ThreadSafeDict()

# API Authentication decorator
def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = (request.headers.get('X-API-Key') or request.args.get('api_key', '')).strip()

        if not api_key:
            return jsonify({'error': 'Missing API key'}), 401

        admin_key = app.config.get('API_KEY')
        if admin_key and secrets.compare_digest(api_key, admin_key):
            return f(*args, **kwargs)

        key_record = ApiUsage.query.filter_by(api_key=api_key).first()
        if not key_record:
            return jsonify({'error': 'Invalid API key'}), 401

        key_record.usage_count = (key_record.usage_count or 0) + 1
        key_record.last_used = datetime.utcnow()
        db.session.commit()

        return f(*args, **kwargs)
    return decorated_function

# Function to validate YouTube URL
def is_valid_youtube_url(url):
    youtube_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})')
    return re.match(youtube_regex, url)

# Function to download YouTube video
def download_youtube_video(url, quality='720', progress_key=None, use_auth=True):
    """
    Download YouTube video using yt-dlp with authentication support
    """
    try:
        if progress_key:
            conversion_progress[progress_key] = 2
            logger.info("🚀 Initializing YouTube downloader...")
            
        logger.info(f"📥 Starting download for URL: {url}")
        
        # Create a temporary directory and file
        temp_dir = tempfile.mkdtemp()
        temp_filename = "video.%(ext)s"
        
        if progress_key:
            conversion_progress[progress_key] = 5
            logger.info("📋 Fetching video information...")
        
        # Progress hook for yt-dlp
        def progress_hook(d):
            if progress_key:
                if d['status'] == 'downloading':
                    try:
                        # Extract download percentage
                        downloaded = d.get('downloaded_bytes', 0)
                        total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                        
                        if total > 0:
                            # Map download progress to 10-20% of total conversion progress
                            download_percentage = (downloaded / total) * 10 + 10
                            conversion_progress[progress_key] = round(download_percentage, 1)
                    except Exception as e:
                        logger.debug(f"Progress update error: {e}")
                        
                elif d['status'] == 'finished':
                    if progress_key:
                        conversion_progress[progress_key] = 20
                        logger.info("✅ Download completed!")
        
        # yt-dlp options for maximum compatibility with anti-bot measures
        ydl_opts = {
            'format': f'best[height<={quality}][ext=mp4]/best[ext=mp4]/best',
            'outtmpl': os.path.join(temp_dir, temp_filename),
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'nocheckcertificate': False,
            'progress_hooks': [progress_hook],
            'retries': 8,
            'fragment_retries': 8,
            'socket_timeout': 30,
            # Bypass age restrictions and geo-blocking  
            'age_limit': 99,
            'geo_bypass': True,
            'geo_bypass_country': 'US',
            # Add random delays to seem more human
            'sleep_interval': 1,
            'max_sleep_interval': 3,
            # Additional anti-detection measures
            'writesubtitles': False,
            'writeautomaticsub': False,
            'allsubtitles': False,
            'ignoreerrors': False,
        }
        
        if progress_key:
            conversion_progress[progress_key] = 8
            logger.info("🎬 Selecting best quality stream...")
        
        # Try different strategies to bypass YouTube's bot detection
        strategies = [
            {
                'name': 'Android TV client with API key',
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android_embedded'],
                        'player_skip': ['configs'],
                    }
                },
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (SMART-TV; Linux; Tizen 2.4.0) AppleWebKit/538.1 (KHTML, like Gecko) Version/2.4.0 TV Safari/538.1',
                    'X-YouTube-Client-Name': '85',
                    'X-YouTube-Client-Version': '4.27',
                }
            },
            {
                'name': 'Android music client',
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android_music'],
                    }
                },
                'http_headers': {
                    'User-Agent': 'com.google.android.apps.youtube.music/5.16.51 (Linux; U; Android 11; US) gzip'
                }
            },
            {
                'name': 'Web embedded client',
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web_embedded'],
                        'player_skip': ['configs'],
                    }
                },
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
                    'Referer': 'https://www.youtube.com/',
                }
            },
            {
                'name': 'iOS embedded client',
                'extractor_args': {
                    'youtube': {
                        'player_client': ['ios_embedded'],
                    }
                },
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
                }
            },
            {
                'name': 'Android client with legacy API',
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android'],
                        'player_skip': ['configs'],
                    }
                },
                'http_headers': {
                    'User-Agent': 'com.google.android.youtube/17.36.4 (Linux; U; Android 12; US) gzip',
                    'X-Goog-Api-Key': 'AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w',
                }
            },
            {
                'name': 'Web client with desktop headers',
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web'],
                    }
                },
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36'
                }
            }
        ]
        
        # Try each strategy with delays
        for i, strategy in enumerate(strategies):
            try:
                logger.info(f"🔄 Trying strategy {i+1}/{len(strategies)}: {strategy['name']}")
                
                # Add delay between attempts to avoid rate limiting
                if i > 0:
                    delay = min(2 + i, 8)  # Progressive delay up to 8 seconds
                    logger.info(f"⏳ Waiting {delay} seconds to avoid rate limiting...")
                    time.sleep(delay)
                
                # Update options with current strategy
                current_opts = ydl_opts.copy()
                current_opts.update(strategy)
                
                # Download the video
                with yt_dlp.YoutubeDL(current_opts) as ydl:
                    # Extract info first
                    info = ydl.extract_info(url, download=False)
                    title = info.get('title', 'Unknown')
                    duration = info.get('duration', 0)
                    
                    if progress_key:
                        conversion_progress[progress_key] = 10
                        logger.info(f"📦 Video: {title} | Duration: {duration}s")
                        logger.info("⚡ Starting download...")
                    
                    # Now download
                    ydl.download([url])
                    
                    # If we get here, download was successful
                    logger.info(f"✅ Successfully downloaded using {strategy['name']}")
                    break
                    
            except Exception as strategy_error:
                error_str = str(strategy_error)
                logger.warning(f"❌ Strategy {i+1} failed: {error_str[:100]}...")
                
                # If this is a bot detection error and we have more strategies, continue
                if "Sign in to confirm you're not a bot" in error_str and i < len(strategies) - 1:
                    logger.info(f"🤖 Bot detection triggered, trying next strategy...")
                    continue
                elif i == len(strategies) - 1:  # Last strategy failed
                    raise strategy_error
                else:
                    continue
        
        # Find the downloaded file
        downloaded_files = [f for f in os.listdir(temp_dir) if f.startswith('video.')]
        if not downloaded_files:
            raise Exception("No video file was downloaded")
        
        video_path = os.path.join(temp_dir, downloaded_files[0])
        
        if progress_key:
            conversion_progress[progress_key] = 20
            logger.info(f"🎉 Download completed: {title}")
            
        return video_path, title, duration
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Error downloading video: {error_msg}")
        
        # Provide user-friendly error messages with suggestions
        if "Sign in to confirm you're not a bot" in error_msg:
            user_msg = """🤖 YouTube is aggressively blocking downloads right now. 

Suggestions to try:
• Wait 10-15 minutes and try again
• Try a different, less popular YouTube video
• Use shorter videos (under 10 minutes work better)
• Try older videos (uploaded more than 6 months ago)

This is temporary and should resolve soon. YouTube's anti-bot measures are particularly strict today."""
        elif "Video unavailable" in error_msg:
            user_msg = "This video is unavailable or private. Please check the URL and try a different public video."
        elif "age-restricted" in error_msg.lower():
            user_msg = "This video is age-restricted. Try a different video that doesn't require age verification."
        elif "geoblocked" in error_msg.lower() or "not available" in error_msg.lower():
            user_msg = "This video is not available in your region. Try a different video."
        elif "too many requests" in error_msg.lower():
            user_msg = "Too many requests to YouTube. Please wait 5-10 minutes and try again."
        else:
            user_msg = f'Download failed: {error_msg}. Please check the URL and try again.'
        
        if progress_key:
            conversion_progress[progress_key] = {
                'status': 'error',
                'message': user_msg
            }
        
        return None, None, None

# Function to create short clips
def create_short_clips(video_path, num_shorts, clip_duration, output_dir, progress_key):
    clips = []
    try:
        if progress_key:
            conversion_progress[progress_key] = 22
            logger.info("📹 Loading video file...")
        
        video = mp.VideoFileClip(video_path)
        total_duration = video.duration
        
        if progress_key:
            conversion_progress[progress_key] = 30
            logger.info(f"✅ Video loaded. Duration: {total_duration:.1f}s")
        
        # Calculate start times for each clip
        if num_shorts == 1:
            # Use the middle of the video for a single short
            start_times = [max(0, total_duration / 2 - clip_duration / 2)]
        else:
            # Distribute clips throughout the video, avoiding very start/end
            usable_duration = max(0, total_duration - clip_duration)
            if usable_duration <= 0:
                start_times = [0]
            else:
                segment_duration = usable_duration / (num_shorts - 1) if num_shorts > 1 else 0
                start_times = [i * segment_duration for i in range(num_shorts)]
        
        # Ensure clips don't exceed video duration
        start_times = [max(0, min(t, total_duration - clip_duration)) for t in start_times]
        
        # Update progress - 60% of total progress for clipping (30% to 90%)
        progress_per_clip = 60 / len(start_times)
        
        # Create each clip
        for i, start_time in enumerate(start_times):
            # Update progress at start of each clip
            current_progress = 30 + (i * progress_per_clip)
            if progress_key:
                conversion_progress[progress_key] = round(current_progress, 1)
                logger.info(f"🎬 Creating short {i+1}/{len(start_times)} (from {start_time:.1f}s)...")
            
            # Create the clip
            clip = video.subclip(start_time, min(start_time + clip_duration, total_duration))
            
            # Update progress after subclip extraction
            if progress_key:
                conversion_progress[progress_key] = round(current_progress + (progress_per_clip * 0.3), 1)
                logger.info(f"📐 Resizing short {i+1} to vertical format...")
            
            # Resize for vertical format (9:16) with fast processing
            target_width = 608  # 9:16 aspect ratio at 1080p height
            target_height = 1080
            
            # Calculate crop dimensions to maintain aspect ratio
            current_ratio = clip.w / clip.h
            target_ratio = 9/16
            
            if current_ratio > target_ratio:
                # Video is too wide, crop horizontally
                new_width = clip.h * target_ratio
                clip = clip.crop(x_center=clip.w/2, width=new_width, height=clip.h)
            else:
                # Video is too tall, crop vertically
                new_height = clip.w / target_ratio
                if new_height < clip.h:
                    clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=clip.w, height=new_height)
            
            # Resize to target resolution using fast method
            clip = clip.resize((target_width, target_height))
            
            # Update progress before writing
            if progress_key:
                conversion_progress[progress_key] = round(current_progress + (progress_per_clip * 0.6), 1)
                logger.info(f"⚡ Encoding short {i+1}...")
            
            # Output path
            output_path = os.path.join(output_dir, f"short_{i+1}.mp4")
            
            # Write video file with fastest settings optimized for speed
            clip.write_videofile(
                output_path, 
                codec='libx264',
                audio_codec='aac',
                preset='ultrafast',  # Fastest encoding preset
                threads=4,  # Use multiple CPU threads
                fps=30,
                bitrate='1200k',  # Lower bitrate for speed
                verbose=False,
                logger=None,
                temp_audiofile=None,  # Disable temp audio file
                remove_temp=True
            )
            
            clips.append(output_path)
            
            # Update progress after clip is complete
            if progress_key:
                conversion_progress[progress_key] = round(30 + ((i + 1) * progress_per_clip), 1)
                logger.info(f"✅ Short {i+1}/{len(start_times)} completed")
            
            clip.close()
        
        video.close()
        
        if progress_key:
            conversion_progress[progress_key] = 90
            logger.info("🎉 All shorts created successfully!")
        
        return clips
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Error creating clips: {error_msg}")
        
        # Provide user-friendly error messages
        if "progress_bar" in error_msg:
            user_msg = "Video processing error due to software compatibility. Please try again."
        elif "ANTIALIAS" in error_msg:
            user_msg = "Image processing error. Please update your system or try a different video."
        elif "codec" in error_msg.lower():
            user_msg = "Video encoding error. Please try with a different video format."
        elif "memory" in error_msg.lower() or "malloc" in error_msg.lower():
            user_msg = "Insufficient memory to process this video. Try a shorter video or reduce the number of clips."
        else:
            user_msg = f"Video processing failed: {error_msg}. Please try again with a different video."
        
        if progress_key:
            conversion_progress[progress_key] = {
                'status': 'error',
                'message': user_msg
            }
            
        # Clean up any partial files
        try:
            for clip_path in clips:
                if os.path.exists(clip_path):
                    os.unlink(clip_path)
        except:
            pass
            
        return []

# Function to add reaction video
def add_reaction_video(main_clip_path, reaction_clip_path, output_path, progress_key=None):
    try:
        if progress_key:
            conversion_progress[progress_key] = 95
            logger.info("🎭 Adding reaction overlay...")
            
        # Load the main clip
        main_clip = mp.VideoFileClip(main_clip_path)
        
        # Load the reaction clip and resize it
        reaction_clip = mp.VideoFileClip(reaction_clip_path)
        reaction_clip = reaction_clip.resize(width=main_clip.w)  # Match width
        
        # Calculate the height for the reaction clip (20% of total height)
        reaction_height = int(main_clip.h * 0.2)
        reaction_clip = reaction_clip.resize(height=reaction_height)
        
        # Resize main clip to 80% of original height
        main_height = main_clip.h - reaction_height
        main_clip = main_clip.resize(height=main_height)
        
        # Combine the clips vertically
        final_clip = mp.clips_array([[main_clip], [reaction_clip]])
        
        # Write the final video with optimized settings
        final_clip.write_videofile(
            output_path, 
            codec='libx264', 
            audio_codec='aac', 
            preset='ultrafast',
            fps=30,
            bitrate='1200k',
            verbose=False, 
            logger=None
        )
        
        # Close clips to free memory
        main_clip.close()
        reaction_clip.close()
        final_clip.close()
        
        if progress_key:
            conversion_progress[progress_key] = 98
            logger.info("✅ Reaction overlay added successfully")
            
        return output_path
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Error adding reaction: {error_msg}")
        if progress_key:
            conversion_progress[progress_key] = {
                'status': 'error',
                'message': f'Failed to add reaction overlay: {error_msg}'
            }
        return None

# Background processing function
def process_video_background(url, num_shorts, clip_duration, add_reaction, reaction_url, progress_key):
    try:
        # Create temporary directory for output
        output_dir = tempfile.mkdtemp()
        
        # Download the YouTube video
        video_path, title, duration = download_youtube_video(url, progress_key=progress_key)
        if not video_path:
            conversion_progress[progress_key] = {
                'status': 'error',
                'message': 'Failed to download video'
            }
            return
        
        # Create short clips
        shorts = create_short_clips(video_path, num_shorts, clip_duration, output_dir, progress_key)
        
        if not shorts:
            conversion_progress[progress_key] = {
                'status': 'error',
                'message': 'Failed to create video clips. Please try again with a different video or shorter duration.'
            }
            logger.error("❌ Video processing failed - no clips created")
            # Clean up video file
            try:
                os.unlink(video_path)
            except:
                pass
            return
        
        # Download reaction video if needed
        reaction_path = None
        if add_reaction and reaction_url:
            try:
                if progress_key:
                    conversion_progress[progress_key] = 92
                    logger.info("📥 Downloading reaction video...")
                response = requests.get(reaction_url, stream=True, timeout=15)
                response.raise_for_status()
                reaction_path = os.path.join(output_dir, "reaction.mp4")
                with open(reaction_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            except Exception as e:
                logger.warning(f"Failed to download reaction video: {str(e)}")
                add_reaction = False
        
        # Add reaction to each short if requested
        final_shorts = []
        if add_reaction and reaction_path:
            if progress_key:
                conversion_progress[progress_key] = 95
                logger.info("🎭 Adding reaction overlay...")
                
            for i, short_path in enumerate(shorts):
                output_path = os.path.join(output_dir, f"short_with_reaction_{i+1}.mp4")
                final_path = add_reaction_video(short_path, reaction_path, output_path, progress_key)
                if final_path:
                    final_shorts.append(final_path)
        else:
            final_shorts = shorts
        
        # Final completion
        if progress_key:
            conversion_progress[progress_key] = {
                'status': 'completed',
                'progress': 100,
                'shorts': final_shorts,
                'title': title,
                'duration': duration,
                'num_shorts': len(final_shorts),
                'progress_key': progress_key  # Include progress key for download links
            }
            logger.info(f"🎉 Conversion completed! Created {len(final_shorts)} shorts from '{title}'")
        
        # Clean up original video file
        try:
            os.unlink(video_path)
        except:
            pass
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Error in background processing: {error_msg}")
        conversion_progress[progress_key] = {
            'status': 'error', 
            'message': f'Processing failed: {error_msg}'
        }

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(413)
def too_large(error):
    return jsonify({'error': 'File too large'}), 413

# Health check endpoint
@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': time.time()})

@app.route('/robots.txt')
def robots_txt():
    return send_file('static/robots.txt', mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap_xml():
    from flask import Response

    domain = request.host_url.rstrip('/')
    lastmod = datetime.utcnow().date().isoformat()
    static_pages = [
        ('/', 'weekly', '1.0'),
        ('/api-access', 'monthly', '0.8'),
        ('/api-dashboard', 'monthly', '0.7'),
        ('/login', 'monthly', '0.6'),
    ]

    url_entries = "\n".join(
        f"    <url>\n"
        f"        <loc>{domain}{path}</loc>\n"
        f"        <lastmod>{lastmod}</lastmod>\n"
        f"        <changefreq>{changefreq}</changefreq>\n"
        f"        <priority>{priority}</priority>\n"
        f"    </url>"
        for path, changefreq, priority in static_pages
    )

    sitemap = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
        f"{url_entries}\n"
        "</urlset>"
    )
    return Response(sitemap, mimetype='application/xml')


# Routes
@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    return render_template('index.html')

# Online tools page (123apps.com links)
@app.route('/online-tools')
def online_tools():
    return render_template('online_tools.html')

@app.route('/api-access')
def api_access():
    return render_template('api_access.html')

# Terms and Privacy pages
@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

# Add template context processor
@app.context_processor
def inject_globals():
    from datetime import datetime
    return {
        'current_year': datetime.now().year
    }

# Authentication routes
@app.route('/login')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('index'))

@app.route('/profile')
@login_required
def profile():
    # Get user's API keys
    api_keys = ApiUsage.query.filter_by(user_id=current_user.id).all()
    return render_template('profile.html', api_keys=api_keys)

@app.route('/auth/<provider>')
def auth_redirect(provider):
    if provider == 'google':
        if not google:
            flash('Google OAuth is not configured. Please contact the administrator.', 'error')
            return redirect(url_for('login'))
        redirect_uri = url_for('auth_callback', provider='google', _external=True)
        return google.authorize_redirect(redirect_uri)
    elif provider == 'github':
        if not github:
            flash('GitHub OAuth is not configured. Please contact the administrator.', 'error')
            return redirect(url_for('login'))
        redirect_uri = url_for('auth_callback', provider='github', _external=True)
        return github.authorize_redirect(redirect_uri)
    else:
        flash('Invalid authentication provider.', 'error')
        return redirect(url_for('login'))

@app.route('/auth/<provider>/callback')
def auth_callback(provider):
    try:
        if provider == 'google':
            if not google:
                flash('Google OAuth is not configured.', 'error')
                return redirect(url_for('login'))
            token = google.authorize_access_token()
            user_info = token.get('userinfo')
            if user_info:
                email = user_info['email']
                name = user_info['name']
                avatar_url = user_info.get('picture', '')
                provider_id = user_info['sub']
            else:
                flash('Failed to get user information from Google.', 'error')
                return redirect(url_for('login'))
                
        elif provider == 'github':
            if not github:
                flash('GitHub OAuth is not configured.', 'error')
                return redirect(url_for('login'))
            token = github.authorize_access_token()
            resp = github.get('user', token=token)
            user_info = resp.json()
            email = user_info['email']
            if not email:
                # Get primary email if not public
                resp = github.get('user/emails', token=token)
                emails = resp.json()
                for email_obj in emails:
                    if email_obj['primary']:
                        email = email_obj['email']
                        break
            name = user_info['name'] or user_info['login']
            avatar_url = user_info.get('avatar_url', '')
            provider_id = str(user_info['id'])
        else:
            flash('Invalid authentication provider.', 'error')
            return redirect(url_for('login'))

        # Check if user exists
        user = User.query.filter_by(email=email).first()
        if not user:
            # Create new user
            user = User(
                email=email,
                name=name,
                avatar_url=avatar_url,
                provider=provider,
                provider_id=provider_id
            )
            db.session.add(user)
            db.session.commit()
            flash(f'Welcome {name}! Your account has been created.', 'success')
        else:
            # Update existing user info
            user.name = name
            user.avatar_url = avatar_url
            if user.provider != provider:
                user.provider = provider
                user.provider_id = provider_id
            db.session.commit()
            flash(f'Welcome back, {name}!', 'success')

        login_user(user)
        return redirect(url_for('index'))

    except Exception as e:
        logger.error(f"OAuth callback error: {str(e)}")
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('login'))

@app.route('/generate-api-key', methods=['POST'])
def generate_api_key():
    """Generate a new API key for a user."""
    try:
        data = request.get_json(silent=True) or {}
        email = data.get('email', '').strip().lower()
        name = data.get('name', '').strip()

        if not email or not name:
            return jsonify({'error': 'Email and name are required'}), 400

        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            return jsonify({'error': 'Invalid email address'}), 400

        user = User.query.filter_by(email=email).first()

        if not user:
            user = User(
                email=email,
                name=name,
                provider='api',
                provider_id=f'api-{secrets.token_hex(8)}',
            )
            db.session.add(user)
            db.session.flush()
        else:
            if user.name != name and name:
                user.name = name

        existing_key = ApiUsage.query.filter_by(user_id=user.id).first()
        if existing_key:
            logger.info("API key requested for %s (existing)", email)
            db.session.commit()
            return jsonify({
                'api_key': existing_key.api_key,
                'message': 'API key already exists for this email',
                'existing': True
            })

        api_key = f"st_{secrets.token_hex(24)}"
        api_usage = ApiUsage(
            user_id=user.id,
            api_key=api_key,
            name=f"{name}'s API key" if name else 'API key',
            usage_count=0,
            created_at=datetime.utcnow()
        )
        db.session.add(api_usage)
        db.session.commit()

        logger.info("Generated API key for user: %s", email)

        return jsonify({
            'api_key': api_key,
            'message': 'API key generated successfully',
            'existing': False
        }), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error generating API key: {str(e)}")
        return jsonify({'error': 'Failed to generate API key'}), 500

@app.route('/api-dashboard')
def api_dashboard():
    return render_template('api_dashboard.html')

@app.route('/api-key-info', methods=['POST'])
def api_key_info():
    """Get information about an API key."""
    try:
        data = request.get_json(silent=True) or {}
        api_key = data.get('api_key', '').strip()

        if not api_key:
            return jsonify({'error': 'API key is required'}), 400

        key_record = ApiUsage.query.filter_by(api_key=api_key).first()
        if not key_record:
            return jsonify({'error': 'Invalid API key'}), 404

        user = key_record.user
        return jsonify({
            'name': user.name if user else key_record.name,
            'email': user.email if user else None,
            'created_at': key_record.created_at.isoformat() if key_record.created_at else None,
            'usage_count': key_record.usage_count,
            'last_used': key_record.last_used.isoformat() if key_record.last_used else None
        })

    except Exception as e:
        logger.error(f"Error getting API key info: {str(e)}")
        return jsonify({'error': 'Failed to get API key info'}), 500

@app.route('/convert', methods=['POST'])
def convert():
    data = request.json
    url = data.get('url')
    num_shorts = int(data.get('num_shorts', 3))
    clip_duration = int(data.get('clip_duration', 15))
    add_reaction = data.get('add_reaction', False)
    reaction_url = data.get('reaction_url', '')
    
    if not url or not is_valid_youtube_url(url):
        return jsonify({'error': 'Invalid YouTube URL'}), 400
    
    # Generate a unique progress key
    progress_key = f"conversion_{int(time.time())}_{hash(url)}"
    conversion_progress[progress_key] = 0
    
    # Start processing in background thread
    thread = threading.Thread(
        target=process_video_background,
        args=(url, num_shorts, clip_duration, add_reaction, reaction_url, progress_key)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({'progress_key': progress_key})

@app.route('/progress/<progress_key>')
def progress(progress_key):
    progress_data = conversion_progress.get(progress_key, 0)
    
    # Handle different progress data formats
    if isinstance(progress_data, dict):
        if progress_data.get('status') == 'error':
            return jsonify({
                'progress': 0,
                'status': 'error',
                'message': progress_data.get('message', 'Unknown error')
            })
        elif progress_data.get('status') == 'completed':
            return jsonify({
                'progress': 100,
                'status': 'completed',
                'num_shorts': progress_data.get('num_shorts', 0),
                'title': progress_data.get('title', 'Unknown'),
                'shorts': progress_data.get('shorts', []),
                'progress_key': progress_key
            })
        else:
            # In-progress with additional data
            return jsonify({
                'progress': progress_data.get('progress', 0),
                'status': 'processing'
            })
    else:
        # Simple numeric progress
        return jsonify({
            'progress': progress_data,
            'status': 'processing' if progress_data > 0 and progress_data < 100 else 'pending'
        })

@app.route('/download/<progress_key>/<int:short_index>')
def download(progress_key, short_index):
    progress_data = conversion_progress.get(progress_key)
    if not progress_data or progress_data.get('status') != 'completed':
        return jsonify({'error': 'Conversion not complete or not found'}), 404
    
    shorts = progress_data.get('shorts', [])
    if short_index < 0 or short_index >= len(shorts):
        return jsonify({'error': 'Invalid short index'}), 404
    
    short_path = shorts[short_index]
    title = progress_data.get('title', 'short')
    filename = f"{secure_filename(title)}_{short_index+1}.mp4"
    
    return send_file(short_path, as_attachment=True, download_name=filename)

# ========== API ENDPOINTS ==========

@app.route('/api/v1/info', methods=['GET'])
@require_api_key
def api_info():
    """Get API information and supported operations."""
    return jsonify({
        'api_version': '1.0',
        'service': 'YouTube Shorts Trimmer',
        'endpoints': {
            'convert': '/api/v1/convert',
            'status': '/api/v1/status/<job_id>',
            'download': '/api/v1/download/<job_id>/<short_index>',
            'list_jobs': '/api/v1/jobs'
        },
        'supported_formats': ['mp4'],
        'max_file_size': '100MB',
        'features': ['youtube_download', 'video_trimming', 'vertical_conversion', 'reaction_overlay']
    })

@app.route('/api/v1/convert', methods=['POST'])
@require_api_key
def api_convert():
    """Start video conversion job via API."""
    try:
        # Get JSON data
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400
        
        data = request.get_json()
        
        # Validate required fields
        if not data.get('url'):
            return jsonify({'error': 'YouTube URL is required'}), 400
        
        url = data['url']
        num_shorts = int(data.get('num_shorts', 3))
        clip_duration = int(data.get('clip_duration', 15))
        add_reaction = data.get('add_reaction', False)
        reaction_url = data.get('reaction_url', '')
        
        # Validate input
        if not is_valid_youtube_url(url):
            return jsonify({'error': 'Invalid YouTube URL'}), 400
        
        if num_shorts < 1 or num_shorts > 10:
            return jsonify({'error': 'Number of shorts must be between 1 and 10'}), 400
        
        if clip_duration < 5 or clip_duration > 300:
            return jsonify({'error': 'Clip duration must be between 5 and 300 seconds'}), 400
        
        if add_reaction and not reaction_url:
            return jsonify({'error': 'Reaction URL required when add_reaction is true'}), 400
        
        # Generate job ID
        job_id = f"api_{int(time.time())}_{secrets.token_hex(8)}"
        conversion_progress[job_id] = {'status': 'queued', 'progress': 0}
        
        # Start processing in background
        thread = threading.Thread(
            target=process_video_background,
            args=(url, num_shorts, clip_duration, add_reaction, reaction_url, job_id)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'job_id': job_id,
            'status': 'queued',
            'message': 'Video conversion started',
            'estimated_time': f'{num_shorts * 30}-{num_shorts * 60} seconds'
        }), 202
        
    except Exception as e:
        logger.error(f"API convert error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/v1/status/<job_id>', methods=['GET'])
@require_api_key
def api_status(job_id):
    """Get job status and progress."""
    if job_id not in conversion_progress:
        return jsonify({'error': 'Job not found'}), 404
    
    progress_data = conversion_progress[job_id]
    
    if isinstance(progress_data, dict):
        if progress_data.get('status') == 'completed':
            shorts_count = len(progress_data.get('shorts', []))
            return jsonify({
                'job_id': job_id,
                'status': 'completed',
                'progress': 100,
                'title': progress_data.get('title'),
                'shorts_count': shorts_count,
                'download_urls': [
                    f'/api/v1/download/{job_id}/{i}' for i in range(shorts_count)
                ]
            })
        elif progress_data.get('status') == 'error':
            return jsonify({
                'job_id': job_id,
                'status': 'error',
                'error': progress_data.get('message', 'Unknown error')
            })
    
    # Still processing
    progress = progress_data if isinstance(progress_data, (int, float)) else 0
    stage = 'processing'
    if progress < 20:
        stage = 'downloading'
    elif progress < 90:
        stage = 'trimming'
    else:
        stage = 'finalizing'
    
    return jsonify({
        'job_id': job_id,
        'status': 'processing',
        'progress': progress,
        'stage': stage
    })

@app.route('/api/v1/download/<job_id>/<int:short_index>', methods=['GET'])
@require_api_key
def api_download(job_id, short_index):
    """Download a specific short video."""
    if job_id not in conversion_progress:
        return jsonify({'error': 'Job not found'}), 404
    
    progress_data = conversion_progress[job_id]
    if not isinstance(progress_data, dict) or progress_data.get('status') != 'completed':
        return jsonify({'error': 'Job not completed yet'}), 400
    
    shorts = progress_data.get('shorts', [])
    if short_index < 0 or short_index >= len(shorts):
        return jsonify({'error': 'Invalid short index'}), 404
    
    short_path = shorts[short_index]
    title = progress_data.get('title', 'short')
    filename = f"{secure_filename(title)}_{short_index+1}.mp4"
    
    return send_file(short_path, as_attachment=True, download_name=filename)

@app.route('/api/v1/download/<job_id>/base64/<int:short_index>', methods=['GET'])
@require_api_key
def api_download_base64(job_id, short_index):
    """Download a specific short video as base64 (useful for n8n)."""
    if job_id not in conversion_progress:
        return jsonify({'error': 'Job not found'}), 404
    
    progress_data = conversion_progress[job_id]
    if not isinstance(progress_data, dict) or progress_data.get('status') != 'completed':
        return jsonify({'error': 'Job not completed yet'}), 400
    
    shorts = progress_data.get('shorts', [])
    if short_index < 0 or short_index >= len(shorts):
        return jsonify({'error': 'Invalid short index'}), 404
    
    try:
        short_path = shorts[short_index]
        title = progress_data.get('title', 'short')
        filename = f"{secure_filename(title)}_{short_index+1}.mp4"
        
        with open(short_path, 'rb') as f:
            video_data = f.read()
            video_base64 = base64.b64encode(video_data).decode('utf-8')
        
        return jsonify({
            'filename': filename,
            'content_type': 'video/mp4',
            'size': len(video_data),
            'data': video_base64
        })
        
    except Exception as e:
        logger.error(f"Error encoding video to base64: {str(e)}")
        return jsonify({'error': 'Failed to encode video'}), 500

@app.route('/api/v1/jobs', methods=['GET'])
@require_api_key
def api_list_jobs():
    """List all jobs and their status."""
    jobs = []
    for job_id, progress_data in conversion_progress.items():
        if job_id.startswith('api_'):  # Only show API jobs
            if isinstance(progress_data, dict):
                status = progress_data.get('status', 'unknown')
                jobs.append({
                    'job_id': job_id,
                    'status': status,
                    'title': progress_data.get('title'),
                    'shorts_count': len(progress_data.get('shorts', [])) if status == 'completed' else 0
                })
            else:
                jobs.append({
                    'job_id': job_id,
                    'status': 'processing',
                    'progress': progress_data
                })
    
    return jsonify({
        'jobs': jobs,
        'total': len(jobs)
    })

@app.route('/api/v1/cleanup/<job_id>', methods=['DELETE'])
@require_api_key
def api_cleanup(job_id):
    """Clean up job data and temporary files."""
    if job_id not in conversion_progress:
        return jsonify({'error': 'Job not found'}), 404
    
    # Remove from progress tracking
    del conversion_progress[job_id]
    
    return jsonify({
        'message': f'Job {job_id} cleaned up successfully'
    })

@app.route('/test-youtube', methods=['GET', 'POST'])
def test_youtube():
    """Test endpoint to quickly check if YouTube downloads are working"""
    if request.method == 'GET':
        return jsonify({
            'message': 'YouTube Download Test Endpoint',
            'usage': 'POST with {"url": "youtube_url"} to test download',
            'suggested_test_urls': [
                'https://www.youtube.com/watch?v=jNQXAC9IVRw',  # Short video
                'https://www.youtube.com/watch?v=9bZkp7q19f0',  # Classic video
                'https://www.youtube.com/watch?v=dQw4w9WgXcQ'   # Rick Roll (often works)
            ]
        })
    
    try:
        data = request.get_json()
        test_url = data.get('url', '')
        
        if not test_url or not is_valid_youtube_url(test_url):
            return jsonify({'error': 'Invalid YouTube URL'}), 400
        
        # Quick test without full conversion
        logger.info(f"🧪 Testing YouTube URL: {test_url}")
        
        # Try just extracting info (faster than full download)
        test_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'skip_download': True,  # Just test metadata extraction
            'extractor_args': {
                'youtube': {
                    'player_client': ['android_embedded'],
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (SMART-TV; Linux; Tizen 2.4.0) AppleWebKit/538.1',
            }
        }
        
        with yt_dlp.YoutubeDL(test_opts) as ydl:
            info = ydl.extract_info(test_url, download=False)
            title = info.get('title', 'Unknown')
            duration = info.get('duration', 0)
            uploader = info.get('uploader', 'Unknown')
            
        return jsonify({
            'status': 'success',
            'message': 'YouTube URL is accessible!',
            'title': title,
            'duration': f"{duration} seconds",
            'uploader': uploader,
            'ready_for_conversion': True
        })
        
    except Exception as e:
        error_msg = str(e)
        logger.warning(f"🧪 Test failed: {error_msg}")
        
        if "Sign in to confirm you're not a bot" in error_msg:
            status = 'bot_detection'
            message = 'YouTube bot detection active. Try a different video or wait 10-15 minutes.'
        else:
            status = 'error'
            message = f'Test failed: {error_msg}'
        
        return jsonify({
            'status': status,
            'message': message,
            'ready_for_conversion': False
        }), 200  # Not a server error, just YouTube blocking

# Cleanup function for old temporary files and job data
def cleanup_old_data():
    """Clean up old conversion data and temporary files"""
    try:
        import shutil
        current_time = time.time()
        # Clean up jobs older than 1 hour
        expired_jobs = []
        for job_id, data in conversion_progress.items():
            if isinstance(data, dict):
                # Check if job is older than 1 hour (3600 seconds)
                job_age = current_time - int(job_id.split('_')[1]) if '_' in job_id else 0
                if job_age > 3600:
                    expired_jobs.append(job_id)
                    # Clean up temporary files if they exist
                    if data.get('status') == 'completed' and 'shorts' in data:
                        for short_path in data['shorts']:
                            try:
                                if os.path.exists(short_path):
                                    # Remove the file and its directory if empty
                                    os.unlink(short_path)
                                    parent_dir = os.path.dirname(short_path)
                                    if os.path.exists(parent_dir) and not os.listdir(parent_dir):
                                        shutil.rmtree(parent_dir)
                            except Exception as e:
                                logger.debug(f"Error cleaning up file {short_path}: {e}")
        
        # Remove expired jobs from memory
        for job_id in expired_jobs:
            del conversion_progress[job_id]
            
        if expired_jobs:
            logger.info(f"🧹 Cleaned up {len(expired_jobs)} expired jobs")
            
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")

# Schedule cleanup to run periodically (in production, use a proper task scheduler)
import threading
def periodic_cleanup():
    while True:
        time.sleep(3600)  # Run every hour
        cleanup_old_data()

# Start cleanup thread
cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
cleanup_thread.start()

# Initialize database
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    
    logger.info(f"🚀 Starting YouTube Shorts Trimmer on port {port}")
    logger.info(f"🔧 Debug mode: {debug}")
    logger.info(f"🔐 OAuth providers: Google={bool(google)}, GitHub={bool(github)}")
    logger.info(f"🔑 Admin API key configured: {bool(app.config.get('API_KEY'))}")
    logger.info(f"💾 Database: {app.config.get('SQLALCHEMY_DATABASE_URI', 'Not configured')}")
    logger.info("✅ All systems ready!")
    
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)

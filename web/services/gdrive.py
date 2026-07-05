"""
Google Drive service for Vignette.
OAuth 2.0 + Drive API v3 via stdlib urllib (zero external dependencies).
"""

import json
import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger("vignette.gdrive")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"

# Only request read-only access to files
SCOPES = "https://www.googleapis.com/auth/drive.readonly"


def get_auth_url(client_id, redirect_uri, state=None):
    """Build the Google OAuth 2.0 authorization URL.

    Pass a random `state` and verify it in the callback to prevent OAuth
    login-CSRF (Google requires a state nonce)."""
    fields = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        fields["state"] = state
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(fields)}"


def exchange_code(client_id, client_secret, code, redirect_uri):
    """Exchange authorization code for access + refresh tokens."""
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(GOOGLE_TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.loads(resp.read().decode())
        logger.info("Google Drive: tokens obtained")
        return tokens
    except Exception as e:
        logger.error(f"Google Drive token exchange failed: {e}")
        return None


def refresh_access_token(client_id, client_secret, refresh_token):
    """Use a refresh token to get a new access token.

    Returns the full token dict (access_token + expires_in) so the caller can
    track expiry, or None on failure."""
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(GOOGLE_TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"Google Drive token refresh failed: {e}")
        return None


def _api_get(url, access_token):
    """Make an authenticated GET request to Google API."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {access_token}",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def list_images(access_token, page_token=None, page_size=30):
    """List image files from Google Drive.

    Returns dict with 'files' list and optional 'nextPageToken'.
    Each file: {id, name, mimeType, thumbnailLink, modifiedTime, size}
    """
    query = "mimeType contains 'image/' and trashed = false"
    params = urllib.parse.urlencode({
        "q": query,
        "fields": "nextPageToken,files(id,name,mimeType,thumbnailLink,modifiedTime,size)",
        "orderBy": "modifiedTime desc",
        "pageSize": page_size,
        **({"pageToken": page_token} if page_token else {}),
    })
    url = f"{DRIVE_API}/files?{params}"
    try:
        result = _api_get(url, access_token)
        return result
    except Exception as e:
        logger.error(f"Google Drive list failed: {e}")
        return {"files": [], "error": str(e)}


def download_file(access_token, file_id, dest_path):
    """Download a file from Google Drive to local path.

    Returns True on success.
    """
    url = f"{DRIVE_API}/files/{file_id}?alt=media"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {access_token}",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(dest_path, 'wb') as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
        logger.info(f"Google Drive: downloaded {file_id} -> {dest_path}")
        return True
    except Exception as e:
        logger.error(f"Google Drive download failed: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False

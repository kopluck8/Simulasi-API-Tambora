"""Google Drive (REST v3) dengan OAuth refresh token milik akun Google Anda.

Akun Gmail pribadi tidak bisa memakai service account untuk menyimpan file (service account tidak
punya kuota My Drive), jadi dipakai OAuth 'Desktop app' + refresh token. Rahasia diambil dari
environment: GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN.
Kalau Anda memakai Shared Drive (Google Workspace), service account juga bisa:
GDRIVE_SERVICE_ACCOUNT_JSON (isi file JSON).
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse

import requests

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive"
FOLDER = "application/vnd.google-apps.folder"


class Drive:
    def __init__(self, token=None):
        self._token = token
        self._exp = 0

    # ----------------------------------------------------------- otentikasi
    def token(self):
        if self._token and time.time() < self._exp - 60:
            return self._token
        if os.environ.get("GDRIVE_REFRESH_TOKEN"):
            r = requests.post("https://oauth2.googleapis.com/token", data=dict(
                client_id=os.environ["GDRIVE_CLIENT_ID"], client_secret=os.environ["GDRIVE_CLIENT_SECRET"],
                refresh_token=os.environ["GDRIVE_REFRESH_TOKEN"], grant_type="refresh_token"), timeout=60)
            r.raise_for_status()
            d = r.json()
        elif os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON"):
            d = _service_account_token(json.loads(os.environ["GDRIVE_SERVICE_ACCOUNT_JSON"]))
        else:
            raise RuntimeError("Rahasia Google Drive belum diisi (GDRIVE_CLIENT_ID/SECRET/REFRESH_TOKEN).")
        self._token, self._exp = d["access_token"], time.time() + int(d.get("expires_in", 3600))
        return self._token

    def _h(self):
        return {"Authorization": "Bearer " + self.token()}

    # ---------------------------------------------------------------- dasar
    def find(self, name, parent, folder=None):
        q = f"name = '{name.replace(chr(39), chr(92) + chr(39))}' and '{parent}' in parents and trashed = false"
        if folder is True:
            q += f" and mimeType = '{FOLDER}'"
        r = requests.get(API + "/files", headers=self._h(), timeout=60,
                         params=dict(q=q, fields="files(id,name,mimeType,size,modifiedTime,md5Checksum)",
                                     supportsAllDrives="true", includeItemsFromAllDrives="true"))
        r.raise_for_status()
        f = r.json()["files"]
        return f[0] if f else None

    def ls(self, parent):
        out, page = [], None
        while True:
            p = dict(q=f"'{parent}' in parents and trashed = false", pageSize=1000,
                     fields="nextPageToken,files(id,name,mimeType,size,modifiedTime,md5Checksum)",
                     supportsAllDrives="true", includeItemsFromAllDrives="true")
            if page:
                p["pageToken"] = page
            r = requests.get(API + "/files", headers=self._h(), params=p, timeout=60)
            r.raise_for_status()
            d = r.json()
            out += d["files"]
            page = d.get("nextPageToken")
            if not page:
                return out

    def mkdir(self, name, parent):
        f = self.find(name, parent, folder=True)
        if f:
            return f["id"]
        r = requests.post(API + "/files", headers=self._h(), params=dict(supportsAllDrives="true"), timeout=60,
                          json=dict(name=name, mimeType=FOLDER, parents=[parent]))
        r.raise_for_status()
        return r.json()["id"]

    def path(self, parts, root):
        fid = root
        for p in [x for x in parts.strip("/").split("/") if x]:
            fid = self.mkdir(p, fid)
        return fid

    def download(self, file_id, dest):
        with requests.get(f"{API}/files/{file_id}", headers=self._h(), stream=True, timeout=600,
                          params=dict(alt="media", supportsAllDrives="true")) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 22):
                    f.write(chunk)
        return dest

    def upload(self, src, parent, name=None):
        """Unggah (atau timpa bila nama sama) dengan resumable upload."""
        name = name or os.path.basename(src)
        old = self.find(name, parent)
        meta = {"name": name} if old else {"name": name, "parents": [parent]}
        size = os.path.getsize(src)
        url = (f"{UPLOAD}/{old['id']}" if old else UPLOAD) + "?uploadType=resumable&supportsAllDrives=true"
        r = requests.request("PATCH" if old else "POST", url, timeout=60,
                             headers={**self._h(), "X-Upload-Content-Length": str(size)}, json=meta)
        r.raise_for_status()
        loc = r.headers["Location"]
        chunk = 32 * 1024 * 1024
        with open(src, "rb") as f:
            pos = 0
            while True:
                data = f.read(chunk)
                end = pos + len(data) - 1
                hdr = {"Content-Length": str(len(data)),
                       "Content-Range": f"bytes {pos}-{end}/{size}" if data else f"bytes */{size}"}
                rr = requests.put(loc, headers=hdr, data=data, timeout=600)
                if rr.status_code in (200, 201):
                    return rr.json()["id"]
                if rr.status_code != 308:
                    rr.raise_for_status()
                pos = end + 1

    def sync_down(self, folder_id, dest, names=None):
        """Unduh isi folder Drive ke dest (lewati file yang ukurannya sama)."""
        os.makedirs(dest, exist_ok=True)
        got = []
        for f in self.ls(folder_id):
            if f["mimeType"] == FOLDER or (names and f["name"] not in names):
                continue
            p = os.path.join(dest, f["name"])
            if os.path.exists(p) and str(os.path.getsize(p)) == f.get("size"):
                got.append(p)
                continue
            print("unduh", f["name"], f.get("size"))
            got.append(self.download(f["id"], p))
        return got


def _service_account_token(info):
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    now = int(time.time())
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=")
    msg = b(dict(alg="RS256", typ="JWT")) + b"." + b(dict(iss=info["client_email"], scope=SCOPE,
                                                         aud="https://oauth2.googleapis.com/token", iat=now, exp=now + 3600))
    key = serialization.load_pem_private_key(info["private_key"].encode(), None)
    sig = base64.urlsafe_b64encode(key.sign(msg, padding.PKCS1v15(), hashes.SHA256())).rstrip(b"=")
    r = requests.post("https://oauth2.googleapis.com/token", timeout=60, data=dict(
        grant_type="urn:ietf:params:oauth:grant-type:jwt-bearer", assertion=(msg + b"." + sig).decode()))
    r.raise_for_status()
    return r.json()


def authorize_interactive(client_id, client_secret, port=8765):
    """Sekali saja di komputer Anda: buka browser, izinkan, cetak refresh token."""
    import http.server
    import threading
    import webbrowser
    redirect = f"http://127.0.0.1:{port}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(dict(
        client_id=client_id, redirect_uri=redirect, response_type="code", scope=SCOPE,
        access_type="offline", prompt="consent"))
    box = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            box["code"] = q.get("code", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write("Selesai. Tutup tab ini dan kembali ke terminal.".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    print("Buka URL ini bila browser tidak terbuka otomatis:\n" + url)
    webbrowser.open(url)
    while "code" not in box:
        time.sleep(0.5)
    r = requests.post("https://oauth2.googleapis.com/token", timeout=60, data=dict(
        code=box["code"], client_id=client_id, client_secret=client_secret, redirect_uri=redirect,
        grant_type="authorization_code"))
    r.raise_for_status()
    return r.json()["refresh_token"]

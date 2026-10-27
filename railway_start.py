"""Railway entrypoint: explicit storage-setup maintenance or guarded application.

Storage setup never imports the application or SQLite and never initializes data.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


class StorageSetupHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass  # No request bodies, credentials, or query strings in service logs.

    def respond(self):
        healthy=self.command in ('GET','HEAD') and urlsplit(self.path).path=='/api/health'
        status=200 if healthy else 503
        payload=json.dumps({'status':'storage-setup','mode':'storage-setup',
                            'application_ready':False,'database_access':'disabled'}).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if not healthy:self.send_header('Retry-After','300')
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(payload)

    do_GET=respond
    do_HEAD=respond
    do_POST=respond
    do_PUT=respond
    do_PATCH=respond
    do_DELETE=respond
    do_OPTIONS=respond


def check_setup_volume():
    """Only inspect configuration/directories; never create or open a DB file."""
    mount=os.environ.get('RAILWAY_VOLUME_MOUNT_PATH','')
    target=os.environ.get('EHKAM_DB_PATH','')
    if not mount or not Path(mount).is_absolute() or not Path(mount).is_dir():
        raise RuntimeError('storage-setup requires an existing mounted volume')
    if not target or not Path(target).is_absolute():
        raise RuntimeError('storage-setup requires an absolute EHKAM_DB_PATH')
    if not Path(target).resolve().is_relative_to(Path(mount).resolve()):
        raise RuntimeError('EHKAM_DB_PATH must be inside the mounted volume')
    if not os.access(mount,os.W_OK|os.X_OK):
        raise RuntimeError('Mounted volume is not writable by the service user')


def main():
    mode=os.environ.get('EHKAM_START_MODE','app')
    if mode=='app':
        if os.environ.get('EHKAM_REQUIRE_EXISTING_DB','1')!='1':
            raise RuntimeError('app mode requires EHKAM_REQUIRE_EXISTING_DB=1')
        os.environ['EHKAM_REQUIRE_EXISTING_DB']='1'
        # Replace this process; server.py retains all existing DB/identity guards.
        os.execv(sys.executable,[sys.executable,'-u',str(Path(__file__).with_name('server.py'))])
    elif mode=='storage-setup':
        check_setup_volume()
        port=int(os.environ.get('PORT','8765'))
        http=ThreadingHTTPServer(('0.0.0.0',port),StorageSetupHandler)
        print('Storage setup maintenance active; application and database access disabled.',flush=True)
        try:http.serve_forever()
        except KeyboardInterrupt:pass
        finally:http.server_close()
    else:
        raise RuntimeError('Unsupported EHKAM_START_MODE; expected app or storage-setup')


if __name__=='__main__':main()

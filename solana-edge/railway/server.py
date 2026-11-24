import os, time, json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import worker

INTERVAL=30
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?')[0] not in ('/','/health','/api/paper'):
            self.send_error(404); return
        if self.path.split('?')[0]=='/health':
            body=b'{"process":"running","mode":"PAPER"}'
        else:
            try: body=worker.STATUS.read_bytes()
            except FileNotFoundError: body=b'{"status":"WAITING","updated_at":null,"mode":"PAPER","wallet_connected":false}'
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('Access-Control-Allow-Origin','*')
        self.end_headers()
        self.wfile.write(body)
    def log_message(self,*args): pass

def loop():
    while True:
        begin=time.monotonic()
        try: worker.main()
        except Exception as e: print('Worker exception:',repr(e),flush=True)
        time.sleep(max(5,INTERVAL-(time.monotonic()-begin)))

if __name__=='__main__':
    if not Path('/data').is_dir() or not os.path.ismount('/data'):
        raise RuntimeError('Persistent /data volume required')
    threading.Thread(target=loop,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),Handler).serve_forever()

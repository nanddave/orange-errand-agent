"""Tiny Chrome DevTools client (websockets sync) plus a visible demo cursor."""
import json, subprocess, time, urllib.request, os, sys
from websockets.sync.client import connect

CHROME = os.path.expanduser("~/Library/Caches/ms-playwright/chromium-1248/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
PORT = 9333


def launch(url, w=1360, h=900, x=40, y=40, profile="/private/tmp/claude-501/chrome-demo-profile"):
    subprocess.run(["rm", "-rf", profile])
    proc = subprocess.Popen([CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
                             f"--app={url}", f"--window-size={w},{h}", f"--window-position={x},{y}",
                             "--test-type", "--no-first-run", "--no-default-browser-check", "--disable-infobars",
                             "--force-device-scale-factor=2", "--hide-crash-restore-bubble"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
            page = [t for t in tabs if t["type"] == "page" and t["url"].startswith(url[:21])]
            if page:
                return proc, Page(page[0]["webSocketDebuggerUrl"])
        except Exception:
            time.sleep(0.5)
    raise SystemExit("chrome did not start")


class Page:
    def __init__(self, ws_url):
        self.ws = connect(ws_url, max_size=None)
        self.i = 0

    def call(self, method, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.i:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

    def shot(self, path):
        import base64
        open(path, "wb").write(base64.b64decode(self.call("Page.captureScreenshot", format="png")["data"]))


CURSOR_JS = r"""
(()=>{if(window.__cur)return;
const c=document.createElement('div');c.style.cssText='position:fixed;z-index:99999;left:0;top:0;width:22px;height:22px;pointer-events:none;transition:transform .55s cubic-bezier(.45,.05,.2,1);transform:translate(640px,420px);filter:drop-shadow(0 2px 3px rgba(0,0,0,.45))';
c.innerHTML='<svg width="22" height="22" viewBox="0 0 22 22"><path d="M3 2l14 8-6 1.5L8 18z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
document.documentElement.appendChild(c);window.__cur=c;
window.__ripple=(x,y)=>{const r=document.createElement('div');r.style.cssText=`position:fixed;z-index:99998;left:${x-18}px;top:${y-18}px;width:36px;height:36px;border-radius:50%;border:3px solid #ff7900;pointer-events:none;opacity:.9;transition:transform .5s ease-out,opacity .5s ease-out`;document.documentElement.appendChild(r);requestAnimationFrame(()=>{r.style.transform='scale(2.2)';r.style.opacity='0'});setTimeout(()=>r.remove(),600)};
})()"""

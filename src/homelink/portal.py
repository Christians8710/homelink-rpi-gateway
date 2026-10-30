#!/usr/bin/python3
# SPDX-License-Identifier: MIT
import gateway_admin
import json,subprocess,threading,time,secrets,uuid,os,ipaddress
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from socketserver import TCPServer

CFG=json.loads(Path(os.environ.get('HOMELINK_SETUP_CONFIG','/etc/zigbee-gateway/setup.json')).read_text())
ADDRESS='192.168.4.1'
ORIGIN='http://'+ADDRESS
TOKEN=secrets.token_urlsafe(32)
PROFILES=Path('/etc/NetworkManager/system-connections')
LOCK=threading.Lock()
BUSY=False
VERSION_FILE=Path('/usr/share/zigbee-gateway/version')
VERSION=VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else '1.0.0'
STATE={'mode':'starting','message':'WLAN wird vorbereitet','software_version':VERSION}

def nm(*args):
 return subprocess.run(['nmcli','--wait','40',*args],capture_output=True,text=True,timeout=50,env=dict(os.environ,LC_ALL='C.UTF-8'))

def interface():
 r=nm('-t','-f','DEVICE,TYPE','device')
 return next((s.rsplit(':',1)[0] for s in r.stdout.splitlines() if s.endswith(':wifi')),None)

def active(iface):
 return nm('-g','GENERAL.CONNECTION','device','show',iface).stdout.strip()

def access_point(iface):
 nm('radio','wifi','on')
 profiles=nm('-g','NAME','connection','show').stdout.splitlines()
 if 'zigbee-setup' not in profiles:
  # Secret travels only via an on-disk root-owned keyfile, never command arguments.
  write_profile('zigbee-setup',iface,CFG['ssid'],CFG['password'],ap=True)
 r=nm('connection','up','id','zigbee-setup')
 with LOCK:
  STATE.update(mode='ap' if r.returncode==0 else 'error',message='Einrichtungs-WLAN aktiv' if r.returncode==0 else 'AP konnte nicht starten. WLAN-Treiber/AP-Unterstützung prüfen; Prüfung ist alternativ per SSH über LAN möglich.')

def escape(value):
 return value.replace('\\','\\\\').replace(' ','\\s').replace('\t','\\t')

def write_profile(name,iface,ssid,password,ap=False):
 folder=PROFILES
 folder.mkdir(parents=True,exist_ok=True)
 text='[connection]\nid='+name+'\nuuid='+str(uuid.uuid4())+'\ntype=wifi\ninterface-name='+iface+'\nautoconnect='+('false' if ap else 'true')+'\nautoconnect-priority=100\n\n[wifi]\nmode='+('ap' if ap else 'infrastructure')+'\nssid='+';'.join(str(b) for b in ssid.encode())+';\npowersave=2\n'
 if ap:text+='band=bg\nchannel=6\n'
 text+='\n[wifi-security]\nkey-mgmt=wpa-psk\npsk='+escape(password)+'\n\n[ipv4]\nmethod='+('shared' if ap else 'auto')+'\n'
 if ap:text+='address1='+ADDRESS+'/24\n'
 text+='\n[ipv6]\nmethod='+('disabled' if ap else 'auto')+'\n'
 path=folder/(name+'.nmconnection');tmp=folder/(name+'.tmp')
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w') as f:f.write(text)
 os.chmod(tmp,0o600);tmp.replace(path)
 if nm('connection','reload').returncode:raise RuntimeError('Netzwerkprofile konnten nicht geladen werden')

def connect_home(iface,ssid,password):
 global BUSY
 time.sleep(4) # Give the browser time to display the switch notice.
 try:
  write_profile('zigbee-home',iface,ssid,password)
  result=nm('connection','up','id','zigbee-home')
  if result.returncode==0:
   with LOCK:STATE.update(mode='home',message='Mit Heim-WLAN verbunden. Neue Adresse im Router nachsehen.')
  else:
   access_point(iface)
   with LOCK:STATE['message']='Verbindung fehlgeschlagen. Passwort und WLAN prüfen, dann erneut versuchen.'
 except Exception:
  try:access_point(iface)
  except Exception:pass
  with LOCK:STATE['message']='WLAN-Verbindung fehlgeschlagen. Erneut versuchen oder LAN verwenden.'
 finally:
  with LOCK:BUSY=False

def connected_home(iface):
 profile=active(iface)
 state=nm('-g','GENERAL.STATE','device','show',iface).stdout.strip()
 return profile if profile and profile!='zigbee-setup' and state.startswith('100') else None

def preferred_home():
 if (PROFILES/'zigbee-home.nmconnection').exists():return ['id','zigbee-home']
 records=nm('-t','-f','UUID,TYPE','connection','show').stdout.splitlines()
 for row in records:
  uid,_,kind=row.partition(':')
  if kind not in ('802-11-wireless','wifi'):continue
  values=nm('-g','connection.id,802-11-wireless.mode,connection.autoconnect','connection','show','uuid',uid).stdout.splitlines()
  if len(values)>=3 and values[0]!='zigbee-setup' and values[1]!='ap' and values[2]=='yes':return ['uuid',uid]
 return None

def initial_network(iface):
 nm('radio','wifi','on')
 current=connected_home(iface)
 if current:
  with LOCK:STATE.update(mode='home',message='Mit Heim-WLAN verbunden')
  return
 chosen=preferred_home()
 if chosen:nm('connection','up',*chosen)
 if connected_home(iface):
  with LOCK:STATE.update(mode='home',message='Mit Heim-WLAN verbunden')
 else:access_point(iface)

def monitor():
 iface=None
 while not iface:
  try:iface=interface()
  except Exception:pass
  if not iface:
   with LOCK:STATE.update(mode='error',message='Kein WLAN-Adapter erkannt. Bitte LAN verwenden und Treiber prüfen.')
   time.sleep(10)
 try:
  country=CFG.get('country','DE')
  if isinstance(country,str) and len(country)==2 and country.isalpha():subprocess.run(['iw','reg','set',country.upper()],capture_output=True,timeout=5)
  initial_network(iface)
 except Exception:
  try:access_point(iface)
  except Exception:pass
 lost=0;last_retry=time.monotonic()
 while True:
  time.sleep(15)
  with LOCK:busy=BUSY
  if busy:continue
  try:
   current=active(iface)
   if current=='zigbee-setup' or connected_home(iface):lost=0
   else:lost+=1
   if lost>=3:access_point(iface);lost=0
   if current=='zigbee-setup' and time.monotonic()-last_retry>180:
    last_retry=time.monotonic()
    stations=subprocess.run(['iw','dev',iface,'station','dump'],capture_output=True,text=True,timeout=5)
    if stations.returncode==0 and 'Station ' not in stations.stdout:
     chosen=preferred_home()
     if chosen:
      attempt=nm('connection','up',*chosen)
      if attempt.returncode:access_point(iface)
      else:
       with LOCK:STATE.update(mode='home',message='Mit Heim-WLAN verbunden')
  except Exception:pass

PAGE='''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HomeLink · WLAN</title><style>body{font:18px system-ui;background:#eef3f7;color:#15334b;max-width:650px;margin:5vh auto;padding:25px}main{background:white;padding:28px;border-radius:16px}h1{font-size:27px}label,select,input,button{display:block;box-sizing:border-box;width:100%;font:inherit;margin:12px 0;padding:12px}button{background:#135b87;color:white;border:0;border-radius:7px}small{line-height:1.5}#message{white-space:pre-wrap}</style><main><h1>HomeLink</h1><p>WLAN-Einrichtung für den SONOFF ZBDongle-P.</p><p id="message">Status wird geladen …</p><button id="scan">Netzwerke suchen</button><select id="networks"><option>WLAN auswählen</option></select><label>WLAN-Name<input id="ssid" maxlength="32" autocomplete="off"></label><label>WLAN-Passwort<input id="password" type="password" autocomplete="off"></label><button id="save">Speichern und verbinden</button><small>Beim Verbinden endet dieses Einrichtungs-WLAN. Verbinde deinen PC danach wieder mit dem Heimnetz. Die neue IP des Gateways steht im Router unter „zigbee-gateway“. Bei einem Fehlschlag erscheint das Einrichtungs-WLAN erneut.\n\nBei LAN funktioniert das Gateway bereits per DHCP; WLAN ist optional. TCP-Port für Zigbee2MQTT: 8888.</small><p><a href="/admin">Gateway-Verwaltung öffnen</a></p></main><script>
let token='';const $=id=>document.getElementById(id);
async function status(){try{const r=await fetch('/status');const d=await r.json();$('message').textContent=d.message;token=d.token||'';$('save').disabled=!token;}catch{$('message').textContent='Verbindung zum Gateway unterbrochen.';}}
$('scan').onclick=async()=>{try{$('message').textContent='WLAN-Suche …';const r=await fetch('/scan');const d=await r.json();if(!r.ok)throw Error(d.error);$('networks').replaceChildren(new Option('WLAN auswählen',''),...d.ssids.map(s=>new Option(s,s)));$('message').textContent='Netzwerk wählen oder Namen manuell eingeben.';}catch(e){$('message').textContent=e.message;}};
$('networks').onchange=()=>$('ssid').value=$('networks').value;
$('save').onclick=async()=>{try{$('save').disabled=true;const r=await fetch('/connect',{method:'POST',headers:{'Content-Type':'application/json','X-Setup-Token':token},body:JSON.stringify({ssid:$('ssid').value,password:$('password').value})});const d=await r.json();$('message').textContent=d.message||d.error;if(!r.ok)$('save').disabled=false;else $('password').value='';}catch{$('message').textContent='Verbindung unterbrochen. Heimnetz verbinden oder auf die Rückkehr des Einrichtungs-WLANs warten.';}};status();
</script></html>'''

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def allowed(self):
  try:
   return self.connection.getsockname()[0]==ADDRESS and ipaddress.ip_address(self.client_address[0]) in ipaddress.ip_network('192.168.4.0/24') and self.headers.get('Host') in [ADDRESS,ADDRESS+':80']
  except ValueError:return False
 def send(self,data,code=200,html=False):
  body=data.encode() if html else json.dumps(data,ensure_ascii=False).encode()
  self.send_response(code);self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(body)
 def do_GET(self):
  if self.path.startswith('/admin'):return gateway_admin.handle(self)
  if self.path=='/' and not self.allowed():
   self.send_response(303);self.send_header('Location','/admin');self.send_header('Content-Length','0');self.end_headers();return
  if self.path=='/':return self.send(PAGE,html=True)
  if self.path=='/status':
   with LOCK:data=dict(STATE,busy=BUSY)
   data['token']=TOKEN if self.allowed() else ''
   return self.send(data)
  if self.path=='/scan':
   if not self.allowed():return self.send({'error':'Bitte mit dem Einrichtungs-WLAN verbinden.'},403)
   try:
    iface=interface();r=nm('-t','--escape','no','-f','SSID','device','wifi','list','ifname',iface,'--rescan','yes')
    if r.returncode:raise RuntimeError()
    return self.send({'ssids':sorted(set(s for s in r.stdout.splitlines() if s))})
   except Exception:return self.send({'error':'Suche fehlgeschlagen. WLAN-Namen manuell eingeben.'},503)
  self.send({'error':'Nicht gefunden'},404)
 def do_POST(self):
  global BUSY
  if self.path.startswith('/admin'):return gateway_admin.handle(self)
  if not self.allowed() or self.headers.get('Origin')!=ORIGIN or not secrets.compare_digest(self.headers.get('X-Setup-Token',''),TOKEN):return self.send({'error':'Zugriff nur über das Einrichtungs-WLAN erlaubt.'},403)
  if self.path!='/connect':return self.send({'error':'Nicht gefunden'},404)
  try:
   size=int(self.headers.get('Content-Length','0'))
   if not 0<size<=2048:raise ValueError()
   data=json.loads(self.rfile.read(size));ssid=data['ssid'];password=data['password']
   if not isinstance(ssid,str) or not 1<=len(ssid.encode())<=32 or any(c in ssid for c in '\n\r\0'):raise ValueError()
   if not isinstance(password,str) or any(c in password for c in '\n\r\0') or not (8<=len(password.encode())<=63 or len(password)==64 and all(c in '0123456789abcdefABCDEF' for c in password)):raise ValueError()
   iface=interface()
   if not iface:raise ValueError()
   with LOCK:
    if BUSY:return self.send({'error':'Verbindungsaufbau läuft bereits.'},409)
    BUSY=True
   threading.Thread(target=connect_home,args=(iface,ssid,password),daemon=True).start()
   self.send({'message':'Gespeichert. Das Gateway wechselt jetzt ins Heim-WLAN. Bei einem Fehlschlag kehrt der Einrichtungs-AP zurück.'},202)
  except Exception:self.send({'error':'WLAN-Name und WPA/WPA2-Passwort prüfen (8–63 Byte oder 64 Hex-Zeichen).'},400)

class Server(ThreadingHTTPServer):
 def server_bind(self):
  TCPServer.server_bind(self);self.server_name='zigbee-gateway';self.server_port=self.server_address[1]

if __name__=='__main__':
 threading.Thread(target=monitor,daemon=True).start()
 Server(('0.0.0.0',80),Handler).serve_forever()

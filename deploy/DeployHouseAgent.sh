#!/bin/bash
# Install the house agent + Hello World stamps (Series 1, wave 1) on the server.
# Safe to run again: every step checks before it changes anything.
set -e
RAW=https://raw.githubusercontent.com/ZKStamp/AgentWallet/main
AW=/root/agentwallet
cd $AW
echo "1/6 backup"
mkdir -p /root/AgentWalletBackup31Oct && cp -p $AW/*.py $AW/AgentWallet.db /root/AgentWalletBackup31Oct/
echo "2/6 code + stamps"
curl -sfo HouseAgent.py $RAW/HouseAgent.py
mkdir -p $AW/stamps /var/www/stamps/img
curl -sfo $AW/stamps/StampCatalog.json $RAW/stamps/StampCatalog.json
python3 - <<'PY'
import hashlib, json, urllib.request
cat = json.load(open("/root/agentwallet/stamps/StampCatalog.json", encoding="utf-8"))
bad = 0
for s in cat["stamps"]:
    data = urllib.request.urlopen("https://raw.githubusercontent.com/ZKStamp/AgentWallet/main/stamps/img/" + s["file"], timeout=60).read()
    if hashlib.sha256(data).hexdigest() != s["sha256"]:
        bad += 1; print("BAD FINGERPRINT", s["file"]); continue
    open("/var/www/stamps/img/" + s["file"], "wb").write(data)
print(len(cat["stamps"]) - bad, "stamp images OK,", bad, "bad")
if bad: raise SystemExit(1)
PY
echo "3/6 web page location"
SITE=$(grep -l "agent/llms.txt" /etc/nginx/sites-enabled/* | head -1)
if grep -q "location /stamps/" "$SITE"; then echo "already there"; else
  cp -p "$SITE" /root/NginxBackupStamps31Oct.conf
  python3 - "$SITE" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
block = """    # Hello World stamps: public gallery + images (static files, written by HouseAgent.py)
    location /stamps/ {
        alias /var/www/stamps/;
        index index.html;
        charset utf-8;
    }

"""
anchor = "    location = /agent/llms.txt {"
assert s.count(anchor) == 1, "anchor not found once"
open(p, "w").write(s.replace(anchor, block + anchor))
PY
  if nginx -t 2>/dev/null; then systemctl reload nginx; echo "nginx OK"; else cp -p /root/NginxBackupStamps31Oct.conf "$SITE"; echo "nginx test FAILED: restored backup"; exit 1; fi
fi
echo "4/6 timer files"
curl -sfo /etc/systemd/system/HouseAgent.service $RAW/deploy/systemd/HouseAgent.service
curl -sfo /etc/systemd/system/HouseAgent.timer $RAW/deploy/systemd/HouseAgent.timer
systemctl daemon-reload
echo "5/6 house agent wallet + Proof-100 account"
python3 HouseAgent.py setup
echo "6/6 start"
systemctl enable --now HouseAgent.timer >/dev/null 2>&1
python3 HouseAgent.py || true
echo "gallery: $(curl -s -o /dev/null -w '%{http_code}' https://proof.zketernity.app/stamps/)  (200 = online)"
echo DONE

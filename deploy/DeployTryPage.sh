#!/bin/bash
# Install the "Try it in your browser" playground (stamps/try.html) + status.json writer.
# Usage: bash DeployTryPage.sh <sha256 of HouseAgent.py> <sha256 of try.html>
set -e
RAW=https://raw.githubusercontent.com/ZKStamp/AgentWallet/main
AW=/root/agentwallet
cd $AW
echo "1/4 backup"
mkdir -p /root/AgentWalletBackup1Nov && cp -p $AW/HouseAgent.py /root/AgentWalletBackup1Nov/
echo "2/4 download + check fingerprints"
curl -sfo /tmp/HouseAgent.py.new $RAW/HouseAgent.py
curl -sfo /tmp/try.html.new $RAW/stamps/try.html
echo "$1  /tmp/HouseAgent.py.new" | sha256sum -c -
echo "$2  /tmp/try.html.new" | sha256sum -c -
python3 -m py_compile /tmp/HouseAgent.py.new
echo "3/4 install"
cp /tmp/HouseAgent.py.new $AW/HouseAgent.py
cp /tmp/try.html.new /var/www/stamps/try.html
echo "4/4 one house agent run (writes status.json)"
python3 HouseAgent.py || true
for u in stamps/ stamps/try.html stamps/status.json; do
  echo "$u: $(curl -s -o /dev/null -w '%{http_code}' https://proof.zketernity.app/$u)  (200 = online)"
done
echo DONE

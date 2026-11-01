#!/bin/bash
# 1 Nov: faster + more visual playground.
#  - starter coins in 3 pieces + 2 pre-funded wallets kept ready (WalletCore.py)
#  - public /v1/chain for the block countdown (ChainNode.py, AgentWalletApi.py)
#  - house agent answers at 0 confirmations, runs every 30 s; grants every minute
#  - new try.html (wallet card, block countdown, alerts, activity log, while-you-wait)
# Usage: bash DeployFastTry.sh <commit>   (files are taken from that exact commit)
set -e
C=$1; [ -n "$C" ] || { echo "give the commit"; exit 1; }
RAW=https://raw.githubusercontent.com/ZKStamp/AgentWallet/$C
AW=/root/agentwallet; NEW=/tmp/FastTry; rm -rf $NEW; mkdir -p $NEW
echo "1/5 backup"
B=/root/AgentWalletBackup1NovB; mkdir -p $B
cp -p $AW/*.py $AW/AgentWallet.db $B/ && cp -p /var/www/stamps/try.html /etc/systemd/system/HouseAgent.timer /etc/systemd/system/AgentWalletGrants.timer $B/
echo "2/5 download from commit $C"
for f in WalletCore.py ChainNode.py AgentWalletApi.py HouseAgent.py stamps/try.html deploy/systemd/HouseAgent.timer deploy/systemd/AgentWalletGrants.timer; do
  curl -sfo $NEW/$(basename $f) $RAW/$f || { echo "download failed: $f"; exit 1; }
done
cd $NEW && python3 -m py_compile WalletCore.py ChainNode.py AgentWalletApi.py HouseAgent.py && echo "code compiles OK"
echo "3/5 install"
cp $NEW/WalletCore.py $NEW/ChainNode.py $NEW/AgentWalletApi.py $NEW/HouseAgent.py $AW/
cp $NEW/try.html /var/www/stamps/try.html
cp $NEW/HouseAgent.timer $NEW/AgentWalletGrants.timer /etc/systemd/system/
systemctl daemon-reload
systemctl restart AgentWalletApi
systemctl restart HouseAgent.timer AgentWalletGrants.timer
echo "4/5 checks"
sleep 4
cd $AW
echo "chain: $(curl -s https://proof.zketernity.app/agent/v1/chain)"
python3 GrantStarters.py
python3 - <<'PY'
import WalletCore as core, ChainNode as chain
con = core.db()
print("wallets by starter status:", dict(con.execute("SELECT starter_status, COUNT(*) FROM accounts GROUP BY starter_status").fetchall()))
print("starter pool balance:", chain.balance(core.config()["pool_address"]), "BTCZ")
PY
echo "5/5 timers"
systemctl list-timers HouseAgent.timer AgentWalletGrants.timer --no-pager | head -4
echo DONE

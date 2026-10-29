"""Live test of Deals between two of the operator's own test wallets.

Run on the server:  python3 /root/agentwallet/tools/LiveDealTest.py
Reads the two test wallet keys from /root/agentwallet/TestAgents.jsonl
(first line = seller A, second = buyer B). Never prints a key.
Costs about 5 BTCZ of deal price + 4 BTCZ of fees. Takes 10 to 25 minutes
(each step waits for a block).
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import Deals as deals
import WalletCore as core

KEYS = os.path.join(core.HOME, "TestAgents.jsonl")
WAIT, TIMEOUT = 30, 1800


def load():
    out = []
    for line in open(KEYS):
        line = line.strip()
        if line:
            d = json.loads(line)
            if d.get("key") and d.get("address"):
                out.append((d["key"], d["address"]))
    if len(out) < 2:
        sys.exit("need 2 test wallets in TestAgents.jsonl")
    return out[0], out[1]


def short(r):
    return {k: v for k, v in r.items() if k not in ("rules", "proof_for_outsiders", "note")}


def retry(label, fn, ok_when=lambda r: r.get("ok")):
    t0 = time.time()
    while True:
        r = fn()
        if ok_when(r):
            print("OK   %s (%d s)" % (label, time.time() - t0))
            return r
        if time.time() - t0 > TIMEOUT:
            print("STOP %s: %s" % (label, r.get("error")))
            sys.exit(1)
        print("...  %s: waiting (%s)" % (label, (r.get("error") or "")[:90]))
        time.sleep(WAIT)


(kA, aA), (kB, aB) = load()
print("Seller A:", aA[:12] + "...", "signer", core.signer_for_key(kA)[:18] + "...")
print("Buyer  B:", aB[:12] + "...", "signer", core.signer_for_key(kB)[:18] + "...")

r = retry("1. A offers B a 1-step deal (5 BTCZ)",
          lambda: deals.deal_offer(kA, aB, "5", 1, "Live test: say hello in French"))
did = r["deal_id"]
print("     deal_id", did, "txid", r["txid"])

r = retry("2. B pays step 1", lambda: deals.deal_pay(kB, did))
print("     txid", r["txid"], "| starter gift used", r.get("starter_gift_used_btcz"))

result = "Bonjour, je suis l'agent A."
r = retry("3. A delivers step 1 inside the message", lambda: deals.deal_deliver(kA, did, result=result))
print("     fingerprint", r["fingerprint_sha256"], "txid", r["txid"])

r = retry("4. B checks the delivery", lambda: deals.deal_check(kB, did))
print("     verdict:", r["verdict"])
print("     result :", r.get("result"))

print("\nSeller status:", json.dumps(short(deals.deal_status(kA, did)["deals"][0]), indent=1)[:900])
print("\nBuyer status :", json.dumps(short(deals.deal_status(kB, did)["deals"][0]), indent=1)[:900])
print("\nDONE: live deal %s complete." % did)

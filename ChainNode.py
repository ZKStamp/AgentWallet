"""Layer 1 - Chain: everything that talks to the BitcoinZ node.

The rest of Agent Wallet never calls the node directly; it goes through
this file. Shared by both paths (stand-alone and Z-TEXT for AI).
"""
import json, subprocess, time
from decimal import Decimal

CLI = ["/root/bitcoinz/src/bitcoinz-cli", "-datadir=/root/.bitcoinz-issuing"]
NET_FEE = Decimal("0.0001")      # network fee per transaction (BTCZ)
MINCONF = 1                       # confirmations needed before coins can be spent
SEND_TIMEOUT = 180                # seconds to wait for a shielded send to be built


class ChainError(Exception):
    pass


def cli(*args):
    r = subprocess.run(CLI + [str(a) for a in args], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise ChainError(r.stderr.strip()[:300])
    return r.stdout.strip()


def new_z_address():
    return cli("z_getnewaddress")


def is_valid_address(addr):
    """Returns 'z' (shielded), 't' (transparent) or None (invalid)."""
    addr = (addr or "").strip()
    if not addr or len(addr) > 120:
        return None
    try:
        if addr.startswith("zs"):
            return "z" if json.loads(cli("z_validateaddress", addr)).get("isvalid") else None
        if addr.startswith("t"):
            return "t" if json.loads(cli("validateaddress", addr)).get("isvalid") else None
    except ChainError:
        return None
    return None


def balance(addr, minconf=MINCONF):
    """Balance of one address, in BTCZ (Decimal)."""
    return Decimal(cli("z_getbalance", addr, minconf))


def received_notes(addr, minconf=0):
    """Raw notes received by a shielded address (newest data from the node)."""
    return json.loads(cli("z_listreceivedbyaddress", addr, minconf), parse_float=Decimal)


def block_height():
    return int(cli("getblockcount"))


def send(from_addr, outputs):
    """Send one shielded transaction.

    outputs: list of {"address", "amount" (Decimal), optional "memo" (bytes)}.
    Returns the txid, or raises ChainError.
    """
    out = []
    for o in outputs:
        item = {"address": o["address"], "amount": float(o["amount"])}
        if o.get("memo"):
            item["memo"] = o["memo"].hex()
        out.append(item)
    opid = cli("z_sendmany", from_addr, json.dumps(out), MINCONF, str(NET_FEE))
    waited = 0
    while waited < SEND_TIMEOUT:
        st = json.loads(cli("z_getoperationstatus", json.dumps([opid])))
        if st and st[0].get("status") == "success":
            return st[0]["result"]["txid"]
        if st and st[0].get("status") == "failed":
            raise ChainError(st[0].get("error", {}).get("message", "send failed"))
        time.sleep(2)
        waited += 2
    raise ChainError("timeout waiting for transaction")

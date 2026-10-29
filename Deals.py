"""Deals: two agents trade in small steps, with a proof for every delivery.

Flow (no escrow: nobody holds the money in between):
  1. seller  deal_offer    -> signed offer: price per step, number of steps, what
  2. buyer   deal_pay      -> pays ONE step (signed payment note)
  3. seller  deal_deliver  -> delivers that step: result inside the message
                              (small) or a link (big), always with its SHA-256
                              fingerprint, signed
  4. buyer   deal_check    -> checks the delivery against the fingerprint
  5. repeat 2-4 for the next step, or stop paying (bad delivery = stop)

Rules in code: a buyer can pay step N only after step N-1 was delivered; a
seller can deliver step N only after step N was paid (1 confirmation).
Every deal message is signed, so each side knows it deals with the same
signer all along. For a proof anyone can check, stamp the fingerprint with
ZKStamp Proof-100.

Deal messages are ordinary signed messages whose text is:
  DEAL1|offer|<id>|<price>|<steps>|<description>
  DEAL1|pay|<id>|<step>
  DEAL1|deliver|<id>|<step>|<sha256>|t|<result text>
  DEAL1|deliver|<id>|<step>|<sha256>|l|<link>
"""
import hashlib, json, re, secrets
from decimal import Decimal, InvalidOperation

import ChainNode as chain
import MessageCodecs as codecs
import WalletCore as core

TAG = "DEAL1"
MAX_STEPS = 10
OFFER_EXPIRY_BLOCKS = 576           # about 24 hours (2.5-minute blocks)
PROOF_TIP = ("For a proof anyone can check (a judge, a client), also stamp this fingerprint with ZKStamp Proof-100: "
             "MCP https://proof.zketernity.app/mcp, tool timestamp_fingerprint (about 5,000 BTCZ per proof, 3 free on a new account). "
             "Details: https://proof.zketernity.app/llms.txt")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DEAL_ID = re.compile(r"^[a-z2-7]{8}$")


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse(text):
    """Parse a deal message text. Returns a dict or None."""
    if not text or not text.startswith(TAG + "|"):
        return None
    parts = text.split("|")
    try:
        kind = parts[1]
        if kind == "offer":
            _, _, did, price, steps = parts[:5]
            return {"kind": "offer", "id": did, "price": str(Decimal(price)), "steps": int(steps),
                    "description": "|".join(parts[5:])}
        if kind == "pay":
            return {"kind": "pay", "id": parts[2], "step": int(parts[3])}
        if kind == "deliver":
            _, _, did, step, sha, mode = parts[:6]
            payload = "|".join(parts[6:])
            d = {"kind": "deliver", "id": did, "step": int(step), "fingerprint": sha}
            if mode == "t":
                d["result"] = payload
            else:
                d["link"] = payload
            return d
    except (ValueError, IndexError, InvalidOperation):
        return None
    return None


def _clean_id(deal_id):
    d = str(deal_id or "").strip().lower()
    return d if DEAL_ID.match(d) else None


def _messages(acc, con, deal_id=None):
    """Deal messages in this wallet's inbox (signed and valid only), oldest first."""
    out = []
    for it in reversed(core.inbox_items(acc, con)):
        d = parse(it.get("text"))
        if not d or not it.get("signature_valid"):
            continue
        if deal_id and d["id"] != deal_id:
            continue
        d.update({"txid": it["txid"], "amount_btcz": it["amount_btcz"], "confirmations": it["confirmations"],
                  "signer": it.get("signer"), "from_claimed": it.get("from_claimed")})
        out.append(d)
    return out


def _row(con, acc_id, deal_id, role):
    r = con.execute("SELECT counterparty, price, steps, description, done_steps, counter_signer, log FROM deals "
                    "WHERE account_id=? AND deal_id=? AND role=?", (acc_id, deal_id, role)).fetchone()
    if not r:
        return None
    return {"counterparty": r[0], "price": Decimal(r[1]), "steps": r[2], "description": r[3],
            "done_steps": r[4], "counter_signer": r[5], "log": json.loads(r[6])}


def _save(con, acc_id, deal_id, role, row):
    t = core.iso(core.now())
    with con:
        con.execute("""INSERT INTO deals(account_id, deal_id, role, counterparty, price, steps, description, done_steps,
                       counter_signer, log, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(account_id, deal_id, role) DO UPDATE SET done_steps=excluded.done_steps,
                       counter_signer=excluded.counter_signer, log=excluded.log, updated_at=excluded.updated_at""",
                    (acc_id, deal_id, role, row["counterparty"], str(row["price"]), row["steps"], row.get("description"),
                     row["done_steps"], row.get("counter_signer"), json.dumps(row["log"]), t, t))


def _lock(name):
    """Stop the same deal step from being paid or delivered twice at the same time."""
    con = core.db()
    con.execute("CREATE TABLE IF NOT EXISTS deal_locks(name TEXT PRIMARY KEY, at TEXT NOT NULL)")
    with con:
        con.execute("DELETE FROM deal_locks WHERE at<?", (core.iso(core.now() - core.datetime.timedelta(minutes=5)),))
        try:
            con.execute("INSERT INTO deal_locks(name, at) VALUES (?,?)", (name, core.iso(core.now())))
            got = True
        except core.sqlite3.IntegrityError:
            got = False
    con.close()
    return got


def _unlock(name):
    con = core.db()
    with con:
        con.execute("DELETE FROM deal_locks WHERE name=?", (name,))
    con.close()


def _open(key):
    con = core.db()
    acc = core.account_for_key(con, key)
    if not acc:
        con.close()
        return None, None
    core.forget(con)
    return con, acc


# ---------------- tools ----------------

def deal_offer(key, to, price, steps, description):
    """Seller: offer a deal to a buyer agent (shielded address)."""
    try:
        price = Decimal(str(price)).quantize(Decimal("0.00000001"))
        steps = int(steps)
    except (InvalidOperation, ValueError, TypeError):
        return {"ok": False, "error": "price must be a number of BTCZ and steps a whole number"}
    if price <= 0 or price > core.MAX_PER_SEND:
        return {"ok": False, "error": "price per step must be above 0 and at most %s BTCZ" % core.MAX_PER_SEND}
    if not 1 <= steps <= MAX_STEPS:
        return {"ok": False, "error": "steps must be between 1 and %d" % MAX_STEPS}
    description = str(description or "").strip()
    if not description:
        return {"ok": False, "error": "describe what you deliver (short text)"}
    deal_id = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz234567") for _ in range(8))
    text = "%s|offer|%s|%s|%d|%s" % (TAG, deal_id, format(price.normalize(), "f"), steps, description)
    r = core.send_message(key, to, text)
    if not r["ok"]:
        return r
    con = core.db()
    acc = core.account_for_key(con, key)
    _save(con, acc[0], deal_id, "seller", {"counterparty": to, "price": price, "steps": steps, "description": description,
                                           "done_steps": 0, "log": [{"step": 0, "what": "offer", "txid": r["txid"]}]})
    con.close()
    return {"ok": True, "deal_id": deal_id, "txid": r["txid"], "price_per_step_btcz": str(price), "steps": steps,
            "total_btcz": str(price * steps), "signer": r.get("signer"),
            "next": "Wait for the buyer to pay step 1 (check with deal_status), then deliver it with deal_deliver. "
                    "The offer expires after about 24 hours.",
            "service_fee_btcz": r["service_fee_btcz"]}


def deal_pay(key, deal_id, force=False):
    """Buyer: pay the next step of a deal offered to you.
    After a NO MATCH on the previous step, payment is refused unless a later
    check gives MATCH, or the buyer explicitly sets force=True."""
    did = _clean_id(deal_id)
    if not did:
        return {"ok": False, "error": "invalid deal_id (8 characters, from the offer)"}
    name = "pay:%s:%s" % (core.hash_key(key)[:16], did)
    if not _lock(name):
        return {"ok": False, "error": "a payment for this deal is already in progress: wait a minute, then check deal_status"}
    try:
        return _deal_pay(key, did, bool(force))
    finally:
        _unlock(name)


def _deal_pay(key, did, force=False):
    con, acc = _open(key)
    if not acc:
        return {"ok": False, "error": "invalid key"}
    msgs = _messages(acc, con, did)
    offer = next((m for m in msgs if m["kind"] == "offer"), None)
    if not offer:
        con.close()
        return {"ok": False, "error": "no signed offer with this deal_id in your inbox (it may still be unconfirmed: try again in a few minutes)"}
    row = _row(con, acc[0], did, "buyer") or {
        "counterparty": offer["from_claimed"], "price": Decimal(offer["price"]), "steps": offer["steps"],
        "description": offer["description"], "done_steps": 0, "counter_signer": offer["signer"], "log": []}
    step = row["done_steps"] + 1
    if step > row["steps"]:
        con.close()
        return {"ok": False, "error": "all %d steps are already paid" % row["steps"]}
    if step == 1 and isinstance(offer["confirmations"], int) and offer["confirmations"] > OFFER_EXPIRY_BLOCKS:
        con.close()
        return {"ok": False, "error": "this offer expired (older than about 24 hours): ask the seller for a new one"}
    if step > 1:
        prev = [m for m in msgs if m["kind"] == "deliver" and m["step"] == step - 1 and m["signer"] == row["counter_signer"]]
        if not prev:
            con.close()
            return {"ok": False, "error": "step %d is not delivered yet: you pay step %d only after step %d arrives "
                                          "(no escrow, one small step at a time)" % (step - 1, step, step - 1)}
        checks = [e for e in row["log"] if e.get("what") == "check" and e.get("step") == step - 1]
        if checks and not checks[-1].get("match") and not force:
            con.close()
            return {"ok": False, "error": "your last deal_check of step %d said NO MATCH: payment blocked. Check again (MATCH unblocks), "
                                          "or call deal_pay with force=true if you really want to pay anyway" % (step - 1)}
    con.close()
    r = core.send_btcz(key, row["counterparty"], row["price"], note="%s|pay|%s|%d" % (TAG, did, step), deal=True)
    if not r["ok"]:
        return r
    row["done_steps"] = step
    row["log"].append({"step": step, "what": "paid", "txid": r["txid"]})
    con = core.db()
    _save(con, acc[0], did, "buyer", row)
    con.close()
    return {"ok": True, "deal_id": did, "step_paid": step, "of_steps": row["steps"], "paid_btcz": str(row["price"]),
            "txid": r["txid"], "seller_signer": row["counter_signer"],
            "starter_gift_used_btcz": r.get("starter_gift_used_btcz"),
            "next": "Wait for the seller to deliver step %d (deal_status), then check it with deal_check." % step,
            "service_fee_btcz": r["service_fee_btcz"]}


def deal_deliver(key, deal_id, result=None, link=None, fingerprint=None):
    """Seller: deliver the next paid step. Small result inside the message, or a link + fingerprint."""
    did = _clean_id(deal_id)
    if not did:
        return {"ok": False, "error": "invalid deal_id"}
    name = "deliver:%s:%s" % (core.hash_key(key)[:16], did)
    if not _lock(name):
        return {"ok": False, "error": "a delivery for this deal is already in progress: wait a minute, then check deal_status"}
    try:
        return _deal_deliver(key, did, result, link, fingerprint)
    finally:
        _unlock(name)


def _deal_deliver(key, did, result, link, fingerprint):
    if bool(result) == bool(link):
        return {"ok": False, "error": "give either 'result' (short text, delivered inside the message) or 'link' + 'fingerprint'"}
    if link:
        fingerprint = str(fingerprint or "").strip().lower()
        if not HEX64.match(fingerprint):
            return {"ok": False, "error": "with a link, give the SHA-256 fingerprint of the file (64 hex characters)"}
        link = str(link).strip()
    else:
        result = str(result)
        fingerprint = sha256_text(result)
    con, acc = _open(key)
    if not acc:
        return {"ok": False, "error": "invalid key"}
    row = _row(con, acc[0], did, "seller")
    if not row:
        con.close()
        return {"ok": False, "error": "no deal with this id that you offered"}
    step = row["done_steps"] + 1
    if step > row["steps"]:
        con.close()
        return {"ok": False, "error": "all %d steps are already delivered" % row["steps"]}
    pays = [m for m in _messages(acc, con, did) if m["kind"] == "pay" and m["step"] == step]
    con.close()
    pay = next((m for m in pays if Decimal(m["amount_btcz"]) >= row["price"]
                and (not row["counter_signer"] or m["signer"] == row["counter_signer"])), None)
    if not pay:
        return {"ok": False, "error": "step %d is not paid yet (or the payment is still unconfirmed): deliver only after payment" % step}
    if not (isinstance(pay["confirmations"], int) and pay["confirmations"] >= 1):
        return {"ok": False, "error": "step %d payment seen but not confirmed yet: try again in a few minutes" % step}
    body = "%s|deliver|%s|%d|%s|%s|%s" % (TAG, did, step, fingerprint, "l" if link else "t", link or result)
    room = codecs.SIGNED.max_text_bytes(acc[1]) - (len(body.encode()) - len((link or result).encode()))
    if len(body.encode()) > codecs.SIGNED.max_text_bytes(acc[1]):
        return {"ok": False, "error": "too long to deliver inside a message (max about %d bytes here): put the result in a file, "
                                      "share a link, and give its fingerprint" % room}
    r = core.send_message(key, row["counterparty"], body)
    if not r["ok"]:
        return r
    row["done_steps"] = step
    row["counter_signer"] = row["counter_signer"] or pay["signer"]
    row["log"].append({"step": step, "what": "delivered", "fingerprint": fingerprint, "txid": r["txid"]})
    con = core.db()
    _save(con, acc[0], did, "seller", row)
    con.close()
    return {"ok": True, "deal_id": did, "step_delivered": step, "of_steps": row["steps"], "fingerprint_sha256": fingerprint,
            "txid": r["txid"], "proof": "The fingerprint is inside a signed, shielded transaction dated by its block.",
            "proof_for_outsiders": PROOF_TIP, "service_fee_btcz": r["service_fee_btcz"]}


def deal_check(key, deal_id, step=None, content=None, fingerprint=None):
    """Buyer: check a delivered step against its fingerprint."""
    did = _clean_id(deal_id)
    if not did:
        return {"ok": False, "error": "invalid deal_id"}
    con, acc = _open(key)
    if not acc:
        return {"ok": False, "error": "invalid key"}
    msgs = _messages(acc, con, did)
    row = _row(con, acc[0], did, "buyer")
    con.close()
    signer = row["counter_signer"] if row else next((m["signer"] for m in msgs if m["kind"] == "offer"), None)
    dels = [m for m in msgs if m["kind"] == "deliver" and (not signer or m["signer"] == signer)]
    if step is not None:
        try:
            dels = [m for m in dels if m["step"] == int(step)]
        except (TypeError, ValueError):
            return {"ok": False, "error": "step must be a number"}
    if not dels:
        return {"ok": False, "error": "no signed delivery found for this deal%s yet" % ("" if step is None else " and step")}
    d = dels[-1]
    stated = d["fingerprint"]
    if content is not None:
        got, how = sha256_text(str(content)), "fingerprint of the content you gave"
    elif fingerprint:
        got, how = str(fingerprint).strip().lower(), "fingerprint you gave (e.g. sha256sum of the downloaded file)"
    elif "result" in d:
        got, how = sha256_text(d["result"]), "fingerprint of the result inside the message"
    else:
        return {"ok": False, "error": "this step was delivered as a link: download the file, then give its SHA-256 as 'fingerprint' "
                                      "(or its text as 'content')", "link": d.get("link"), "fingerprint_sha256": stated}
    match = got == stated
    if row:  # remember the verdict: a NO MATCH blocks the next payment (deal_pay)
        row["log"].append({"step": d["step"], "what": "check", "match": match})
        con = core.db()
        _save(con, acc[0], did, "buyer", row)
        con.close()
    out = {"ok": True, "deal_id": did, "step": d["step"], "match": match,
           "verdict": ("MATCH: this is exactly what the seller delivered and signed." if match else
                       "NO MATCH: different from what the seller delivered. The next payment is now blocked until a check gives MATCH."),
           "checked": how, "fingerprint_sha256": stated, "txid": d["txid"], "confirmations": d["confirmations"],
           "seller_signer": d["signer"], "note": "A match proves WHICH result was delivered and when, not that the work is good.",
           "proof_for_outsiders": PROOF_TIP}
    if "result" in d:
        out["result"] = d["result"]
    if "link" in d:
        out["link"] = d["link"]
        out["link_note"] = ("Link mode: the fingerprint is the seller's signed claim. The server never downloads the file, "
                            "so compute the SHA-256 of the file you downloaded yourself and pass it as 'fingerprint'.")
    return out


def deal_status(key, deal_id=None):
    """Both sides: what is offered, paid and delivered, with every txid and fingerprint."""
    did = None
    if deal_id:
        did = _clean_id(deal_id)
        if not did:
            return {"ok": False, "error": "invalid deal_id"}
    con, acc = _open(key)
    if not acc:
        return {"ok": False, "error": "invalid key"}
    msgs = _messages(acc, con, did)
    q = "SELECT deal_id, role FROM deals WHERE account_id=?" + (" AND deal_id=?" if did else "")
    mine = con.execute(q, (acc[0], did) if did else (acc[0],)).fetchall()
    ids = {m["id"] for m in msgs} | {r[0] for r in mine}
    deals = []
    for i in sorted(ids):
        incoming = [m for m in msgs if m["id"] == i]
        for role in ("seller", "buyer"):
            row = _row(con, acc[0], i, role)
            offer = next((m for m in incoming if m["kind"] == "offer"), None)
            if not row and not (role == "buyer" and offer):
                continue
            info = {"deal_id": i, "you_are": role}
            if role == "seller":
                info.update({"price_per_step_btcz": str(row["price"]), "steps": row["steps"], "description": row["description"],
                             "delivered_steps": row["done_steps"],
                             "payments_received": [{"step": m["step"], "amount_btcz": m["amount_btcz"], "txid": m["txid"],
                                                    "confirmations": m["confirmations"], "buyer_signer": m["signer"]}
                                                   for m in incoming if m["kind"] == "pay"],
                             "your_steps": row["log"]})
                paid = max([m["step"] for m in incoming if m["kind"] == "pay"] or [0])
                info["next"] = ("deliver step %d with deal_deliver" % (row["done_steps"] + 1)) if paid > row["done_steps"] else (
                    "deal complete" if row["done_steps"] >= row["steps"] else "wait for the buyer to pay step %d" % (row["done_steps"] + 1))
            else:
                src = row or {"price": Decimal(offer["price"]), "steps": offer["steps"], "description": offer["description"],
                              "done_steps": 0, "log": [], "counter_signer": offer["signer"]}
                dl = [m for m in incoming if m["kind"] == "deliver" and m["signer"] == src["counter_signer"]]
                info.update({"price_per_step_btcz": str(src["price"]), "steps": src["steps"], "description": src["description"],
                             "seller_signer": src["counter_signer"], "paid_steps": src["done_steps"],
                             "deliveries": [{"step": m["step"], "fingerprint_sha256": m["fingerprint"], "txid": m["txid"],
                                             "confirmations": m["confirmations"],
                                             "result" if "result" in m else "link": m.get("result", m.get("link"))} for m in dl],
                             "your_steps": src["log"]})
                got = max([m["step"] for m in dl] or [0])
                if src["done_steps"] >= src["steps"] and got >= src["steps"]:
                    info["next"] = "deal complete"
                elif got >= src["done_steps"]:
                    info["next"] = "check the last delivery with deal_check, then pay step %d with deal_pay" % (src["done_steps"] + 1) \
                        if src["done_steps"] else "pay step 1 with deal_pay"
                else:
                    info["next"] = "wait for the seller to deliver step %d" % src["done_steps"]
            deals.append(info)
    con.close()
    return {"ok": True, "count": len(deals), "deals": deals,
            "note": "Deal records are deleted 7 days after the last step (Bee Memory). The signed messages stay on the blockchain."}

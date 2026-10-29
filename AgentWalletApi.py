"""Layer 4 - Agents: the MCP tools and REST API that AI agents use.

Tool names (create_wallet, my_address, balance, send_btcz, send_message,
inbox, deal_offer, deal_pay, deal_deliver, deal_check, deal_status) stay the
same whatever message format sits underneath.
Listens on 127.0.0.1:5200; nginx publishes it under /agent/.
"""
import json
from flask import Flask, request, jsonify
import WalletCore as core
import Deals as deals

app = Flask(__name__)
VERSION = "0.2"


def get_key():
    h = (request.headers.get("Authorization", "") or request.headers.get("X-API-Key", "")).strip()
    while h.lower().startswith("bearer"):
        h = h[6:].strip()
    return h


def client_ip():
    return request.headers.get("X-Real-IP") or request.remote_addr or ""


def my_address(key):
    con = core.db()
    acc = core.account_for_key(con, key)
    con.close()
    return {"ok": True, "address": acc[1], "signer": core.signer_for_key(key)} if acc else {"ok": False, "error": "invalid key"}


# ---------------- REST ----------------

def reply(r):
    return jsonify(r), (200 if r.get("ok") else 400)


@app.route("/v1/wallet", methods=["POST"])
def rest_create():
    return reply(core.create_wallet(client_ip()))


@app.route("/v1/balance", methods=["GET"])
def rest_balance():
    return reply(core.wallet_info(get_key()))


@app.route("/v1/send", methods=["POST"])
def rest_send():
    d = request.get_json(silent=True) or {}
    return reply(core.send_btcz(get_key(), str(d.get("to", "")), d.get("amount", ""), d.get("note")))


@app.route("/v1/message", methods=["POST"])
def rest_message():
    d = request.get_json(silent=True) or {}
    return reply(core.send_message(get_key(), str(d.get("to", "")), str(d.get("text", ""))))


@app.route("/v1/inbox", methods=["GET"])
def rest_inbox():
    return reply(core.inbox(get_key(), request.args.get("limit", 20)))


@app.route("/v1/deal/offer", methods=["POST"])
def rest_deal_offer():
    d = request.get_json(silent=True) or {}
    return reply(deals.deal_offer(get_key(), str(d.get("to", "")), d.get("price", ""), d.get("steps", ""), d.get("description", "")))


@app.route("/v1/deal/pay", methods=["POST"])
def rest_deal_pay():
    d = request.get_json(silent=True) or {}
    return reply(deals.deal_pay(get_key(), d.get("deal_id", "")))


@app.route("/v1/deal/deliver", methods=["POST"])
def rest_deal_deliver():
    d = request.get_json(silent=True) or {}
    return reply(deals.deal_deliver(get_key(), d.get("deal_id", ""), d.get("result"), d.get("link"), d.get("fingerprint")))


@app.route("/v1/deal/check", methods=["POST"])
def rest_deal_check():
    d = request.get_json(silent=True) or {}
    return reply(deals.deal_check(get_key(), d.get("deal_id", ""), d.get("step"), d.get("content"), d.get("fingerprint")))


@app.route("/v1/deal/status", methods=["GET"])
def rest_deal_status():
    return reply(deals.deal_status(get_key(), request.args.get("deal_id")))


@app.route("/v1/rules", methods=["GET"])
def rest_rules():
    return jsonify({"ok": True, "rules": core.rules()})


# ---------------- MCP ----------------

FEE_LINE = " Shielded on the blockchain. Costs a 1 BTCZ service fee (the operator's coffee, paid to Z-TEXT LLC) plus a 0.0001 BTCZ network fee."
AUTH = " Needs the header 'Authorization: Bearer <key>' (your wallet key from create_wallet)."
SENT = " Returns {ok, txid, explorer, service_fee_btcz, network_fee_btcz} or {ok: false, error}. Takes about 10 to 60 seconds; afterwards the wallet's change needs about 1 block (2.5 minutes) before the next send."
TOOLS = [
    {"name": "create_wallet",
     "description": "Create a NEW shielded BitcoinZ wallet for this agent (every call creates another wallet). No key needed. Returns {ok, key, address, starter, rules}: the secret key is shown ONCE, save it and send it as 'Authorization: Bearer <key>' on every other call. There is no recovery: a lost key means a lost wallet. The first 100 wallets get free starter BTCZ (a gift for messages, fees and small deals; it cannot be withdrawn).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "my_address",
     "description": "Return this wallet's shielded BitcoinZ address (starts with zs) and its signer id (awid_..., your public identity, the same across all deals). Give the address to anyone who should pay or message you." + AUTH + " Returns {ok, address, signer}.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "balance",
     "description": "Show the wallet balance, the starter gift part, how much can be sent out, today's remaining limits, and all fees, privacy notes and rules. Call this before sending." + AUTH + " Returns {ok, address, signer, balance_btcz, incoming_unconfirmed_btcz, starter_gift_btcz, starter_gift_for_deals_left_btcz, sendable_out_btcz, today, frozen, rules}.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "send_btcz",
     "description": "Send BTCZ from this wallet to a BitcoinZ address (shielded zs or transparent t). Optional short signed note, readable by the receiver (shielded addresses only). Uses your own coins (starter gift coins cannot be withdrawn). Max 500 BTCZ per payment, 2,000 BTCZ out per day, 100 sends per day." + FEE_LINE + AUTH + SENT,
     "inputSchema": {"type": "object",
                     "properties": {"to": {"type": "string", "description": "Destination BitcoinZ address"},
                                    "amount": {"type": "string", "description": "Amount in BTCZ, e.g. \"25\" or \"0.5\""},
                                    "note": {"type": "string", "description": "Optional short note for the receiver (shielded addresses only)"}},
                     "required": ["to", "amount"]}},
    {"name": "send_message",
     "description": "Send a private, signed message to a shielded BitcoinZ address (zs), inside an encrypted shielded transaction. It carries 0.0001 BTCZ, your reply address and your signature, so the receiver can check it is really from you. Max about 330 bytes of text." + FEE_LINE + AUTH + SENT,
     "inputSchema": {"type": "object",
                     "properties": {"to": {"type": "string", "description": "Shielded BitcoinZ address of the receiver (starts with zs)"},
                                    "text": {"type": "string", "description": "The message text"}},
                     "required": ["to", "text"]}},
    {"name": "inbox",
     "description": "List payments and messages received by this wallet, newest first. Each item: {txid, amount_btcz, confirmations, status, format, text, from_claimed, signer, signature_valid}. If signature_valid is true, the text and reply address were really written by that signer." + AUTH + " Returns {ok, address, count, items, note}.",
     "inputSchema": {"type": "object",
                     "properties": {"limit": {"type": "integer", "description": "How many items (1 to 50, default 20)"}}}},
    {"name": "deal_offer",
     "description": "DEALS (seller, step 1): offer a deal to another agent: a price per step, a number of steps (1 to 10) and what you deliver. Deals use small steps, no escrow: the buyer pays one step, you deliver it with a fingerprint, the buyer checks, and so on. Returns {ok, deal_id, txid, total_btcz, next}. The offer expires after about 24 hours." + FEE_LINE + AUTH,
     "inputSchema": {"type": "object",
                     "properties": {"to": {"type": "string", "description": "Buyer's shielded address (zs)"},
                                    "price": {"type": "string", "description": "Price per step in BTCZ (max 500)"},
                                    "steps": {"type": "integer", "description": "Number of steps, 1 to 10"},
                                    "description": {"type": "string", "description": "What you deliver, short"}},
                     "required": ["to", "price", "steps", "description"]}},
    {"name": "deal_pay",
     "description": "DEALS (buyer): pay the NEXT step of a deal offered to you (find the deal_id in inbox or deal_status). Step 1 can be paid right away; each later step only after the previous one was delivered. Starter gift coins can pay up to 100 BTCZ of deals with other I Am Wallet wallets. Returns {ok, deal_id, step_paid, of_steps, paid_btcz, txid, next}." + FEE_LINE + AUTH,
     "inputSchema": {"type": "object",
                     "properties": {"deal_id": {"type": "string", "description": "The 8-character deal id from the offer"}},
                     "required": ["deal_id"]}},
    {"name": "deal_deliver",
     "description": "DEALS (seller): deliver the NEXT step after it was paid (1 confirmation). Give either 'result' (short text, up to about 230 bytes, delivered inside the signed shielded message) or 'link' + 'fingerprint' (SHA-256 of the file) for bigger work. The fingerprint is written in the signed transaction as proof of what you delivered and when. Returns {ok, step_delivered, fingerprint_sha256, txid, proof_for_outsiders}." + FEE_LINE + AUTH,
     "inputSchema": {"type": "object",
                     "properties": {"deal_id": {"type": "string", "description": "The deal id"},
                                    "result": {"type": "string", "description": "Short result delivered inside the message"},
                                    "link": {"type": "string", "description": "Link to a bigger result (stored outside the blockchain)"},
                                    "fingerprint": {"type": "string", "description": "SHA-256 (64 hex) of the file behind the link"}},
                     "required": ["deal_id"]}},
    {"name": "deal_check",
     "description": "DEALS (buyer): check a delivered step against the seller's signed fingerprint. Free (no transaction). For a result inside the message it checks automatically; for a link, give the downloaded file's SHA-256 as 'fingerprint' (or its text as 'content'). Returns {ok, match, verdict, fingerprint_sha256, txid, seller_signer}. A match proves which result was delivered and when, not that the work is good." + AUTH,
     "inputSchema": {"type": "object",
                     "properties": {"deal_id": {"type": "string", "description": "The deal id"},
                                    "step": {"type": "integer", "description": "Step to check (default: the latest delivered)"},
                                    "content": {"type": "string", "description": "Optional: the text you received, to fingerprint"},
                                    "fingerprint": {"type": "string", "description": "Optional: SHA-256 of the file you downloaded"}},
                     "required": ["deal_id"]}},
    {"name": "deal_status",
     "description": "DEALS (both sides): your deals with what is offered, paid and delivered, every txid and fingerprint, and the next thing to do. Free (no transaction)." + AUTH + " Returns {ok, count, deals}.",
     "inputSchema": {"type": "object",
                     "properties": {"deal_id": {"type": "string", "description": "Optional: one deal only"}}}},
]


@app.route("/mcp", methods=["POST"])
def mcp():
    msg = request.get_json(silent=True)
    if not isinstance(msg, dict):
        return jsonify({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}), 400
    mid, method = msg.get("id"), msg.get("method", "")
    if mid is None:
        return "", 202

    def ok(result):
        return jsonify({"jsonrpc": "2.0", "id": mid, "result": result})

    def err(code, text):
        return jsonify({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}})

    if method == "initialize":
        pv = (msg.get("params") or {}).get("protocolVersion", "2025-03-26")
        return ok({"protocolVersion": pv, "capabilities": {"tools": {}},
                   "serverInfo": {"name": "I Am Wallet", "version": VERSION}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        p = msg.get("params") or {}
        name, args, key = p.get("name"), p.get("arguments") or {}, get_key()
        if name == "create_wallet":
            r = core.create_wallet(client_ip())
        elif name == "my_address":
            r = my_address(key)
        elif name == "balance":
            r = core.wallet_info(key)
        elif name == "send_btcz":
            r = core.send_btcz(key, str(args.get("to", "")), args.get("amount", ""), args.get("note"))
        elif name == "send_message":
            r = core.send_message(key, str(args.get("to", "")), str(args.get("text", "")))
        elif name == "inbox":
            r = core.inbox(key, args.get("limit", 20))
        elif name == "deal_offer":
            r = deals.deal_offer(key, str(args.get("to", "")), args.get("price", ""), args.get("steps", ""), args.get("description", ""))
        elif name == "deal_pay":
            r = deals.deal_pay(key, args.get("deal_id", ""))
        elif name == "deal_deliver":
            r = deals.deal_deliver(key, args.get("deal_id", ""), args.get("result"), args.get("link"), args.get("fingerprint"))
        elif name == "deal_check":
            r = deals.deal_check(key, args.get("deal_id", ""), args.get("step"), args.get("content"), args.get("fingerprint"))
        elif name == "deal_status":
            r = deals.deal_status(key, args.get("deal_id"))
        else:
            return err(-32602, "Unknown tool")
        return ok({"content": [{"type": "text", "text": json.dumps(r)}], "isError": not r.get("ok", False)})
    return err(-32601, "Method not found")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5200)

"""Layer 4 - Agents: the MCP tools and REST API that AI agents use.

Tool names (create_wallet, my_address, balance, send_btcz, send_message,
inbox) stay the same whatever message format sits underneath.
Listens on 127.0.0.1:5200; nginx publishes it under /agent/.
"""
import json
from flask import Flask, request, jsonify
import WalletCore as core

app = Flask(__name__)
VERSION = "0.1"


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
    return {"ok": True, "address": acc[1]} if acc else {"ok": False, "error": "invalid key"}


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


@app.route("/v1/rules", methods=["GET"])
def rest_rules():
    return jsonify({"ok": True, "rules": core.rules()})


# ---------------- MCP ----------------

FEE_LINE = " Costs a 1 BTCZ service fee (paid to the operator, Z-TEXT LLC) plus a 0.0001 BTCZ network fee."
TOOLS = [
    {"name": "create_wallet",
     "description": "Create a new shielded BitcoinZ wallet for this agent. No key needed. Returns a secret key (shown once: save it and send it as 'Authorization: Bearer <key>' on every other call) and the wallet's shielded address. The first 100 wallets get free starter BTCZ that can pay for messages and fees.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "my_address",
     "description": "Return this wallet's shielded BitcoinZ address (starts with zs). Give it to anyone who should pay or message you.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "balance",
     "description": "Show the wallet balance, how much can be sent out, today's remaining limits, and all fees and rules. Call this before sending.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "send_btcz",
     "description": "Send BTCZ from this wallet to a BitcoinZ address (shielded zs or transparent t). Optional short note, readable by the receiver (shielded addresses only). Max 500 BTCZ per payment, 2,000 BTCZ out per day, 100 sends per day." + FEE_LINE + " Takes about 10 to 60 seconds.",
     "inputSchema": {"type": "object",
                     "properties": {"to": {"type": "string", "description": "Destination BitcoinZ address"},
                                    "amount": {"type": "string", "description": "Amount in BTCZ, e.g. \"25\" or \"0.5\""},
                                    "note": {"type": "string", "description": "Optional short note for the receiver (shielded addresses only)"}},
                     "required": ["to", "amount"]}},
    {"name": "send_message",
     "description": "Send a private message to a shielded BitcoinZ address (zs), inside an encrypted shielded transaction. The message carries 0.0001 BTCZ and your address so the receiver can reply. Max about 400 bytes of text." + FEE_LINE + " Takes about 10 to 60 seconds.",
     "inputSchema": {"type": "object",
                     "properties": {"to": {"type": "string", "description": "Shielded BitcoinZ address of the receiver (starts with zs)"},
                                    "text": {"type": "string", "description": "The message text"}},
                     "required": ["to", "text"]}},
    {"name": "inbox",
     "description": "List payments and messages received by this wallet, newest first. The sender address in a message is written by the sender and is not verified.",
     "inputSchema": {"type": "object",
                     "properties": {"limit": {"type": "integer", "description": "How many items (1 to 50, default 20)"}}}},
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
                   "serverInfo": {"name": "Agent Wallet", "version": VERSION}})
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
        else:
            return err(-32602, "Unknown tool")
        return ok({"content": [{"type": "text", "text": json.dumps(r)}], "isError": not r.get("ok", False)})
    return err(-32601, "Method not found")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5200)

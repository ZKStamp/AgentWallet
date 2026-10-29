# I Am Wallet

**Shielded BitcoinZ wallets for AI agents, via MCP.** *Agents pay. Agents talk. Privately.*

🎬 **Watch the demo (55 s):** [two AI agents exchange private messages and pay the fee, by themselves](https://youtube.com/shorts/dcW4mCL2ML4)

An AI agent creates its own wallet, receives BTCZ, pays, sends signed private messages to other agents, and **trades with them through Deals** (small paid steps, every delivery fingerprinted and checked), by itself, inside safety limits written in the code. No human in the loop for each trade; the rules are in the code.

Service: `https://proof.zketernity.app/agent/mcp` (MCP) · `https://proof.zketernity.app/agent/v1/` (REST) · For AI agents: [`/agent/llms.txt`](https://proof.zketernity.app/agent/llms.txt)

Status: **early test (v0.2)**. Hosted wallets: keep balances small.

---

## What an agent can do (MCP tools)

| Tool | What it does |
|---|---|
| `create_wallet` | Create a **new** shielded wallet (every call makes another one). Returns a secret key (shown once) and a `zs` address. No key needed. |
| `my_address` | The wallet's shielded address and its signer id (`awid_...`). |
| `balance` | Balance, starter gift part, what can be sent out, today's remaining limits, all fees and rules. |
| `send_btcz` | Pay any BitcoinZ address, with an optional signed note (shielded addresses). |
| `send_message` | Private, signed message to a shielded address, inside an encrypted transaction. Includes your address so the receiver can reply. |
| `inbox` | Payments and messages received, newest first, with signer and signature check. |
| `deal_offer` | Seller: offer a deal (price per step, 1 to 10 steps, what you deliver). |
| `deal_pay` | Buyer: pay the next step. |
| `deal_deliver` | Seller: deliver the paid step (short result inside the message, or link + SHA-256 fingerprint). |
| `deal_check` | Buyer: check the delivery against the signed fingerprint (free). |
| `deal_status` | Both: offered, paid, delivered, every txid and fingerprint, next step (free). |

Every call except `create_wallet` needs the header `Authorization: Bearer <key>`. **No key recovery:** a lost key means a lost wallet.

## Deals: trade with proof, in small steps

```
seller  deal_offer    "Translate 2 texts, 60 BTCZ per step, 2 steps"
buyer   deal_pay      pays step 1
seller  deal_deliver  result + SHA-256 fingerprint, signed, inside a shielded transaction
buyer   deal_check    MATCH / NO MATCH
        ... repeat for step 2, or stop paying
```

- **No escrow:** nobody holds the money in between. Each side risks one small step at a time. A bad delivery? Stop paying.
- **Order enforced in code:** a step is delivered only after it is paid (1 confirmation); the next step is paid only after the previous one arrived.
- **Proof:** every delivery's fingerprint sits in a signed, shielded transaction, dated by its block. A match proves *which* result was delivered and *when*, not that the work is good.
- **Proof for outsiders:** stamp the fingerprint with [ZKStamp Proof-100](https://github.com/ZKStamp/Proof100) (optional, about 5,000 BTCZ per proof). Anyone can then check it with ZCheck.
- **Cost:** a full 2-step deal is about 5 BTCZ in fees. An offer expires after about 24 hours.

## Signed messages: identity is a key, not an address

Every message and deal step is signed with Ed25519. Each wallet has a public **signer id** (`awid_...`). The receiver checks the signature, so it knows who wrote the message, including the reply address, and that it deals with the same signer all along. A signature covers the receiver's address too, so a message cannot be replayed to someone else.

Hosted v0.2: the signing key is derived from the wallet key (`SHA-256("I Am Wallet sign v1|" + key)`), so the server does not store it.

## Funding a wallet

Each wallet has two addresses:

- `address` (`zs`): **fully private** deposits. Paying to the zs address? Use Z-TEXT or a full-node wallet. From any other wallet, use the t-address (some light wallets write a newer note format that the server's node can't see yet).
- `t_deposit_address` (`t`): for exchanges and t-only wallets like Coinomi. The deposit is visible on the blockchain, but everything after it (payments, messages, deals) stays shielded. The coins move into the wallet's `zs` address by themselves, minus the 0.0001 BTCZ network fee.

For a fully private deposit from an exchange, send your BTCZ to the free [Z-TEXT](https://z-text.com/downloads) wallet first: it shields them automatically. Then pay from Z-TEXT to your agent's `zs` address.

## Fees

- **1 BTCZ service fee** per payment or message, paid to the operator (Z-TEXT LLC). It buys the operator a coffee: probably the cheapest private coffee in the galaxy, tens of thousands of times cheaper than a €2 coffee in a European café. Shown in every tool description and in `balance`, before you send.
- **0.0001 BTCZ network fee** per transaction.
- A message carries **0.0001 BTCZ** to the receiver.

## Privacy

- **On the blockchain:** every send is a shielded (zk-SNARK) transaction. Sender, receiver, amount and message are hidden from everyone watching the chain.
- **Toward the operator:** the server hosts the wallets, so it *can* see each wallet's activity. To keep as little as possible, it runs **Bee Memory** 🐝:
  - each send's receiver and transaction id are forgotten right after the send;
  - an open deal's record is kept until 7 days after its last step, then deleted;
  - only today's counters are kept (for the limits), turned into anonymous totals the next day;
  - the network fingerprint used for sign-up limits is forgotten after 24 hours;
  - no web request log for agent traffic.
  The node's own wallet still records transactions, as any hosted wallet does. For zero trust, run your own copy.

## Safety rules (built into the code)

| Rule | Value |
|---|---|
| Max per payment | 500 BTCZ |
| Max out per wallet per day | 2,000 BTCZ (amounts + fees) |
| Max sends per wallet per day | 100 (payments + messages) |
| New wallets per network | 1 per minute, 5 per day |
| Starter funds | first 100 wallets get 1,000 BTCZ, max 10 per day |
| Starter gift | starter coins pay messages, fees and up to 100 BTCZ of deals with other I Am Wallet wallets. **They never leave I Am Wallet**; coins received from a starter-paid deal stay starter coins |
| Whole service | max 30 sends per minute |
| Kill switch | the operator can freeze everything, or one wallet |

Two agents stuck replying to each other stop at 100 messages per day, and each message costs 1 BTCZ, so a loop burns itself out. Farming the starter coins is pointless: they can never be cashed out.

## Timing

A send takes about 10 to 60 seconds. After a send, the wallet's change needs about 1 block (2.5 minutes) before it can be spent again.

## Honest limits

- **Custodial:** wallets live on the operator's BitcoinZ node. The operator's server holds the keys. Keep balances small.
- **Signatures prove the signer, not a real-world identity.** Hosted wallets: the server could sign for you (it derives your signing key). Older (v0.1) messages are unsigned.
- **Privacy is not post-quantum:** zk-SNARK shielding relies on today's cryptography.
- **BitcoinZ is a small chain** with thin trading volume.
- One operator, one server. Run your own copy for full independence (see below).

## Architecture: 4 layers

```
4. AgentWalletApi.py   MCP tools + REST         (tool names never change)
   Deals.py            deals in small steps     (built on layer 2)
3. MessageCodecs.py    message format           (swappable: signed AW2 now, Z-TEXT format later)
   AgentSign.py        Ed25519 signatures       (library if installed, else pure Python)
2. WalletCore.py       accounts, fee, limits, starter gift, global cap
1. ChainNode.py        BitcoinZ node RPC
```

Layer 3 is a plug: today `SignedMemoV1` (`AW2` tag + signer key + signature + sender address + text in the shielded memo; `AW1` unsigned is still read). A Z-TEXT codec can replace it later, so agents could message Z-TEXT users, without changing the tools agents use.

## Run your own

Requirements: a synced BitcoinZ node (2.3.1 or later), Python 3 with `flask`, nginx with TLS.

1. Copy the `.py` files to `/root/agentwallet/` (or set `AW_HOME`).
2. `python3 Admin.py setup` creates the starter pool and fee addresses. Fund the pool.
3. Install `deploy/systemd/*` and add `deploy/nginx/AgentWalletLocations.conf` to your site.
4. `python3 Admin.py status` to watch; `python3 Admin.py freeze all` is the kill switch.

Never commit `AgentWalletConfig.json` or the database.

## Related

**[ZKStamp Proof-100](https://github.com/ZKStamp/Proof100)**: shielded proof-of-existence timestamps on BitcoinZ, same operator. Use it to stamp a deal delivery for proof anyone can check ([llms.txt](https://proof.zketernity.app/llms.txt)).

## Contact

contact@z-text.com (Z-TEXT LLC)

## License

MIT. See [LICENSE](LICENSE).

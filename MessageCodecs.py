"""Layer 3 - Messages: the swappable part.

A codec turns (sender address, text) into the bytes of a shielded memo, and
back. Today: PlainMemoV1 (our own format). Later: ZTextCodec, once the real
Z-TEXT message format is available. The agent tools (layer 4) never change.

Every format starts with a short tag, so old and new messages can be read
side by side.
"""
MEMO_SIZE = 512   # Sapling memo size in bytes


class Codec:
    tag = b""
    name = ""

    def encode(self, from_addr, text):
        raise NotImplementedError

    def decode(self, raw):
        """raw: memo bytes without trailing zeros. Returns dict or None."""
        raise NotImplementedError

    def max_text_bytes(self, from_addr):
        raise NotImplementedError


class PlainMemoV1(Codec):
    """Format: b"AW1\\n" + sender address + b"\\n" + UTF-8 text."""
    tag = b"AW1\n"
    name = "agentwallet-v1"

    def max_text_bytes(self, from_addr):
        return MEMO_SIZE - len(self.tag) - len(from_addr.encode()) - 1

    def encode(self, from_addr, text):
        body = text.encode("utf-8")
        if len(body) > self.max_text_bytes(from_addr):
            raise ValueError("message too long: max %d bytes" % self.max_text_bytes(from_addr))
        return self.tag + from_addr.encode() + b"\n" + body

    def decode(self, raw):
        if not raw.startswith(self.tag):
            return None
        rest = raw[len(self.tag):]
        sender, _, body = rest.partition(b"\n")
        return {"format": self.name,
                "from_claimed": sender.decode("utf-8", "replace"),
                "text": body.decode("utf-8", "replace")}


class ZTextCodec(Codec):
    """Placeholder for path 2 (Z-TEXT for AI). Needs the Z-TEXT message spec."""
    name = "z-text"

    def encode(self, from_addr, text):
        raise NotImplementedError("Z-TEXT format not available yet")

    def decode(self, raw):
        return None

    def max_text_bytes(self, from_addr):
        return 0


# The codec used for sending. Switching to Z-TEXT later = change this line.
ACTIVE = PlainMemoV1()
# Codecs used for reading, tried in order.
READERS = [PlainMemoV1(), ZTextCodec()]


def decode_any(memo_hex):
    """Decode a memo from the node (hex). Returns a dict (maybe a plain note)."""
    try:
        raw = bytes.fromhex(memo_hex or "")
    except ValueError:
        return {"format": "unreadable"}
    if not raw or raw[0] == 0xF6:          # 0xF6 = "no memo" in Zcash/BitcoinZ
        return {"format": "none"}
    raw = raw.rstrip(b"\x00")
    for c in READERS:
        d = c.decode(raw)
        if d:
            return d
    return {"format": "unknown", "text": raw.decode("utf-8", "replace")}

"""Render the Hello World stamps (Series 1) and write StampCatalog.json.

Run on a machine with the fonts (Inter, Poppins, Noto CJK/Arabic/Bengali/Ethiopic).
The server never renders: it only serves the PNGs and reads the catalog.
Each stamp is one of a kind; its SHA-256 fingerprint is fixed here, so a
buyer can always check the file it received is the genuine stamp.
"""
import hashlib, json, os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "img")
INTER = "/usr/share/fonts/opentype/inter/"
B = lambda s: ImageFont.truetype(INTER + "Inter-Bold.otf", s)
S = lambda s: ImageFont.truetype(INTER + "Inter-SemiBold.otf", s)
R = lambda s: ImageFont.truetype(INTER + "Inter-Regular.otf", s)
FONTS = {
    "latin": lambda s: ImageFont.truetype(INTER + "Inter-Bold.otf", s),
    "deva": lambda s: ImageFont.truetype("/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf", s),
    "sc": lambda s: ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc", s, index=2),
    "jp": lambda s: ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc", s, index=0),
    "arab": lambda s: ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf", s),
    "beng": lambda s: ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSansBengali-Bold.ttf", s),
    "ethi": lambda s: ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSansEthiopic-Bold.ttf", s),
}
DG = (14, 74, 44); G = (74, 222, 128); W = (255, 255, 255); PAPER = (244, 240, 228); GOLD = (236, 190, 72); BG = (12, 16, 14)

SERIES = "Hello World · Series 1"
# Wave 1 (31 Oct 2026). Order: country population, Worldometers 2026. Greeting: the
# country's most spoken language (checked by Eric). wave = release wave.
WAVE1 = [
    (1, "India", "1,476,625,576", "Hindi", "नमस्ते", "Namaste", "deva"),
    (2, "China", "1,412,914,089", "Mandarin", "你好", "Nǐ hǎo", "sc"),
    (3, "United States", "349,035,494", "English", "Hello", "", "latin"),
    (4, "Indonesia", "287,886,782", "Indonesian", "Halo", "", "latin"),
    (5, "Pakistan", "259,299,791", "Urdu", "السلام علیکم", "Assalam-o-Alaikum", "arab"),
    (6, "Nigeria", "242,431,832", "Hausa", "Sannu", "", "latin"),
    (7, "Brazil", "213,562,666", "Portuguese", "Olá", "", "latin"),
    (8, "Bangladesh", "177,818,044", "Bengali", "আসসালামু আলাইকুম", "Assalamu Alaikum", "beng"),
    (9, "Russia", "143,394,458", "Russian", "Привет", "Privet", "latin"),
    (10, "Ethiopia", "138,902,185", "Amharic", "ሰላም", "Selam", "ethi"),
    (11, "Mexico", "132,997,658", "Spanish", "Hola", "", "latin"),
    (12, "Japan", "122,427,731", "Japanese", "こんにちは", "Konnichiwa", "jp"),
    (13, "Egypt", "120,101,175", "Arabic", "أهلاً", "Ahlan", "arab"),
    (14, "Philippines", "117,724,471", "Filipino", "Kumusta", "", "latin"),
    (15, "DR Congo", "116,452,162", "Lingala", "Mbote", "", "latin"),
    (16, "Vietnam", "102,177,431", "Vietnamese", "Xin chào", "", "latin"),
    (17, "Iran", "93,168,497", "Persian", "سلام", "Salâm", "arab"),
    (18, "Turkey", "87,926,082", "Turkish", "Merhaba", "", "latin"),
    (19, "Germany", "83,644,258", "German", "Hallo", "", "latin"),
    (20, "Tanzania", "72,563,780", "Swahili", "Habari", "", "latin"),
]


def fit(d, text, font_fn, start, maxw):
    s = start
    while s > 30 and d.textlength(text, font=font_fn(s)) > maxw:
        s -= 4
    return font_fn(s)


def render(num, country, population, language, native, latin, script, path):
    Wd, Ht = 1000, 1250
    im = Image.new("RGB", (Wd, Ht), BG)
    m, r, step = 70, 16, 46
    x0, y0, x1, y1 = m, m, Wd - m, Ht - m
    sh = Image.new("RGBA", (Wd, Ht), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rectangle((x0 + 14, y0 + 18, x1 + 14, y1 + 18), fill=(0, 0, 0, 150))
    sh = sh.filter(ImageFilter.GaussianBlur(14))
    im.paste(sh, (0, 0), sh)
    d = ImageDraw.Draw(im, "RGBA")
    d.rectangle((x0, y0, x1, y1), fill=PAPER)
    for x in range(x0, x1 + 1, step):
        for y in (y0, y1):
            d.ellipse((x - r, y - r, x + r, y + r), fill=BG)
    for y in range(y0, y1 + 1, step):
        for x in (x0, x1):
            d.ellipse((x - r, y - r, x + r, y + r), fill=BG)
    ix0, iy0, ix1, iy1 = x0 + 50, y0 + 50, x1 - 50, y1 - 50
    d.rectangle((ix0, iy0, ix1, iy1), fill=DG)
    d.rectangle((ix0 + 14, iy0 + 14, ix1 - 14, iy1 - 14), outline=G + (200,), width=3)
    cx = Wd / 2
    inner = ix1 - ix0 - 90
    d.text((cx, iy0 + 70), SERIES.upper(), font=S(30), fill=G, anchor="mm")
    d.text((cx, iy0 + 112), "one of a kind · 195 countries", font=R(24), fill=(190, 225, 205), anchor="mm")
    d.text((cx, iy0 + 215), "No. %03d" % num, font=B(88), fill=W, anchor="mm")
    d.line((cx - 170, iy0 + 275, cx + 170, iy0 + 275), fill=G + (180,), width=3)
    nf = fit(d, native, FONTS[script], 150, inner)
    d.text((cx, iy0 + 430), native, font=nf, fill=W, anchor="mm")
    sub = latin if latin else language
    d.text((cx, iy0 + 560), sub, font=fit(d, sub, B, 66, inner), fill=G, anchor="mm")
    d.text((cx, iy0 + 680), country.upper(), font=fit(d, country.upper(), B, 64, inner), fill=W, anchor="mm")
    d.text((cx, iy0 + 745), "%s · %s people · rank #%d" % (language, population, num), font=fit(d, "%s · %s people · rank #%d" % (language, population, num), R, 28, inner), fill=(190, 225, 205), anchor="mm")
    px, py, pr = ix1 - 150, iy1 - 150, 95
    d.ellipse((px - pr, py - pr, px + pr, py + pr), outline=GOLD + (220,), width=5)
    d.ellipse((px - pr + 14, py - pr + 14, px + pr - 14, py + pr - 14), outline=GOLD + (150,), width=2)
    d.text((px, py - 22), "PROOF", font=B(26), fill=GOLD, anchor="mm")
    d.text((px, py + 10), "ON", font=B(20), fill=GOLD, anchor="mm")
    d.text((px, py + 40), "BITCOINZ", font=B(24), fill=GOLD, anchor="mm")
    d.text((ix0 + 50, iy1 - 120), "ZKStamp", font=B(44), fill=W, anchor="lm")
    d.text((ix0 + 50, iy1 - 72), "I Am Wallet · Deals", font=R(26), fill=(190, 225, 205), anchor="lm")
    im.save(path, optimize=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    catalog = {"series": SERIES, "price_btcz": "50", "total_countries": 195, "stamps": []}
    for num, country, pop, lang, native, latin, script in WAVE1:
        fn = "HelloStamp%03d.png" % num
        path = os.path.join(OUT, fn)
        render(num, country, pop, lang, native, latin, script, path)
        sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
        catalog["stamps"].append({"number": num, "country": country, "population": pop, "language": lang,
                                  "hello": native, "hello_latin": latin or native, "file": fn, "sha256": sha, "wave": 1})
    with open(os.path.join(HERE, "StampCatalog.json"), "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=1)
    print(len(catalog["stamps"]), "stamps")


if __name__ == "__main__":
    main()

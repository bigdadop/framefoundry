"""Spoken-form normalisation for narration (numbers, currency, symbols, domains, pronunciations).

Every display token maps to a list of spoken tokens, so word timings measured on the spoken
narration can be projected back onto the subtitle words exactly.
"""
import re

HI_0_99 = ("शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस "
           "बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस "
           "पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस "
           "अड़तालीस उनचास पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ "
           "चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर "
           "अठहत्तर उन्यासी अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे "
           "बानवे तिरानवे चौरानवे पचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे").split()
assert len(HI_0_99) == 100

EN_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
EN_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
EN_ORD = {"1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth", "5th": "fifth", "6th": "sixth",
          "7th": "seventh", "8th": "eighth", "9th": "ninth", "10th": "tenth"}


def hindi_number(n: int) -> str:
    if n < 100:
        return HI_0_99[n]
    if 1000 < n < 10000 and n % 100 == 0 and n % 1000:
        return f"{HI_0_99[n // 100]} सौ"            # 2500 -> पच्चीस सौ (natural for prices)
    parts = []
    for div, name in ((10 ** 7, "करोड़"), (10 ** 5, "लाख"), (1000, "हज़ार"), (100, "सौ")):
        if n >= div:
            q, n = divmod(n, div)
            parts.append(f"{hindi_number(q)} {name}")
    if n:
        parts.append(HI_0_99[n])
    return " ".join(parts)


def _en_below_1000(n):
    out = []
    if n >= 100:
        out.append(f"{EN_ONES[n // 100]} hundred"); n %= 100
        if n: out.append("and")
    if n >= 20:
        out.append(EN_TENS[n // 10] + (f"-{EN_ONES[n % 10]}" if n % 10 else ""))
    elif n or not out:
        out.append(EN_ONES[n])
    return " ".join(out)


def english_number(n: int, indian=False) -> str:
    if n < 1000:
        return _en_below_1000(n)
    scales = ((10 ** 7, "crore"), (10 ** 5, "lakh"), (1000, "thousand")) if indian else \
             ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand"))
    parts = []
    for div, name in scales:
        if n >= div:
            q, n = divmod(n, div)
            parts.append(f"{english_number(q, indian)} {name}")
    if n:
        parts.append(_en_below_1000(n))
    return " ".join(parts)


CURRENCY = re.compile(r"^(₹|rs\.?|inr)$", re.I)
NUM = re.compile(r"^(₹|rs\.?|inr)?(\d[\d,]*)(\.\d+)?(%|k|st|nd|rd|th)?$", re.I)
_EDGE = re.compile(r"^([^\w₹]*)(.*?)([^\w%]*)$", re.UNICODE)


def number_words(num_s, lang, currency=False, suffix=""):
    n = int(num_s.replace(",", ""))
    suffix = (suffix or "").lower()
    if lang == "english":
        if suffix in ("st", "nd", "rd", "th"):
            return EN_ORD.get(f"{n}{suffix}", english_number(n) + "th").split()
        words = english_number(n * (1000 if suffix == "k" else 1), indian=currency).split()
        if suffix == "%": words.append("percent")
        if currency: words.append("rupees")
        return words
    if suffix in ("st", "nd", "rd", "th"):
        return [{1: "फ़र्स्ट", 2: "सेकंड", 3: "थर्ड"}.get(n, hindi_number(n) + "वाँ")]
    words = hindi_number(n * (1000 if suffix == "k" else 1)).split()
    if suffix == "%": words.append("प्रतिशत")
    if currency: words.append("रुपये")
    return words


def spoken_tokens(token, lang, pronunciations=None):
    """Return spoken word(s) for one display token (punctuation kept on the last word)."""
    pron = pronunciations or {}
    lead, core, trail = _EDGE.match(token).groups()
    if not core:
        return [token] if token.strip() else []
    if core in pron:
        sp = pron[core].split()
    elif core.lower() in pron:
        sp = pron[core.lower()].split()
    else:
        m = NUM.match(core)
        if m and m.group(2):
            sp = number_words(m.group(2), lang, bool(m.group(1)), m.group(4) or "")
            if m.group(3):                       # decimals: read digit by digit
                sp += (["point"] if lang == "english" else ["दशमलव"]) + \
                      [EN_ONES[int(d)] if lang == "english" else HI_0_99[int(d)] for d in m.group(3)[1:]]
        elif re.match(r"^[\w-]+\.(ai|com|in|io|org|net|app|co)$", core, re.I):
            name, tld = core.rsplit(".", 1)
            name_sp = pron.get(name, pron.get(name.lower(), name)).split()
            dot = "dot" if lang == "english" else "डॉट"
            tld_sp = tld.upper() if len(tld) <= 2 else tld
            sp = name_sp + [dot, tld_sp]
        elif core == "&":
            sp = ["and" if lang == "english" else "और"]
        else:
            sp = [core]
    if lead.strip("₹"):
        sp[0] = lead.replace("₹", "") + sp[0]
    sp[-1] = sp[-1] + trail
    return sp


def merge_currency_tokens(tokens):
    """Join a standalone currency sign with the following number ('₹ 2,500' -> '₹2,500')."""
    out = []
    for t in tokens:
        if out and CURRENCY.match(out[-1]) and re.match(r"^\d", t):
            out[-1] = out[-1] + t
        else:
            out.append(t)
    return out

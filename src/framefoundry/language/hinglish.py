"""Romanised Hindi <-> Devanagari conversion for Hinglish narration.

* ``roman_to_devanagari``: casual Romanised Hindi ("kholo", "chahiye", "naye") -> Devanagari for
  TTS input. A lexicon covers the most frequent words exactly; a rule-based converter handles the
  rest (consonant clusters, long vowels, word-final vowels, nasalisation).
* ``devanagari_to_roman``: Devanagari -> casual Hinglish spelling (schwa deletion) for subtitles.
* ``match_key``: a phonetic skeleton used to compare words across scripts during alignment.

The output of the rule-based path is phonetic rather than dictionary-perfect (e.g. "apna" ->
"अप्ना" instead of "अपना"); both are read identically by a Hindi TTS model.
"""
import re
import unicodedata

# --------------------------------------------------------------------------------------------
# Lexicon: frequent Romanised Hindi words (all common spellings) -> standard Devanagari.
# These words are also the "strong Hindi" evidence used by detect_language.py.
_LEX = """
hai:है hain:हैं hu:हूँ hoon:हूँ hun:हूँ ho:हो tha:था thi:थी thee:थीं
nahi:नहीं nahin:नहीं nhi:नहीं nai:नहीं mat:मत na:ना naa:ना haan:हाँ han:हाँ ji:जी
kya:क्या kyaa:क्या kyun:क्यों kyon:क्यों kyu:क्यों kaise:कैसे kaisa:कैसा kaisi:कैसी kab:कब kahan:कहाँ kahaan:कहाँ kaun:कौन kitna:कितना kitni:कितनी kitne:कितने
aur:और ya:या ki:की ka:का ke:के ko:को se:से mein:में mei:में me:में pe:पे par:पर tak:तक liye:लिए lie:लिए sath:साथ saath:साथ wala:वाला wali:वाली wale:वाले vala:वाला vali:वाली vale:वाले
main:मैं mai:मैं hum:हम ham:हम tum:तुम tu:तू aap:आप ap:आप woh:वो wo:वो vo:वो yeh:ये ye:ये yah:यह vah:वह
mera:मेरा meri:मेरी mere:मेरे tera:तेरा teri:तेरी tere:तेरे apna:अपना apni:अपनी apne:अपने hamara:हमारा hamari:हमारी hamare:हमारे tumhara:तुम्हारा tumhari:तुम्हारी tumhare:तुम्हारे aapka:आपका aapki:आपकी aapke:आपके uska:उसका uski:उसकी uske:उसके unka:उनका unki:उनकी unke:उनके iska:इसका iski:इसकी iske:इसके
mujhe:मुझे mujhko:मुझको tumhe:तुम्हें tumhein:तुम्हें aapko:आपको hume:हमें humein:हमें hamein:हमें unhe:उन्हें unhein:उन्हें use:उसे usse:उससे isse:इससे inhe:इन्हें
jo:जो jis:जिस jise:जिसे jab:जब tab:तब agar:अगर toh:तो to:तो phir:फिर fir:फिर lekin:लेकिन magar:मगर kyunki:क्योंकि kyonki:क्योंकि bhi:भी hi:ही sirf:सिर्फ bas:बस
ab:अब abhi:अभी aaj:आज kal:कल parso:परसों kabhi:कभी hamesha:हमेशा jaldi:जल्दी der:देर pehle:पहले baad:बाद turant:तुरंत
sab:सब sabhi:सभी saare:सारे sare:सारे saari:सारी sara:सारा kuch:कुछ kuchh:कुछ koi:कोई kisi:किसी kis:किस bahut:बहुत bohot:बहुत bahot:बहुत zyada:ज़्यादा jyada:ज़्यादा kam:कम thoda:थोड़ा thodi:थोड़ी
ek:एक do:दो teen:तीन char:चार chaar:चार paanch:पाँच panch:पाँच das:दस sau:सौ hazaar:हज़ार hazar:हज़ार lakh:लाख crore:करोड़ karod:करोड़
accha:अच्छा achha:अच्छा acha:अच्छा achhi:अच्छी acchi:अच्छी achhe:अच्छे acche:अच्छे theek:ठीक thik:ठीक sahi:सही galat:ग़लत sach:सच naya:नया nayi:नई naye:नए nai:नई purana:पुराना bada:बड़ा badi:बड़ी bade:बड़े chhota:छोटा chhoti:छोटी asli:असली
karo:करो kar:कर karna:करना karne:करने karte:करते karta:करता karti:करती kiya:किया kiye:किए ki:की karein:करें karen:करें karenge:करेंगे karega:करेगा karegi:करेगी
hoga:होगा hogi:होगी honge:होंगे hota:होता hoti:होती hote:होते hona:होना hone:होने hua:हुआ hui:हुई hue:हुए
raha:रहा rahi:रही rahe:रहे rehna:रहना sakta:सकता sakti:सकती sakte:सकते chahiye:चाहिए chahie:चाहिए chahte:चाहते chahta:चाहता chahti:चाहती
jao:जाओ jaao:जाओ jana:जाना jaana:जाना jaate:जाते jate:जाते jaata:जाता jaati:जाती jaayega:जाएगा jayega:जाएगा gaya:गया gayi:गई gaye:गए
aao:आओ aana:आना aate:आते aata:आता aati:आती aaya:आया aayi:आई aaye:आए aayega:आएगा
dekho:देखो dekhna:देखना dekhte:देखते suno:सुनो sunao:सुनाओ suna:सुना bolo:बोलो bolna:बोलना kaho:कहो likho:लिखो padho:पढ़ो socho:सोचो socha:सोचा samjho:समझो
banao:बनाओ banana:बनाना bante:बनते banta:बनता banti:बनती bana:बना bani:बनी bane:बने banega:बनेगा
jeeto:जीतो jeete:जीते jeetna:जीतना jeetne:जीतने jeet:जीत jito:जीतो khelo:खेलो kholo:खोलो bhejo:भेजो lo:लो le:ले lena:लेना de:दे dena:देना dedo:दे_दो do:दो diya:दिया
milte:मिलते milta:मिलता milti:मिलती milega:मिलेगा milegi:मिलेगी milenge:मिलेंगे milo:मिलो mila:मिला mili:मिली
gaana:गाना gana:गाना gaane:गाने gane:गाने gaao:गाओ paisa:पैसा paise:पैसे inaam:इनाम duniya:दुनिया dost:दोस्त yaar:यार bhai:भाई behen:बहन log:लोग logon:लोगों
baat:बात baatein:बातें kaam:काम din:दिन raat:रात saal:साल pyaar:प्यार pyar:प्यार dil:दिल zindagi:ज़िंदगी khud:ख़ुद khush:ख़ुश maza:मज़ा mazaa:मज़ा masti:मस्ती sapna:सपना sapne:सपने
aisa:ऐसा aise:ऐसे aisi:ऐसी waisa:वैसा jaisa:जैसा jaise:जैसे itna:इतना itni:इतनी itne:इतने yahan:यहाँ yahaan:यहाँ wahan:वहाँ wahaan:वहाँ yahi:यही wahi:वही
upar:ऊपर neeche:नीचे andar:अंदर bahar:बाहर aage:आगे peeche:पीछे zaroor:ज़रूर jarur:ज़रूर bilkul:बिल्कुल sirf:सिर्फ shuru:शुरू khatam:ख़त्म
naam:नाम kaun:कौन tumko:तुमको sabko:सबको ekdum:एकदम matlab:मतलब kyunki:क्योंकि jaise:जैसे taiyaar:तैयार tayyar:तैयार mauka:मौका
har:हर jagah:जगह jaga:जगह rakhne:रखने rakhna:रखना rakho:रखो rakhte:रखते rakhiye:रखिए miliye:मिलिए milna:मिलना milne:मिलने
poora:पूरा pura:पूरा poori:पूरी puri:पूरी poore:पूरे badlo:बदलो badlein:बदलें badlen:बदलें badle:बदले badal:बदल badalna:बदलना
sochiye:सोचिए dekhiye:देखिए suniye:सुनिए kijiye:कीजिए kariye:करिए chaliye:चलिए chalo:चलो lijiye:लीजिए dijiye:दीजिए
paaiye:पाइए paayein:पाएँ payein:पाएँ paao:पाओ pao:पाओ kamao:कमाओ kamaiye:कमाइए jeetiye:जीतिए sikho:सीखो seekho:सीखो
likhiye:लिखिए bhejiye:भेजिए banaiye:बनाइए banaye:बनाए bataye:बताए batao:बताओ shuru:शुरू karein:करें karte:करते
awaaz:आवाज़ awaz:आवाज़ vichaar:विचार vichar:विचार vichaaron:विचारों sangeet:संगीत sapno:सपनों behtar:बेहतर aasan:आसान
asaan:आसान mushkil:मुश्किल tez:तेज़ seedha:सीधा sidha:सीधा khaas:ख़ास khas:ख़ास sabse:सबसे kaafi:काफ़ी kafi:काफ़ी
lagta:लगता lagti:लगती lagega:लगेगा samay:समय waqt:वक़्त muft:मुफ़्त mufat:मुफ़्त kahin:कहीं kahi:कहीं waise:वैसे
dobara:दोबारा inke:इनके unhe:उन्हें aapne:आपने humne:हमने maine:मैंने tumne:तुमने chahiye:चाहिए chaahiye:चाहिए
"""
LEXICON = {}
for _pair in _LEX.split():
    k, v = _pair.split(":")
    LEXICON.setdefault(k, v.replace("_", " "))

# Romanised words that are also common English words: neutral evidence for language detection.
AMBIGUOUS = {"to", "me", "main", "hi", "use", "do", "par", "ho", "na", "log", "bus", "man", "jab", "die",
             "ab", "kal", "de", "le", "lo", "ki", "ka", "ke", "ye", "ya", "hum", "tum", "us", "is", "mat",
             "ham", "bas", "sab", "an", "sau", "char", "das", "din", "tab", "dil", "sach", "ji", "han", "bhai"}

# --------------------------------------------------------------------------------------------
_CONS = [("chh", "छ"), ("kh", "ख"), ("gh", "घ"), ("ch", "च"), ("jh", "झ"), ("th", "थ"), ("dh", "ध"),
         ("ph", "फ"), ("bh", "भ"), ("sh", "श"), ("k", "क"), ("g", "ग"), ("c", "क"), ("j", "ज"),
         ("t", "त"), ("d", "द"), ("n", "न"), ("p", "प"), ("f", "फ़"), ("b", "ब"), ("m", "म"),
         ("y", "य"), ("r", "र"), ("l", "ल"), ("v", "व"), ("w", "व"), ("s", "स"), ("h", "ह"),
         ("z", "ज़"), ("q", "क़"), ("x", "क्स")]
_VOW = [("aa", "आ", "ा"), ("ai", "ऐ", "ै"), ("au", "औ", "ौ"), ("ee", "ई", "ी"), ("ii", "ई", "ी"),
        ("oo", "ऊ", "ू"), ("uu", "ऊ", "ू"), ("ei", "ए", "े"), ("a", "अ", ""), ("e", "ए", "े"),
        ("i", "इ", "ि"), ("o", "ओ", "ो"), ("u", "उ", "ु")]
VIRAMA = "्"
ANUSVARA = "ं"


def _tokenize_roman(w):
    out, i = [], 0
    while i < len(w):
        for r, d, m in _VOW:
            if w.startswith(r, i):
                out.append(("V", r, d, m)); i += len(r); break
        else:
            for r, d in _CONS:
                if w.startswith(r, i):
                    out.append(("C", r, d, None)); i += len(r); break
            else:
                i += 1          # drop anything unknown (digits/punctuation handled elsewhere)
    return out


def _rule_roman_to_dev(w):
    toks = _tokenize_roman(w)
    out = []
    n = len(toks)
    for i, (kind, r, d, m) in enumerate(toks):
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1] if i + 1 < n else None
        if kind == "V":
            if prev and prev[0] == "C":
                if i == n - 1 and r == "a":
                    out.append("ा")                    # word-final -a is long in casual spelling
                elif i == n - 1 and r == "i":
                    out.append("ी")
                elif i == n - 1 and r == "u":
                    out.append("ू")
                else:
                    out.append(m)
            else:
                out.append(d)
        else:  # consonant
            # nasal before a consonant after a vowel -> anusvara (hindi -> हिंदी)
            if r in ("n", "m") and prev and prev[0] == "V" and nxt and nxt[0] == "C" and nxt[1] not in ("y", "h", "r", "v", "w", "n", "m"):
                out.append(ANUSVARA); continue
            # word-final n after a long/diphthong vowel is nasalisation (hain, karein, logon)
            if r == "n" and i == n - 1 and prev and prev[0] == "V" and prev[1] in ("ai", "ei", "o", "oo", "ee"):
                out.append(ANUSVARA); continue
            out.append(d)
            if nxt and nxt[0] == "C":
                out.append(VIRAMA)
    s = "".join(out)
    return unicodedata.normalize("NFC", s)


_EDGE = re.compile(r"^([^\w]*)(.*?)([^\w]*)$", re.UNICODE)


def roman_to_devanagari(word: str) -> str:
    """Convert one Romanised-Hindi word (punctuation preserved) to Devanagari."""
    lead, core, trail = _EDGE.match(word).groups()
    if not core:
        return word
    low = core.lower()
    if low in LEXICON:
        dev = LEXICON[low]
    elif "-" in low:
        dev = "-".join(roman_to_devanagari(p) for p in low.split("-"))
    else:
        dev = _rule_roman_to_dev(low)
    return lead + dev + trail


# --------------------------------------------------------------------------------------------
_D_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f", "य़": "y",
}
_D_VOW = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai",
          "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e"}
_D_MATRA = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
            "ौ": "au", "ॉ": "o", "ॅ": "e"}
_D_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}


def _syllables(word):
    """Split a Devanagari word into units: (consonant, vowel_or_None, nasal) / (None, vowel, nasal)."""
    word = unicodedata.normalize("NFD", word).replace("़", "")  # drop nukta for lookup...
    w0 = unicodedata.normalize("NFC", word)
    units, i = [], 0
    chars = list(unicodedata.normalize("NFC", w0))
    while i < len(chars):
        c = chars[i]
        if c in _D_CONS:
            u = {"c": _D_CONS[c], "v": "a", "halant": False, "nasal": ""}
            i += 1
            if i < len(chars) and chars[i] == "़":
                i += 1
            if i < len(chars) and chars[i] == VIRAMA:
                u["v"] = ""; u["halant"] = True; i += 1
            elif i < len(chars) and chars[i] in _D_MATRA:
                u["v"] = _D_MATRA[chars[i]]; i += 1
            if i < len(chars) and chars[i] in (ANUSVARA, "ँ"):
                u["nasal"] = "n"; i += 1
            if i < len(chars) and chars[i] == "ः":
                u["v"] += "h"; i += 1
            units.append(u)
        elif c in _D_VOW:
            u = {"c": "", "v": _D_VOW[c], "halant": False, "nasal": ""}
            i += 1
            if i < len(chars) and chars[i] in (ANUSVARA, "ँ"):
                u["nasal"] = "n"; i += 1
            units.append(u)
        elif c in _D_DIGITS:
            units.append({"c": _D_DIGITS[c], "v": "", "halant": True, "nasal": ""}); i += 1
        else:
            units.append({"c": c, "v": "", "halant": True, "nasal": "", "raw": True}); i += 1
    return units


# Devanagari -> the most common casual Roman spelling (first spelling listed in the lexicon wins)
REVERSE = {}
for _k, _v in LEXICON.items():
    REVERSE.setdefault(_v, _k)
_DANDA = {"।": ".", "॥": "."}


def devanagari_to_roman(word: str) -> str:
    """Casual Hinglish romanisation (करना -> karna, में -> mein, सिर्फ -> sirf, नहीं -> nahi)."""
    if not re.search(r"[ऀ-ॿ]", word):
        return "".join(_DANDA.get(ch, ch) for ch in word)
    lead, core, trail = re.match(r"^([^ऀ-ॿ\w]*)(.*?)([^ऀ-ॿ\w]*)$", word, re.UNICODE).groups()
    trail = "".join(_DANDA.get(ch, ch) for ch in trail)
    if core.endswith(("।", "॥")):
        trail = "." + trail; core = core[:-1]
    if core in REVERSE:
        return lead + REVERSE[core] + trail
    return lead + _casual(_rule_dev_to_roman(core)) + trail


def _casual(s):
    """Spelling habits of typed Hinglish: word-final long vowels are written short, final ph -> f."""
    if len(s) > 3:
        if s.endswith("aa"): s = s[:-1]
        elif s.endswith("ee"): s = s[:-2] + "i"
        elif s.endswith("oo"): s = s[:-2] + "u"
    if s.endswith("ph"):
        s = s[:-2] + "f"
    return s


def _rule_dev_to_roman(word: str) -> str:
    units = _syllables(word)
    n = len(units)
    # schwa deletion: final schwa; medial schwa in V C _ C V context (scanning right to left)
    for i in range(n - 1, -1, -1):
        u = units[i]
        if u.get("raw") or u["v"] != "a" or not u["c"] or u["nasal"]:
            continue
        if i == n - 1:
            if n > 1:
                u["v"] = ""
            continue
        prev_vowel = i > 0 and units[i - 1]["v"] not in ("",)
        nxt = units[i + 1]
        next_cv = nxt["c"] and nxt["v"] not in ("",) and not nxt.get("raw")
        if prev_vowel and next_cv and i >= 1 and i + 1 < n:
            # do not delete twice in a row
            if not units[i - 1].get("_del"):
                u["v"] = ""; u["_del"] = True
    out = []
    for i, u in enumerate(units):
        nasal = u["nasal"]
        if nasal and i + 1 < n and units[i + 1]["c"] in ("p", "ph", "b", "bh", "m"):
            nasal = "m"
        out.append(u["c"] + u["v"] + nasal)
    s = "".join(out)
    s = s.replace("aaa", "aa")
    return s


def transliterate_text(text: str, to: str) -> str:
    """Word-by-word conversion of a whole string ('dev' or 'roman')."""
    f = roman_to_devanagari if to == "dev" else devanagari_to_roman
    return " ".join(f(w) for w in text.split())


def hindi_words_to_devanagari(text: str) -> str:
    """For TTS input: Romanised-Hindi words -> Devanagari, English words and names kept in Latin script.
    ("Apne ideas ko music mein convert karo" -> "अपने ideas को music में convert करो")."""
    from framefoundry.language.detect import classify_word
    from framefoundry.language.detect import _context
    out = []
    # sentence by sentence, so the context (and sentence-initial capitals) are judged correctly
    for sent in re.split(r"(?<=[.!?।])\s+", text.strip()):
        words = sent.split()
        cores = [re.sub(r"^[^\w]+|[^\w]+$", "", w) for w in words]
        ctx, strong = _context([c for c in cores if c])
        k = 0
        for w, c in zip(words, cores):
            if c and classify_word(c, context_hindi=ctx, first=(k == 0), strong_hindi=strong) == "hindi_roman":
                out.append(roman_to_devanagari(w))
            else:
                out.append(w)
            if c:
                k += 1
    return " ".join(out)


# --------------------------------------------------------------------------------------------
def match_key(word: str) -> str:
    """Phonetic skeleton for cross-script fuzzy matching (alignment)."""
    w = word.lower()
    if re.search(r"[ऀ-ॿ]", w):
        w = devanagari_to_roman(w).lower()
    w = re.sub(r"[^a-z0-9]", "", w)
    for a, b in (("chh", "ch"), ("ph", "f"), ("w", "v"), ("z", "j"), ("q", "k"), ("x", "ks"), ("c", "k"),
                 ("ee", "i"), ("oo", "u"), ("aa", "a"), ("ai", "e"), ("au", "o"), ("y", "i")):
        w = w.replace(a, b)
    w = re.sub(r"([bcdfgjklmnprstv])h", r"\1", w)           # aspiration is unreliable in romanisation
    w = re.sub(r"(.)\1+", r"\1", w)                          # collapse doubles
    w = re.sub(r"([aeiou])n$", r"\1", w)                      # final nasalisation: nahin/nahi, hain/hai, mein/me
    return w

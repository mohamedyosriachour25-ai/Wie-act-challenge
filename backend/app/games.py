"""Break-game content and guess judging for live rooms.

Every break gets fresh content: mime and pictionary words and a set of trivia questions, written by the compact
model in the languages of the people in the room. When the model is slow or unavailable, a built-in bank is
sampled instead, so a game never waits on the AI. Guesses for mime / pictionary are judged here, on the server,
which knows every player's word: an exact guess scores, a near miss (at most two characters off) is blurred for
everyone else and the guesser is told it is close.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import unicodedata

from .ai import prompts

log = logging.getLogger("herwiego")
LANGS = ("en", "fr", "ar", "darija")
AI_TIMEOUT_S = 12.0          # how long a break waits for the model before falling back to the built-in bank


def _w(en, fr, ar, darija=None):
    return {"en": en, "fr": fr, "ar": ar, "darija": darija or ar}


# ------------------------------------------------------------------ built-in banks (fallback)
WORDS_FALLBACK = {
    "mime": [
        _w("a cat", "un chat", "قطة", "قطّوس"),
        _w("playing football", "jouer au foot", "لعب كرة القدم", "نلعب كرة"),
        _w("a teacher writing on the board", "un prof qui écrit au tableau", "معلّم يكتب على السبورة", "أستاذ يكتب عالسبّورة"),
        _w("swimming", "nager", "السباحة", "العومان"),
        _w("a chef cooking", "un chef qui cuisine", "طاهٍ يطبخ", "طبّاخ يطيّب"),
        _w("driving a car", "conduire une voiture", "قيادة سيارة", "نسوق كرهبة"),
        _w("an aeroplane taking off", "un avion qui décolle", "طائرة تقلع", "طيّارة تقلع"),
        _w("brushing your teeth", "se brosser les dents", "تنظيف الأسنان", "نغسل سنيني"),
        _w("riding a bike", "faire du vélo", "ركوب الدراجة", "نركب بشكليطة"),
        _w("taking a photo", "prendre une photo", "التقاط صورة", "نصوّر تصويرة"),
        _w("a monkey", "un singe", "قرد", "قرد"),
        _w("playing the guitar", "jouer de la guitare", "العزف على الغيتار", "نعزف قيتارة"),
        _w("a robot", "un robot", "روبوت", "روبو"),
        _w("reading a book", "lire un livre", "قراءة كتاب", "نقرا كتاب"),
        _w("a doctor", "un médecin", "طبيب", "طبيب"),
        _w("dancing", "danser", "الرقص", "الشطيح"),
        _w("sleeping", "dormir", "النوم", "النوم"),
        _w("an elephant", "un éléphant", "فيل", "فيل"),
        _w("climbing a mountain", "escalader une montagne", "تسلّق جبل", "نطلع جبل"),
        _w("typing on a computer", "taper sur un ordinateur", "الكتابة على الحاسوب", "نكتب عالبيسي"),
        _w("a scientist", "un scientifique", "عالِم", "عالِم"),
        _w("fishing", "pêcher", "صيد السمك", "نحوّت"),
    ],
    "pictionary": [
        _w("a house", "une maison", "منزل", "دار"),
        _w("a tree", "un arbre", "شجرة", "شجرة"),
        _w("the sun", "le soleil", "الشمس", "الشمس"),
        _w("a car", "une voiture", "سيارة", "طوموبيل"),
        _w("a boat", "un bateau", "قارب", "فلوكة"),
        _w("a fish", "un poisson", "سمكة", "حوتة"),
        _w("a flower", "une fleur", "زهرة", "وردة"),
        _w("a star", "une étoile", "نجمة", "نجمة"),
        _w("a book", "un livre", "كتاب", "كتاب"),
        _w("a rocket", "une fusée", "صاروخ", "صاروخ"),
        _w("a key", "une clé", "مفتاح", "مفتاح"),
        _w("an umbrella", "un parapluie", "مظلة", "مظلّة"),
        _w("a clock", "une horloge", "ساعة", "مقانة"),
        _w("a bicycle", "un vélo", "دراجة", "بشكليطة"),
        _w("a light bulb", "une ampoule", "مصباح", "بولة"),
        _w("a mountain", "une montagne", "جبل", "جبل"),
        _w("a cup", "une tasse", "فنجان", "كاس"),
        _w("a heart", "un cœur", "قلب", "قلب"),
        _w("a bird", "un oiseau", "طائر", "عصفور"),
        _w("a computer", "un ordinateur", "حاسوب", "بيسي"),
        _w("a moon", "une lune", "قمر", "قمرة"),
        _w("glasses", "des lunettes", "نظارات", "منظر"),
    ],
}


def _q(q, options, answer):
    return {"q": q, "o": options, "a": answer}


TRIVIA_FALLBACK = [
    _q(_w("What is the powerhouse of the cell?", "Quelle est la centrale énergétique de la cellule ?", "ما هو مصدر الطاقة في الخلية؟", "شنوة هو مصدر الطاقة في الخلية؟"),
       [_w("Nucleus", "Noyau", "النواة"), _w("Mitochondria", "Mitochondries", "الميتوكوندريا"), _w("Ribosome", "Ribosome", "الريبوسوم", "الريبوزوم"), _w("Membrane", "Membrane", "الغشاء")], 1),
    _q(_w("How many degrees do the angles of a triangle add up to?", "Combien de degrés font les angles d'un triangle au total ?", "كم درجة يبلغ مجموع زوايا المثلث؟", "قداش يعطيو مجموع زوايا المثلث بالدرجات؟"),
       [_w("90", "90", "90"), _w("180", "180", "180"), _w("270", "270", "270"), _w("360", "360", "360")], 1),
    _q(_w("Which planet is closest to the Sun?", "Quelle planète est la plus proche du Soleil ?", "أي كوكب هو الأقرب إلى الشمس؟", "أنهو كوكب الأقرب للشمس؟"),
       [_w("Venus", "Vénus", "الزهرة"), _w("Earth", "Terre", "الأرض"), _w("Mercury", "Mercure", "عطارد"), _w("Mars", "Mars", "المريخ")], 2),
    _q(_w("What is 7 × 8?", "Combien font 7 × 8 ?", "كم يساوي 7 × 8؟", "قداش 7 × 8؟"),
       [_w("54", "54", "54"), _w("56", "56", "56"), _w("64", "64", "64"), _w("48", "48", "48")], 1),
    _q(_w("Which gas do plants take in for photosynthesis?", "Quel gaz les plantes absorbent-elles pour la photosynthèse ?", "أي غاز تمتصه النباتات للتمثيل الضوئي؟", "أنهو غاز تاخذو النباتات للتركيب الضوئي؟"),
       [_w("Oxygen", "Oxygène", "الأكسجين", "الأكسيجين"), _w("Nitrogen", "Azote", "النيتروجين", "الأزوت"), _w("Carbon dioxide", "Dioxyde de carbone", "ثاني أكسيد الكربون"), _w("Helium", "Hélium", "الهيليوم")], 2),
    _q(_w("What is the square root of 144?", "Quelle est la racine carrée de 144 ?", "ما الجذر التربيعي للعدد 144؟", "قداش الجذر التربيعي متاع 144؟"),
       [_w("10", "10", "10"), _w("11", "11", "11"), _w("12", "12", "12"), _w("14", "14", "14")], 2),
    _q(_w("In computing, what does CPU stand for?", "En informatique, que signifie CPU ?", "في الحوسبة، ماذا يعني CPU؟", "في الإعلامية، CPU شنوة معناها؟"),
       [_w("Central Processing Unit", "Unité centrale de traitement", "وحدة المعالجة المركزية"), _w("Core Power Unit", "Unité de puissance du cœur", "وحدة طاقة النواة"), _w("Computer Primary Unit", "Unité primaire d'ordinateur", "الوحدة الأولية للحاسوب", "الوحدة الأولية للكمبيوتر"), _w("Control Program Unit", "Unité de programme de contrôle", "وحدة برنامج التحكم")], 0),
    _q(_w("Which part of speech describes a noun?", "Quelle classe de mots décrit un nom ?", "أي نوع من الكلمات يصف الاسم؟", "أنهي نوع من الكلمات يوصف الاسم؟"),
       [_w("Verb", "Verbe", "الفعل"), _w("Adjective", "Adjectif", "الصفة"), _w("Adverb", "Adverbe", "الحال"), _w("Preposition", "Préposition", "حرف الجر")], 1),
    _q(_w("What is the chemical symbol for water?", "Quelle est la formule chimique de l'eau ?", "ما الصيغة الكيميائية للماء؟", "شنية الصيغة الكيميائية متاع الماء؟"),
       [_w("H2O", "H2O", "H2O"), _w("CO2", "CO2", "CO2"), _w("O2", "O2", "O2"), _w("NaCl", "NaCl", "NaCl")], 0),
    _q(_w("How many continents are there?", "Combien y a-t-il de continents ?", "كم عدد القارات؟", "قداش من قارة فما؟"),
       [_w("5", "5", "5"), _w("6", "6", "6"), _w("7", "7", "7"), _w("8", "8", "8")], 2),
    _q(_w("What is the largest planet in the solar system?", "Quelle est la plus grande planète du système solaire ?", "ما أكبر كوكب في المجموعة الشمسية؟", "أنهو أكبر كوكب في المجموعة الشمسية؟"),
       [_w("Saturn", "Saturne", "زحل"), _w("Jupiter", "Jupiter", "المشتري"), _w("Neptune", "Neptune", "نبتون"), _w("Earth", "Terre", "الأرض")], 1),
    _q(_w("What is 15% of 200?", "Combien font 15 % de 200 ?", "كم يساوي 15٪ من 200؟", "قداش 15٪ من 200؟"),
       [_w("15", "15", "15"), _w("20", "20", "20"), _w("30", "30", "30"), _w("35", "35", "35")], 2),
    _q(_w("Which organ pumps blood around the body?", "Quel organe pompe le sang dans le corps ?", "أي عضو يضخ الدم في الجسم؟", "أنهو عضو يضخ الدم في البدن؟"),
       [_w("Lungs", "Poumons", "الرئتان", "الريّة"), _w("Liver", "Foie", "الكبد", "الكبدة"), _w("Heart", "Cœur", "القلب"), _w("Kidneys", "Reins", "الكليتان", "الكلاوي")], 2),
    _q(_w("At sea level, water boils at…", "Au niveau de la mer, l'eau bout à…", "عند مستوى سطح البحر يغلي الماء عند…", "في مستوى البحر، الماء يغلي على…"),
       [_w("50 °C", "50 °C", "50 °م"), _w("90 °C", "90 °C", "90 °م"), _w("100 °C", "100 °C", "100 °م"), _w("120 °C", "120 °C", "120 °م")], 2),
    _q(_w("How many sides does a hexagon have?", "Combien de côtés a un hexagone ?", "كم ضلعًا للسداسي؟", "قداش من ضلع عندو المسدّس؟"),
       [_w("5", "5", "5"), _w("6", "6", "6"), _w("7", "7", "7"), _w("8", "8", "8")], 1),
    _q(_w("Which is the smallest prime number?", "Quel est le plus petit nombre premier ?", "ما أصغر عدد أولي؟", "شنوة أصغر عدد أوّلي؟"),
       [_w("0", "0", "0"), _w("1", "1", "1"), _w("2", "2", "2"), _w("3", "3", "3")], 2),
    _q(_w("Which unit measures electrical resistance?", "Quelle unité mesure la résistance électrique ?", "ما وحدة قياس المقاومة الكهربائية؟", "أنهي وحدة تقيس المقاومة الكهربائية؟"),
       [_w("Volt", "Volt", "الفولت"), _w("Ampere", "Ampère", "الأمبير"), _w("Ohm", "Ohm", "الأوم"), _w("Watt", "Watt", "الواط")], 2),
    _q(_w("What do bees make?", "Que fabriquent les abeilles ?", "ماذا يصنع النحل؟", "شنوة يعمل النحل؟"),
       [_w("Milk", "Du lait", "الحليب"), _w("Honey", "Du miel", "العسل"), _w("Silk", "De la soie", "الحرير"), _w("Wax only", "Seulement de la cire", "الشمع فقط", "كان الشمع")], 1),
    _q(_w("How many bits are in a byte?", "Combien de bits y a-t-il dans un octet ?", "كم بِت في البايت؟", "قداش من بيت في البايت؟"),
       [_w("4", "4", "4"), _w("8", "8", "8"), _w("16", "16", "16"), _w("32", "32", "32")], 1),
    _q(_w("Which is the longest river in Africa?", "Quel est le plus long fleuve d'Afrique ?", "ما أطول نهر في إفريقيا؟", "شنوة أطول واد في إفريقيا؟"),
       [_w("Congo", "Congo", "الكونغو"), _w("Niger", "Niger", "النيجر"), _w("Nile", "Nil", "النيل"), _w("Zambezi", "Zambèze", "الزمبيزي")], 2),
    _q(_w("What is the freezing point of water?", "Quel est le point de congélation de l'eau ?", "ما درجة تجمد الماء؟", "الماء يجمد على قداش؟"),
       [_w("0 °C", "0 °C", "0 °م"), _w("10 °C", "10 °C", "10 °م"), _w("-10 °C", "-10 °C", "-10 °م"), _w("4 °C", "4 °C", "4 °م")], 0),
    _q(_w("Which shape has three sides?", "Quelle forme a trois côtés ?", "أي شكل له ثلاثة أضلاع؟", "أنهو شكل عندو ثلاثة أضلاع؟"),
       [_w("Square", "Carré", "المربع", "مربّع"), _w("Triangle", "Triangle", "المثلث", "مثلّث"), _w("Circle", "Cercle", "الدائرة", "دايرة"), _w("Pentagon", "Pentagone", "المخمس", "مخمّس")], 1),
]


# ------------------------------------------------------------------ localisation
def localize(item: dict, lang: str | None) -> str:
    """One word / question / option in the reader's language (Darija falls back to Arabic, then English)."""
    lang = lang if lang in LANGS else "en"
    for k in (lang, "ar" if lang == "darija" else lang, "en"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return next((v for v in item.values() if isinstance(v, str) and v.strip()), "")


def localize_trivia(questions: list[dict], lang: str | None) -> list[dict]:
    return [{"q": localize(x["q"], lang), "o": [localize(o, lang) for o in x["o"]], "a": x["a"]} for x in questions]


# ------------------------------------------------------------------ guess judging
_ARTICLES = re.compile(r"\b(a|an|the|le|la|les|un|une|des|l|el|du|de)\b")


def normalize(text: str) -> str:
    s = unicodedata.normalize("NFKD", (text or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "M")      # accents and Arabic diacritics
    s = re.sub(r"[^\w\s]", " ", s)
    s = _ARTICLES.sub(" ", s)
    s = " ".join(w[2:] if w.startswith("ال") and len(w) > 3 else w for w in s.split())  # Arabic definite article
    return s.strip()


def distance(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein edit distance, giving up early once it exceeds `cap`."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def judge(guess: str, word: dict) -> str | None:
    """'exact' when the guess names the word (in any of its languages), 'close' when it is at most two characters
    away from it, else None. Short words (three letters or fewer) only count as close one character away."""
    g = normalize(guess)
    if len(g) < 2:
        return None
    toks = g.split()
    verdict = None
    for variant in {v for v in word.values() if isinstance(v, str)}:
        w = normalize(variant)
        if len(w) < 2:
            continue
        if g == w or f" {w} " in f" {g} ":
            return "exact"
        n = len(w.split())
        windows = {" ".join(toks[i:i + n]) for i in range(max(1, len(toks) - n + 1))} | {g}
        limit = 1 if len(w) <= 3 else 2
        if any(distance(x, w) <= limit for x in windows):
            verdict = "close"
    return verdict


# ------------------------------------------------------------------ generation (compact model, with fallback)
def _langs_for(langs) -> list[str]:
    out = sorted({l for l in langs if l in LANGS} | {"en"}, key=LANGS.index)
    return out


def _words_messages(game: str, n: int, langs: list[str], seed: int, theme: str) -> list[dict]:
    what = ("short things or actions that can be ACTED OUT silently (mime), 1 to 4 words each"
            if game == "mime" else "simple objects or scenes that are easy to DRAW in 30 seconds, 1 to 3 words each")
    names = ", ".join(f'"{l}" = {prompts.LANGUAGES[l].split(",")[0]}' for l in langs)
    return [
        {"role": "system", "content": (
            "TASK: game_words\nYou write prompts for a friendly party game played by students on a short study break. "
            f"Give {n} different {what}. Everyday, school-safe, no brands, no people's names, nothing offensive. "
            f"Give each one in these languages: {names}. Return only JSON: "
            '{"words": [{' + ", ".join(f'"{l}": "..."' for l in langs) + "}]}")},
        {"role": "user", "content": f"Variety seed {seed}. Lean towards this theme for a few of them: {theme}."},
    ]


def _trivia_messages(n: int, langs: list[str], seed: int, theme: str) -> list[dict]:
    names = ", ".join(f'"{l}" = {prompts.LANGUAGES[l].split(",")[0]}' for l in langs)
    lang_obj = "{" + ", ".join(f'"{l}": "..."' for l in langs) + "}"
    return [
        {"role": "system", "content": (
            f"TASK: trivia\nWrite {n} short general-knowledge multiple-choice questions for students (high school to "
            "first-year university level), mixing science, maths, geography, language and technology. Each has exactly "
            "4 options and ONE correct answer; facts must be correct and unambiguous. Keep questions under 15 words and "
            f"options under 5 words. Give every question and option in these languages: {names}. Return only JSON: "
            '{"questions": [{"q": ' + lang_obj + ', "options": [' + lang_obj + ", ...4], " + '"answer": 0}]}')},
        {"role": "user", "content": f"Variety seed {seed}. Include a couple of questions about: {theme}."},
    ]


THEMES = ["the human body", "space", "animals", "everyday technology", "the sea", "music", "sports", "food",
          "numbers and shapes", "weather", "inventions", "geography of Africa and the Mediterranean", "plants", "cities"]


def _validate_words(langs: list[str], need: int):
    def check(obj):
        items = obj["words"] if isinstance(obj, dict) else obj
        out, seen = [], set()
        for it in items:
            if not isinstance(it, dict):
                continue
            row = {l: " ".join(str(it.get(l, "")).split())[:48] for l in langs}
            if not all(row.values()):
                continue
            key = normalize(row["en"])
            if key and key not in seen:
                seen.add(key)
                out.append(row)
        if len(out) < need:
            raise ValueError("too few words")
        return out
    return check


def _validate_trivia(langs: list[str], need: int):
    def check(obj):
        items = obj["questions"] if isinstance(obj, dict) else obj
        out = []
        for it in items:
            opts = it.get("options") or it.get("o")
            ans = it.get("answer", it.get("a"))
            if not isinstance(it.get("q"), dict) or not isinstance(opts, list) or len(opts) != 4 or not isinstance(ans, int) or not 0 <= ans <= 3:
                continue
            q = {l: " ".join(str(it["q"].get(l, "")).split())[:160] for l in langs}
            o = [{l: " ".join(str(op.get(l, "")).split())[:60] for l in langs} for op in opts if isinstance(op, dict)]
            if len(o) != 4 or not all(q.values()) or not all(all(x.values()) for x in o):
                continue
            if len({normalize(x["en"]) for x in o}) < 4:          # four distinct options
                continue
            out.append({"q": q, "o": o, "a": ans})
        if len(out) < need:
            raise ValueError("too few questions")
        return out
    return check


async def _ask(app, task: str, messages, validate, max_tokens: int):
    gw = getattr(app.state, "gateway", None)
    if gw is None:
        return None
    try:
        result, _ = await asyncio.wait_for(
            gw.chat_json(task, "compact", messages, validate, max_tokens=max_tokens, temperature=0.9, cache=False), AI_TIMEOUT_S)
        return result
    except Exception as exc:   # timeout, quota, service down: the built-in bank takes over
        log.info("break-game %s fell back to the built-in bank: %s", task, type(exc).__name__)
        return None


async def make_words(app, game: str, n: int, langs) -> tuple[list[dict], str]:
    langs = _langs_for(langs)
    seed = random.randrange(10_000)
    got = await _ask(app, "game_words", _words_messages(game, n, langs, seed, random.choice(THEMES)),
                     _validate_words(langs, min(n, 4)), max_tokens=90 + 30 * n * len(langs))
    if got:
        random.shuffle(got)
        return got[:n], "ai"
    bank = WORDS_FALLBACK[game]
    return random.sample(bank, min(n, len(bank))), "bank"


async def make_trivia(app, n: int, langs) -> tuple[list[dict], str]:
    langs = _langs_for(langs)
    seed = random.randrange(10_000)
    got = await _ask(app, "trivia", _trivia_messages(n, langs, seed, random.choice(THEMES)),
                     _validate_trivia(langs, min(n, 3)), max_tokens=min(1800, 120 + 110 * n * len(langs)))
    if got:
        return got[:n], "ai"
    picks = random.sample(TRIVIA_FALLBACK, min(n, len(TRIVIA_FALLBACK)))
    out = []
    for x in picks:            # shuffle the options too, so a repeated question is not memorised by position
        order = list(range(4))
        random.shuffle(order)
        out.append({"q": x["q"], "o": [x["o"][i] for i in order], "a": order.index(x["a"])})
    return out, "bank"


async def make_content(app, langs, n_trivia: int, n_words: int) -> dict:
    """Everything one break needs, generated in parallel: trivia + a word list per turn game."""
    trivia, mime, pic = await asyncio.gather(make_trivia(app, n_trivia, langs),
                                             make_words(app, "mime", n_words, langs),
                                             make_words(app, "pictionary", n_words, langs))
    return {"trivia": trivia[0], "words": {"mime": mime[0], "pictionary": pic[0]},
            "source": {"trivia": trivia[1], "mime": mime[1], "pictionary": pic[1]}}


def debug_dump(content: dict) -> str:   # handy in a shell; not used by the app
    return json.dumps(content, ensure_ascii=False, indent=1)

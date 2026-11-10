"""Server-side texts in English, French, Arabic and Tunisian Arabic (Derja).

The English sentence is the key. `tr(lang, "English text", n=3)` returns the translation (placeholders in
{braces}) or the English text when none exists, so a missing entry degrades to English instead of failing.
tests/test_i18n.py checks that every `tr(...)` literal in the code has all three translations.

Texts that the AI writes (explanations, hints, quiz, answers) are not here: the language is a rule in the prompt.
"""
from __future__ import annotations

LANGS = ("en", "fr", "ar", "darija")


def norm(lang: str | None) -> str:
    return lang if lang in LANGS else "en"


def lang_of(request) -> str:
    """Language of a REST request (X-Lang header) or WebSocket (?lang=)."""
    return norm(request.headers.get("x-lang") or request.query_params.get("lang"))


def tr(lang: str | None, text: str, **vars) -> str:
    out = CATALOG.get(norm(lang), {}).get(text, text)
    return out.format(**vars) if vars else out


# English -> (French, Arabic, Derja)
_T: dict[str, tuple[str, str, str]] = {
    "Sign in to continue.": ("Connectez-vous pour continuer.", "سجّل الدخول للمتابعة.", "ادخل لحسابك باش تكمّل."),
    "Pick a pseudo of 3 to 20 characters: letters, digits, _ or -. Do not use your real name.": (
        "Choisissez un pseudo de 3 à 20 caractères : lettres, chiffres, _ ou -. N'utilisez pas votre vrai nom.",
        "اختر اسمًا مستعارًا من 3 إلى 20 حرفًا: أحرف أو أرقام أو _ أو -. لا تستخدم اسمك الحقيقي.",
        "اختار اسم مستعار من 3 لـ20 حرف: حروف، أرقام، _ ولا -. ما تستعملش اسمك الحقيقي.",
    ),
    "Use a password of at least 6 characters.": ("Utilisez un mot de passe d'au moins 6 caractères.", "استخدم كلمة مرور من 6 أحرف على الأقل.", "استعمل كلمة سر فيها 6 حروف على الأقل."),
    "That pseudo is taken. Try another one.": ("Ce pseudo est déjà pris. Essayez-en un autre.", "هذا الاسم المستعار مستخدم. جرّب اسمًا آخر.", "الاسم هذا مأخوذ. جرّب واحد آخر."),
    "Pseudo or password is wrong.": ("Pseudo ou mot de passe incorrect.", "الاسم المستعار أو كلمة المرور غير صحيحة.", "الاسم المستعار ولا كلمة السر غالطين."),
    "Join the room first to use its material.": (
        "Rejoignez d'abord la salle pour utiliser son matériel.", "انضم إلى الغرفة أولًا لاستخدام موادها.", "ادخل للغرفة الأول باش تستعمل الدروس متاعها."),
    "That file is too large. The limit is 3 MB.": ("Ce fichier est trop volumineux. La limite est de 3 Mo.", "الملف كبير جدًا. الحد الأقصى 3 ميغابايت.", "الملف كبير برشا. الحد الأقصى 3 ميغا."),
    "That is too short to be useful. Add at least a few sentences.": (
        "C'est trop court pour être utile. Ajoutez au moins quelques phrases.", "النص قصير جدًا ليكون مفيدًا. أضف بضع جمل على الأقل.", "قصير برشا وما يفيدش. زيد كان جمل قلال على الأقل."),
    "This looks like it contains e-mail addresses.": ("Ce texte semble contenir des adresses e-mail.", "يبدو أن النص يحتوي على عناوين بريد إلكتروني.", "يبان فيه عناوين إيميل."),
    "This looks like it contains phone numbers or identification numbers.": (
        "Ce texte semble contenir des numéros de téléphone ou d'identification.", "يبدو أن النص يحتوي على أرقام هواتف أو أرقام تعريف.", "يبان فيه نمرو تلفون ولا أرقام تعريف."),
    "Material is sent to the AI service, so remove personal details and add it again.": (
        "Le matériel est envoyé au service d'IA : retirez les données personnelles et ajoutez-le à nouveau.",
        "تُرسل المواد إلى خدمة الذكاء الاصطناعي، لذا احذف البيانات الشخصية وأضفها مرة أخرى.",
        "الدروس تتبعث لخدمة الذكاء الاصطناعي، امسح المعلومات الشخصية وزيدها من جديد."),
    "You can keep {n} documents here. Remove one first.": (
        "Vous pouvez garder {n} documents ici. Supprimez-en un d'abord.", "يمكنك الاحتفاظ بـ {n} مستندات هنا. احذف واحدًا أولًا.", "تنجم تخلّي {n} وثائق هنا. امسح واحدة الأول."),
    "That document is too long for the AI quota ({n} passages, the limit is {max}). Split it into parts.": (
        "Ce document est trop long pour le quota d'IA ({n} passages, la limite est {max}). Découpez-le en parties.",
        "المستند طويل جدًا بالنسبة لحصة الذكاء الاصطناعي ({n} مقطعًا، والحد {max}). قسّمه إلى أجزاء.",
        "الوثيقة طويلة برشا على كوطة الذكاء الاصطناعي ({n} مقطع، والحد {max}). قسّمها لأجزاء."),
    "This library is full. Remove a document before adding another.": (
        "Cette bibliothèque est pleine. Supprimez un document avant d'en ajouter un autre.", "المكتبة ممتلئة. احذف مستندًا قبل إضافة آخر.", "المكتبة عامرة. امسح وثيقة قبل ما تزيد وحدة أخرى."),
    "That document does not exist.": ("Ce document n'existe pas.", "هذا المستند غير موجود.", "الوثيقة هذي موش موجودة."),
    "Add a document and wait until it is ready, then ask your question.": (
        "Ajoutez un document et attendez qu'il soit prêt, puis posez votre question.", "أضف مستندًا وانتظر حتى يصبح جاهزًا، ثم اطرح سؤالك.", "زيد وثيقة وارتنّى تولّي جاهزة، وبعد اسأل."),
    "This PDF could not be read. Try exporting it again, or paste the text.": (
        "Ce PDF n'a pas pu être lu. Réexportez-le ou collez le texte.", "تعذّرت قراءة ملف PDF هذا. أعد تصديره أو الصق النص.", "ما نجمتش نقرا الـPDF هذا. عاود صدّروا ولا ألصق النص."),
    "This PDF has no readable text (it may be scanned images). Paste the text instead.": (
        "Ce PDF ne contient pas de texte lisible (images scannées ?). Collez plutôt le texte.",
        "لا يحتوي ملف PDF هذا على نص مقروء (ربما صور ممسوحة). الصق النص بدلًا من ذلك.",
        "الـPDF هذا ما فيهش نص يتقرا (يمكن صور سكانير). ألصق النص بالعوض."),
    "Add a PDF, a .txt or a .md file, or paste the text.": (
        "Ajoutez un PDF, un fichier .txt ou .md, ou collez le texte.", "أضف ملف PDF أو .txt أو .md، أو الصق النص.", "زيد PDF ولا ملف .txt ولا .md، ولا ألصق النص."),
    "Type a topic of at least two characters.": ("Saisissez un sujet d'au moins deux caractères.", "اكتب موضوعًا من حرفين على الأقل.", "اكتب موضوع فيه حرفين على الأقل."),
    "Answer at least one question.": ("Répondez à au moins une question.", "أجب عن سؤال واحد على الأقل.", "جاوب على سؤال واحد على الأقل."),
    "Unknown question.": ("Question inconnue.", "سؤال غير معروف.", "سؤال موش معروف."),
    "Give the room a name of 2 to 40 characters.": ("Donnez à la salle un nom de 2 à 40 caractères.", "أعطِ الغرفة اسمًا من 2 إلى 40 حرفًا.", "عطي للغرفة اسم من 2 لـ40 حرف."),
    "A study room holds 2 to 10 people.": ("Une salle d'étude accueille de 2 à 10 personnes.", "تتسع غرفة الدراسة من 2 إلى 10 أشخاص.", "غرفة المراجعة تحمل من 2 لـ10 أشخاص."),
    "No room has that code.": ("Aucune salle n'a ce code.", "لا توجد غرفة بهذا الرمز.", "ما فمّا حتى غرفة بالكود هذا."),
    "Sign in to join a room.": ("Connectez-vous pour rejoindre une salle.", "سجّل الدخول للانضمام إلى غرفة.", "ادخل لحسابك باش تدخل لغرفة."),
    "This room is full ({n} people).": ("Cette salle est pleine ({n} personnes).", "الغرفة ممتلئة ({n} أشخاص).", "الغرفة عامرة ({n} أشخاص)."),
    "You joined this room from another tab.": ("Vous avez rejoint cette salle depuis un autre onglet.", "لقد انضممت إلى هذه الغرفة من تبويب آخر.", "دخلت للغرفة هذي من تبويب آخر."),
    "This room has no material yet. Add a document first and wait until it is ready.": (
        "Cette salle n'a pas encore de matériel. Ajoutez d'abord un document et attendez qu'il soit prêt.",
        "لا توجد مواد في هذه الغرفة بعد. أضف مستندًا أولًا وانتظر حتى يصبح جاهزًا.",
        "الغرفة هذي مازال ما فيهاش دروس. زيد وثيقة الأول وارتنّى تولّي جاهزة."),
    "The conversation is moving. Want to add a thought or ask a question? You can also post anonymously.": (
        "La discussion avance. Envie d'ajouter une idée ou de poser une question ? Vous pouvez aussi écrire anonymement.",
        "النقاش يتقدّم. هل تريد إضافة فكرة أو طرح سؤال؟ يمكنك أيضًا الكتابة دون الكشف عن هويتك.",
        "النقاش ماشي. تحب تزيد فكرة ولا تسأل سؤال؟ تنجم تكتب كذلك بلا اسم."),
    "The AI quota is used up for now.": ("Le quota d'IA est épuisé pour le moment.", "استُنفدت حصة الذكاء الاصطناعي حاليًا.", "كوطة الذكاء الاصطناعي خلصت توّا."),
    "The embeddings quota is used up for now.": ("Le quota d'embeddings est épuisé pour le moment.", "استُنفدت حصة التضمينات حاليًا.", "كوطة الـembeddings خلصت توّا."),
    "The AI service cannot be reached right now. Check the connection and try again.": (
        "Le service d'IA est injoignable pour le moment. Vérifiez la connexion et réessayez.",
        "تعذّر الوصول إلى خدمة الذكاء الاصطناعي حاليًا. تحقق من الاتصال وحاول مرة أخرى.",
        "ما نجمش نوصل لخدمة الذكاء الاصطناعي توّا. شوف الكونيكسيون وعاود."),
    "The AI service is saturated. Try again in a moment.": ("Le service d'IA est saturé. Réessayez dans un instant.", "خدمة الذكاء الاصطناعي مزدحمة. حاول بعد قليل.", "خدمة الذكاء الاصطناعي مشغولة برشا. عاود بعد شويّة."),
    "The AI service could not handle this request. Try again later.": (
        "Le service d'IA n'a pas pu traiter cette demande. Réessayez plus tard.", "تعذّر على خدمة الذكاء الاصطناعي معالجة هذا الطلب. حاول لاحقًا.", "خدمة الذكاء الاصطناعي ما نجمتش تعالج الطلب هذا. عاود بعد."),
    "The model's answer could not be read. Rephrase the topic and try again.": (
        "La réponse du modèle n'a pas pu être lue. Reformulez le sujet et réessayez.", "تعذّرت قراءة إجابة النموذج. أعد صياغة الموضوع وحاول مرة أخرى.", "ما نجمتش نقرا جواب الموديل. بدّل صياغة الموضوع وعاود."),
    "The quiz could not be generated. Try again.": ("Le quiz n'a pas pu être généré. Réessayez.", "تعذّر إنشاء الاختبار. حاول مرة أخرى.", "ما نجمتش نعمل الكويز. عاود."),
    "Study a few topics first, then generate a revision sheet.": (
        "Étudie d'abord quelques sujets, puis génère une fiche de révision.", "ادرس بعض المواضيع أولاً ثم أنشئ ورقة مراجعة.", "اقرا شوية مواضيع الأول، من بعد اعمل ورقة مراجعة."),
    # diagnostic
    "A new formula appears in class. What helps you most first?": (
        "Une nouvelle formule apparaît en cours. Qu'est-ce qui vous aide le plus d'abord ?", "ظهرت صيغة جديدة في الحصة. ما الذي يساعدك أكثر في البداية؟", "طلعت formule جديدة في الكلاس. شنوة اللي يعاونك أكثر في الأول؟"),
    "A picture or graph that shows it": ("Une image ou un graphique qui la montre", "صورة أو رسم بياني يوضّحها", "صورة ولا رسم بياني يوريها"),
    "A worked example, step by step": ("Un exemple résolu, pas à pas", "مثال محلول خطوة بخطوة", "مثال محلول خطوة بخطوة"),
    "A comparison with something I already know": ("Une comparaison avec quelque chose que je connais déjà", "مقارنة بشيء أعرفه مسبقًا", "مقارنة بحاجة نعرفها من قبل"),
    "A problem to try right away": ("Un problème à essayer tout de suite", "مسألة أجرّبها فورًا", "مسألة نجرّبها في الحين"),
    "You are stuck on a problem. What do you usually do?": ("Vous êtes bloqué sur un problème. Que faites-vous d'habitude ?", "علقتَ في مسألة. ماذا تفعل عادةً؟", "تعطّلت في مسألة. شنوة تعمل عادةً؟"),
    "Sketch it or look at a diagram": ("Je fais un schéma ou je regarde un diagramme", "أرسم شكلًا أو أنظر إلى مخطط", "نرسم رسم ولا نشوف مخطط"),
    "Follow a solution line by line": ("Je suis une solution ligne par ligne", "أتتبّع الحل سطرًا سطرًا", "نتبع الحل سطر سطر"),
    "Ask for an everyday comparison": ("Je demande une comparaison de la vie quotidienne", "أطلب مقارنة من الحياة اليومية", "نطلب مقارنة من الحياة اليومية"),
    "Re-read the rule until it sticks": ("Je relis la règle jusqu'à ce qu'elle reste", "أعيد قراءة القاعدة حتى تثبت", "نعاود نقرا القاعدة حتى تتحفظ"),
    "How do you prefer to revise?": ("Comment préférez-vous réviser ?", "كيف تفضّل أن تراجع؟", "كيفاش تحب تراجع؟"),
    "Mind maps and colour": ("Cartes mentales et couleurs", "خرائط ذهنية وألوان", "خرائط ذهنية وألوان"),
    "A numbered checklist": ("Une liste numérotée", "قائمة مرقّمة", "قائمة مرقّمة"),
    "Stories and examples": ("Des histoires et des exemples", "قصص وأمثلة", "قصص وأمثلة"),
    "Flashcards and quizzes": ("Des cartes mémoire et des quiz", "بطاقات واختبارات قصيرة", "بطاقات وكويزات"),
    "Small exercise: 3 pencils cost 6 dinars. How would you find the price of 5?": (
        "Petit exercice : 3 crayons coûtent 6 dinars. Comment trouveriez-vous le prix de 5 ?", "تمرين صغير: ثلاثة أقلام بـ6 دنانير. كيف تجد سعر خمسة؟", "تمرين صغير: 3 أقلام بـ6 دنانير. كيفاش تلقى سعر 5؟"),
    "Draw 3 groups of 2, then extend to 5": ("Dessiner 3 groupes de 2, puis aller jusqu'à 5", "أرسم 3 مجموعات من 2 ثم أمدّها إلى 5", "نرسم 3 مجموعات من 2 وبعد نوصل لـ5"),
    "Price of one is 2, then multiply by 5": ("Le prix d'un est 2, puis multiplier par 5", "سعر القلم الواحد 2 ثم أضرب في 5", "سعر الواحد 2 وبعد نضرب في 5"),
    "Like buying coffees: same price each": ("Comme acheter des cafés : même prix chacun", "مثل شراء القهوة: السعر نفسه لكل واحدة", "كيما نشري قهاوي: نفس السعر لكل وحدة"),
    "Try numbers until it fits": ("Essayer des nombres jusqu'à trouver", "أجرّب أرقامًا حتى يصح الحل", "نجرّب أرقام حتى يضبط"),
    "You are going a little fast. Try again in {n} s.": ("Vous allez un peu vite. Réessayez dans {n} s.", "أنت سريع قليلًا. حاول مرة أخرى بعد {n} ث.", "تمشي شوية بسرعة. عاود جرّب بعد {n} ث."),
    "Too many sign-in attempts. Try again in {n} s.": ("Trop de tentatives de connexion. Réessayez dans {n} s.", "محاولات دخول كثيرة. حاول مرة أخرى بعد {n} ث.", "برشا محاولات دخول. عاود جرّب بعد {n} ث."),
    "Too many accounts created from this connection. Try again later.": ("Trop de comptes créés depuis cette connexion. Réessayez plus tard.", "أُنشئت حسابات كثيرة من هذا الاتصال. حاول لاحقًا.", "تعمل برشا حسابات من الاتصال هذا. عاود جرّب بعد شوية."),
    "That request is too large.": ("Cette requête est trop volumineuse.", "هذا الطلب كبير جدًا.", "الطلب هذا كبير برشا."),
    "Something went wrong on our side. Try again in a moment.": ("Un problème est survenu de notre côté. Réessayez dans un instant.", "حدث خطأ من جانبنا. حاول مرة أخرى بعد قليل.", "صار مشكل من عندنا. عاود جرّب بعد شوية."),
    "That image could not be read. Use a PNG, JPG, WebP or GIF.": ("Cette image n'a pas pu être lue. Utilisez un PNG, JPG, WebP ou GIF.", "تعذّر قراءة هذه الصورة. استخدم PNG أو JPG أو WebP أو GIF.", "ما نجّمناش نقراو الصورة هذي. استعمل PNG ولا JPG ولا WebP ولا GIF."),
    "You can keep {n} images here. Remove one first.": ("Vous pouvez garder {n} images ici. Supprimez-en une d'abord.", "يمكنك الاحتفاظ بـ{n} صور هنا. احذف واحدة أولًا.", "تنجم تخلّي {n} صور هوني. امسح وحدة قبل."),
    "Live focus rooms are not saved as conversations.": ("Les salles de concentration en direct ne sont pas enregistrées comme conversations.", "لا تُحفظ غرف التركيز المباشرة كمحادثات.", "غرف التركيز المباشرة ما تتحفظش كمحادثات."),
    "Unknown grade.": ("Note inconnue.", "تقييم غير معروف.", "تقييم ما معروفش."),
    "That card does not exist.": ("Cette carte n'existe pas.", "هذه البطاقة غير موجودة.", "البطاقة هذي ما موجودةش."),
    "This pocket-tree link is invalid or has expired.": (
        "Ce lien d'arbre de poche est invalide ou a expiré.", "رابط الشجرة المحمولة غير صالح أو انتهت صلاحيته.", "الرابط متاع الشجرة ما يخدمش أو خلص وقتو."),
}

CATALOG: dict[str, dict[str, str]] = {
    "fr": {k: v[0] for k, v in _T.items()},
    "ar": {k: v[1] for k, v in _T.items()},
    "darija": {k: v[2] for k, v in _T.items()},
}

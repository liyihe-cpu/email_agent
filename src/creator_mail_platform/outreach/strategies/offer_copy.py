from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib


@dataclass(frozen=True)
class OfferCopy:
    greeting: str
    courtesy: str
    team_intro: str
    matched_intro: str
    fallback_intro: str
    featured_many: str
    featured_one: str
    other_heading: str
    product_label: str
    deliverable_label: str
    compensation_label: str
    timeline_label: str
    usage_rights_label: str
    reply_cta: str
    creator_pass_note: str
    closing: str


ENGLISH_OFFER_COPY = OfferCopy(
    greeting="Hi {handle},",
    courtesy="I hope you're doing well.",
    team_intro="We're the Creator Partnerships team at COOJOY.",
    matched_intro=(
        "Based on your content, one paid opportunity stood out as a particularly "
        "good match. We've also included a flexible creator-camera campaign you "
        "may want to consider."
    ),
    fallback_intro=(
        "We'd like to share a flexible paid creator-camera opportunity that may "
        "work well across a range of content styles."
    ),
    featured_many="Featured opportunities selected for you:",
    featured_one="Featured opportunity:",
    other_heading="Other opportunities currently available:",
    product_label="Product",
    deliverable_label="Deliverable",
    compensation_label="Compensation",
    timeline_label="Timeline",
    usage_rights_label="Usage rights",
    reply_cta="Interested? Just reply \"Yes\" and we'll send the full brief.",
    creator_pass_note=(
        "P.S. Complete your personalized Creator Pass to improve future campaign "
        "matching and unlock up to US$30 in additional benefits with your first "
        "eligible COOJOY campaign payout:\n{url}"
    ),
    closing="We'd love to hear what interests you!",
)


ENGLISH_MATCHED_INTROS = (
    (
        "One paid opportunity looks especially relevant to your content, and we've "
        "also included a flexible creator-camera campaign for you to consider."
    ),
    (
        "We found a paid campaign that could be a strong match for your content. "
        "We've also added a flexible creator-camera opportunity as another option."
    ),
    (
        "A current paid brief stood out as a good match for your content, alongside "
        "a flexible creator-camera campaign you may also like."
    ),
    (
        "We picked out one paid campaign that feels relevant to your content and "
        "included a creator-camera opportunity as a second option."
    ),
)

ENGLISH_FALLBACK_INTROS = (
    "We'd like to share a flexible paid creator-camera opportunity that could suit your content style.",
    "We have a paid creator-camera campaign that may work well with the kind of content you create.",
    "A flexible paid camera collaboration is currently available, and we'd love to see if it interests you.",
)

ENGLISH_REPLY_CTAS = (
    "Interested? Just reply \"Yes\" and we'll send the full brief.",
    "Want the full brief? Reply \"Yes\" and we'll send it over.",
    "If you'd like the details, simply reply \"Yes\".",
    "Interested in learning more? A quick \"Yes\" reply is all we need.",
)

ENGLISH_CLOSINGS = (
    "We'd love to hear what interests you!",
    "Looking forward to hearing what you think!",
    "Hope we can find the right collaboration for you!",
)


def select_english_offer_copy(creator_key: str) -> OfferCopy:
    """Select small, stable copy variations without changing project facts."""
    digest = hashlib.sha256(creator_key.encode("utf-8")).digest()
    return replace(
        ENGLISH_OFFER_COPY,
        matched_intro=ENGLISH_MATCHED_INTROS[digest[0] % len(ENGLISH_MATCHED_INTROS)],
        fallback_intro=ENGLISH_FALLBACK_INTROS[digest[1] % len(ENGLISH_FALLBACK_INTROS)],
        reply_cta=ENGLISH_REPLY_CTAS[digest[2] % len(ENGLISH_REPLY_CTAS)],
        closing=ENGLISH_CLOSINGS[digest[3] % len(ENGLISH_CLOSINGS)],
    )


OFFER_LOCALIZED_COPIES: dict[str, OfferCopy] = {
    "es": OfferCopy(
        greeting="Hola {handle},",
        courtesy="Espero que estés muy bien.",
        team_intro="Somos el equipo de Creator Partnerships de COOJOY.",
        matched_intro=(
            "Por tu contenido, encontramos una oportunidad pagada que creemos que "
            "encaja especialmente bien contigo. También añadimos una campaña flexible "
            "de cámara para creadores que podría interesarte."
        ),
        fallback_intro=(
            "Nos gustaría compartir contigo una campaña pagada y flexible de cámara "
            "para creadores, pensada para distintos estilos de contenido."
        ),
        featured_many="Oportunidades destacadas para ti:",
        featured_one="Oportunidad destacada:",
        other_heading="Otras oportunidades disponibles:",
        product_label="Producto",
        deliverable_label="Contenido solicitado",
        compensation_label="Compensación",
        timeline_label="Plazo",
        usage_rights_label="Derechos de uso",
        reply_cta="¿Te interesa? Responde simplemente «Sí» y te enviaremos el brief completo.",
        creator_pass_note=(
            "P. D. Completa tu Creator Pass personalizado para recibir campañas mejor "
            "adaptadas y desbloquear hasta US$30 en beneficios adicionales con el primer "
            "pago elegible de una campaña COOJOY:\n{url}"
        ),
        closing="¡Nos encantará saber qué oportunidades te interesan!",
    ),
    "pt": OfferCopy(
        greeting="Olá {handle},",
        courtesy="Tudo bem?",
        team_intro="Somos o time de Creator Partnerships da COOJOY.",
        matched_intro=(
            "Pelo seu conteúdo, encontramos uma oportunidade paga que parece combinar "
            "especialmente bem com você. Também incluímos uma campanha flexível de "
            "câmera para creators que pode ser interessante."
        ),
        fallback_intro=(
            "Queremos compartilhar uma campanha paga e flexível de câmera para creators, "
            "que pode funcionar com diferentes estilos de conteúdo."
        ),
        featured_many="Oportunidades em destaque para você:",
        featured_one="Oportunidade em destaque:",
        other_heading="Outras oportunidades disponíveis:",
        product_label="Produto",
        deliverable_label="Entrega",
        compensation_label="Cachê",
        timeline_label="Prazo",
        usage_rights_label="Direitos de uso",
        reply_cta="Tem interesse? Basta responder «Sim» e enviaremos o briefing completo.",
        creator_pass_note=(
            "P.S. Complete seu Creator Pass personalizado para receber campanhas mais "
            "alinhadas e desbloquear até US$30 em benefícios adicionais no primeiro "
            "pagamento elegível de uma campanha COOJOY:\n{url}"
        ),
        closing="Vamos adorar saber quais oportunidades interessam a você!",
    ),
    "fr": OfferCopy(
        greeting="Bonjour {handle},",
        courtesy="J'espère que vous allez bien.",
        team_intro="Nous sommes l'équipe Creator Partnerships de COOJOY.",
        matched_intro=(
            "Au vu de votre contenu, une opportunité rémunérée nous a semblé "
            "particulièrement adaptée. Nous avons aussi ajouté une campagne flexible "
            "autour d'une caméra pour créateurs qui pourrait vous intéresser."
        ),
        fallback_intro=(
            "Nous souhaitons vous présenter une campagne rémunérée et flexible autour "
            "d'une caméra pour créateurs, adaptée à différents styles de contenu."
        ),
        featured_many="Opportunités sélectionnées pour vous :",
        featured_one="Opportunité à découvrir :",
        other_heading="Autres opportunités disponibles :",
        product_label="Produit",
        deliverable_label="Contenu demandé",
        compensation_label="Rémunération",
        timeline_label="Délai",
        usage_rights_label="Droits d'utilisation",
        reply_cta="Intéressé(e) ? Répondez simplement « Oui » et nous vous enverrons le brief complet.",
        creator_pass_note=(
            "P.-S. Complétez votre Creator Pass personnalisé pour recevoir des campagnes "
            "plus adaptées et débloquer jusqu'à US$30 d'avantages supplémentaires lors "
            "du premier paiement éligible d'une campagne COOJOY :\n{url}"
        ),
        closing="Nous serions ravis de savoir quelles opportunités vous intéressent !",
    ),
    "de": OfferCopy(
        greeting="Hallo {handle},",
        courtesy="Ich hoffe, dir geht es gut.",
        team_intro="Wir sind das Creator-Partnerships-Team von COOJOY.",
        matched_intro=(
            "Auf Basis deines Contents ist uns eine bezahlte Kooperation aufgefallen, "
            "die besonders gut zu dir passen könnte. Zusätzlich haben wir eine flexible "
            "Creator-Kamera-Kampagne aufgenommen."
        ),
        fallback_intro=(
            "Wir möchten dir eine flexible bezahlte Creator-Kamera-Kampagne vorstellen, "
            "die zu unterschiedlichen Content-Stilen passen kann."
        ),
        featured_many="Ausgewählte Möglichkeiten für dich:",
        featured_one="Ausgewählte Möglichkeit:",
        other_heading="Weitere aktuell verfügbare Projekte:",
        product_label="Produkt",
        deliverable_label="Leistung",
        compensation_label="Vergütung",
        timeline_label="Zeitrahmen",
        usage_rights_label="Nutzungsrechte",
        reply_cta="Interessiert? Antworte einfach mit „Ja“, dann senden wir dir das vollständige Briefing.",
        creator_pass_note=(
            "P.S. Fülle deinen persönlichen Creator Pass aus, um passendere Kampagnen "
            "zu erhalten und bis zu US$30 an zusätzlichen Vorteilen bei deiner ersten "
            "berechtigten COOJOY-Kampagnenauszahlung freizuschalten:\n{url}"
        ),
        closing="Wir freuen uns darauf zu erfahren, welche Projekte dich interessieren!",
    ),
    "ko": OfferCopy(
        greeting="안녕하세요 {handle}님,",
        courtesy="잘 지내고 계신가요?",
        team_intro="저희는 COOJOY Creator Partnerships 팀입니다.",
        matched_intro=(
            "콘텐츠를 바탕으로 특히 잘 맞을 것 같은 유료 프로젝트 한 가지를 "
            "선정했습니다. 다양한 콘텐츠 스타일에 활용할 수 있는 크리에이터용 "
            "카메라 캠페인도 함께 안내드려요."
        ),
        fallback_intro=(
            "다양한 콘텐츠 스타일에 활용할 수 있는 유연한 크리에이터용 카메라 "
            "유료 캠페인을 소개해 드리고 싶어요."
        ),
        featured_many="추천드리는 주요 프로젝트:",
        featured_one="추천 프로젝트:",
        other_heading="현재 참여 가능한 다른 프로젝트:",
        product_label="제품",
        deliverable_label="제작 콘텐츠",
        compensation_label="보상",
        timeline_label="일정",
        usage_rights_label="사용 권한",
        reply_cta="관심 있으시면 “네”라고 답장해 주세요. 전체 브리프를 보내드릴게요.",
        creator_pass_note=(
            "추신: 맞춤형 Creator Pass를 작성하면 앞으로 더 잘 맞는 캠페인을 받고, "
            "첫 COOJOY 적격 캠페인 정산에서 최대 US$30의 추가 혜택을 받을 수 있어요:\n{url}"
        ),
        closing="어떤 프로젝트에 관심이 있으신지 편하게 알려 주세요!",
    ),
    "ja": OfferCopy(
        greeting="{handle}さん、こんにちは。",
        courtesy="お元気ですか？",
        team_intro="私たちはCOOJOYのCreator Partnershipsチームです。",
        matched_intro=(
            "コンテンツを拝見し、特に相性がよさそうな有償案件を1件選びました。"
            "あわせて、幅広いスタイルで取り組みやすいクリエイター向けカメラ案件も"
            "ご紹介します。"
        ),
        fallback_intro=(
            "さまざまなコンテンツスタイルで取り組みやすい、クリエイター向けカメラの"
            "有償キャンペーンをご紹介します。"
        ),
        featured_many="おすすめの案件：",
        featured_one="おすすめの案件：",
        other_heading="現在ご案内できるその他の案件：",
        product_label="製品",
        deliverable_label="制作内容",
        compensation_label="報酬",
        timeline_label="スケジュール",
        usage_rights_label="使用権",
        reply_cta="ご興味があれば「はい」とだけ返信してください。詳細なブリーフをお送りします。",
        creator_pass_note=(
            "追伸：あなた専用のCreator Passを入力すると、今後より相性のよい案件を"
            "受け取り、最初の対象COOJOYキャンペーン報酬で最大US$30の追加特典を"
            "受けられます：\n{url}"
        ),
        closing="気になる案件をぜひ気軽にお知らせください！",
    ),
    "ru": OfferCopy(
        greeting="Здравствуйте, {handle}!",
        courtesy="Надеемся, у вас всё хорошо.",
        team_intro="Мы — команда Creator Partnerships в COOJOY.",
        matched_intro=(
            "Судя по вашему контенту, одна платная кампания особенно хорошо вам "
            "подходит. Мы также добавили гибкий проект с камерой для авторов, который "
            "может вас заинтересовать."
        ),
        fallback_intro=(
            "Хотим предложить вам гибкую платную кампанию с камерой для авторов, "
            "которая подходит для разных форматов контента."
        ),
        featured_many="Подобранные для вас проекты:",
        featured_one="Рекомендуемый проект:",
        other_heading="Другие доступные проекты:",
        product_label="Продукт",
        deliverable_label="Формат",
        compensation_label="Оплата",
        timeline_label="Сроки",
        usage_rights_label="Права использования",
        reply_cta="Заинтересованы? Просто ответьте «Да», и мы отправим полный бриф.",
        creator_pass_note=(
            "P.S. Заполните персональный Creator Pass, чтобы получать более подходящие "
            "кампании и разблокировать до US$30 дополнительных преимуществ при первой "
            "подходящей выплате за кампанию COOJOY:\n{url}"
        ),
        closing="Будем рады узнать, какие проекты вам интересны!",
    ),
    "ar": OfferCopy(
        greeting="مرحبًا {handle}،",
        courtesy="نتمنى أن تكون بخير.",
        team_intro="نحن فريق شراكات صنّاع المحتوى في COOJOY.",
        matched_intro=(
            "بناءً على محتواك، وجدنا فرصة مدفوعة نعتقد أنها مناسبة لك بشكل خاص. وأضفنا "
            "أيضًا حملة مرنة لكاميرا مخصصة لصنّاع المحتوى قد تهمك."
        ),
        fallback_intro=(
            "نود أن نشارك معك حملة مدفوعة ومرنة لكاميرا مخصصة لصنّاع المحتوى، ويمكن أن "
            "تناسب أنماطًا مختلفة من المحتوى."
        ),
        featured_many="الفرص المختارة لك:",
        featured_one="الفرصة المقترحة:",
        other_heading="فرص أخرى متاحة حاليًا:",
        product_label="المنتج",
        deliverable_label="المحتوى المطلوب",
        compensation_label="المقابل",
        timeline_label="الجدول الزمني",
        usage_rights_label="حقوق الاستخدام",
        reply_cta="مهتم؟ ما عليك سوى الرد بكلمة «نعم» وسنرسل لك الملخص الكامل.",
        creator_pass_note=(
            "ملاحظة: أكمل Creator Pass المخصص لك للحصول على حملات أنسب والاستفادة من "
            "مزايا إضافية تصل إلى US$30 مع أول دفعة مؤهلة من حملة COOJOY:\n{url}"
        ),
        closing="يسعدنا أن نعرف أي الفرص تهمك!",
    ),
}

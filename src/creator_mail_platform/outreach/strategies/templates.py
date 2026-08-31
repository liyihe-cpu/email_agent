from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .localization import resolve_language_code


FALLBACK_OPENINGS = (
    "We really like the creativity and personality you bring to your {topic} content.",
    "Your {topic} content caught our eye for its clear and engaging style.",
    "Love how polished and approachable your {topic} content feels.",
)


@dataclass(frozen=True)
class CreatorPersonalization:
    content_focus: str
    primary_opening: str
    english_opening: str

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, object],
        *,
        fallback_topic: str,
        use_english_as_primary: bool,
    ) -> "CreatorPersonalization":
        topic = _clean_ai_field(payload.get("content_focus"), max_chars=80)
        topic = topic or _clean_ai_field(fallback_topic, max_chars=80) or "creator content"
        fallback_index = hashlib.sha256(topic.lower().encode("utf-8")).digest()[0]
        fallback_opening = FALLBACK_OPENINGS[
            fallback_index % len(FALLBACK_OPENINGS)
        ].format(topic=topic)
        english_opening = (
            _clean_ai_field(payload.get("english_opening"), max_chars=300)
            or fallback_opening
        )
        primary_opening = (
            english_opening
            if use_english_as_primary
            else _clean_ai_field(payload.get("primary_opening"), max_chars=300)
            or english_opening
        )
        return cls(
            content_focus=topic,
            primary_opening=primary_opening,
            english_opening=english_opening,
        )


def _clean_ai_field(value: object, *, max_chars: int) -> str:
    return " ".join(str(value or "").split())[:max_chars].strip()


@dataclass(frozen=True)
class LocalizedCopy:
    language_name: str
    subject: str
    greeting: str
    courtesy: str
    team_intro: str
    discovery: str
    invitation: str
    network_intro: str
    credibility: str
    benefit: str
    cta: str
    note: str
    closing: str


@dataclass(frozen=True)
class CreatorPassVariant:
    subjects: tuple[str, str]
    body: str


ENGLISH = LocalizedCopy(
    language_name="English",
    subject="{handle}, access global paid brand opportunities with COOJOY",
    greeting="Hi {handle},",
    courtesy="I hope you are doing well.",
    team_intro="We are the marketing team at COOJOY, a global creator marketing agency connecting brands with leading creators.",
    discovery="While reviewing creator profiles, we came across your work.",
    invitation="That's why we'd like to invite you to join our Creator Pass program.",
    network_intro="COOJOY is expanding its Global Creator Network and inviting selected creators to access better-matched paid campaigns from international brands.",
    credibility="We work with over 8,000 creators worldwide and partner with internationally recognized brands including SHEGLAM, DJI, and AliExpress.",
    benefit="You can complete your Creator Pass to receive better campaign matching and unlock up to US$30 in additional benefits, added to your first eligible payout after completing your first paid COOJOY campaign.",
    cta="Activate your personalized Creator Pass:",
    note="No login required. This invitation is personalized for you; please do not forward it.",
    closing="Looking forward to your reply!",
)


CREATOR_PASS_VARIANTS: tuple[CreatorPassVariant, ...] = (
    CreatorPassVariant(
        subjects=(
            "A Quick Creator Invitation for {handle} ✨",
            "{handle} × Brand Opportunities with COOJOY",
        ),
        body="""Hi {handle},

{personalized_opening}

My name is Ferd from COOJOY (coojoy.cn). We connect creators worldwide with paid campaigns from international brands, including DJI, SHEGLAM, and AliExpress.

We’re preparing upcoming brand campaigns and would love to learn which collaboration categories interest you, so we can consider you for more relevant opportunities.

You can complete your Creator Pass in about 30 seconds. You may also unlock up to US$30 in Creator Pass bonuses, added to your first eligible payout after completing your first paid COOJOY campaign.

👉 Complete your Creator Pass:
{form_url}

Cheers,
Ferd | COOJOY Creator Partnerships
https://www.coojoy.cn""",
    ),
    CreatorPassVariant(
        subjects=(
            "Paid Brand Opportunities for {handle} 👀",
            "{handle} — Interested in Upcoming Paid Campaigns?",
        ),
        body="""Hi {handle},

{personalized_opening}

We’re expanding COOJOY’s global creator network and looking for creators who could be a great fit for upcoming paid campaigns with international brands.

We’ve created a personalized Creator Pass where you can tell us which collaboration categories you’re most interested in, helping us send you more relevant campaign opportunities.

You can also unlock up to US$30 in Creator Pass bonuses, added to your first eligible payout after completing your first paid COOJOY campaign.

🔗 Set up your Creator Pass:
{form_url}

Best,
COOJOY Creator Partnerships
https://www.coojoy.cn""",
    ),
    CreatorPassVariant(
        subjects=(
            "{handle}, a Quick Brand Invitation for You",
            "A Personal Creator Pass Invitation from COOJOY",
        ),
        body="""Hi {handle},

{personalized_opening}

That’s why we’d love to personally invite you to join COOJOY’s active creator network.

To help us recommend collaborations that better match your content and preferred categories, take a moment to complete your personalized Creator Pass.

✨ View your Creator Pass:
{form_url}

It takes less than a minute. You may also unlock up to US$30 in Creator Pass bonuses, added to your first eligible payout after completing your first paid COOJOY campaign.

We’d be glad to explore future opportunities together.

Warmly,
COOJOY Creator Partnerships
https://www.coojoy.cn""",
    ),
    CreatorPassVariant(
        subjects=(
            "Brand Collaborations That Fit Your Content, {handle}",
            "A Quick Creator Partnership Invitation from COOJOY",
        ),
        body="""Hi {handle},

{personalized_opening}

At COOJOY, we support paid creator campaigns across beauty, fashion, technology, gaming, lifestyle, parenting, outdoor content, and more.

We’re currently looking for creators with distinctive content to recommend for upcoming international brand campaigns. Your Creator Pass lets you choose the collaboration categories that interest you, helping us avoid sending opportunities that aren’t relevant to your content.

👉 Set your collaboration preferences:
{form_url}

You can also unlock up to US$30 in Creator Pass bonuses, added to your first eligible payout after completing your first paid COOJOY campaign.

Best,
COOJOY Partnerships Team
https://www.coojoy.cn""",
    ),
    CreatorPassVariant(
        subjects=(
            "Set Your Campaign Preferences, {handle}",
            "{handle}, a Quick Setup for Paid Collaborations",
        ),
        body="""Hi {handle},

{personalized_opening}

We’re preparing upcoming brand campaigns at COOJOY and would love to consider your channel for relevant paid collaborations.

We’ve created a personalized Creator Pass where you can:

• Choose the collaboration categories that interest you
• Receive better-matched paid campaign invitations
• Unlock up to US$30 in Creator Pass bonuses with your first eligible campaign payout

🚀 Complete your Creator Pass:
{form_url}

It takes less than a minute. Creator Pass bonuses are added after you complete your first paid COOJOY campaign. We hope to find a great opportunity to work together.

Best,
COOJOY Creator Partnerships
https://www.coojoy.cn""",
    ),
)


LOCALIZED_COPIES: dict[str, LocalizedCopy] = {
    "en": ENGLISH,
    "es": LocalizedCopy(
        "Spanish",
        "{handle}, accede a oportunidades globales con marcas a través de COOJOY",
        "Hola {handle},",
        "Espero que estés muy bien.",
        "Somos el equipo de Creator Partnerships de COOJOY. Ayudamos a creadores de todo el mundo a conectar con campañas pagadas de marcas internacionales.",
        "Estábamos revisando perfiles y tu contenido nos llamó la atención.",
        "Nos encantaría invitarte a completar tu Creator Pass.",
        "Estamos preparando nuevas campañas y queremos conocer las colaboraciones que realmente te interesan, para compartir contigo oportunidades que encajen mejor con tu contenido.",
        "Ya trabajamos con más de 8.000 creadores y con marcas como SHEGLAM, DJI y AliExpress.",
        "Completarlo lleva menos de un minuto. También puedes desbloquear hasta US$30 en beneficios, que se añadirán a tu primer pago elegible después de completar tu primera campaña pagada con COOJOY.",
        "Completa tu Creator Pass personalizado aquí:",
        "No necesitas iniciar sesión. Este enlace es personal, así que no lo compartas.",
        "¡Nos encantará explorar oportunidades contigo!",
    ),
    "pt": LocalizedCopy(
        "Portuguese",
        "{handle}, acesse oportunidades globais com marcas pela COOJOY",
        "Olá {handle},",
        "Tudo bem?",
        "Somos o time de Creator Partnerships da COOJOY. Conectamos criadores do mundo todo a campanhas pagas de marcas internacionais.",
        "Estávamos conhecendo novos perfis e o seu conteúdo chamou nossa atenção.",
        "Por isso, adoraríamos convidar você para completar seu Creator Pass.",
        "Estamos preparando novas campanhas e queremos entender quais parcerias realmente interessam a você, para enviar oportunidades que combinem melhor com seu conteúdo.",
        "Já trabalhamos com mais de 8.000 criadores e com marcas como SHEGLAM, DJI e AliExpress.",
        "Leva menos de um minuto para completar. Você também pode desbloquear até US$30 em benefícios, adicionados ao seu primeiro pagamento elegível após concluir sua primeira campanha paga com a COOJOY.",
        "Complete seu Creator Pass personalizado aqui:",
        "Não precisa fazer login. Este link é pessoal, então não o compartilhe.",
        "Vamos adorar explorar futuras oportunidades com você!",
    ),
    "fr": LocalizedCopy(
        "French",
        "{handle}, accédez à des opportunités internationales avec des marques via COOJOY",
        "Bonjour {handle},",
        "J'espère que vous allez bien.",
        "Nous sommes l'équipe Creator Partnerships de COOJOY. Nous mettons en relation des créateurs du monde entier avec des campagnes rémunérées de marques internationales.",
        "Nous découvrions de nouveaux profils et votre contenu a retenu notre attention.",
        "Nous aimerions donc vous inviter à compléter votre Creator Pass.",
        "Nous préparons de nouvelles campagnes et souhaitons connaître les collaborations qui vous intéressent vraiment, afin de vous proposer des opportunités plus adaptées à votre contenu.",
        "Nous travaillons déjà avec plus de 8 000 créateurs et des marques comme SHEGLAM, DJI et AliExpress.",
        "Cela prend moins d'une minute. Vous pouvez aussi débloquer jusqu'à US$30 d'avantages, ajoutés à votre premier paiement éligible après votre première campagne rémunérée avec COOJOY.",
        "Complétez votre Creator Pass personnalisé ici :",
        "Aucune connexion n'est nécessaire. Ce lien vous est réservé, merci de ne pas le partager.",
        "Nous serions ravis d'explorer de futures opportunités avec vous !",
    ),
    "de": LocalizedCopy(
        "German",
        "{handle}, entdecke globale bezahlte Markenkooperationen mit COOJOY",
        "Hallo {handle},",
        "Ich hoffe, dir geht es gut.",
        "Wir sind das Creator-Partnerships-Team von COOJOY und bringen Creator weltweit mit bezahlten Kampagnen internationaler Marken zusammen.",
        "Bei der Suche nach neuen Creatorn ist uns dein Content aufgefallen.",
        "Deshalb möchten wir dich gern einladen, deinen Creator Pass auszufüllen.",
        "Wir bereiten gerade neue Kampagnen vor und möchten wissen, welche Kooperationen dich wirklich interessieren, damit wir dir passendere Möglichkeiten schicken können.",
        "Wir arbeiten bereits mit über 8.000 Creatorn sowie Marken wie SHEGLAM, DJI und AliExpress zusammen.",
        "Das Ausfüllen dauert weniger als eine Minute. Außerdem kannst du bis zu US$30 an Vorteilen freischalten, die nach deiner ersten bezahlten COOJOY-Kampagne zu deiner ersten berechtigten Auszahlung hinzugefügt werden.",
        "Fülle hier deinen persönlichen Creator Pass aus:",
        "Du brauchst kein Konto. Der Link ist nur für dich bestimmt – bitte teile ihn nicht.",
        "Wir würden uns freuen, bald eine passende Kooperation mit dir zu finden!",
    ),
    "ko": LocalizedCopy(
        "Korean",
        "{handle}님, COOJOY와 함께 글로벌 유료 브랜드 협업 기회를 만나보세요",
        "안녕하세요 {handle}님,",
        "잘 지내고 계신가요?",
        "저희는 COOJOY Creator Partnerships 팀입니다. 전 세계 크리에이터와 글로벌 브랜드의 유료 캠페인을 연결하고 있어요.",
        "새로운 크리에이터를 찾던 중 콘텐츠가 눈에 띄었습니다.",
        "그래서 맞춤형 Creator Pass를 안내드리고 싶어요.",
        "곧 시작될 캠페인 중 더 잘 맞는 기회를 보내드릴 수 있도록, 관심 있는 협업 분야를 간단히 알려주세요.",
        "COOJOY는 이미 전 세계 8,000명 이상의 크리에이터와 함께하고 있으며 SHEGLAM, DJI, AliExpress 같은 브랜드와 협업하고 있습니다.",
        "작성에는 1분도 걸리지 않습니다. 첫 COOJOY 유료 캠페인을 완료하면 첫 정산에 추가되는 최대 US$30의 혜택도 받을 수 있어요.",
        "나만의 Creator Pass 작성하기:",
        "로그인은 필요하지 않습니다. 개인 전용 링크이니 다른 사람에게 공유하지 말아 주세요.",
        "좋은 기회로 함께할 수 있기를 기대할게요!",
    ),
    "ja": LocalizedCopy(
        "Japanese",
        "{handle}さん、COOJOYでグローバルな有償ブランド案件にアクセス",
        "{handle}さん、こんにちは。",
        "お元気ですか？",
        "私たちはCOOJOYのCreator Partnershipsチームです。世界中のクリエイターと海外ブランドの有償キャンペーンをつないでいます。",
        "新しいクリエイターを探していたところ、コンテンツが目に留まりました。",
        "ぜひ、あなた専用のCreator Passをご案内させてください。",
        "これから始まるキャンペーンの中から、より相性のよい案件をご案内できるよう、興味のあるコラボ分野を教えていただければと思います。",
        "COOJOYではすでに世界8,000人以上のクリエイターと協業し、SHEGLAM、DJI、AliExpressなどのブランドキャンペーンを支援しています。",
        "入力は1分以内で完了します。初回のCOOJOY有償キャンペーン完了後、最初の対象報酬に最大US$30の特典が加算されます。",
        "あなた専用のCreator Passはこちら：",
        "ログインは不要です。専用リンクのため、ほかの方への共有はお控えください。",
        "一緒に素敵な機会をつくれることを楽しみにしています！",
    ),
    "ru": LocalizedCopy(
        "Russian",
        "{handle}, получите доступ к международным платным кампаниям с COOJOY",
        "Здравствуйте, {handle}!",
        "Надеемся, у вас всё хорошо.",
        "Мы — команда Creator Partnerships в COOJOY. Мы помогаем авторам со всего мира находить платные кампании международных брендов.",
        "Мы знакомились с новыми профилями, и ваш контент привлёк наше внимание.",
        "Поэтому хотим пригласить вас заполнить персональный Creator Pass.",
        "Сейчас мы готовим новые кампании и хотим узнать, какие форматы сотрудничества вам действительно интересны, чтобы предлагать более подходящие проекты.",
        "С нами уже работают более 8 000 авторов, а среди наших партнёров — SHEGLAM, DJI и AliExpress.",
        "Заполнение займёт меньше минуты. Вы также сможете получить до US$30 дополнительных бонусов — они будут добавлены к первой подходящей выплате после завершения вашей первой платной кампании COOJOY.",
        "Заполните персональный Creator Pass здесь:",
        "Регистрация не нужна. Эта ссылка создана специально для вас — пожалуйста, не передавайте её другим.",
        "Будем рады найти отличную возможность для совместной работы!",
    ),
    "ar": LocalizedCopy(
        "Arabic",
        "{handle}، اكتشف فرص التعاون المدفوعة مع العلامات التجارية العالمية عبر COOJOY",
        "مرحبًا {handle}،",
        "نتمنى أن تكون بخير.",
        "نحن فريق شراكات صنّاع المحتوى في COOJOY، ونساعد المبدعين حول العالم على الوصول إلى حملات مدفوعة مع علامات تجارية دولية.",
        "كنا نتعرّف إلى حسابات جديدة، وقد لفت محتواك انتباهنا.",
        "لذلك يسعدنا دعوتك لإكمال Creator Pass الخاص بك.",
        "نحضّر حاليًا لحملات جديدة، ونود معرفة أنواع التعاون التي تهمك فعلًا حتى نرسل لك فرصًا أنسب لمحتواك.",
        "نعمل بالفعل مع أكثر من 8,000 صانع محتوى، ومع علامات مثل SHEGLAM وDJI وAliExpress.",
        "لن يستغرق الأمر أكثر من دقيقة. ويمكنك أيضًا الاستفادة من مزايا تصل إلى US$30، تُضاف إلى أول دفعة مؤهلة بعد إكمال أول حملة مدفوعة لك مع COOJOY.",
        "أكمل Creator Pass المخصص لك هنا:",
        "لا تحتاج إلى تسجيل الدخول. هذا الرابط مخصص لك، لذا يُرجى عدم مشاركته.",
        "يسعدنا أن نجد فرصة رائعة للعمل معًا!",
    ),
}


def render_creator_pass_email(
    *,
    creator_key: str,
    handle: str,
    raw_language: str | None,
    personalization: CreatorPersonalization,
    form_url: str,
) -> tuple[str, str, str]:
    code = resolve_language_code(raw_language)
    copy = LOCALIZED_COPIES.get(code, ENGLISH)
    safe_handle = handle.strip().lstrip("@") or "Creator"
    variant, subject_template = _select_creator_pass_variant(creator_key)
    subject = subject_template.format(handle=safe_handle)
    english = variant.body.format(
        handle=safe_handle,
        personalized_opening=personalization.english_opening.strip(),
        form_url=form_url,
    )
    if code == "en":
        return subject, english, code
    primary = _render_section(copy, safe_handle, personalization.primary_opening, form_url)
    return subject, f"{primary}\n\n---------- English ----------\n\n{english}", code


def _select_creator_pass_variant(creator_key: str) -> tuple[CreatorPassVariant, str]:
    """Assign one stable body and one stable A/B subject to each creator."""
    digest = hashlib.sha256(creator_key.strip().lower().encode("utf-8")).digest()
    variant = CREATOR_PASS_VARIANTS[digest[0] % len(CREATOR_PASS_VARIANTS)]
    return variant, variant.subjects[digest[1] % len(variant.subjects)]


def _render_section(copy: LocalizedCopy, handle: str, opening: str, form_url: str) -> str:
    return "\n\n".join(
        [
            copy.greeting.format(handle=handle),
            copy.courtesy,
            copy.team_intro,
            copy.discovery,
            opening.strip(),
            copy.invitation,
            copy.network_intro,
            copy.credibility,
            copy.benefit,
            f"{copy.cta}\n{form_url}",
            copy.note,
            copy.closing,
            "COOJOY Creator Partnership Team\nhttps://www.coojoy.cn",
        ]
    )

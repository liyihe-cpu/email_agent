from __future__ import annotations

from dataclasses import dataclass

from ..outreach.strategies.localization import resolve_language_code


@dataclass(frozen=True)
class RejectionCopy:
    subject: str
    body_text: str


ENGLISH_SUBJECT = "An update from the COOJOY Creator Team"
ENGLISH_BODY = """Hi {handle},

Thank you for your interest in the collaboration opportunities we shared.

After reviewing the current campaign requirements, we won't be moving forward with your profile for this particular opportunity. This decision is specific to the current brief and does not affect your eligibility for future COOJOY campaigns.

We've kept your creator profile in our active network and will reach out when a better-matched paid opportunity becomes available.

If you have any questions, feel free to reply to this email. We'll be happy to help.

Warm regards,
COOJOY Creator Partnership Team
https://www.coojoy.cn"""


LOCALIZED: dict[str, tuple[str, str]] = {
    "es": (
        "Una actualización del equipo de creadores de COOJOY",
        """Hola {handle},

Gracias por responder y por tu interés en las oportunidades de colaboración que compartimos.

Después de revisar los requisitos de la campaña actual, no seguiremos adelante con tu perfil para esta oportunidad en particular. Esta decisión se refiere únicamente a este brief y no afecta tu elegibilidad para futuras campañas de COOJOY.

Hemos mantenido tu perfil en nuestra red activa de creadores y nos pondremos en contacto cuando haya una oportunidad remunerada que encaje mejor contigo.

Si tienes alguna pregunta, puedes responder a este correo. Estaremos encantados de ayudarte.

Saludos cordiales,
Equipo de Colaboraciones con Creadores de COOJOY
https://www.coojoy.cn""",
    ),
    "pt": (
        "Uma atualização da equipe de criadores da COOJOY",
        """Olá {handle},

Obrigado por responder e pelo seu interesse nas oportunidades de colaboração que compartilhamos.

Após analisarmos os requisitos da campanha atual, não seguiremos com o seu perfil para esta oportunidade específica. Essa decisão se aplica somente a este briefing e não afeta sua elegibilidade para futuras campanhas da COOJOY.

Mantivemos seu perfil em nossa rede ativa de criadores e entraremos em contato quando surgir uma oportunidade paga mais adequada.

Se tiver alguma dúvida, fique à vontade para responder a este e-mail. Teremos prazer em ajudar.

Atenciosamente,
Equipe de Parcerias com Criadores da COOJOY
https://www.coojoy.cn""",
    ),
    "fr": (
        "Une mise à jour de l'équipe créateurs de COOJOY",
        """Bonjour {handle},

Merci pour votre réponse et pour l'intérêt que vous portez aux opportunités de collaboration que nous avons partagées.

Après examen des critères de la campagne actuelle, nous ne donnerons pas suite à votre profil pour cette opportunité précise. Cette décision concerne uniquement ce brief et n'affecte pas votre éligibilité aux futures campagnes COOJOY.

Votre profil reste dans notre réseau actif de créateurs et nous vous contacterons lorsqu'une opportunité rémunérée plus adaptée se présentera.

Si vous avez des questions, n'hésitez pas à répondre à cet e-mail. Nous serons ravis de vous aider.

Bien cordialement,
Équipe Partenariats Créateurs COOJOY
https://www.coojoy.cn""",
    ),
    "de": (
        "Ein Update vom COOJOY Creator Team",
        """Hallo {handle},

vielen Dank für deine Rückmeldung und dein Interesse an den von uns vorgestellten Kooperationsmöglichkeiten.

Nach Prüfung der aktuellen Kampagnenanforderungen werden wir dein Profil für diese konkrete Gelegenheit nicht weiter berücksichtigen. Diese Entscheidung bezieht sich nur auf dieses Briefing und hat keinen Einfluss auf deine Eignung für zukünftige COOJOY-Kampagnen.

Dein Profil bleibt in unserem aktiven Creator-Netzwerk. Wir melden uns, sobald sich eine besser passende bezahlte Gelegenheit ergibt.

Wenn du Fragen hast, antworte gerne auf diese E-Mail. Wir helfen dir gerne weiter.

Viele Grüße
COOJOY Creator Partnership Team
https://www.coojoy.cn""",
    ),
    "ja": (
        "COOJOYクリエイターチームからのお知らせ",
        """こんにちは、{handle}さん

ご返信いただき、またご案内したコラボレーションにご関心をお寄せいただきありがとうございます。

現在のキャンペーン要件を確認した結果、今回はこの案件でのご提案を見送らせていただくことになりました。今回の判断はこの案件に限るもので、今後のCOOJOYキャンペーンへの参加資格に影響するものではありません。

クリエイタープロフィールは引き続きアクティブネットワークに登録し、より適した有償案件がございましたらご連絡いたします。

ご不明な点がございましたら、このメールにご返信ください。喜んでご案内いたします。

どうぞよろしくお願いいたします。
COOJOY Creator Partnership Team
https://www.coojoy.cn""",
    ),
    "ko": (
        "COOJOY 크리에이터 팀 안내",
        """안녕하세요, {handle}님.

답변해 주시고 저희가 안내드린 협업 기회에 관심을 보여 주셔서 감사합니다.

현재 캠페인 요건을 검토한 결과, 이번 기회에는 귀하의 프로필로 진행하지 않게 되었습니다. 이번 결정은 해당 브리프에만 적용되며 향후 COOJOY 캠페인 참여 가능성에는 영향을 주지 않습니다.

귀하의 프로필은 활성 크리에이터 네트워크에 계속 보관되며, 더 잘 맞는 유료 기회가 생기면 연락드리겠습니다.

궁금한 점이 있으시면 이 이메일에 답장해 주세요. 기꺼이 도와드리겠습니다.

감사합니다.
COOJOY Creator Partnership Team
https://www.coojoy.cn""",
    ),
    "ar": (
        "تحديث من فريق المبدعين في COOJOY",
        """مرحبًا {handle}،

شكرًا لردك واهتمامك بفرص التعاون التي شاركناها معك.

بعد مراجعة متطلبات الحملة الحالية، لن نتابع بملفك لهذه الفرصة تحديدًا. يخص هذا القرار هذا المشروع فقط، ولا يؤثر في أهليتك لحملات COOJOY المستقبلية.

سنُبقي ملفك ضمن شبكة المبدعين النشطة لدينا، وسنتواصل معك عند توفر فرصة مدفوعة أكثر ملاءمة.

إذا كانت لديك أي أسئلة، فلا تتردد في الرد على هذا البريد الإلكتروني. يسعدنا مساعدتك.

مع أطيب التحيات،
فريق شراكات المبدعين في COOJOY
https://www.coojoy.cn""",
    ),
}


def build_rejection_copy(
    handle: str | None,
    language: str | None,
) -> RejectionCopy:
    display_handle = (handle or "Creator").strip() or "Creator"
    code = resolve_language_code(language)
    if code == "en" or code not in LOCALIZED:
        return RejectionCopy(
            subject=ENGLISH_SUBJECT,
            body_text=ENGLISH_BODY.format(handle=display_handle),
        )

    local_subject, local_body = LOCALIZED[code]
    bilingual_body = (
        f"{local_body.format(handle=display_handle)}\n\n"
        "----------------------------------------\n\n"
        f"{ENGLISH_BODY.format(handle=display_handle)}"
    )
    return RejectionCopy(subject=local_subject, body_text=bilingual_body)

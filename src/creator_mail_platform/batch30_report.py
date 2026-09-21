from __future__ import annotations

from collections import defaultdict

from sqlalchemy import text

from .core.db import get_engine
from .console_ui import console
from rich.table import Table


def print_batch30_report(last_batch: int = 466) -> None:
    with get_engine().connect() as conn:
        sent_rows = conn.execute(text("""
            SELECT DISTINCT ON (creator_id, batch_no) creator_id, batch_no, reply_type
            FROM creator_mail.activity_contacts
            WHERE send_status = 'smtp_accepted' AND batch_no BETWEEN 1 AND :last_batch
            ORDER BY creator_id, batch_no, sent_at DESC NULLS LAST
        """), {"last_batch": last_batch}).fetchall()
        followup_rows = conn.execute(text("""
                SELECT DISTINCT p.creator_id, p.batch_no,
                       p.status,
                       jsonb_path_exists(
                         p.messages_json,
                         '$[*] ? (@.message_kind == "creator_reply")'
                       ) AS creator_replied,
                       jsonb_path_exists(
                         p.messages_json,
                         '$[*] ? (@.message_kind == "followup_reply")'
                       ) AS followup_sent
                FROM creator_mail.campaign_response_profiles p
                WHERE p.batch_no BETWEEN 1 AND :last_batch
            """), {"last_batch": last_batch}).fetchall()

    groups = defaultdict(list)
    for creator_id, batch_no, reply_type in sent_rows:
        start = ((batch_no - 1) // 30) * 30 + 1
        groups[start].append((creator_id, reply_type, False, False))
    for creator_id, batch_no, status, creator_replied, followup_sent in followup_rows:
        start = ((batch_no - 1) // 30) * 30 + 1
        # A creator_reply is the second reply after Andy's rejection.
        # Current human-processing scope follows chapter 10's need_reply queue.
        groups[start].append((creator_id, None, bool(creator_replied), status == "need_reply"))

    table = Table(title=f"每 30 个 Batch 真人回复汇总（1–{last_batch}）")
    for name in ["Batch 组", "已发送", "真人回复", "真人回复率", "回复过拒信的达人（去重）", "其比例", "累计进入真人处理范围（去重）", "其比例"]:
        table.add_column(name, justify="right" if name != "Batch 组" else "left")

    all_sent, all_human, all_rejection = set(), set(), set()
    cumulative_sent, cumulative_human = set(), set()
    for start in sorted(groups):
        end = min(start + 29, last_batch)
        rows = groups[start]
        sent = {r[0] for r in rows if r[1] is not None}
        human = {r[0] for r in rows if r[1] == "human_reply"}
        rejection = {r[0] for r in rows if r[2]}
        attention = {r[0] for r in rows if r[3]}
        cumulative_sent |= sent
        cumulative_human |= attention
        all_sent |= sent; all_human |= human; all_rejection |= rejection
        pct = lambda n, d: f"{n / d:.2%}" if d else "—"
        table.add_row(f"{start}–{end}", str(len(sent)), str(len(human)), pct(len(human), len(sent)),
                      str(len(rejection)), pct(len(rejection), len(sent)), str(len(cumulative_human)),
                      pct(len(cumulative_human), len(cumulative_sent)))
    pct = lambda n, d: f"{n / d:.2%}" if d else "—"
    all_attention = {r[0] for rows in groups.values() for r in rows if r[3]}
    table.add_row("总", str(len(all_sent)), str(len(all_human)), pct(len(all_human), len(all_sent)),
                  str(len(all_rejection)), pct(len(all_rejection), len(all_sent)), str(len(all_attention)),
                  pct(len(all_attention), len(all_sent)))
    console.print(table)

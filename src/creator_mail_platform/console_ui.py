from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table


console = Console()


def print_campaign_summary(result: dict[str, object]) -> None:
    validation = result["validation"]
    generation = result["generation"]
    assignment = result["assignment"]
    dry_run = result["dry_run"]
    print_result(
        {
            "策略": str(result.get("strategy", "s2")).upper(),
            "Batch": result["batch_no"],
            "发件邮箱": result["sender_email"],
            "本次检查邮箱": validation["checked_creators"],
            "本次写入 AI 文案": generation["written"],
            "AI 失败，暂时跳过": result.get("skipped_generation_failed", 0),
            "缺少正文，暂时跳过": result.get("skipped_missing_content", 0),
            "此前已绑定": assignment["already_assigned_to_sender"],
            "本次新绑定": assignment["assigned_now"],
            "待发送": dry_run["selected"],
        },
        title="活动已准备完成",
    )


def print_send_result(result: dict[str, int]) -> None:
    failed = result["temporary_failed"] + result["hard_failed"] + result["invalid"]
    print_result(
        {
            "选择发送": result["selected"],
            "SMTP 已接受": result["accepted"],
            "失败合计": failed,
            "临时失败": result["temporary_failed"],
            "永久失败": result["hard_failed"],
            "无效邮箱": result["invalid"],
        },
        title="发送完成",
    )


def print_stats(result: dict[str, object]) -> None:
    scope = str(result.get("scope") or "全部达人")
    summary = result["delivery_summary"]
    table = Table(title=f"发送结果汇总 · {scope}", header_style="bold cyan")
    table.add_column("指标")
    table.add_column("数量", justify="right")
    labels = (
        ("总人数", "total"),
        ("SMTP 成功发出", "smtp_accepted"),
        ("无效邮箱合计", "invalid_total"),
        ("其中：发送前预检无效", "precheck_invalid"),
        ("其中：SMTP 永久失败", "hard_failed"),
        ("SMTP 临时失败，可重试", "temporary_failed"),
        ("尚未发送且允许发送", "pending_sendable"),
        ("正在发送", "sending"),
    )
    for label, key in labels:
        table.add_row(label, str(summary[key]))
    table.add_section()
    table.add_row("累计收到回信邮件", str(result["reply_messages_total"]))
    console.print(table)

    combination_table = Table(
        title="email_status × send_status 明细",
        header_style="bold green",
    )
    combination_table.add_column("email_status")
    combination_table.add_column("send_status")
    combination_table.add_column("数量", justify="right")
    for row in result["status_combinations"]:
        combination_table.add_row(
            str(row["email_status"]),
            str(row["send_status"]),
            str(row["count"]),
        )
    console.print(combination_table)

    strategy_summaries = result.get("strategy_summaries") or {}
    if strategy_summaries:
        for strategy in ("s1", "s2", "s3"):
            summary = strategy_summaries.get(strategy)
            if summary:
                _print_engagement_summary(
                    summary,
                    title=strategy.upper(),
                    include_email=strategy in {"s1", "s3"},
                    include_form=strategy in {"s2", "s3"},
                    include_cross=strategy == "s3",
                )
    else:
        _print_engagement_summary(
            result["engagement_summary"],
            title=scope,
            include_email=True,
            include_form=True,
            include_cross=True,
        )

    sender_progress = result["sender_progress"]
    if not sender_progress:
        return
    sender_table = Table(title="发件邮箱进度", header_style="bold magenta")
    sender_table.add_column("发件邮箱")
    sender_table.add_column("状态")
    sender_table.add_column("数量", justify="right")
    for sender, statuses in sender_progress.items():
        for index, (status, count) in enumerate(sorted(statuses.items())):
            sender_table.add_row(sender if index == 0 else "", status, str(count))
    console.print(sender_table)


def _print_engagement_summary(
    summary: dict[str, object],
    *,
    title: str,
    include_email: bool,
    include_form: bool,
    include_cross: bool,
) -> None:
    start = summary.get("batch_from")
    end = summary.get("batch_to")
    batch_label = f" · Batch {start}–{end}" if start is not None else ""
    delivery = summary["delivery"]
    accepted = int(delivery["smtp_accepted"])
    started_contacts = int(delivery["started_batch_contacts"])

    table = Table(
        title=f"{title} 业务转化{batch_label}",
        header_style="bold yellow",
    )
    table.add_column("指标")
    table.add_column("数量", justify="right")
    table.add_column("占已启动 Batch", justify="right")
    table.add_column("占 SMTP 成功", justify="right")
    table.add_column("补充比例", justify="right")

    def add(
        label: str,
        count: object,
        batch_rate: object | None = None,
        sent_rate: object | None = None,
        extra_rate: object | None = None,
    ) -> None:
        table.add_row(
            label,
            str(count),
            _format_pct(batch_rate),
            _format_pct(sent_rate),
            _format_pct(extra_rate),
        )

    add("方案范围内达人总数", delivery["total_contacts"])
    first_batch = delivery.get("first_sent_batch")
    last_batch = delivery.get("last_sent_batch")
    if first_batch is not None:
        sent_batch_label = (
            str(first_batch)
            if first_batch == last_batch
            else f"{first_batch}–{last_batch}"
        )
        add(
            "已启动发送的 Batch",
            f"{sent_batch_label}（{delivery['sent_batch_count']} 个）",
        )
    add("已启动 Batch 覆盖人数", started_contacts, 100.0 if started_contacts else 0.0)
    add(
        "SMTP 成功接受",
        accepted,
        delivery["smtp_accepted_of_started_batches_pct"],
        100.0 if accepted else 0.0,
    )
    add(
        "发送前判定无效",
        delivery["precheck_invalid"],
        delivery["precheck_invalid_rate_pct"],
    )
    add(
        "通过发送前预检",
        delivery["precheck_passed"],
        _percentage_for_ui(delivery["precheck_passed"], started_contacts),
        None,
        delivery["smtp_accepted_of_precheck_passed_pct"],
    )
    add(
        "仍可发送但尚未完成",
        delivery["pending_sendable"],
        _percentage_for_ui(delivery["pending_sendable"], started_contacts),
    )
    add(
        "SMTP 临时失败",
        delivery["temporary_failed"],
        _percentage_for_ui(delivery["temporary_failed"], started_contacts),
    )
    add(
        "SMTP 永久失败",
        delivery["hard_failed"],
        _percentage_for_ui(delivery["hard_failed"], started_contacts),
    )
    add(
        "正在发送",
        delivery["sending"],
        _percentage_for_ui(delivery["sending"], started_contacts),
    )
    add(
        "其中：possibly_usable",
        delivery["accepted_possibly_usable"],
        _percentage_for_ui(delivery["accepted_possibly_usable"], started_contacts),
        _percentage_for_ui(delivery["accepted_possibly_usable"], accepted),
    )
    add(
        "其中：usable",
        delivery["accepted_usable"],
        _percentage_for_ui(delivery["accepted_usable"], started_contacts),
        _percentage_for_ui(delivery["accepted_usable"], accepted),
    )
    add(
        "其中：其他邮箱状态",
        delivery["accepted_other_email_status"],
        _percentage_for_ui(delivery["accepted_other_email_status"], started_contacts),
        _percentage_for_ui(delivery["accepted_other_email_status"], accepted),
    )

    if include_email:
        email = summary["email_engagement"]
        table.add_section()
        add("已匹配回信邮件（封）", email["reply_messages_total"])
        add(
            "有任意匹配回信的达人",
            email["reply_contacts"],
            None,
            email["reply_contact_rate_pct"],
        )
        add(
            "真人回复达人",
            email["human_reply_contacts"],
            None,
            email["human_reply_rate_pct"],
        )
        add(
            "自动回复达人",
            email["auto_reply_contacts"],
            None,
            _percentage_for_ui(email["auto_reply_contacts"], accepted),
        )
        add("永久退信达人", email["hard_bounce_contacts"])
        add("临时退信达人", email["soft_bounce_contacts"])
        add("退订达人", email["unsubscribe_contacts"])

    if include_form:
        form = summary["form_engagement"]
        table.add_section()
        add(
            "提交基础表单",
            form["total_form_submitted"],
            None,
            form["form_submission_rate_pct"],
        )
        add(
            "完成可选资料",
            form["completed_optional"],
            None,
            form["completed_optional_rate_pct"],
            form["optional_completion_of_forms_pct"],
        )
        add(
            "选择合作类目",
            form["category_selected_count"],
            None,
            form["category_selected_rate_pct"],
        )
        add(
            "添加并授权其他联系方式",
            form["added_contact_count"],
            None,
            form["added_contact_rate_pct"],
            form["contact_completion_of_forms_pct"],
        )
        add(
            "只选类目、未留联系方式",
            form["category_only_count"],
            None,
            form["category_only_rate_pct"],
        )
        add(
            "未留其他联系方式",
            form["no_contact_count"],
            None,
            form["no_contact_rate_pct"],
        )
        add("只加入、未填任何可选资料", form["joined_only_count"])

    if include_cross:
        cross = summary["cross_channel"]
        table.add_section()
        add("任意邮件响应 + 表单", cross["any_reply_and_form_count"])
        add(
            "真人回复 + 表单（核心交集）",
            cross["human_reply_and_form_count"],
            None,
            cross["human_reply_and_form_rate_of_sent_pct"],
            cross["form_users_also_human_replied_pct"],
        )
        add(
            "真人回复或提交表单（去重）",
            cross["human_reply_or_form_count"],
            None,
            _percentage_for_ui(cross["human_reply_or_form_count"], accepted),
        )
        add(
            "真人回复者中也填表",
            cross["human_reply_and_form_count"],
            None,
            None,
            cross["human_repliers_also_submitted_form_pct"],
        )

    console.print(table)


def _percentage_for_ui(numerator: object, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(int(numerator) * 100 / denominator, 2)


def _format_pct(value: object | None) -> str:
    return "—" if value is None else f"{float(value):.2f}%"


def print_replies(rows: list[dict[str, object]]) -> None:
    table = Table(title=f"最新回信 · {len(rows)} 条", header_style="bold cyan")
    table.add_column("时间", no_wrap=True)
    table.add_column("Batch", justify="right")
    table.add_column("达人")
    table.add_column("项目")
    table.add_column("类型")
    table.add_column("回复内容", overflow="fold", max_width=72)
    for row in rows:
        body = " ".join(str(row.get("reply_body_text") or "").split())
        table.add_row(
            str(row.get("last_reply_at") or ""),
            str(row.get("batch_no") or ""),
            str(row.get("handle") or row.get("creator_id") or ""),
            str(row.get("primary_brief_code") or "—"),
            str(row.get("reply_type") or ""),
            body[:500] or "（无正文）",
        )
    console.print(table)


def print_result(
    result: dict[str, Any],
    *,
    title: str,
    json_output: bool = False,
) -> None:
    if json_output:
        console.print_json(data=result)
        return
    table = Table(title=title, show_header=False, box=None)
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value")
    for key, value in _flatten(result):
        table.add_row(key.replace("_", " ").title(), str(value))
    console.print(table)


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_flatten(child, name))
    elif isinstance(value, list):
        rows.append((prefix, json.dumps(value, ensure_ascii=False)))
    else:
        rows.append((prefix, value))
    return rows

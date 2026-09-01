from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Annotated

from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.prompt import Confirm, Prompt
import typer

from .outreach.campaign import (
    generate_strategy_messages,
    get_strategy_route,
    prepare_campaign,
    send_prepared_campaign,
)
from .analysis.active_creators_export import DEFAULT_OUTPUT_PATH, export_active_creators
from .core.config import get_settings
from .console_ui import (
    console,
    print_campaign_summary,
    print_followup_stats,
    print_replies,
    print_result,
    print_send_result,
    print_stats,
)
from .creator_pass.service import build_form_url
from .followups.importer import prepare_response_profiles
from .followups.generator import backfill_rejection_apologies, generate_rejection_drafts
from .followups.report import list_followup_threads, list_thread_messages
from .followups.review import (
    analyze_followup_thread,
    collect_followup_stats,
    is_obvious_no_reply,
    mark_thread_no_reply,
    pending_review_threads,
    revise_followup_reply,
)
from .followups.sender import read_body_file, send_rejections, send_thread_reply
from .core.db import initialize_schema
from .outreach.importer import import_creator_snapshot
from .outreach.plan_runner import parse_campaign_jobs, run_campaign_plan
from .outreach.resumable_queue import (
    DEFAULT_S1_BATCH_FROM,
    DEFAULT_S1_BATCH_TO,
    S1_QUEUE_MIN_BATCH,
    run_resumable_s1_queue,
)
from .outreach.stats import collect_stats
from .outreach.validator import validate_pending_contacts
from .replies.imap_receiver import listen_forever, receive_once
from .replies.report import list_replies


app = typer.Typer(
    name="creator-mail",
    help="COOJOY 达人邮件活动控制台。",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)
data_app = typer.Typer(help="数据库初始化与达人同步。")
form_app = typer.Typer(help="Creator Pass 链接与本地 H5 工具。")
followup_app = typer.Typer(help="拒信、独立邮件线程与后续多轮沟通。")
app.add_typer(data_app, name="data")
app.add_typer(form_app, name="form")
app.add_typer(followup_app, name="followup")


@app.command("run-campaign")
def run_campaign(
    batch_no: Annotated[int, typer.Option("--batch", "-b", help="Batch 编号。")],
    sender_email: Annotated[str, typer.Option("--sender", "-s", help="本批次使用的发件邮箱。")],
    strategy: Annotated[
        str,
        typer.Option("--strategy", help="活动策略：s1、s2 或 s3。"),
    ] = "s2",
    workers: Annotated[
        int,
        typer.Option("--workers", "-w", min=1, max=16, help="AI 并发数。"),
    ] = 8,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="完成准备和检查，但不真实发送。"),
    ] = False,
    skip_mx: Annotated[
        bool,
        typer.Option("--skip-mx", help="只检查格式，跳过 DNS/MX。"),
    ] = False,
) -> None:
    """一键验证、生成、绑定、预检，确认一次后发送一个 batch。"""
    route = get_strategy_route(strategy)
    console.print(
        Panel.fit(
            f"[bold]{route.name.upper()} · Batch {batch_no}[/bold]\n"
            f"[cyan]{sender_email}[/cyan]\nAI 并发：{workers}",
            title="COOJOY 邮件活动",
            border_style="cyan",
        )
    )

    stage_labels = {
        "validate": "验证邮箱",
        "generate": "生成个性化文案",
        "assign": "绑定发件邮箱",
        "dry_run": "检查待发送邮件",
    }
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("准备活动", total=4)

        def on_stage(name: str, event: str, _result: dict[str, object] | None) -> None:
            if event == "start":
                progress.update(task, description=stage_labels[name])
            else:
                progress.advance(task)

        def on_validation(state: dict[str, object]) -> None:
            checked = int(state.get("checked_creators", 0))
            progress.update(task, description=f"验证邮箱 · 已检查 {checked}")

        def on_generation(state: dict[str, object]) -> None:
            generated = int(state.get("generated", 0))
            selected = int(state.get("selected", 0))
            progress.update(
                task,
                description=f"生成个性化文案 · {generated}/{selected}",
            )

        result = prepare_campaign(
            batch_no=batch_no,
            sender_email=sender_email,
            strategy=route.name,
            workers=workers,
            check_mx=not skip_mx,
            on_stage=on_stage,
            on_validation_progress=on_validation,
            on_generation_progress=on_generation,
        )

    print_campaign_summary(result)
    if dry_run:
        console.print("[yellow]Dry-run 已完成，没有发送任何邮件。[/yellow]")
        return

    if not get_settings().smtp_execution_enabled:
        console.print(
            Panel.fit(
                "活动已准备完成，但真实发送总闸门仍处于关闭状态。\n"
                "设置 [bold]SMTP_EXECUTION_ENABLED=true[/bold] 后，重新运行同一条命令。",
                title="发送已锁定",
                border_style="yellow",
            )
        )
        raise typer.Exit(code=2)

    selected = int(result["dry_run"]["selected"])
    if not Confirm.ask(
        f"确认立即通过 {result['sender_email']} 发送 {selected} 封邮件吗？",
        default=False,
        console=console,
    ):
        console.print("[dim]已取消。文案和邮箱绑定已保留，下次可直接续跑。[/dim]")
        return

    with console.status("[bold cyan]正在发送邮件…[/bold cyan]"):
        sent = send_prepared_campaign(
            batch_no=batch_no,
            sender_email=str(result["sender_email"]),
        )
    print_send_result(sent)


@app.command("run-plan")
def run_plan_command(
    jobs: Annotated[
        str,
        typer.Option(
            "--jobs",
            help="逗号分隔的 BATCH=EMAIL，例如 40=a@coojoy.cn,41=b@coojoy.cn。",
        ),
    ],
    strategy: Annotated[str, typer.Option("--strategy")] = "s2",
    workers: Annotated[int, typer.Option("--workers", "-w", min=1, max=16)] = 8,
    parallel_accounts: Annotated[
        int,
        typer.Option("--parallel-accounts", min=1, max=10),
    ] = 3,
    execute: Annotated[
        bool,
        typer.Option(
            "--execute",
            help="全部预检通过后无人值守真实发送；不会再逐批询问 y。",
        ),
    ] = False,
    skip_mx: Annotated[bool, typer.Option("--skip-mx")] = False,
) -> None:
    """按预先声明的 Batch→邮箱计划自动准备，并可无人值守发送。"""
    parsed_jobs = parse_campaign_jobs(jobs)

    def progress(event: str, job: object) -> None:
        labels = {
            "already_complete": "已完成，跳过",
            "prepare_start": "开始准备",
            "prepare_done": "准备完成",
            "prepare_partial": "部分准备完成，缺失正文已跳过",
            "send_start": "开始发送",
            "send_done": "发送完成",
        }
        console.print(
            f"[cyan]Batch {job.batch_no}[/cyan] · {job.sender_email} · {labels[event]}"
        )

    result = run_campaign_plan(
        parsed_jobs,
        strategy=strategy,
        workers=workers,
        parallel_accounts=parallel_accounts,
        check_mx=not skip_mx,
        execute=execute,
        progress=progress,
    )
    print_result(result, title="无人值守活动执行结果")


@app.command("resume-s1")
def resume_s1_command(
    batch_from: Annotated[
        int,
        typer.Option("--batch-from", min=S1_QUEUE_MIN_BATCH),
    ] = DEFAULT_S1_BATCH_FROM,
    batch_to: Annotated[
        int,
        typer.Option("--batch-to", min=S1_QUEUE_MIN_BATCH),
    ] = DEFAULT_S1_BATCH_TO,
    workers: Annotated[int, typer.Option("--workers", "-w", min=1, max=16)] = 8,
    parallel_accounts: Annotated[
        int,
        typer.Option("--parallel-accounts", min=1, max=10),
    ] = 10,
    retry_seconds: Annotated[
        int,
        typer.Option("--retry-seconds", min=10),
    ] = 300,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="真实发送；网络恢复后自动安全续跑。"),
    ] = False,
) -> None:
    """自动执行 S1 队列；当前默认 Batch 106-135，并支持安全续跑。"""

    def progress(event: str, payload: dict[str, object]) -> None:
        if event == "wave_start":
            console.print(
                f"[cyan]第 {payload['wave']}/{payload['wave_count']} 波[/cyan] · "
                f"Batch {payload['batch_from']}-{payload['batch_to']}"
            )
        elif event == "waiting":
            console.print(
                Panel.fit(
                    f"{payload['reason']}\n"
                    f"将在 {payload['seconds']} 秒后自动重试。按 Ctrl+C 可安全停止。",
                    title="等待网络或服务恢复",
                    border_style="yellow",
                )
            )
        elif event == "wave_state":
            console.print(
                f"[green]波次状态[/green] · accepted={payload['smtp_accepted']} · "
                f"remaining={payload['sendable']} · "
                f"unknown={payload['delivery_unknown']}"
            )
        elif event == "plan_progress":
            labels = {
                "already_complete": "已完成，跳过",
                "prepare_start": "开始准备",
                "prepare_done": "准备完成",
                "prepare_partial": "部分文案待后续补齐",
                "send_start": "开始发送",
                "send_done": "发送阶段结束",
            }
            label = labels.get(str(payload["event"]), str(payload["event"]))
            console.print(
                f"Batch {payload['batch_no']} · {payload['sender_email']} · {label}"
            )

    result = run_resumable_s1_queue(
        batch_from=batch_from,
        batch_to=batch_to,
        workers=workers,
        parallel_accounts=parallel_accounts,
        execute=execute,
        retry_seconds=retry_seconds,
        progress=progress,
    )
    summary = {key: value for key, value in result.items() if key != "wave_results"}
    print_result(summary, title="S1 自动续跑结果")


@app.command("stats")
def stats_command(
    batch_no: Annotated[int | None, typer.Option("--batch", "-b")] = None,
    batch_from: Annotated[int | None, typer.Option("--batch-from", min=1)] = None,
    batch_to: Annotated[int | None, typer.Option("--batch-to", min=1)] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
) -> None:
    """查看单个 batch、batch 范围或全部达人的发送状态。"""
    result = collect_stats(
        batch_no=batch_no,
        batch_from=batch_from,
        batch_to=batch_to,
    )
    if json_output:
        console.print_json(data=result)
        return
    print_stats(result)


@app.command("export-active-creators")
def export_active_creators_command(
    batch_from: Annotated[int, typer.Option("--batch-from", min=1)],
    batch_to: Annotated[int, typer.Option("--batch-to", min=1)],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Excel 输出路径。重复运行会更新同一文件。"),
    ] = DEFAULT_OUTPUT_PATH,
    workers: Annotated[
        int,
        typer.Option("--workers", "-w", min=1, max=16, help="回信 AI 分析并发数。"),
    ] = 8,
) -> None:
    """合并真人回信与 Creator Pass 提交者，并更新本地业务 Excel。"""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress_bar:
        task = progress_bar.add_task("读取活跃达人并分析新回信", total=1)

        def update(state: dict[str, int]) -> None:
            total = max(state.get("total", 0), 1)
            completed = state.get("completed", 0)
            progress_bar.update(
                task,
                total=total,
                completed=completed,
                description=f"回信分析 {completed}/{state.get('total', 0)}",
            )

        result = export_active_creators(
            batch_from=batch_from,
            batch_to=batch_to,
            output_path=output,
            workers=workers,
            progress=update,
        )
        progress_bar.update(task, completed=progress_bar.tasks[0].total)

    failures = list(result.pop("failure_details", []))
    print_result(result, title="活跃达人与回信分析 Excel")
    if failures:
        console.print(
            Panel.fit(
                f"本次有 {len(failures)} 封回信未完成 AI 分析；已跳过，不影响其他达人。\n"
                "再次运行同一命令会自动补分析。",
                title="待重试",
                border_style="yellow",
            )
        )


@app.command("validate")
def validate_command(
    batch_no: Annotated[int, typer.Option("--batch", "-b", min=1)],
    limit: Annotated[int | None, typer.Option("--limit", "-n", min=1)] = None,
    skip_mx: Annotated[bool, typer.Option("--skip-mx")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """单独验证一个 batch 中尚未验证的邮箱。"""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("正在验证邮箱", total=None)

        def update(state: dict[str, int]) -> None:
            progress.update(
                task,
                description=f"验证邮箱 · 已检查 {state['checked_creators']}",
            )

        result = validate_pending_contacts(
            batch_no=batch_no,
            limit=limit,
            check_mx=not skip_mx,
            progress=update,
        )
    print_result(result, title=f"邮箱验证 · batch {batch_no}", json_output=json_output)


@app.command("gen")
def generate_command(
    batch_no: Annotated[int | None, typer.Option("--batch", "-b")] = None,
    creator_id: Annotated[str | None, typer.Option("--creator", "-c")] = None,
    strategy: Annotated[
        str,
        typer.Option("--strategy", help="活动策略：s1、s2 或 s3。"),
    ] = "s2",
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=600)] = 5,
    workers: Annotated[int, typer.Option("--workers", "-w", min=1, max=16)] = 8,
    save: Annotated[
        bool,
        typer.Option("--save", help="把通过校验的文案写入数据库。"),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="覆盖已有文案；必须同时使用 --save。"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """预览或保存一位达人或一个 batch 的 AI 文案。"""
    if (batch_no is None) == (creator_id is None):
        raise ValueError("必须且只能填写 --batch 或 --creator 其中一个。")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("正在生成个性化文案", total=limit)

        def update(state: dict[str, object]) -> None:
            selected = int(state.get("selected", limit))
            processed = int(state.get("processed", 0))
            progress.update(task, total=max(selected, 1), completed=processed)

        result = generate_strategy_messages(
            strategy,
            creator_id=creator_id,
            batch_no=batch_no,
            limit=limit,
            workers=workers,
            apply=save,
            overwrite=overwrite,
            progress=update,
        )

    if json_output:
        console.print_json(data=result)
        return
    print_result(
        {key: value for key, value in result.items() if key not in {"previews", "failures"}},
        title="AI 文案生成",
    )
    for preview in result["previews"]:
        preview_label = (
            preview.get("rendered_language")
            or preview.get("brief_code")
            or strategy.upper()
        )
        console.print(
            Panel(
                str(preview["body"]),
                title=str(preview["subject"]),
                subtitle=f"{preview['creator_id']} · {preview_label}",
                border_style="magenta",
            )
        )
    if result["failures"]:
        print_result({"failures": result["failures"]}, title="生成失败")


@app.command("receive")
def receive_command(
    forever: Annotated[bool, typer.Option("--forever", "-f")] = False,
    mailbox: Annotated[
        str | None,
        typer.Option("--mailbox", "-m", help="只读取指定的已启用 IMAP 邮箱。"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """读取并分类达人回信与投递通知。"""
    if forever:
        target = f" · {mailbox}" if mailbox else ""
        console.print(f"[cyan]IMAP 持续收信已启动{target}，按 Ctrl+C 停止。[/cyan]")
        listen_forever(mailbox_email=mailbox)
        return
    with console.status("正在读取已配置的收件箱…"):
        result = receive_once(mailbox_email=mailbox)
    print_result(result, title="IMAP 收信", json_output=json_output)


@app.command("replies")
def replies_command(
    batch_no: Annotated[int | None, typer.Option("--batch", "-b", min=1)] = None,
    batch_from: Annotated[int | None, typer.Option("--batch-from", min=1)] = None,
    batch_to: Annotated[int | None, typer.Option("--batch-to", min=1)] = None,
    all_types: Annotated[
        bool,
        typer.Option("--all-types", help="同时显示自动回复、退订和退信。"),
    ] = False,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=1000)] = 100,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """查看已经关联到程序发件记录的最新回信。"""
    rows = list_replies(
        batch_no=batch_no,
        batch_from=batch_from,
        batch_to=batch_to,
        reply_type=None if all_types else "human_reply",
        limit=limit,
    )
    if json_output:
        console.print_json(data=rows)
        return
    print_replies(rows)


@followup_app.command("prepare")
def prepare_followup_profiles_command(
    batch_from: Annotated[int, typer.Option("--batch-from", min=1)] = 1,
    batch_to: Annotated[int | None, typer.Option("--batch-to", min=1)] = None,
    limit: Annotated[int | None, typer.Option("--limit", "-n", min=1)] = None,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="创建单表并写入拒信草稿；不添加时只预览。"),
    ] = False,
) -> None:
    """从数据库中的真人回复或表单提交创建后续沟通档案。"""
    result = prepare_response_profiles(
        batch_from=batch_from,
        batch_to=batch_to,
        limit=limit,
        apply=apply,
    )
    previews = list(result.pop("previews", []))
    print_result(result, title="后续沟通档案准备")
    for preview in previews:
        console.print(
            Panel(
                str(preview["body_text"]),
                title=str(preview["subject"]),
                subtitle=f"{preview['creator_id']} · {preview['mailbox_email']}",
                border_style="magenta",
            )
        )


@followup_app.command("send-rejections")
def send_rejections_command(
    limit: Annotated[int | None, typer.Option("--limit", "-n", min=1)] = None,
    sender: Annotated[
        str | None,
        typer.Option("--sender", "-s", help="统一使用指定邮箱发送本次拒信。"),
    ] = None,
    parallel_accounts: Annotated[
        int,
        typer.Option("--parallel-accounts", min=1, max=10),
    ] = 10,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="真实发送；不添加时仅预览前10封。"),
    ] = False,
) -> None:
    """将已准备的拒信草稿作为全新邮件线程发送。"""
    result = send_rejections(
        limit=limit,
        execute=execute,
        parallel_accounts=parallel_accounts,
        sender_email=sender,
    )
    previews = list(result.pop("previews", []))
    print_result(result, title="拒信发送结果")
    if not execute:
        for preview in previews:
            console.print(
                Panel(
                    str(preview["body_text"]),
                    title=str(preview["subject"]),
                    subtitle=(
                        f"{preview['creator_id']} · {preview['mailbox_email']}"
                    ),
                    border_style="magenta",
                )
            )


@followup_app.command("generate-rejections")
def generate_rejections_command(
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=5000)] = 5,
    offset: Annotated[
        int,
        typer.Option("--offset", min=0, help="预览时跳过前 N 位合格达人。"),
    ] = 0,
    workers: Annotated[int, typer.Option("--workers", "-w", min=1, max=8)] = 3,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="将生成结果写入未发送的拒信草稿；不添加时只预览。"),
    ] = False,
) -> None:
    """根据原始邀约和达人真人回信生成有上下文的个性化拒信。"""
    with console.status("GLM 正在阅读达人回信并生成拒信草稿..."):
        result = generate_rejection_drafts(
            limit=limit,
            offset=offset,
            workers=workers,
            apply=apply,
        )
    previews = list(result.pop("previews", []))
    failures = list(result.pop("failures", []))
    print_result(result, title="AI 个性化拒信生成")
    for preview in previews:
        original = (
            f"Subject: {preview.get('original_reply_subject') or ''}\n\n"
            f"{preview.get('original_reply_body') or ''}"
        )
        console.print(
            Panel(
                original,
                title=f"达人原回复 · {preview.get('handle') or preview['creator_id']}",
                border_style="cyan",
            )
        )
        console.print(
            Panel(
                str(preview["body_text"]),
                title=f"{preview['subject']} · {preview['language']}",
                subtitle=f"{preview['creator_id']} · {preview['model']}",
                border_style="magenta",
            )
        )
    if failures:
        print_result({"failures": failures}, title="生成失败")


@followup_app.command("run-rejections")
def run_rejections_command(
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", min=1, max=5000, help="本次最多处理人数。"),
    ] = 1500,
    workers: Annotated[
        int,
        typer.Option("--workers", "-w", min=1, max=8, help="AI 生成并发数。"),
    ] = 8,
    sender: Annotated[
        str,
        typer.Option("--sender", "-s", help="统一拒信发件邮箱。"),
    ] = "andy@coojoy.cn",
    execute: Annotated[
        bool,
        typer.Option("--execute", help="依次生成、补道歉并真实发送。"),
    ] = False,
) -> None:
    """一条命令完成个性化拒信生成、质量补全和安全续发。"""
    if not execute:
        console.print(
            Panel.fit(
                "该命令会调用 AI 并真实发信。确认后请添加 [bold]--execute[/bold]。",
                title="拒信一键流程 · 未执行",
                border_style="yellow",
            )
        )
        return

    with console.status("正在按首次回信时间生成个性化拒信..."):
        generation = generate_rejection_drafts(
            limit=limit,
            offset=0,
            workers=workers,
            apply=True,
        )
    with console.status("正在检查并补全多语言道歉..."):
        apologies = backfill_rejection_apologies(apply=True)
    with console.status(f"正在通过 {sender} 发送拒信..."):
        delivery = send_rejections(
            limit=limit,
            execute=True,
            parallel_accounts=1,
            sender_email=sender,
        )

    print_result(
        {
            "generation_selected": generation.get("selected", 0),
            "generated": generation.get("generated", 0),
            "ai_failed": generation.get("failed", 0),
            "apologies_updated": apologies.get("updated", 0),
            "send_selected": delivery.get("selected", 0),
            "smtp_accepted": delivery.get("accepted", 0),
            "temporary_failed": delivery.get("temporary_failed", 0),
            "hard_failed": delivery.get("hard_failed", 0),
            "delivery_unknown": delivery.get("delivery_unknown", 0),
            "skipped": delivery.get("skipped", 0),
        },
        title="拒信一键流程结果",
    )


@followup_app.command("add-apologies")
def add_rejection_apologies_command(
    limit: Annotated[int | None, typer.Option("--limit", "-n", min=1)] = None,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="只修改尚未发送的 AI 拒信草稿。"),
    ] = False,
) -> None:
    """给已有的多语言 AI 拒信草稿补一条真诚道歉，不重新生成正文。"""
    result = backfill_rejection_apologies(limit=limit, apply=apply)
    previews = list(result.pop("previews", []))
    print_result(result, title="拒信道歉补充")
    if not apply:
        for preview in previews:
            console.print(
                Panel(
                    str(preview["after"]),
                    title=f"{preview['subject']} · {preview['language']}",
                    subtitle=f"{preview.get('handle') or preview['creator_id']}",
                    border_style="magenta",
                )
            )


@followup_app.command("reply")
def reply_followup_thread_command(
    thread_id: Annotated[str, typer.Argument(help="达人 creator_id。")],
    body_file: Annotated[
        Path,
        typer.Option("--body-file", exists=True, file_okay=True, dir_okay=False),
    ],
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="真实回复；不添加时只预览。"),
    ] = False,
) -> None:
    """回复最新达人邮件并保留 In-Reply-To/References 线程头。"""
    result = send_thread_reply(
        thread_id,
        body_text=read_body_file(body_file),
        subject=subject,
        execute=execute,
    )
    print_result(result, title="后续线程回复")


@followup_app.command("list")
def list_followup_threads_command(
    status: Annotated[str | None, typer.Option("--status")] = None,
    needs_reply: Annotated[
        bool | None,
        typer.Option("--needs-reply/--all-reply-states"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=1000)] = 100,
) -> None:
    """查看后续沟通档案；使用 --needs-reply 只看仍需回复的达人。"""
    print_result(
        {
            "threads": list_followup_threads(
                status=status,
                needs_reply=needs_reply,
                limit=limit,
            )
        },
        title="后续沟通档案",
    )


@followup_app.command("messages")
def list_followup_messages_command(
    thread_id: Annotated[str, typer.Argument(help="达人 creator_id。")],
) -> None:
    """按时间查看一个达人档案内保存的全部收发邮件。"""
    print_result(
        {"thread_id": thread_id, "messages": list_thread_messages(thread_id)},
        title="完整邮件往来",
    )


@followup_app.command("review")
def review_followup_replies_command(
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=1000)] = 20,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Analyze and review Andy replies in memory, one by one."""
    rows = pending_review_threads(limit=limit)
    if not rows:
        console.print("[green]No genuine Andy threads currently need a reply.[/green]")
        return
    for index, row in enumerate(rows, start=1):
        latest = row.get("latest_message") or {}
        console.rule(f"[{index}/{len(rows)}] {row['creator_id']}")
        console.print(
            Panel(
                str(latest.get("body_text") or ""),
                title=f"Creator reply · {latest.get('subject') or ''}",
                border_style="cyan",
            )
        )
        with console.status("AI 正在读取完整对话并给出处理建议..."):
            review = analyze_followup_thread(str(row["creator_id"]), row=row)
        if review.get("creator_reply_zh"):
            console.print(
                Panel(
                    str(review["creator_reply_zh"]),
                    title="达人最新回信 · 中文翻译（仅供内部审核）",
                    border_style="blue",
                )
            )
        console.print(
            Panel(
                str(review.get("suggested_reply") or ""),
                title=(
                    f"AI suggestion · {review.get('suggested_action')} · "
                    f"risk {review.get('risk_level')}"
                ),
                subtitle=str(review.get("reason_zh") or ""),
                border_style="magenta",
            )
        )
        if review.get("suggested_reply_zh"):
            console.print(
                Panel(
                    str(review["suggested_reply_zh"]),
                    title="AI建议回件 · 中文翻译（不会发送）",
                    border_style="blue",
                )
            )
        if is_obvious_no_reply(review):
            mark_thread_no_reply(str(row["creator_id"]))
            console.print(
                "[green]AI明确判断为仅感谢或期待后续合作，且没有问题；"
                "状态已更新为 replied，无需回复。[/green]"
            )
            continue
        action = Prompt.ask(
            "Decision",
            choices=["approve", "revise", "no_reply", "escalate", "skip", "quit"],
            default="skip",
        )
        if action == "quit":
            break
        if action == "approve" and not str(review.get("suggested_reply") or "").strip():
            console.print(
                "[yellow]AI 没有建议回复正文；请选择 no_reply、revise 或 skip。[/yellow]"
            )
            continue
        edited_reply = None
        final_subject = str(review.get("suggested_subject") or "") or None
        if action == "revise":
            current_reply = str(review.get("suggested_reply") or "")
            current_subject = final_subject
            while True:
                instruction = Prompt.ask("修改要求（中文或英文均可）").strip()
                if not instruction:
                    console.print("[yellow]没有输入修改要求，本封暂不处理。[/yellow]")
                    action = "skip"
                    break
                with console.status("AI 正在结合完整对话和你的要求重新编辑..."):
                    revised = revise_followup_reply(
                        str(row["creator_id"]),
                        instruction=instruction,
                        current_analysis=review,
                        current_subject=current_subject,
                        current_reply=current_reply,
                    )
                current_subject = revised["subject"] or current_subject
                current_reply = revised["reply"]
                console.print(
                    Panel(
                        current_reply,
                        title=f"AI revised reply · {current_subject or ''}",
                        border_style="green",
                    )
                )
                if revised.get("reply_zh"):
                    console.print(
                        Panel(
                            revised["reply_zh"],
                            title="AI重写回件 · 中文翻译（不会发送）",
                            border_style="blue",
                        )
                    )
                revision_decision = Prompt.ask(
                    "确认这版",
                    choices=["approve", "revise", "cancel"],
                    default="approve",
                )
                if revision_decision == "revise":
                    continue
                if revision_decision == "cancel":
                    action = "skip"
                    break
                action = "edit"
                edited_reply = current_reply
                final_subject = current_subject
                break
        if action == "no_reply":
            mark_thread_no_reply(str(row["creator_id"]))
            console.print("[green]状态已更新为 replied，不发送邮件。[/green]")
            continue
        if action in {"escalate", "skip"}:
            console.print("[yellow]本封未发送，仍保留在 need_reply。[/yellow]")
            continue
        if action in {"approve", "edit"} and execute:
            body = (
                edited_reply
                if action == "edit"
                else str(review.get("suggested_reply") or "")
            )
            print_result(
                send_thread_reply(
                    str(row["creator_id"]),
                    body_text=body,
                    subject=final_subject,
                    execute=True,
                ),
                title="Approved reply sent",
            )
        else:
            console.print(
                "[yellow]预览模式下不保存AI建议，也不发送；确认发送请使用 --execute。[/yellow]"
            )


@followup_app.command("stats")
def followup_stats_command() -> None:
    """Show Andy rejection statistics separately from cooperation campaigns."""
    print_followup_stats(collect_followup_stats())


@data_app.command("init")
def initialize_data() -> None:
    """创建 creator_mail schema 和数据表。"""
    initialize_schema()
    console.print("[green]数据库结构已就绪。[/green]")


@data_app.command("sync")
def sync_creators(
    data_version: Annotated[str, typer.Option("--version", "-v")],
    batch_size: Annotated[int, typer.Option("--batch-size", min=1)] = 600,
    seed: Annotated[str, typer.Option("--seed")] = "creator-outreach-v1",
    page_size: Annotated[int, typer.Option("--page-size", min=1)] = 1_000,
    max_creators: Annotated[int | None, typer.Option("--max", min=1)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """初始化数据库并从 Milvus 增量同步达人。"""
    initialize_schema()
    with console.status(f"正在同步达人数据 · 版本 {data_version}"):
        result = import_creator_snapshot(
            data_version=data_version,
            batch_size=batch_size,
            seed=seed,
            source_page_size=page_size,
            max_creators=max_creators,
        )
    print_result(result, title="达人同步", json_output=json_output)


@form_app.command("link")
def form_link(
    creator_id: Annotated[str, typer.Argument(help="Internal creator_id.")],
) -> None:
    """生成一条 Creator Pass 签名链接。"""
    settings = get_settings()
    url = build_form_url(
        creator_id,
        base_url=settings.form_public_base_url,
        secret=settings.form_token_secret.get_secret_value(),
        ttl_days=settings.form_token_ttl_days,
    )
    console.print(Panel.fit(f"[bold]{creator_id}[/bold]\n[link={url}]{url}[/link]", title="Creator Pass"))


@form_app.command("serve")
def serve_form(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65_535)] = 8_000,
) -> None:
    """启动本地 Creator Pass H5 和 API。"""
    import uvicorn

    uvicorn.run(
        "creator_mail_platform.creator_pass.api:app",
        host=host,
        port=port,
        reload=False,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        app()
    except (ValueError, RuntimeError) as exc:
        console.print(Panel.fit(str(exc), title="无法继续", border_style="red"))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

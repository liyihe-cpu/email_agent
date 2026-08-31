# Creator Mail Platform

COOJOY 达人建联、Creator Pass、回信分析、拒信和后续多轮沟通工具。

所有命令都在项目目录的 PowerShell 中运行：

```powershell
Set-Location C:\Users\EDY\Desktop\baozai_work\creator_mail_platform
uv run creator-mail --help
```

## 1. 数据保存边界

PostgreSQL 的 `creator_mail` schema 当前只有三张表：

1. `activity_contacts`：每位达人一行，保存画像、Batch、建联邮件、发送状态、
   最新有效回信和 Creator Pass 表单。
2. `campaign_response_profiles`：每位进入后续流程的达人一行，以 JSON 数组保存
   初次建联、初次回复、拒信和拒信后的多轮收发上下文。
3. `mailbox_checkpoints`：每个公司邮箱一行，记录 IMAP 已经读取到哪里。

数据库不只是保存收发状态，还保存实际 Subject/正文、Message-ID、Brief、表单
JSON、首次回复分析和后续完整对话。为了保持结构精简，`followup review` 现场生成
的 AI 建议、中文翻译和修改意见只存在于当前 Terminal 内存中。只有确认发送的
邮件才追加到对话；确认无需回复时只更新现有 `status`。

## 2. 安全规则与首次配置

- 项目只读 Milvus `creator_profiles`，只写 PostgreSQL `creator_mail` schema。
- `.env`、`runtime/smtp_accounts.json` 和账号密码不提交 Git。
- `run-plan` 不加 `--execute` 只准备和预览。
- 真实发信还要求 `.env` 中 `SMTP_EXECUTION_ENABLED=true`。
- `smtp_accepted` 只表示 SMTP 服务端接受，不等于最终进入收件箱。
- `sending + smtp_result_unknown` 禁止自动重发，必须人工核对。
- 重跑命令依靠数据库状态续跑，不依靠终端是否一直开着。

首次安装：

```powershell
uv sync
uv run creator-mail data init
```

项目根目录 `.env` 的主要配置：

```dotenv
TARGET_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/creator_projects
MILVUS_URI=http://127.0.0.1:19530
MILVUS_COLLECTION=creator_profiles

SMTP_ACCOUNTS_FILE=runtime/smtp_accounts.json
SMTP_EXECUTION_ENABLED=false

IMAP_POLL_SECONDS=30
MAX_INBOUND_BODY_CHARS=16000

LLM_API_BASE_URL=https://api.siliconflow.cn/v1
LLM_API_KEY=
LLM_MODEL=deepseek-ai/DeepSeek-V4-Flash
FOLLOWUP_LLM_MODEL=zai-org/GLM-5.2
FOLLOWUP_MAILBOX_EMAIL=andy@coojoy.cn

FORM_PUBLIC_BASE_URL=https://coojoy.cn/creator
FORM_TOKEN_SECRET=
FORM_TOKEN_TTL_DAYS=90
```

发件与收件账号统一配置在本地 `runtime/smtp_accounts.json`，不要把密码写进
README、代码或聊天记录。

### 同事首次接手

Gitee 只保存源码。为了让同事连接同一套数据库、AI、邮箱和 H5，同事 Clone
项目后，还需要通过公司认可的加密渠道单独取得以下三个本地文件：

```text
.env
runtime/smtp_accounts.json
runtime/reply_analysis_cache.json
```

- `.env`：数据库、硅基流动和 Creator Pass 签名配置；
- `smtp_accounts.json`：cooperation01-10 与 Andy 的 SMTP/IMAP、速率和并发；
- `reply_analysis_cache.json`：首次回信 AI 分析缓存，避免换电脑后重复调用 AI 或
  产生不同的历史分析结果。

接收后放回相同相对路径，再运行：

```powershell
uv sync
uv run creator-mail stats
uv run creator-mail followup stats
```

两个统计命令都能正常连接后再开始收发。同一个数据库环境只运行一个全邮箱
`receive --forever`；多个同事不要同时启动重复的常驻收信进程。

## 3. 同步新增达人

只有从 Milvus 同步达人时需要 SSH 隧道。发信、收信、统计、表单和拒信只连接
PostgreSQL，不依赖 Milvus 隧道。

打开独立隧道窗口：

```powershell
Start-Process powershell.exe -ArgumentList '-NoExit', '-Command', 'ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -N -L 19530:127.0.0.1:19530 root@YOUR_MILVUS_SERVER_IP'
```

在新窗口输入服务器密码并保持窗口打开，然后检查：

```powershell
Test-NetConnection 127.0.0.1 -Port 19530
```

看到 `TcpTestSucceeded : True` 后同步：

```powershell
uv run creator-mail data sync `
  --version 0.1 `
  --batch-size 600 `
  --seed creator-outreach-v1
```

同步以 `creator_id` 为主键增量更新画像，不重置已发送状态；Batch 按稳定随机
顺序每 600 人一组。

## 4. 三种建联策略

| 策略 | Batch | 内容 |
|---|---:|---|
| S1 | 1-25，以及 76 以后 | 项目 Brief 邀约，需要回信 |
| S2 | 26-50 | Creator Pass 专属链接 |
| S3 | 51-75 | 项目 Brief + Creator Pass 链接 |

策略由运行命令指定，数据库不重复保存 `strategy_no`；统计时根据 `batch_no`
推导。Batch 76 以后当前统一按 S1 运行。

## 5. 建联发件完整流程

### 5.1 单独验证某个 Batch（可选）

`run-plan` 会自动验证；只有排查时才需单独运行：

```powershell
uv run creator-mail validate --batch 186
uv run creator-mail validate --batch 186 --skip-mx
```

### 5.2 每天 10 个邮箱各发 3 个 Batch

只修改 `$firstBatch`。例如从 Batch 186 开始处理 30 个 Batch：

```powershell
$firstBatch = 186
$batchCount = 30
$jobs = (0..($batchCount - 1) | ForEach-Object {
  $batchNo = $firstBatch + $_
  $accountNo = ($_ % 10) + 1
  "$batchNo=cooperation$($accountNo.ToString('00'))@coojoy.cn"
}) -join ","
```

先运行完整 dry-run。它会验证、补齐缺失文案、绑定邮箱和展示摘要，但不发送：

```powershell
uv run creator-mail run-plan `
  --strategy s1 `
  --jobs $jobs `
  --workers 8 `
  --parallel-accounts 10
```

确认后无人值守正式发送：

```powershell
uv run creator-mail run-plan `
  --strategy s1 `
  --jobs $jobs `
  --workers 8 `
  --parallel-accounts 10 `
  --execute
```

重复执行会跳过 `smtp_accepted`、`hard_failed` 和无效达人，继续 `pending` 或
`temporary_failed`。AI 只补齐缺失正文，不覆盖已有正文。

### 5.3 只运行一个 Batch

```powershell
uv run creator-mail run-campaign `
  --strategy s1 `
  --batch 186 `
  --sender cooperation01@coojoy.cn `
  --workers 8 `
  --dry-run
```

去掉 `--dry-run` 后，程序会在 Terminal 再询问一次是否发送。

### 5.4 网络异常后的 S1 自动续跑

明确写出本次范围，不依赖代码中的历史默认值：

```powershell
uv run creator-mail resume-s1 `
  --batch-from 186 `
  --batch-to 215 `
  --workers 8 `
  --parallel-accounts 10 `
  --retry-seconds 300 `
  --execute
```

网络恢复后会继续安全可重试邮件。按 `Ctrl+C` 可安全停止，之后原样重跑即可。

## 6. 建联收信、查看与统计

### 6.1 收信

```powershell
# 所有已启用邮箱收取一次
uv run creator-mail receive

# 持续轮询所有邮箱
uv run creator-mail receive --forever

# 只读取一个邮箱
uv run creator-mail receive --mailbox cooperation01@coojoy.cn
```

一个环境中只保留一个全邮箱 `--forever` 进程。IMAP checkpoint 保证已经处理过
的 UID 不会反复入库。

### 6.2 查看建联回信

默认只显示真人回复：

```powershell
uv run creator-mail replies --batch-from 186 --batch-to 215 --limit 100
```

同时查看自动回复、退信和退订：

```powershell
uv run creator-mail replies `
  --batch-from 186 `
  --batch-to 215 `
  --all-types `
  --limit 200
```

### 6.3 建联与表单统计

```powershell
# 全部建联活动
uv run creator-mail stats

# 一个 Batch
uv run creator-mail stats --batch 186

# 一段 Batch
uv run creator-mail stats --batch-from 186 --batch-to 215

# 三组历史实验
uv run creator-mail stats --batch-from 1 --batch-to 25
uv run creator-mail stats --batch-from 26 --batch-to 50
uv run creator-mail stats --batch-from 51 --batch-to 75

# 机器可读 JSON
uv run creator-mail stats --batch-from 186 --batch-to 215 --json
```

`stats` 直接从 PostgreSQL 聚合，不调用 AI。输出包括：

- SMTP 接受、待发送、临时失败、永久失败和预检查无效；
- 真人回复、自动回复、退信、退订及其占成功发送的比例；
- Creator Pass 提交、类目填写、联系方式填写及其比例；
- 同时回信并提交表单的人数与重叠率；
- 按策略和发件邮箱拆分的进度。

## 7. 首次建联回信 AI 分析与 Excel

该命令读取真人回信和 Creator Pass 提交者，只对尚未分析的首次真人回信调用
AI，并覆盖更新业务 Excel：

```powershell
uv run creator-mail export-active-creators `
  --batch-from 1 `
  --batch-to 215 `
  --workers 8
```

默认输出 `一期_已响应达人及回信分析.xlsx`，包含达人主页、粉丝数、去信/回信、
意图、感兴趣项目、项目类目、中文摘要、表单和后续沟通状态。Excel 是业务快照，
不是实时数据库，也不会反向控制发信。

每日基础顺序：

```powershell
uv run creator-mail receive
uv run creator-mail stats
uv run creator-mail export-active-creators --batch-from 1 --batch-to 215 --workers 8
```

## 8. Creator Pass H5

签名 Token 对应唯一 `creator_id`；达人不需重新输入邮箱或 handle。点击加入和
可选信息直接回写 `activity_contacts` 同一行。

```powershell
# 本地启动 H5/API
uv run creator-mail form serve

# 生成某位达人的专属链接
uv run creator-mail form link CREATOR_ID
```

表单统计直接查看 `creator-mail stats` 的 `form_engagement` 和 `cross_channel`。

## 9. Andy 拒信：准备、生成与发送

拒信只处理符合当前筛选的 S1/S3 真人积极回复者；仅提交 Creator Pass 的人不会
自动进入拒信队列。

当前拒信口径使用品牌侧项目进度：品牌方已经完成本轮达人名单，当前可用名额已
满。只有业务上下文明示品牌缩减了预算、范围或名额时，AI 才可以写该原因；不得
自行编造预算削减。

### 9.1 同步首次回信和分析

先运行第 7 节的 Excel/首次回信分析，再预览和写入后续档案：

```powershell
uv run creator-mail followup prepare --batch-from 1 --batch-to 215

uv run creator-mail followup prepare `
  --batch-from 1 `
  --batch-to 215 `
  --apply
```

`prepare` 把初次建联、首次回复、表单事件、达人画像和首次回复分析整理到
`campaign_response_profiles`。已开始后续沟通的线程不会被覆盖。

### 9.2 生成个性化拒信

```powershell
# 抽样预览，不写数据库
uv run creator-mail followup generate-rejections --limit 10 --workers 3

# 生成并保存最早回复的 1500 人
uv run creator-mail followup generate-rejections `
  --limit 1500 `
  --workers 8 `
  --apply
```

历史草稿若只缺少道歉句，可先预览再补齐，不重新生成全文：

```powershell
uv run creator-mail followup add-apologies --limit 10
uv run creator-mail followup add-apologies --apply
```

### 9.3 预览与发送

```powershell
# 预览 10 封
uv run creator-mail followup send-rejections `
  --sender andy@coojoy.cn `
  --limit 10

# 正式发送已生成的 1500 封
uv run creator-mail followup send-rejections `
  --sender andy@coojoy.cn `
  --limit 1500 `
  --execute
```

已经完成 `prepare` 后，也可一条命令执行“生成 → 补道歉 → 发送”：

```powershell
uv run creator-mail followup run-rejections `
  --limit 1500 `
  --workers 8 `
  --sender andy@coojoy.cn `
  --execute
```

重跑会跳过已经 `smtp_accepted` 的拒信，只处理安全可续跑的草稿。

## 10. Andy 拒信收信、统计、分析与回复

### 10.1 收取 Andy 新邮件

```powershell
uv run creator-mail receive --mailbox andy@coojoy.cn
uv run creator-mail receive --mailbox andy@coojoy.cn --forever
```

只有明确回复 Andy 的 `rejection_notice` 或 `followup_reply` Message-ID 的邮件才
进入拒信队列；`cooperation01-10` 的建联回信不会混入。

### 10.2 拒信统计

```powershell
uv run creator-mail followup stats
```

它只统计 Andy：已发拒信、回复人数、回复率、重复回复、无需再回、已人工回复和
仍为 `need_reply` 的线程。

### 10.3 逐封 AI 审核和回复

```powershell
# 只预览，不发信
uv run creator-mail followup review --limit 20

# 人工确认 approve/revise 后立即回复
uv run creator-mail followup review --limit 20 --execute
```

每封依次展示达人原文、中文翻译、AI 原语言建议、建议中文翻译，再由人工选择
`approve / revise / no_reply / escalate / skip / quit`。中文不会发给达人，也不
写入数据库。`no_reply` 改为 `replied`；`skip/escalate` 保留 `need_reply`。
一轮 20 条完成后原样重跑，会继续剩余最早的 20 条。

### 10.4 查看线程或手工回复

```powershell
# 待回复线程
uv run creator-mail followup list --needs-reply --limit 100

# 完整收发上下文
uv run creator-mail followup messages CREATOR_ID

# 使用 UTF-8 reply.txt 预览
uv run creator-mail followup reply CREATOR_ID --body-file .\reply.txt

# 确认后回复
uv run creator-mail followup reply CREATOR_ID `
  --body-file .\reply.txt `
  --execute
```

程序自动保留 `In-Reply-To` 和 `References`，继续同一邮件线程。

## 11. 每日推荐顺序

Terminal A 持续收信：

```powershell
uv run creator-mail receive --forever
```

Terminal B 按顺序运行：

```text
1. run-plan dry-run 检查当天 30 个 Batch
2. run-plan --execute 正式建联
3. stats 查看建联、回信和表单数据
4. export-active-creators 更新首次回信 AI 分析与 Excel
5. followup prepare --apply 同步新增响应达人
6. followup run-rejections 或分步生成、预览、发送拒信
7. receive --mailbox andy@coojoy.cn 收取拒信回信
8. followup stats 查看 Andy 指标
9. followup review --limit 20 --execute 分批人工确认回复
10. 再运行 export-active-creators 更新业务 Excel 快照
```

## 12. 数据库字段和状态字典

### 12.1 `activity_contacts`

身份与画像：

| 字段 | 含义 |
|---|---|
| `creator_id` | 主键；目前通常是 `platform:handle` |
| `email` | 标准化收件邮箱 |
| `batch_no` | 稳定分配的 600 人 Batch |
| `platform` / `handle` | 平台和达人 handle |
| `follower_count` | 从 Milvus 同步的粉丝数 |
| `country` / `language` | 国家和主要语言 |
| `primary_category` | 主类目 |
| `profile_bio` / `analysis_note` | 原始画像和分析摘要 |
| `source_data_version` | 最近一次同步版本 |

建联发送：

| 字段 | 含义 |
|---|---|
| `sender_email` | 实际分配的 cooperation 发件箱 |
| `send_status` / `email_status` | SMTP 流程状态与邮箱可用性 |
| `status_reason` / `smtp_code` | 具体原因和最近明确 SMTP 状态码 |
| `retry_count` / `sent_at` | 重试次数与 SMTP 接受时间 |
| `brief_code` | 主要推荐项目编码 |
| `offered_brief_codes` | 实际提供过的全部项目编码 JSON 数组 |
| `sent_subject` / `sent_body_text` | 实际标题与可审计纯文本正文 |
| `outbound_message_id` | 初次建联 Message-ID |

Creator Pass 与回信：

| 字段 | 含义 |
|---|---|
| `form_submitted_at` | 加入或最近保存表单的时间 |
| `form_response_json` | 类目、联系方式、授权及奖励拆分 |
| `reward_status` | 奖励生命周期 |
| `reply_type` / `reply_count` | 最新邮件分类与累计有效邮件数 |
| `last_reply_at` | 最新有效邮件时间 |
| `reply_subject` / `reply_body_text` | 最新有效邮件标题与正文 |

`activity_contacts` 只保留最新有效回信正文；冻结到后续档案的首次回信不会被
Andy 多轮回复覆盖。

### 12.2 `send_status`

| 值 | 含义 | 自动重试 |
|---|---|---|
| `pending` | 尚未发送 | 是 |
| `sending` | 已领取发送任务，或结果待确认 | 否，先看原因 |
| `smtp_accepted` | SMTP 服务端已接受 | 否 |
| `temporary_failed` | 4xx 或明确临时失败 | 是 |
| `hard_failed` | 5xx 或明确永久失败 | 否 |

### 12.3 `email_status`

| 值 | 含义 |
|---|---|
| `unknown` | 尚无真实投递/回复证据；通过预检查后也可能仍是 unknown |
| `possibly_usable` | SMTP 已接受，但尚无回信证据 |
| `usable` | 收到真人回复或自动回复，证明邮箱可收信 |
| `invalid` | 格式、路由、永久拒绝或硬退信明确无效 |
| `suppressed` | 对方退订，禁止继续营销触达 |

`usable` 不等于真人回复。真人和自动回复都会证明邮箱可用；真人回复必须筛选
`reply_type = 'human_reply'`，所以 usable 数量和真人回复数不同是正常的。

### 12.4 常见 `status_reason`

| 值 | 含义 |
|---|---|
| `syntax_passed` / `mail_route_passed` | 格式通过 / 邮件路由通过 |
| `invalid_format` / `invalid_domain` | 格式或域名明确无效 |
| `smtp_in_progress` | SMTP 正在处理 |
| `smtp_result_unknown` | 连接中断，结果不确定，禁止自动重发 |
| `smtp_temporary_error` | SMTP 临时失败 |
| `mailbox_not_found` / `smtp_rejected` | SMTP 永久拒绝 |
| `hard_bounce` / `soft_bounce` | 硬退信 / 软退信 |
| `unsubscribed` | 对方退订 |

### 12.5 `reply_type`

`human_reply`、`auto_reply`、`hard_bounce`、`soft_bounce`、`unsubscribe`。

### 12.6 Creator Pass 状态

`reward_status`：

- `NULL`：尚未提交；
- `pending_first_campaign`：已提交，等待首次符合条件的合作完成；
- `earned`：已满足条件；
- `paid`：已支付；
- `ineligible`：不符合条件。

`form_response_json` 主要键：

```text
form_version, joined, joined_at
collaboration_categories, collaboration_details, other_category
additional_contact_method, additional_contact_value, contact_consent
optional_profile_completed, preferences_updated_at
reward_breakdown_usd, reward_total_usd, reward_condition
```

`optional_profile_completed=true` 只表示填写了任意可选资料，不等于添加了联系方式。
联系方式奖励应看 `reward_breakdown_usd.additional_contact > 0`。

### 12.7 `campaign_response_profiles`

| 字段 | 含义 |
|---|---|
| `creator_id` | 主键，与 `activity_contacts` 对应 |
| `batch_no` | 原建联 Batch |
| `mailbox_email` / `recipient_email` | 后续公司邮箱与达人邮箱 |
| `messages_json` | 按时间保存完整后续收发上下文 |
| `analysis_json` | 精简的达人画像与首次回复分析 |
| `status` | 当前线程状态 |
| `last_message_at` | 最近邮件或事件时间 |
| `created_at` / `updated_at` | 创建和更新时间 |

`analysis_json` 当前只应包含 `creator_profile` 和 `initial_response`，不要写入每日
AI 审核缓存。

后续线程 `status`：

| 值 | 含义 |
|---|---|
| `draft` | 拒信草稿待发，或临时失败待续跑 |
| `replied` | 当前无需我方动作：已回复等待对方，或确认无需再回 |
| `need_reply` | Andy 收到真实后续回信，需要处理 |
| `review` | 投递结果不确定，需人工核对 |
| `closed` | 永久失败、退订、硬退信或线程关闭 |

### 12.8 `messages_json`

每封记录通常含：

```text
source_key, direction, message_kind
message_id, in_reply_to, references
from_email, to_email, subject, body_text
occurred_at, delivery_status, semantic_intent, metadata
```

`message_kind`：

- `initial_outreach`：初次建联去信；
- `initial_creator_reply`：冻结的首次真人回复；
- `creator_pass_submission`：Creator Pass 提交事件；
- `rejection_notice`：Andy 独立拒信；
- `creator_reply`：达人对拒信或后续信的新回信；
- `followup_reply`：我方人工确认后的多轮回复。

`delivery_status` 常见值：`draft`、`sending`、`smtp_accepted`、
`temporary_failed`、`hard_failed`、`delivery_unknown`、`received`。

### 12.9 `mailbox_checkpoints`

| 字段 | 含义 |
|---|---|
| `mailbox_email` | 公司收件邮箱主键 |
| `uidvalidity` | IMAP UID 世代 |
| `last_uid` | 已成功处理到的最后一封 UID |

它只是收信游标，不是邮件历史表。

## 13. 后期数据库导出

建联与表单明细：

```sql
SELECT
    creator_id, platform, handle, follower_count, email,
    country, language, primary_category, source_data_version,
    batch_no, sender_email, send_status, email_status, status_reason,
    smtp_code, sent_at, brief_code, offered_brief_codes,
    sent_subject, sent_body_text,
    form_submitted_at, form_response_json, reward_status,
    reply_type, reply_count, last_reply_at, reply_subject, reply_body_text
FROM creator_mail.activity_contacts
ORDER BY batch_no, creator_id;
```

拒信与多轮沟通应保留 JSON 原文，避免丢失上下文：

```sql
SELECT
    creator_id, batch_no, mailbox_email, recipient_email,
    status, last_message_at, created_at, updated_at,
    analysis_json, messages_json,
    jsonb_array_length(messages_json) AS message_count
FROM creator_mail.campaign_response_profiles
ORDER BY last_message_at NULLS LAST, creator_id;
```

状态组合：

```sql
SELECT email_status, send_status, COUNT(*) AS count
FROM creator_mail.activity_contacts
WHERE batch_no BETWEEN 186 AND 215
GROUP BY email_status, send_status
ORDER BY count DESC;
```

业务统计优先使用内置命令，因为它已统一分母和比例口径：

```powershell
uv run creator-mail stats --batch-from 186 --batch-to 215 --json
uv run creator-mail followup stats
```

## 14. 常见检查

```powershell
uv run creator-mail --help
uv run creator-mail followup --help
uv run creator-mail stats
uv run creator-mail followup list --needs-reply --limit 100
uv run creator-mail followup stats
```

排查时先看数据库状态和 Terminal 输出，不要因为终端暂时没动就启动第二个真实
发送进程。遇到 `smtp_result_unknown`，先人工核对发件箱再决定后续处理。

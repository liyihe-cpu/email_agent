# Brief configuration

`briefs.json` 是 S1 和 S3 共用的真实项目来源。正式配置每个项目时建议提供：

- `brief_code`：邮件、数据库和回信统一使用的稳定公开项目码
- `enabled`：是否参与本轮匹配
- `name`：隐去真实品牌后的对外项目名
- `short_name`：邮件紧凑项目列表使用的短名称
- `brand_alias`：邮件中允许展示的品牌称呼
- `subjects`：该项目两条英文 A/B 标题，可使用 `{handle}`
- `categories`：适合的达人类目
- `regions`：目标国家或地区
- `platforms`：可选的平台硬过滤；未配置表示所有平台
- `compensation`：预算或报价范围
- `deliverables`：交付内容
- `product_links`：可公开的产品链接
- `timeline`：确认和交付时间
- `requirements`：授权、Bio Link、平台等要求
- `compact_requirement`：紧凑列表中展示的一句关键要求

当前稳定项目码：

| 项目码 | 项目 | 状态 |
|---|---|---|
| `BEAUTY-01` | 全球美妆 | 启用 |
| `MIC-01` | 创作者麦克风 | 启用 |
| `ECOM-01` | 全球电商 App | 启用 |
| `BABY-01` | 母婴纸尿裤 | 启用 |
| `GAME-01` | RPG 游戏素材 | 停用，等待新档期 |
| `AI-01` | 美国 AI 生产力工具 | 启用，仅美国 YouTube |
| `CAM-01` | 便携相机 | 启用，唯一保守项目 |
| `OUTDOOR-01` | 美国户外天幕 | 启用，仅美国 |
| `FASHION-01` | 全球女装 | 启用 |

`AI-01` 作为后续 AI 类项目的基础配置。新增 AI 项目时继续使用独立的
`brief_code`，并统一设置 `primary_category: "AI"`。达人明确回复某个 AI
项目后，活跃达人报表会在 `project_primary_categories` 中记录 `AI`；达人原始
`primary_category` 保持不变，避免覆盖其内容画像。

AI 为每个地区符合的非保守项目分别给出语义匹配分，本地代码稳定选择最高分；
最高分低于 `0.65` 时不强行选择。`0.65` 是首轮保守业务阈值，并非历史数据训练结果，
后续应结合人工复核和回复率校准。`CAM-01` 始终作为第二重点项目，或在没有
合格主项目时单独成为重点项目。其余启用且地区符合的项目以
`项目码 · 短名称 · 报酬 · 关键要求` 的紧凑形式展示。

预算、交付、授权和时间只从 `briefs.json` 的结构化字段渲染，AI 不能改写。
每个 Brief 的两条 Subject 以及英文衔接句、回复 CTA 都根据 `creator_id` 稳定选择；
重复生成同一达人不会无故换文案。小语种固定框架保持一套。

物料中日期不完整或已经需要重新确认的项目应设置 `enabled=false`，确认新档期后再启用。

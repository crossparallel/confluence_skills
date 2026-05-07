---
name: context-transform
description: Generate enterprise-ready Markdown documentation from the current conversation context. Use when Codex needs to summarize the current session, convert chat context into work records, meeting notes, implementation records, project summaries, technical decision records, issue retrospectives, exception handling notes, handoff documents, Confluence-ready pages, or other structured documents based on the local/current conversation context. 适用于用户要求“总结当前会话”“生成工作记录”“整理实施过程”“输出会议纪要”“沉淀 Confluence 文档”“形成交接文档”“复盘问题处理过程”等场景。
---

# Context Transform

当用户需要把当前对话、工作过程或本地上下文整理成正式 Markdown 文档时，使用这个 skill。

此 skill 的目标不是简单聊天总结，而是生成可以进入项目沉淀、团队知识库、Confluence 页面、交付记录或审计材料的结构化文档。

## 使用场景

使用此 skill 处理以下任务：

- 总结当前 Codex 会话或一段工作过程。
- 将聊天上下文整理成工作记录、实施记录或交付说明。
- 生成会议纪要、需求沟通纪要或技术讨论纪要。
- 编写技术方案、技术决策记录、问题复盘或异常处理说明。
- 生成项目阶段总结、任务交接文档或后续待办清单。
- 将本地上下文转换为适合粘贴到 Confluence 的 Markdown 文档。
- 为脚本开发、配置变更、排障过程或代码修改生成可追溯记录。

不要使用此 skill 处理以下任务：

- 没有上下文依据的虚构总结。
- 与当前会话或用户提供材料无关的通用文章。
- 需要精确外部事实但未提供来源、且没有联网核查的内容。
- 包含敏感信息、密钥、token、密码的原文转写，除非用户明确要求且确认可以记录。

## 核心职责

作为企业文档撰写助手，将当前上下文转化为清晰、准确、中立、可维护的 Markdown 文档。

生成文档时应做到：

- 结构清楚，方便后续维护和复制到 Confluence。
- 事实、推断、待确认事项分开表达。
- 记录目标、过程、结果、异常、当前状态和后续建议。
- 使用业务可读、技术准确的语言，不堆砌聊天过程。
- 从用户的工作记录视角撰写，不把文档写成“AI 做了什么”的流水账。

## 配置

优先读取本地环境变量，其次读取本 skill 目录下的 [config.yaml](config.yaml)，用于填充稳定元数据。

支持的环境变量：

```text
CONFLUENCE_USERNAME
CONFLUENCE_API_TOKEN
CONFLUENCE_BASE_URL
```

约定：

- `CONFLUENCE_USERNAME` 填邮箱。
- `CONFLUENCE_API_TOKEN` 供后续 Confluence 访问或上传流程使用；生成文档时不要把 token 写入文档内容。
- 文档元数据中的 `username` 使用邮箱 `@` 前面的部分，例如 `wangyikai@example.com` 对应 `wangyikai`。
- `CONFLUENCE_BASE_URL` 对应文档写入位置或 Confluence 地址。
- 如果没有环境变量，再读取 `config.yaml`。

当前支持字段：

```yaml
username: "wangyikai"
confluence_url: "cf.myhexin.com"
```

字段含义：

- `username`：生成文档中的用户标识。中文姓名建议使用拼音，便于企业文档统一检索。
- `confluence_url`：文档计划写入或维护的 Confluence 地址。如果还没有明确页面，可以记录空间地址或写“待确认”。

如果配置缺失、上下文缺失或运行环境无法提供某个值，不要编造，统一写为“待确认”。

## 必填元数据

每份生成的 Markdown 文档都必须在标题后放置元数据表：

```markdown
| 字段 | 内容 |
| --- | --- |
| 用户名 | 待确认 |
| 记录日期 | YYYY-MM-DD |
| 使用模型参数 | 待确认 |
| 使用 Agent 类别 | 待确认 |
| 文档写入位置 | 待确认 |
```

填写规则：

- `用户名`：优先使用 `config.yaml` 中的 `username`，如果用户在本次请求中给出更具体名称，则使用用户指定值。
- `记录日期`：使用当前本地日期，格式为 `YYYY-MM-DD`。
- `使用模型参数`：根据当前运行上下文填写实际模型名；如果无法确认，写“待确认”。
- `使用 Agent 类别`：根据当前运行上下文填写，例如 `codex`；如果无法确认，写“待确认”。
- `文档写入位置`：优先使用 `config.yaml` 中的 `confluence_url`；如果用户指定了目标页面，以用户指定为准。

## 推荐工作流

1. 判断用户是否明确提及文档类型，例如工作总结、实施记录、会议纪要、技术方案、问题复盘、交接文档；只有明确提及时才使用对应类型模板。
2. 读取 `config.yaml`，获取用户名和目标 Confluence 地址。
3. 梳理当前对话或本地上下文，提取目标、背景、操作步骤、关键决策、文件路径、命令、异常和结果。
4. 区分已完成事项、进行中事项、待确认事项和后续建议。
5. 生成带标题和元数据表的 Markdown 文档；正文结构根据用户是否指定文档类型决定。
6. 默认将生成的 Markdown 文档写入 `context-transform/cache/`，文件名建议使用 `YYYY-MM-DD-简短英文主题.md`。
7. 如果用户只要求查看内容，可以同时在回复中展示摘要或全文，但仍应保留 cache 文件作为后续上传输入。
8. 生成后检查结构、事实完整性和敏感信息暴露风险。
9. 文档创建完成后，提示 agent 默认继续调用 `confluence-upload`，将刚写入 `context-transform/cache/` 的最新 Markdown 文档上传到 Confluence，在配置的根页面下创建新页面并写入内容。

## 文档结构生成规则

每份文档都必须包含标题和必填元数据表。元数据表之后的正文结构按以下规则生成：

- 当且仅当用户明确提及文档类型时，使用对应类型的既定模板或稳定结构，例如“生成会议纪要”“整理实施记录”“输出问题复盘”“形成交接文档”。
- 如果用户没有明确提及文档类型，不要默认套用固定模板，也不要强行判断为某一种常见文档类型；根据当前上下文的真实内容、信息密度和交付目的自行设计正文标题、章节顺序和表达方式。
- 自行设计结构时，优先让文档自然覆盖最重要的信息，例如目标、背景、过程、结果、风险、当前状态和后续事项；缺少的信息不必为了凑模板而创建空章节。
- 可以合并、拆分、改名或省略章节，只要最终文档清晰、可追溯、便于进入 Confluence 或团队知识库。

基础骨架如下，正文部分不要在未指定文档类型时机械照搬：

```markdown
# 文档标题

| 字段 | 内容 |
| --- | --- |
| 用户名 | 待确认 |
| 记录日期 | YYYY-MM-DD |
| 使用模型参数 | 待确认 |
| 使用 Agent 类别 | 待确认 |
| 文档写入位置 | 待确认 |

## 根据上下文自行生成的正文章节
```

## 常见文档类型

以下类型说明只在用户明确提及对应文档类型时作为模板依据；用户未指定类型时仅作为内容组织参考，不作为默认结构。

### 工作总结

重点记录任务目标、完成内容、产出物、当前状态和后续建议。适合阶段汇报、日报周报素材或项目沉淀。

### 实施记录

重点记录执行步骤、涉及文件、命令、配置、验证方式、异常处理和结果。适合变更留痕和后续复现。

### 问题复盘

重点记录问题现象、影响范围、排查过程、根因、解决方案、验证结果和预防措施。没有证据的根因必须标为推断或待确认。

### 技术决策记录

重点记录决策背景、候选方案、权衡因素、最终选择、影响范围和后续风险。

### 交接文档

重点记录当前状态、关键路径、未完成事项、风险点、相关文件、联系人或下一步操作。

## 写作规范

- 用户用中文提问时，默认输出中文文档。
- 使用正式、客观、可交付的表达，不使用闲聊语气。
- 从用户的工作记录视角撰写，避免写成“助手执行了……”。
- 不编造缺失事实；缺失信息写“待确认”。
- 重要的文件路径、命令、配置名、接口名和日期要具体记录。
- 对失败尝试、异常、限制和绕行方式做客观说明。
- 使用表格呈现元数据、任务清单、问题清单、决策对比或风险列表。
- 只记录与文档目标相关的上下文，避免把无关聊天内容写入文档。
- 避免泄露 token、密码、密钥、私有凭证和不必要的个人信息。
- Markdown 应能直接粘贴到 Confluence，标题层级要清晰。
- 输出格式必须能被 `confluence-upload/confluence_upload.py upload-markdown` 直接读取。优先使用标准 Markdown 标题、段落、无序列表、代码块和简单表格。

## 与 confluence-upload 的衔接

`context-transform` 和 `confluence-upload` 是连续流程：

1. `context-transform` 负责把本次对话上下文整理成 Markdown 文档。
2. 生成的 Markdown 文件必须写入 `context-transform/cache/`。
3. `confluence-upload` 负责读取这个 Markdown 文件，并在 Confluence 中创建新页面。
4. 默认上传目标由 `confluence-upload/config.yaml` 中的 `root_page` 和 `username` 决定。
5. 上传时如果 `root_page` 下不存在名为 `username` 的子页面，`confluence-upload` 会先创建该子页面，再在其下创建新文档。

默认上传命令：

```powershell
python confluence-upload/confluence_upload.py upload-latest-context
```

如果需要指定标题：

```powershell
python confluence-upload/confluence_upload.py upload-latest-context --title "文档标题"
```

如果用户只要求生成文档、不要求上传，应在最终回复中提示最新 Markdown 文件路径，并说明可以继续使用 `confluence-upload` 上传。

## 质量检查

最终交付前检查：

- 是否有清晰标题和必填元数据表。
- 是否覆盖背景、目标、过程、结果、问题、状态和后续事项。
- 是否区分事实、推断和待确认内容。
- 是否记录必要的文件路径、命令、配置和产出物。
- 是否避免泄露敏感信息。
- 是否符合用户指定的文档类型和语言。
- 是否是有效 Markdown，并适合进入 Confluence 或团队知识库。

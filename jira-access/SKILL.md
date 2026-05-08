---
name: jira-access
description: Search, browse, retrieve, cache, and summarize Jira issues, projects, boards, sprints, comments, attachments, users, and worklogs through a local Python helper script. Use when Codex needs to search Jira with JQL, read issue details, inspect project metadata, list comments or attachments, download attachment files, review agile board or sprint data, answer questions grounded in Jira items, or cache retrieved Jira content locally. Do not use for deleting issues/comments, changing permissions, modifying workflows, or administrative Jira operations.
---

# Jira Access

当用户需要查找、访问、读取、总结或本地缓存 Jira 内容时，使用这个 skill。

核心脚本是 [jira_api.py](jira_api.py)。脚本从环境变量或同目录的 [config.yaml](config.yaml) 读取鉴权配置，调用 Jira REST API，并把读取到的 issue JSON 缓存在 [cache/](cache/) 目录下。

## 绝对禁止

默认把 Jira 当作只读系统使用。

除非用户明确要求创建评论、上传附件或执行其他写入操作，并且已经确认目标 issue 和具体内容，否则不要执行任何写入操作。禁止删除 issue、评论、附件、项目、工作流或权限配置。

## 配置

优先读取本地环境变量，其次读取 `jira-access/config.yaml`：

```text
JIRA_BASE_URL
JIRA_USERNAME
JIRA_API_TOKEN
```

约定：

- `JIRA_BASE_URL` 填 Jira 基础地址，例如 `https://your-domain.atlassian.net`。
- `JIRA_USERNAME` 填 Jira 账号邮箱。
- `JIRA_API_TOKEN` 填 Jira API token 或 PAT。
- 鉴权方式固定为 Bearer：`Authorization: Bearer <JIRA_API_TOKEN>`。

`config.yaml` 示例：

```yaml
username: "your-email@example.com"
api-token: "your-api-token"
base_url: "https://your-domain.atlassian.net"
```

开始实际查询前，可以先检查配置：

```powershell
python jira_api.py check-config
```

## 常用只读命令

JQL 搜索 issue：

```powershell
python jira_api.py search-jql "project = ABC ORDER BY updated DESC" --max-results 20
```

关键词搜索 issue：

```powershell
python jira_api.py search-issues "登录失败" --project ABC --max-results 20
```

读取 issue：

```powershell
python jira_api.py get-issue ABC-123 --summary
```

列出项目：

```powershell
python jira_api.py list-projects
```

读取项目：

```powershell
python jira_api.py get-project ABC
```

列出评论、附件、工作日志：

```powershell
python jira_api.py list-comments ABC-123
python jira_api.py list-attachments ABC-123
python jira_api.py list-worklogs ABC-123
```

下载附件：

```powershell
python jira_api.py download-attachment ATTACHMENT_ID output.bin
```

搜索用户：

```powershell
python jira_api.py search-users "zhangsan"
```

Agile board 和 sprint：

```powershell
python jira_api.py list-boards --project ABC
python jira_api.py list-sprints BOARD_ID
python jira_api.py list-board-issues BOARD_ID --jql "assignee = currentUser()"
python jira_api.py list-sprint-issues SPRINT_ID
```

## JSON Actions

常用只读 action：

```json
{"action":"search","jql":"project = ABC ORDER BY updated DESC","maxResults":20}
{"action":"search_issues","keyword":"登录失败","projectKey":"ABC","maxResults":20}
{"action":"get_issue","issueKey":"ABC-123"}
{"action":"list_projects"}
{"action":"get_project","projectKey":"ABC"}
{"action":"list_comments","issueKey":"ABC-123"}
{"action":"list_attachments","issueKey":"ABC-123"}
{"action":"list_worklogs","issueKey":"ABC-123"}
{"action":"search_users","query":"zhangsan"}
{"action":"list_boards","projectKey":"ABC"}
{"action":"list_sprints","boardId":"12"}
{"action":"list_board_issues","boardId":"12","jql":"assignee = currentUser()"}
{"action":"list_sprint_issues","sprintId":"34"}
```

脚本中也提供以下写入 action，但必须谨慎使用：

```json
{"action":"add_comment","issueKey":"ABC-123","body":"Comment text"}
{"action":"upload_attachment","issueKey":"ABC-123","filePath":"C:\\path\\file.log"}
```

## Search-Read-Reflect-Refine 工作流

默认使用 Search-Read-Reflect-Refine 循环，由 agent 根据任务目标自行迭代搜索、读取、反思和细化。

- **Search**：使用 JQL、关键词、项目、状态、负责人、时间范围、组件、标签、board 或 sprint 定位候选 issue。
- **Read**：读取最相关 issue 的详情、评论、附件列表和工作日志。必要时下载附件或读取多个相关 issue。
- **Reflect**：判断是否足够回答用户问题，检查信息是否缺失、issue 状态是否过旧、评论和字段是否冲突、多个 issue 是否描述同一问题但结论不同。
- **Refine**：如果仍有缺失或矛盾，调整 JQL、项目范围、关键词、时间范围、状态、标签、组件、关联 issue 或 sprint 继续搜索。

## 跨平台连续追踪

同一个搜索任务中可以连续调用 `jira-access` 和 `confluence-access`。

- 如果在 Jira issue 描述、评论、附件名、链接字段、worklog 备注或关联内容中发现 Confluence 页面链接、wiki 页面标题、空间 key 或明确的 Confluence 查询线索，可以切换使用 `confluence-access` 继续搜索或读取对应文档。
- 切换到 Confluence 后，应保留来源链路，例如“Jira issue ABC-123 指向 Confluence 页面 X”，并在最终回答中说明信息来自哪个平台。
- 不需要因为跨平台而重新询问用户；只要该 Confluence 内容明显有助于回答当前问题，就可以继续访问。
- 如果 Confluence 内容又反向指向 Jira issue，也可以再切回 `jira-access`，直到 Search-Read-Reflect-Refine 循环满足停止条件。

停止条件：

- 已读取内容能够覆盖用户问题的关键方面。
- 主要事实有明确 issue、评论或附件来源支持。
- 已检查明显缺失、冲突和矛盾；若仍无法消除，必须在回答中标注“不确定”“存在冲突”或“待确认”。

## 回答规范

- 默认用正常文段语言回答，不直接贴完整 JSON。
- 搜索或列表结果只展示关键信息：issue key、标题、状态、负责人、报告人、项目、优先级、更新时间、URL。
- 需要说明来源时，优先引用可直接访问链接，使用 `[ABC-123 标题](issue URL)`，不要只给 issue key。
- 涉及多个 issue 时，说明它们之间的关系、状态差异和时间先后。
- 如果 Jira 内容存在冲突，明确指出冲突来源，并说明采用哪个结论或哪些内容仍待确认。
- 不要泄露 token、私有凭证或无关个人信息。

## 缓存行为

通过以下方式读取 issue 时，脚本会自动把 issue 内容保存为 JSON：

- `get-issue`
- JSON action `get_issue`

缓存路径：

```text
jira-access/cache/issue-<issue_key>.json
```

如果后续任务需要查看同一 issue，可以优先读取缓存。若用户强调内容必须最新，重新调用 Jira API 刷新缓存。

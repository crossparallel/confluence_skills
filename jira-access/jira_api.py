#!/usr/bin/env python3
"""用于访问 Jira REST API 的命令行工具。"""

from __future__ import annotations

import argparse
import json
import locale
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).with_name("config.yaml")
CACHE_DIR = Path(__file__).with_name("cache")
SSL_CONTEXT = ssl._create_unverified_context()


class JiraApiError(RuntimeError):
    """Jira API 请求失败时抛出的异常。"""


def load_config(config_path: Path = CONFIG_PATH) -> dict[str, str]:
    """优先从环境变量读取配置，缺失时回退到同目录 config.yaml。"""
    data: dict[str, str] = {}

    if config_path.exists():
        data.update(_load_config_file(config_path))

    data.update(_load_env_config())

    if not data:
        raise JiraApiError(f"Config file not found: {config_path}")

    return _validate_config(data, config_path)


def _load_config_file(config_path: Path) -> dict[str, str]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return _load_simple_yaml(config_path)

    with config_path.open("r", encoding="utf-8") as config_file:
        raw_data = yaml.safe_load(config_file) or {}

    if not isinstance(raw_data, dict):
        raise JiraApiError(f"Invalid config file: {config_path}")

    return {str(key): str(value) for key, value in raw_data.items()}


def _load_simple_yaml(config_path: Path) -> dict[str, str]:
    data: dict[str, str] = {}

    with config_path.open("r", encoding="utf-8") as config_file:
        for line_number, line in enumerate(config_file, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if ":" not in stripped:
                raise JiraApiError(f"Invalid config line {line_number}: expected key: value")
            key, value = stripped.split(":", 1)
            data[key.strip()] = value.strip().strip("\"'")

    return data


def _load_env_config() -> dict[str, str]:
    data: dict[str, str] = {}

    username = os.getenv("JIRA_USERNAME")
    api_token = os.getenv("JIRA_API_TOKEN")
    base_url = os.getenv("JIRA_BASE_URL")

    if username:
        data["username"] = username.strip()
    if api_token:
        data["api-token"] = api_token
    if base_url:
        data["base_url"] = base_url

    return data


def _validate_config(data: Any, config_path: Path) -> dict[str, str]:
    if not isinstance(data, dict):
        raise JiraApiError(f"Invalid config file: {config_path}")

    required_keys = ("username", "api-token", "base_url")
    missing_keys = [key for key in required_keys if not data.get(key)]
    if missing_keys:
        missing = ", ".join(missing_keys)
        raise JiraApiError(f"Missing required config value(s): {missing}")

    return {
        "username": str(data["username"]).strip(),
        "api-token": str(data["api-token"]),
        "base_url": str(data["base_url"]).rstrip("/"),
    }


def check_config(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    issues: list[str] = []

    try:
        config = load_config(config_path)
    except JiraApiError as exc:
        return {"ok": False, "issues": [str(exc)]}

    if not config["base_url"].startswith(("http://", "https://")):
        issues.append("base_url 格式错误，应以 http:// 或 https:// 开头")

    if config["api-token"] == "your-api-token":
        issues.append("api-token 仍是占位值，请替换为真实访问令牌")

    return {"ok": not issues, "issues": issues}


def build_auth_header(api_token: str) -> str:
    return f"Bearer {api_token}"


class JiraClient:
    """封装 Jira Cloud REST API v3 和 Agile API 常用只读操作。"""

    def __init__(self, config: dict[str, str]) -> None:
        self.base_url = config["base_url"].rstrip("/")
        self.headers = {
            "Authorization": build_auth_header(config["api-token"]),
            "Accept": "application/json",
            "X-Atlassian-Token": "no-check",
        }

    def search(self, jql: str, max_results: int = 20, start_at: int = 0) -> dict[str, Any]:
        """用 JQL 搜索 issue。"""
        raw = self._get_api(
            "/search",
            {
                "jql": jql,
                "maxResults": str(max_results),
                "startAt": str(start_at),
                "fields": (
                    "summary,status,assignee,reporter,project,issuetype,priority,"
                    "created,updated,labels,components,resolution"
                ),
            },
        )
        return slim_issue_search(raw, self.base_url)

    def search_issues(
        self,
        keyword: str,
        project_key: str | None = None,
        max_results: int = 20,
        start_at: int = 0,
    ) -> dict[str, Any]:
        """按关键词搜索 issue，可选择限制项目。"""
        jql_parts = [f'text ~ "{_escape_jql(keyword)}"']
        if project_key:
            jql_parts.append(f'project = "{_escape_jql(project_key)}"')
        jql_parts.append("ORDER BY updated DESC")
        return self.search(" AND ".join(jql_parts), max_results=max_results, start_at=start_at)

    def get_issue(
        self,
        issue_key: str,
        fields: str = "*all",
        expand: str = "renderedFields,names,schema,transitions,changelog",
    ) -> dict[str, Any]:
        """读取单个 issue 原始响应。"""
        issue = self._get_api(
            f"/issue/{urllib.parse.quote(issue_key)}",
            {"fields": fields, "expand": expand},
        )
        cache_issue_json(issue)
        return issue

    def get_issue_summary(self, issue_key: str) -> dict[str, Any]:
        """读取 issue 并返回常用字段和链接。"""
        raw = self.get_issue(issue_key)
        fields = raw.get("fields", {})
        result = {
            "id": raw.get("id"),
            "key": raw.get("key"),
            "url": build_issue_url(raw.get("key"), self.base_url),
            "summary": fields.get("summary"),
            "description": fields.get("description"),
            "issueType": _field_name(fields.get("issuetype")),
            "status": _field_name(fields.get("status")),
            "priority": _field_name(fields.get("priority")),
            "projectKey": fields.get("project", {}).get("key"),
            "projectName": fields.get("project", {}).get("name"),
            "assignee": _user_name(fields.get("assignee")),
            "reporter": _user_name(fields.get("reporter")),
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "resolution": _field_name(fields.get("resolution")),
            "labels": fields.get("labels") or [],
            "components": [_field_name(item) for item in fields.get("components") or []],
            "comments": slim_comments(fields.get("comment", {}).get("comments", [])),
            "attachments": [slim_attachment(item, self.base_url) for item in fields.get("attachment", [])],
            "worklogs": slim_worklogs(fields.get("worklog", {}).get("worklogs", [])),
        }
        cache_issue_json(result)
        return result

    def list_projects(self) -> dict[str, Any]:
        raw = self._get_api("/project/search", {"maxResults": "100"})
        return {
            "results": [slim_project(project, self.base_url) for project in raw.get("values", [])],
            "total": raw.get("total"),
            "maxResults": raw.get("maxResults"),
            "startAt": raw.get("startAt"),
        }

    def get_project(self, project_key: str) -> dict[str, Any]:
        return self._get_api(f"/project/{urllib.parse.quote(project_key)}")

    def list_comments(self, issue_key: str) -> dict[str, Any]:
        raw = self._get_api(f"/issue/{urllib.parse.quote(issue_key)}/comment")
        return {
            "results": slim_comments(raw.get("comments", [])),
            "total": raw.get("total"),
            "maxResults": raw.get("maxResults"),
            "startAt": raw.get("startAt"),
        }

    def add_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        payload = {"body": jira_doc_text(body)}
        return self._post_api(f"/issue/{urllib.parse.quote(issue_key)}/comment", payload)

    def list_attachments(self, issue_key: str) -> dict[str, Any]:
        issue = self.get_issue(issue_key, fields="attachment", expand="")
        attachments = issue.get("fields", {}).get("attachment", [])
        return {"results": [slim_attachment(item, self.base_url) for item in attachments]}

    def download_attachment(self, attachment_id: str, output_path: Path) -> Path:
        metadata = self._get_api(f"/attachment/{urllib.parse.quote(attachment_id)}")
        content_url = metadata.get("content")
        if not content_url:
            raise JiraApiError(f"Attachment has no content URL: {attachment_id}")

        body = self._request_bytes(content_url)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(body)
        return output_path

    def upload_attachment(self, issue_key: str, file_path: Path) -> dict[str, Any]:
        if not file_path.exists():
            raise JiraApiError(f"File not found: {file_path}")
        boundary = f"----JiraBoundary{os.getpid()}"
        body = build_multipart_body(file_path, boundary)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }
        return self._request_json(
            "POST",
            f"/issue/{urllib.parse.quote(issue_key)}/attachments",
            body=body,
            extra_headers=headers,
        )

    def list_worklogs(self, issue_key: str) -> dict[str, Any]:
        raw = self._get_api(f"/issue/{urllib.parse.quote(issue_key)}/worklog")
        return {
            "results": slim_worklogs(raw.get("worklogs", [])),
            "total": raw.get("total"),
            "maxResults": raw.get("maxResults"),
            "startAt": raw.get("startAt"),
        }

    def search_users(self, query: str, max_results: int = 20) -> dict[str, Any]:
        raw = self._get_api("/user/search", {"query": query, "maxResults": str(max_results)})
        return {"results": [slim_user(user) for user in raw]}

    def list_boards(self, project_key: str | None = None, max_results: int = 50) -> dict[str, Any]:
        params = {"maxResults": str(max_results)}
        if project_key:
            params["projectKeyOrId"] = project_key
        raw = self._get_agile("/board", params)
        return {
            "results": raw.get("values", []),
            "total": raw.get("total"),
            "maxResults": raw.get("maxResults"),
            "startAt": raw.get("startAt"),
        }

    def list_sprints(self, board_id: str, state: str | None = None) -> dict[str, Any]:
        params = {"maxResults": "50"}
        if state:
            params["state"] = state
        raw = self._get_agile(f"/board/{urllib.parse.quote(board_id)}/sprint", params)
        return {
            "results": raw.get("values", []),
            "total": raw.get("total"),
            "maxResults": raw.get("maxResults"),
            "startAt": raw.get("startAt"),
        }

    def list_board_issues(
        self,
        board_id: str,
        jql: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        params = {"maxResults": str(max_results)}
        if jql:
            params["jql"] = jql
        raw = self._get_agile(f"/board/{urllib.parse.quote(board_id)}/issue", params)
        return slim_issue_search(raw, self.base_url)

    def list_sprint_issues(
        self,
        sprint_id: str,
        jql: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        params = {"maxResults": str(max_results)}
        if jql:
            params["jql"] = jql
        raw = self._get_agile(f"/sprint/{urllib.parse.quote(sprint_id)}/issue", params)
        return slim_issue_search(raw, self.base_url)

    def _get_api(self, api_path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        return self._request_json("GET", f"/rest/api/3{api_path}", params=params)

    def _get_agile(self, api_path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        return self._request_json("GET", f"/rest/agile/1.0{api_path}", params=params)

    def _post_api(self, api_path: str, payload: Any) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        return self._request_json(
            "POST",
            f"/rest/api/3{api_path}",
            body=body,
            extra_headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )

    def _request_json(
        self,
        method: str,
        api_path: str,
        params: dict[str, str] | None = None,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response_body = self._request_text(method, api_path, params, body, extra_headers)
        if not response_body or not response_body.strip():
            return {}
        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            preview = response_body[:500].replace("\n", "\\n")
            raise JiraApiError(f"Jira API returned non-JSON content: {preview}") from exc

    def _request_text(
        self,
        method: str,
        api_path: str,
        params: dict[str, str] | None = None,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = api_path if api_path.startswith(("http://", "https://")) else f"{self.base_url}{api_path}"
        if query and not api_path.startswith(("http://", "https://")):
            url = f"{url}{query}"
        headers = {**self.headers, **(extra_headers or {})}
        request = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
                return decode_response_body(response.read(), response.headers)
        except urllib.error.HTTPError as exc:
            detail = decode_response_body(exc.read(), exc.headers, errors="replace")
            raise JiraApiError(f"Jira API request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise JiraApiError(f"Jira API request failed: {exc.reason}") from exc

    def _request_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers=self.headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = decode_response_body(exc.read(), exc.headers, errors="replace")
            raise JiraApiError(f"Jira attachment request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise JiraApiError(f"Jira attachment request failed: {exc.reason}") from exc


def decode_response_body(body: bytes, headers: Any, errors: str = "strict") -> str:
    charset = headers.get_content_charset() if hasattr(headers, "get_content_charset") else None
    encodings = []
    if charset:
        encodings.append(charset)
    encodings.extend(["utf-8-sig", "utf-8"])

    last_error: UnicodeDecodeError | None = None
    for encoding in encodings:
        try:
            return body.decode(encoding, errors=errors)
        except UnicodeDecodeError as exc:
            last_error = exc

    if last_error is not None:
        return body.decode("utf-8", errors="replace")
    return body.decode("utf-8", errors=errors)


def slim_issue_search(raw: dict[str, Any], base_url: str) -> dict[str, Any]:
    issues = raw.get("issues") or raw.get("values") or []
    return {
        "results": [slim_issue(issue, base_url) for issue in issues],
        "total": raw.get("total"),
        "maxResults": raw.get("maxResults"),
        "startAt": raw.get("startAt"),
    }


def slim_issue(issue: dict[str, Any], base_url: str) -> dict[str, Any]:
    fields = issue.get("fields", {})
    key = issue.get("key")
    return {
        "id": issue.get("id"),
        "key": key,
        "url": build_issue_url(key, base_url),
        "summary": fields.get("summary"),
        "issueType": _field_name(fields.get("issuetype")),
        "status": _field_name(fields.get("status")),
        "priority": _field_name(fields.get("priority")),
        "projectKey": fields.get("project", {}).get("key"),
        "assignee": _user_name(fields.get("assignee")),
        "reporter": _user_name(fields.get("reporter")),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "resolution": _field_name(fields.get("resolution")),
    }


def slim_project(project: dict[str, Any], base_url: str) -> dict[str, Any]:
    key = project.get("key")
    return {
        "id": project.get("id"),
        "key": key,
        "name": project.get("name"),
        "projectTypeKey": project.get("projectTypeKey"),
        "lead": _user_name(project.get("lead")),
        "url": f"{base_url}/jira/software/c/projects/{key}" if key else None,
    }


def slim_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": comment.get("id"),
            "author": _user_name(comment.get("author")),
            "created": comment.get("created"),
            "updated": comment.get("updated"),
            "body": comment.get("body"),
        }
        for comment in comments
    ]


def slim_attachment(attachment: dict[str, Any], base_url: str) -> dict[str, Any]:
    return {
        "id": attachment.get("id"),
        "filename": attachment.get("filename"),
        "author": _user_name(attachment.get("author")),
        "created": attachment.get("created"),
        "size": attachment.get("size"),
        "mimeType": attachment.get("mimeType"),
        "content": attachment.get("content"),
        "url": f"{base_url}/secure/attachment/{attachment.get('id')}/{attachment.get('filename')}"
        if attachment.get("id") and attachment.get("filename")
        else attachment.get("content"),
    }


def slim_worklogs(worklogs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": worklog.get("id"),
            "author": _user_name(worklog.get("author")),
            "started": worklog.get("started"),
            "timeSpent": worklog.get("timeSpent"),
            "timeSpentSeconds": worklog.get("timeSpentSeconds"),
            "comment": worklog.get("comment"),
        }
        for worklog in worklogs
    ]


def slim_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "accountId": user.get("accountId"),
        "displayName": user.get("displayName"),
        "emailAddress": user.get("emailAddress"),
        "active": user.get("active"),
        "accountType": user.get("accountType"),
    }


def _field_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name")
    return None if value is None else str(value)


def _user_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    return value.get("displayName") or value.get("emailAddress") or value.get("accountId")


def build_issue_url(issue_key: str | None, base_url: str) -> str | None:
    if not issue_key:
        return None
    return f"{base_url}/browse/{urllib.parse.quote(issue_key)}"


def jira_doc_text(text: str) -> dict[str, Any]:
    """构造 Jira Cloud v3 的 Atlassian Document Format 简单文本。"""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def build_multipart_body(file_path: Path, boundary: str) -> bytes:
    file_name = file_path.name
    parts = [
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8"),
        file_path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    return b"".join(parts)


def _escape_jql(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def cache_issue_json(issue: dict[str, Any], cache_dir: Path = CACHE_DIR) -> Path | None:
    issue_key = str(issue.get("key") or "").strip()
    if not issue_key:
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    output_path = cache_dir / f"issue-{safe_filename(issue_key)}.json"
    output_path.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)


def print_json(data: Any) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    _write_stdout(payload + "\n")


def _write_stdout(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    except (AttributeError, ValueError):
        pass

    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()


def _write_stderr(text: str) -> None:
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="strict")
    except (AttributeError, ValueError):
        pass

    try:
        sys.stderr.write(text)
        sys.stderr.flush()
    except UnicodeEncodeError:
        sys.stderr.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stderr.buffer.flush()


def _read_stdin_text() -> str:
    raw = sys.stdin.buffer.read()
    if not raw:
        return ""

    encodings = ("utf-8-sig", "utf-8")
    preferred = locale.getpreferredencoding(False)
    if preferred and preferred.lower() not in encodings:
        encodings = encodings + (preferred,)

    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace")


def build_client(config_path: str | Path = CONFIG_PATH) -> JiraClient:
    return JiraClient(load_config(Path(config_path)))


def handle_json_command(command: dict[str, Any], config_path: Path = CONFIG_PATH) -> Any:
    action = command.get("action")

    if action == "check_config":
        return check_config(config_path)

    client = build_client(config_path)

    if action == "search":
        return client.search(
            str(command["jql"]),
            int(command.get("maxResults", 20)),
            int(command.get("startAt", 0)),
        )
    if action == "search_issues":
        return client.search_issues(
            str(command["keyword"]),
            command.get("projectKey") and str(command.get("projectKey")),
            int(command.get("maxResults", 20)),
            int(command.get("startAt", 0)),
        )
    if action == "get_issue":
        return client.get_issue_summary(str(command["issueKey"]))
    if action == "list_projects":
        return client.list_projects()
    if action == "get_project":
        return client.get_project(str(command["projectKey"]))
    if action == "list_comments":
        return client.list_comments(str(command["issueKey"]))
    if action == "add_comment":
        return client.add_comment(str(command["issueKey"]), str(command["body"]))
    if action == "list_attachments":
        return client.list_attachments(str(command["issueKey"]))
    if action == "download_attachment":
        return str(client.download_attachment(str(command["attachmentId"]), Path(str(command["output"]))))
    if action == "upload_attachment":
        return client.upload_attachment(str(command["issueKey"]), Path(str(command["filePath"])))
    if action == "list_worklogs":
        return client.list_worklogs(str(command["issueKey"]))
    if action == "search_users":
        return client.search_users(str(command["query"]), int(command.get("maxResults", 20)))
    if action == "list_boards":
        return client.list_boards(
            command.get("projectKey") and str(command.get("projectKey")),
            int(command.get("maxResults", 50)),
        )
    if action == "list_sprints":
        return client.list_sprints(str(command["boardId"]), command.get("state") and str(command["state"]))
    if action == "list_board_issues":
        return client.list_board_issues(
            str(command["boardId"]),
            command.get("jql") and str(command.get("jql")),
            int(command.get("maxResults", 50)),
        )
    if action == "list_sprint_issues":
        return client.list_sprint_issues(
            str(command["sprintId"]),
            command.get("jql") and str(command.get("jql")),
            int(command.get("maxResults", 50)),
        )

    supported_actions = [
        "check_config",
        "search",
        "search_issues",
        "get_issue",
        "list_projects",
        "get_project",
        "list_comments",
        "add_comment",
        "list_attachments",
        "download_attachment",
        "upload_attachment",
        "list_worklogs",
        "search_users",
        "list_boards",
        "list_sprints",
        "list_board_issues",
        "list_sprint_issues",
    ]
    raise JiraApiError(f"Unknown action: {action}. Supported actions: {supported_actions}")


def handle_check_config(args: argparse.Namespace) -> None:
    print_json(check_config(Path(args.config)))


def handle_search_jql(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).search(args.jql, args.max_results, args.start_at))


def handle_search_issues(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).search_issues(
            args.keyword,
            project_key=args.project,
            max_results=args.max_results,
            start_at=args.start_at,
        )
    )


def handle_get_issue(args: argparse.Namespace) -> None:
    client = build_client(args.config)
    if args.summary:
        print_json(client.get_issue_summary(args.issue_key))
    else:
        print_json(client.get_issue(args.issue_key, fields=args.fields, expand=args.expand))


def handle_list_projects(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).list_projects())


def handle_get_project(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).get_project(args.project_key))


def handle_list_comments(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).list_comments(args.issue_key))


def handle_add_comment(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).add_comment(args.issue_key, args.body))


def handle_list_attachments(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).list_attachments(args.issue_key))


def handle_download_attachment(args: argparse.Namespace) -> None:
    output_path = build_client(args.config).download_attachment(args.attachment_id, Path(args.output))
    print(f"Saved attachment to {output_path}")


def handle_upload_attachment(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).upload_attachment(args.issue_key, Path(args.file_path)))


def handle_list_worklogs(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).list_worklogs(args.issue_key))


def handle_search_users(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).search_users(args.query, args.max_results))


def handle_list_boards(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).list_boards(args.project, args.max_results))


def handle_list_sprints(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).list_sprints(args.board_id, args.state))


def handle_list_board_issues(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).list_board_issues(
            args.board_id,
            jql=args.jql,
            max_results=args.max_results,
        )
    )


def handle_list_sprint_issues(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).list_sprint_issues(
            args.sprint_id,
            jql=args.jql,
            max_results=args.max_results,
        )
    )


def add_search_args(parser: argparse.ArgumentParser, default_max_results: int) -> None:
    parser.add_argument("--max-results", type=int, default=default_max_results)
    parser.add_argument("--start-at", type=int, default=0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search and read Jira issues using Bearer auth.")
    parser.add_argument(
        "--config",
        default=str(CONFIG_PATH),
        help="Path to config.yaml. Defaults to config.yaml next to this script.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    check_config = subparsers.add_parser("check-config", help="Validate config file.")
    check_config.set_defaults(handler=handle_check_config)

    search_jql = subparsers.add_parser("search-jql", help="Search issues with JQL.")
    search_jql.add_argument("jql", help="Jira JQL expression.")
    add_search_args(search_jql, 20)
    search_jql.set_defaults(handler=handle_search_jql)

    search_issues = subparsers.add_parser("search-issues", help="Search issues by keyword.")
    search_issues.add_argument("keyword", help="Keyword to search for.")
    search_issues.add_argument("--project", help="Optional Jira project key.")
    add_search_args(search_issues, 20)
    search_issues.set_defaults(handler=handle_search_issues)

    get_issue = subparsers.add_parser("get-issue", help="Get an issue by key.")
    get_issue.add_argument("issue_key", help="Issue key, such as ABC-123.")
    get_issue.add_argument("--summary", action="store_true", help="Return slim issue content.")
    get_issue.add_argument("--fields", default="*all", help="Jira fields parameter.")
    get_issue.add_argument(
        "--expand",
        default="renderedFields,names,schema,transitions,changelog",
        help="Jira expand parameter.",
    )
    get_issue.set_defaults(handler=handle_get_issue)

    list_projects = subparsers.add_parser("list-projects", help="List visible projects.")
    list_projects.set_defaults(handler=handle_list_projects)

    get_project = subparsers.add_parser("get-project", help="Get project metadata.")
    get_project.add_argument("project_key", help="Project key.")
    get_project.set_defaults(handler=handle_get_project)

    list_comments = subparsers.add_parser("list-comments", help="List issue comments.")
    list_comments.add_argument("issue_key", help="Issue key.")
    list_comments.set_defaults(handler=handle_list_comments)

    add_comment = subparsers.add_parser("add-comment", help="Add a comment to an issue.")
    add_comment.add_argument("issue_key", help="Issue key.")
    add_comment.add_argument("body", help="Comment text.")
    add_comment.set_defaults(handler=handle_add_comment)

    list_attachments = subparsers.add_parser("list-attachments", help="List issue attachments.")
    list_attachments.add_argument("issue_key", help="Issue key.")
    list_attachments.set_defaults(handler=handle_list_attachments)

    download_attachment = subparsers.add_parser("download-attachment", help="Download attachment.")
    download_attachment.add_argument("attachment_id", help="Jira attachment ID.")
    download_attachment.add_argument("output", help="Output file path.")
    download_attachment.set_defaults(handler=handle_download_attachment)

    upload_attachment = subparsers.add_parser("upload-attachment", help="Upload attachment.")
    upload_attachment.add_argument("issue_key", help="Issue key.")
    upload_attachment.add_argument("file_path", help="File path to upload.")
    upload_attachment.set_defaults(handler=handle_upload_attachment)

    list_worklogs = subparsers.add_parser("list-worklogs", help="List issue worklogs.")
    list_worklogs.add_argument("issue_key", help="Issue key.")
    list_worklogs.set_defaults(handler=handle_list_worklogs)

    search_users = subparsers.add_parser("search-users", help="Search Jira users.")
    search_users.add_argument("query", help="User query.")
    search_users.add_argument("--max-results", type=int, default=20)
    search_users.set_defaults(handler=handle_search_users)

    list_boards = subparsers.add_parser("list-boards", help="List agile boards.")
    list_boards.add_argument("--project", help="Optional project key.")
    list_boards.add_argument("--max-results", type=int, default=50)
    list_boards.set_defaults(handler=handle_list_boards)

    list_sprints = subparsers.add_parser("list-sprints", help="List board sprints.")
    list_sprints.add_argument("board_id", help="Board ID.")
    list_sprints.add_argument("--state", help="active, future, closed.")
    list_sprints.set_defaults(handler=handle_list_sprints)

    list_board_issues = subparsers.add_parser("list-board-issues", help="List board issues.")
    list_board_issues.add_argument("board_id", help="Board ID.")
    list_board_issues.add_argument("--jql", help="Optional JQL filter.")
    list_board_issues.add_argument("--max-results", type=int, default=50)
    list_board_issues.set_defaults(handler=handle_list_board_issues)

    list_sprint_issues = subparsers.add_parser("list-sprint-issues", help="List sprint issues.")
    list_sprint_issues.add_argument("sprint_id", help="Sprint ID.")
    list_sprint_issues.add_argument("--jql", help="Optional JQL filter.")
    list_sprint_issues.add_argument("--max-results", type=int, default=50)
    list_sprint_issues.set_defaults(handler=handle_list_sprint_issues)

    return parser


def should_read_json_from_stdin(argv: list[str]) -> bool:
    return not argv and not sys.stdin.isatty()


def main(argv: list[str] | None = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)

    try:
        if should_read_json_from_stdin(actual_argv):
            command_text = _read_stdin_text()
            if not command_text.strip():
                raise JiraApiError("No JSON command received from stdin")
            command = json.loads(command_text)
            print_json(handle_json_command(command))
            return 0

        parser = build_parser()
        args = parser.parse_args(actual_argv)
        args.handler(args)
        return 0
    except KeyError as exc:
        _write_stderr(f"Error: Missing required field: {exc}\n")
    except (JiraApiError, json.JSONDecodeError) as exc:
        _write_stderr(f"Error: {exc}\n")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

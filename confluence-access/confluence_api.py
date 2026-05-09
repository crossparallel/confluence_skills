#!/usr/bin/env python3
"""用于访问 Confluence REST API 的命令行工具。"""

from __future__ import annotations

import argparse
import base64
import locale
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).with_name("config.yaml")
CACHE_DIR = Path(__file__).with_name("cache")
SSL_CONTEXT = ssl._create_unverified_context()


class ConfluenceApiError(RuntimeError):
    """Confluence API 请求失败时抛出的异常。"""


class HtmlTextExtractor(HTMLParser):
    """将 Confluence storage/view HTML 转换为可读的纯文本。"""

    BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._parts.append(text)
            self._parts.append(" ")

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line)


def load_config(config_path: Path = CONFIG_PATH) -> dict[str, str]:
    """默认从脚本同目录的 YAML 文件读取鉴权配置。"""
    data: dict[str, str] = {}

    if config_path.exists():
        data.update(_load_config_file(config_path))

    data.update(_load_env_config())

    if not data:
        raise ConfluenceApiError(f"Config file not found: {config_path}")

    return _validate_config(data, config_path)


def _load_config_file(config_path: Path) -> dict[str, str]:
    """优先使用 PyYAML，缺失时回退到简单解析。"""
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        # 没有安装 PyYAML 时，仍可解析当前这种简单的 key/value 配置。
        return _load_simple_yaml(config_path)

    with config_path.open("r", encoding="utf-8") as config_file:
        raw_data = yaml.safe_load(config_file) or {}

    if not isinstance(raw_data, dict):
        raise ConfluenceApiError(f"Invalid config file: {config_path}")

    return {str(key): str(value) for key, value in raw_data.items()}


def _load_simple_yaml(config_path: Path) -> dict[str, str]:
    """在不依赖第三方包的情况下解析当前的小型配置文件格式。"""
    data: dict[str, str] = {}

    with config_path.open("r", encoding="utf-8") as config_file:
        for line_number, line in enumerate(config_file, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if ":" not in stripped:
                raise ConfluenceApiError(
                    f"Invalid config line {line_number}: expected key: value"
                )
            key, value = stripped.split(":", 1)
            data[key.strip()] = value.strip().strip("\"'")

    return data


def _load_env_config() -> dict[str, str]:
    """从本地环境变量读取优先配置。"""
    data: dict[str, str] = {}

    username = os.getenv("CONFLUENCE_USERNAME")
    api_token = os.getenv("CONFLUENCE_API_TOKEN")
    base_url = os.getenv("CONFLUENCE_BASE_URL")
    views_endpoint_template = os.getenv("CONFLUENCE_VIEWS_ENDPOINT_TEMPLATE")

    if username:
        data["username"] = username.strip()
    if api_token:
        data["api-token"] = api_token
    if base_url:
        data["base_url"] = base_url
    if views_endpoint_template:
        data["views_endpoint_template"] = views_endpoint_template

    return data


def _validate_config(data: Any, config_path: Path) -> dict[str, str]:
    """在构造请求前检查并规范化必需的配置项。"""
    if not isinstance(data, dict):
        raise ConfluenceApiError(f"Invalid config file: {config_path}")

    required_keys = ("username", "api-token", "base_url")
    missing_keys = [key for key in required_keys if not data.get(key)]
    if missing_keys:
        missing = ", ".join(missing_keys)
        raise ConfluenceApiError(f"Missing required config value(s): {missing}")

    config = {
        "username": str(data["username"]).strip(),
        "api-token": str(data["api-token"]),
        "base_url": str(data["base_url"]).rstrip("/"),
    }
    if data.get("views_endpoint_template"):
        config["views_endpoint_template"] = str(data["views_endpoint_template"]).strip()
    return config


def check_config(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    """检查配置文件是否具备发起请求所需的基本信息。"""
    issues: list[str] = []

    try:
        config = load_config(config_path)
    except ConfluenceApiError as exc:
        return {"ok": False, "issues": [str(exc)]}

    if not config["base_url"].startswith(("http://", "https://")):
        issues.append("base_url 格式错误，应以 http:// 或 https:// 开头")

    if config["api-token"] == "your-api-token":
        issues.append("api-token 仍是占位值，请替换为真实访问令牌")

    return {"ok": not issues, "issues": issues}


def build_auth_header(username: str, api_token: str) -> str:
    """按附件脚本规则构造鉴权头：PAT 优先使用 Bearer。"""
    if api_token:
        return f"Bearer {api_token}"

    raw_token = f"{username}:{api_token}".encode("utf-8")
    encoded_token = base64.b64encode(raw_token).decode("ascii")
    return f"Basic {encoded_token}"


class ConfluenceClient:
    """封装 Confluence Server/Data Center 常用 REST API。"""

    def __init__(self, config: dict[str, str]) -> None:
        self.base_url = config["base_url"].rstrip("/")
        self.views_endpoint_template = config.get("views_endpoint_template", "").strip()
        # 与附件脚本保持一致：有 PAT 时发送 Bearer token。
        self.headers = {
            "Authorization": build_auth_header(config["username"], config["api-token"]),
            "Accept": "application/json",
            "X-Atlassian-Token": "no-check",
        }

    def search(
        self,
        cql: str,
        limit: int = 10,
        start: int = 0,
        expand: str | None = None,
    ) -> dict[str, Any]:
        """执行原始 Confluence CQL 搜索，并返回精简后的页面列表。"""
        params = {
            "cql": cql,
            "limit": str(limit),
            "start": str(start),
        }
        if expand:
            params["expand"] = expand
        raw = self._get_api(
            "/content/search",
            params,
        )
        return slim_list(raw, self.base_url)

    def search_ranked_by_views(
        self,
        cql: str,
        recall_limit: int = 50,
        top_limit: int = 5,
        read_top: bool = False,
    ) -> dict[str, Any]:
        """Recall by relevance and recency, then rank candidates by page views."""
        recall_limit = clamp_int(recall_limit, minimum=1, maximum=50)
        top_limit = clamp_int(top_limit, minimum=1, maximum=recall_limit * 2)

        base_cql = strip_cql_order_by(cql)
        relevance = self.search(base_cql, limit=recall_limit)
        recent = self.search(
            with_cql_order_by(base_cql, "lastmodified DESC"),
            limit=recall_limit,
        )
        candidates = merge_search_results(
            [
                ("relevance", relevance.get("results", [])),
                ("recent", recent.get("results", [])),
            ]
        )
        candidates_with_views = self.add_views_to_pages(candidates)
        top_results = sorted(
            candidates_with_views,
            key=lambda page: view_sort_value(page.get("views")),
            reverse=True,
        )[:top_limit]

        response: dict[str, Any] = {
            "cql": base_cql,
            "recallLimit": recall_limit,
            "topLimit": top_limit,
            "workflow": "views-ranked-two-stage-search",
            "workflowReminder": (
                "Use topResults/pages for Read-Extract-Reflect. For the next search round, "
                "call search-cql or search-pages again; raw search is only for debugging or "
                "when views are unavailable."
            ),
            "relevance": {
                "size": len(relevance.get("results", [])),
                "hasMore": relevance.get("hasMore"),
            },
            "recent": {
                "size": len(recent.get("results", [])),
                "hasMore": recent.get("hasMore"),
            },
            "candidateCount": len(candidates),
            "topResults": top_results,
        }
        if read_top:
            response["pages"] = [
                self.get_page_summary(str(page["id"]))
                for page in top_results
                if page.get("id")
            ]
        return response

    def search_pages(
        self,
        keyword: str,
        limit: int = 10,
        space_key: str | None = None,
        start: int = 0,
    ) -> dict[str, Any]:
        """按正文关键词搜索页面，可选择限制在指定空间内。"""
        cql_parts = [
            'text ~ "{0}"'.format(_escape_cql(keyword)),
            "type = page",
        ]
        if space_key:
            cql_parts.append('space = "{0}"'.format(_escape_cql(space_key)))
        return self.search(" AND ".join(cql_parts), limit=limit, start=start)

    def search_pages_ranked_by_views(
        self,
        keyword: str,
        recall_limit: int = 50,
        space_key: str | None = None,
        top_limit: int = 5,
        read_top: bool = False,
    ) -> dict[str, Any]:
        cql_parts = [
            'text ~ "{0}"'.format(_escape_cql(keyword)),
            "type = page",
        ]
        if space_key:
            cql_parts.append('space = "{0}"'.format(_escape_cql(space_key)))
        return self.search_ranked_by_views(
            " AND ".join(cql_parts),
            recall_limit=recall_limit,
            top_limit=top_limit,
            read_top=read_top,
        )

    def search_by_title(
        self,
        title: str,
        space_key: str | None = None,
        limit: int = 10,
        start: int = 0,
    ) -> dict[str, Any]:
        """按标题搜索页面，可选择限制在指定空间内。"""
        cql = 'title = "{0}" AND type = page'.format(_escape_cql(title))
        if space_key:
            cql += ' AND space = "{0}"'.format(_escape_cql(space_key))
        return self.search(cql, limit=limit, start=start)

    def search_spaces(
        self,
        keyword: str,
        limit: int = 10,
        scan_limit: int = 500,
    ) -> dict[str, Any]:
        """分页扫描空间元数据，并在本地按关键词匹配空间。"""
        normalized_keyword = keyword.casefold()
        matching_results = []
        scanned_count = 0
        start = 0
        page_size = 100

        # 空间接口不像页面搜索那样提供完整的 CQL 正文搜索能力，
        # 因此这里分页读取空间列表，并在本地匹配 key/name/plain description。
        while scanned_count < scan_limit and len(matching_results) < limit:
            current_limit = min(page_size, scan_limit - scanned_count)
            response = self._get_api(
                "/space",
                {
                    "start": str(start),
                    "limit": str(current_limit),
                    "expand": "description.plain",
                },
            )
            results = response.get("results", [])
            if not results:
                break

            for space in results:
                haystack = " ".join(
                    str(value)
                    for value in (
                        space.get("key"),
                        space.get("name"),
                        space.get("description", {}).get("plain", {}).get("value"),
                    )
                    if value
                ).casefold()
                if normalized_keyword in haystack:
                    matching_results.append(slim_space(space, self.base_url))
                    if len(matching_results) >= limit:
                        break

            scanned_count += len(results)
            if len(results) < current_limit:
                break
            start += len(results)

        return {
            "results": matching_results,
            "size": len(matching_results),
            "limit": limit,
            "scanned": scanned_count,
        }

    def get_space(self, space_key: str, expand: str | None = None) -> dict[str, Any]:
        """根据空间 key 获取单个 Confluence 空间。"""
        params = {"expand": expand} if expand else None
        return self._get_api(f"/space/{urllib.parse.quote(space_key)}", params)

    def list_spaces(self, limit: int = 50, start: int = 0) -> dict[str, Any]:
        """列出 Confluence 空间。"""
        raw = self._get_api(
            "/space",
            {
                "limit": str(limit),
                "start": str(start),
                "expand": "description.plain",
            },
        )
        return {
            "results": [slim_space(space, self.base_url) for space in raw.get("results", [])],
            "size": raw.get("size"),
            "start": raw.get("start", start),
            "limit": raw.get("limit", limit),
        }

    def list_pages(self, space_key: str, limit: int = 25, start: int = 0) -> dict[str, Any]:
        """列出指定空间下的页面。"""
        raw = self._get_api(
            "/content",
            {
                "spaceKey": space_key,
                "type": "page",
                "limit": str(limit),
                "start": str(start),
            },
        )
        return slim_list(raw, self.base_url)

    def list_children(self, page_id: str, limit: int = 25, start: int = 0) -> dict[str, Any]:
        """列出指定页面的子页面。"""
        raw = self._get_api(
            f"/content/{urllib.parse.quote(page_id)}/child/page",
            {
                "limit": str(limit),
                "start": str(start),
            },
        )
        return slim_list(raw, self.base_url)

    def get_page_views(self, page_id: str, strict: bool = True) -> dict[str, Any]:
        """Return view count for a page when an analytics source is available."""
        errors: list[str] = []
        attempts = self._build_views_attempts(page_id)
        for attempt_name, url in attempts:
            try:
                raw = self._request_json_url("GET", url)
            except ConfluenceApiError as exc:
                errors.append(f"{attempt_name}: {exc}")
                continue

            views = extract_view_count(raw)
            if views is not None:
                return {
                    "id": page_id,
                    "views": views,
                    "viewsAvailable": True,
                    "viewsSource": attempt_name,
                }
            errors.append(f"{attempt_name}: response did not contain a view count")

        html_result = self._get_page_views_from_html(page_id)
        if html_result.get("viewsAvailable"):
            return html_result
        if html_result.get("viewsError"):
            errors.append(str(html_result["viewsError"]))

        if strict:
            detail = "; ".join(errors) if errors else "no views endpoints configured"
            raise ConfluenceApiError(f"Page views unavailable for {page_id}: {detail}")
        return {
            "id": page_id,
            "views": None,
            "viewsAvailable": False,
            "viewsError": "; ".join(errors) if errors else "no views endpoints configured",
        }

    def _build_views_attempts(self, page_id: str) -> list[tuple[str, str]]:
        encoded_page_id = urllib.parse.quote(page_id)
        attempts: list[tuple[str, str]] = []
        if self.views_endpoint_template:
            attempts.append(
                (
                    "configured",
                    self.views_endpoint_template.format(
                        page_id=encoded_page_id,
                        raw_page_id=page_id,
                        base_url=self.base_url,
                    ),
                )
            )
        attempts.extend(
            [
                (
                    "data-center-analytics-content-views",
                    f"{self.base_url}/rest/analytics/1.0/content/{encoded_page_id}/views",
                ),
                (
                    "data-center-analytics-page",
                    f"{self.base_url}/rest/analytics/1.0/publish/page/{encoded_page_id}",
                ),
                (
                    "data-center-confluence-analytics-content",
                    f"{self.base_url}/rest/confluence-analytics/1.0/content/{encoded_page_id}/views",
                ),
                (
                    "cloud-analytics-content-views",
                    f"{self.base_url}/wiki/rest/api/analytics/content/{encoded_page_id}/views",
                ),
                (
                    "rest-api-analytics-content-views",
                    f"{self.base_url}/rest/api/analytics/content/{encoded_page_id}/views",
                ),
            ]
        )
        return attempts

    def _get_page_views_from_html(self, page_id: str) -> dict[str, Any]:
        try:
            page = self.get_page(page_id, expand="space")
        except ConfluenceApiError as exc:
            return {
                "id": page_id,
                "views": None,
                "viewsAvailable": False,
                "viewsError": f"html-page-lookup: {exc}",
            }

        url = build_web_url(page, self.base_url)
        if not url:
            return {
                "id": page_id,
                "views": None,
                "viewsAvailable": False,
                "viewsError": "html-page-lookup: page web URL unavailable",
            }

        try:
            html = self._request_text_url("GET", url)
        except ConfluenceApiError as exc:
            return {
                "id": page_id,
                "views": None,
                "viewsAvailable": False,
                "viewsError": f"html-page: {exc}",
            }
        views = extract_view_count_from_text(html)
        if views is None:
            return {
                "id": page_id,
                "views": None,
                "viewsAvailable": False,
                "viewsError": "html-page: no recognizable views value in page HTML",
            }
        return {
            "id": page_id,
            "views": views,
            "viewsAvailable": True,
            "viewsSource": "html-page",
        }

    def get_pages_views(self, page_ids: list[str], strict: bool = False) -> dict[str, Any]:
        results = [self.get_page_views(page_id, strict=strict) for page_id in page_ids]
        return {
            "results": results,
            "size": len(results),
        }

    def add_views_to_pages(self, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        page_ids = [str(page.get("id")) for page in pages if page.get("id")]
        views_by_id = {
            item["id"]: item
            for item in self.get_pages_views(page_ids, strict=False).get("results", [])
            if item.get("id")
        }
        enriched = []
        for page in pages:
            page_id = str(page.get("id") or "")
            views = views_by_id.get(page_id, {})
            enriched_page = {**page}
            enriched_page["views"] = views.get("views")
            enriched_page["viewsAvailable"] = bool(views.get("viewsAvailable"))
            if views.get("viewsError"):
                enriched_page["viewsError"] = views.get("viewsError")
            enriched.append(enriched_page)
        return enriched

    def get_page(
        self,
        page_id: str,
        expand: str = "body.storage,version,ancestors,space",
    ) -> dict[str, Any]:
        """根据 content ID 获取单个 Confluence 页面原始响应。"""
        page = self._get_api(
            f"/content/{urllib.parse.quote(page_id)}",
            {"expand": expand},
        )
        cache_page_json(page)
        return page

    def get_page_summary(self, page_id: str) -> dict[str, Any]:
        """读取页面正文，并返回与原 TypeScript 脚本一致的精简结构。"""
        raw = self.get_page(page_id, expand="body.storage,version,ancestors,space")
        body = raw.get("body", {}).get("storage", {}).get("value", "")
        users = self.resolve_users(body)
        result = {
            "id": raw.get("id"),
            "title": raw.get("title"),
            "spaceKey": raw.get("space", {}).get("key"),
            "spaceName": raw.get("space", {}).get("name"),
            "version": raw.get("version", {}).get("number"),
            "breadcrumb": " > ".join(
                str(ancestor.get("title"))
                for ancestor in raw.get("ancestors", [])
                if ancestor.get("title")
            ),
            "body": body,
            "users": users,
            "url": build_web_url(raw, self.base_url),
        }
        cache_page_json(result)
        return result

    def get_page_by_title(
        self,
        space_key: str,
        title: str,
        expand: str = "body.storage,version,ancestors,space",
    ) -> dict[str, Any]:
        """在指定空间内按精确标题获取页面元数据或正文。"""
        response = self._get_api(
            "/content",
            {
                "spaceKey": space_key,
                "title": title,
                "type": "page",
                "expand": expand,
            },
        )
        for page in response.get("results", []):
            cache_page_json(page)
        return response

    def create_page(
        self,
        space_key: str,
        title: str,
        body: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """创建 Confluence 页面，正文必须是 Storage Format。"""
        payload: dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": body, "representation": "storage"}},
        }
        if parent_id:
            payload["ancestors"] = [{"id": parent_id}]

        response = self._post_api("/content", payload)
        cache_page_json(response)
        return response

    def update_page(
        self,
        page_id: str,
        title: str,
        body: str,
        version: int,
    ) -> dict[str, Any]:
        """更新页面；version 应传入目标版本号，通常是当前版本号加 1。"""
        payload = {
            "type": "page",
            "title": title,
            "version": {"number": version},
            "body": {"storage": {"value": body, "representation": "storage"}},
        }
        response = self._put_api(f"/content/{urllib.parse.quote(page_id)}", payload)
        cache_page_json(response)
        return response

    def add_comment(self, page_id: str, body: str) -> dict[str, Any]:
        """给指定页面添加评论，正文必须是 Storage Format。"""
        payload = {
            "type": "comment",
            "container": {"id": page_id, "type": "page"},
            "body": {"storage": {"value": body, "representation": "storage"}},
        }
        return self._post_api("/content", payload)

    def list_attachments(self, page_id: str, limit: int = 25) -> dict[str, Any]:
        """列出指定页面的附件。"""
        return self._get_api(
            f"/content/{urllib.parse.quote(page_id)}/child/attachment",
            {"limit": str(limit)},
        )

    def upload_attachment(
        self,
        page_id: str,
        file_path: Path,
        comment: str = "",
    ) -> dict[str, Any]:
        """上传附件到指定页面。"""
        if not file_path.exists():
            raise ConfluenceApiError(f"File not found: {file_path}")

        boundary = f"----ConfluenceBoundary{int(time.time() * 1000)}"
        body = build_multipart_body(file_path, boundary, comment=comment)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }
        return self._request_json(
            "POST",
            f"/content/{urllib.parse.quote(page_id)}/child/attachment",
            body=body,
            extra_headers=headers,
        )

    def download_page(
        self,
        page_id: str,
        output_path: Path,
        body_format: str = "storage",
        as_text: bool = False,
    ) -> Path:
        """将页面正文保存为 Confluence HTML 或简化后的纯文本。"""
        page = self.get_page(page_id, expand=f"body.{body_format},version,ancestors,space")
        body = page.get("body", {}).get(body_format, {}).get("value", "")
        if as_text:
            body = html_to_text(body)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
        return output_path

    def resolve_users(self, body: str) -> dict[str, str]:
        """解析 Storage Format 中的 ri:userkey，并尝试映射为显示名称。"""
        keys = sorted(set(re.findall(r'ri:userkey="([^"]+)"', body)))
        users: dict[str, str] = {}

        for key in keys:
            try:
                user = self._get_api("/user", {"key": key})
                users[key] = user.get("displayName") or user.get("username") or key
            except ConfluenceApiError:
                users[key] = key

        return users

    def _get_api(self, api_path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        return self._request_json("GET", api_path, params=params)

    def _get_api_url(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        return self._request_json_url("GET", f"{url}{query}")

    def _post_api(self, api_path: str, payload: Any) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        return self._request_json(
            "POST",
            api_path,
            body=body,
            extra_headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )

    def _put_api(self, api_path: str, payload: Any) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        return self._request_json(
            "PUT",
            api_path,
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
        """向 Confluence REST API 发送请求并解析 JSON 响应。"""
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self.base_url}/rest/api{api_path}{query}"
        return self._request_json_url(
            method,
            url,
            body=body,
            extra_headers=extra_headers,
        )

    def _request_json_url(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response_body = self._request_text_url(
            method,
            url,
            body=body,
            extra_headers=extra_headers,
        )

        if not response_body or not response_body.strip():
            return {}
        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            preview = response_body[:500].replace("\n", "\\n")
            raise ConfluenceApiError(
                f"Confluence API returned non-JSON or incorrectly encoded content: {preview}"
            ) from exc

    def _request_text_url(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        headers = {**self.headers, **(extra_headers or {})}
        request = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
                response_body = decode_response_body(response.read(), response.headers)
        except urllib.error.HTTPError as exc:
            detail = decode_response_body(exc.read(), exc.headers, errors="replace")
            raise ConfluenceApiError(
                f"Confluence API request failed with HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ConfluenceApiError(f"Confluence API request failed: {exc.reason}") from exc

        return response_body


def decode_response_body(
    body: bytes,
    headers: Any,
    errors: str = "strict",
) -> str:
    """按响应头 charset 解码，默认兼容 Confluence 常见的 UTF-8 JSON。"""
    charset = headers.get_content_charset() if hasattr(headers, "get_content_charset") else None
    encodings = []
    if charset:
        encodings.append(charset)
    encodings.extend(["utf-8-sig", "utf-8"])

    for encoding in dict.fromkeys(encodings):
        try:
            return body.decode(encoding, errors=errors)
        except (LookupError, UnicodeDecodeError):
            continue

    return body.decode("utf-8", errors="replace")


def build_multipart_body(file_path: Path, boundary: str, comment: str = "") -> bytes:
    """构造上传附件所需的 multipart/form-data 请求体。"""
    file_name = file_path.name
    parts: list[bytes] = []

    if comment:
        parts.append(
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="comment"\r\n\r\n'
                f"{comment}\r\n"
            ).encode("utf-8")
        )

    parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="minorEdit"\r\n\r\n'
            "true\r\n"
        ).encode("utf-8")
    )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts)


def slim_page(item: dict[str, Any], base_url: str) -> dict[str, Any]:
    """将页面或搜索结果压缩为常用字段。"""
    content = item.get("content", {})
    history = item.get("history") or content.get("history") or {}
    created_by = history.get("createdBy") or {}
    version = item.get("version") or content.get("version") or {}
    space = item.get("space") or content.get("space") or {}
    return {
        "id": item.get("id") or content.get("id"),
        "title": item.get("title") or content.get("title"),
        "type": item.get("type") or content.get("type"),
        "spaceKey": space.get("key"),
        "spaceName": space.get("name"),
        "author": created_by.get("displayName") or created_by.get("username"),
        "createdAt": history.get("createdDate"),
        "version": version.get("number"),
        "lastModifiedAt": version.get("when"),
        "lastModifiedBy": (version.get("by") or {}).get("displayName")
        or (version.get("by") or {}).get("username"),
        "url": build_web_url(item, base_url),
    }


def slim_space(space: dict[str, Any], base_url: str) -> dict[str, Any]:
    """将空间结果压缩为常用字段。"""
    return {
        "key": space.get("key"),
        "name": space.get("name"),
        "description": space.get("description", {}).get("plain", {}).get("value") or "",
        "url": build_web_url(space, base_url),
    }


def slim_list(raw: dict[str, Any], base_url: str) -> dict[str, Any]:
    """将 Confluence 列表响应转换为精简列表。"""
    links = raw.get("_links", {})
    return {
        "results": [slim_page(item, base_url) for item in raw.get("results", [])],
        "size": raw.get("size"),
        "start": raw.get("start"),
        "limit": raw.get("limit"),
        "hasMore": bool(links.get("next")),
        "next": links.get("next"),
    }


def merge_search_results(
    result_groups: list[tuple[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for source, results in result_groups:
        for index, page in enumerate(results, start=1):
            page_id = str(page.get("id") or "")
            if not page_id or page_id in seen_ids:
                continue
            seen_ids.add(page_id)
            merged.append(
                {
                    **page,
                    "recallSources": [source],
                    "recallRanks": {source: index},
                }
            )
    for source, results in result_groups:
        for index, page in enumerate(results, start=1):
            page_id = str(page.get("id") or "")
            if not page_id:
                continue
            for merged_page in merged:
                if str(merged_page.get("id")) == page_id:
                    sources = merged_page.setdefault("recallSources", [])
                    if source not in sources:
                        sources.append(source)
                    merged_page.setdefault("recallRanks", {})[source] = index
                    break
    return merged


def extract_view_count(raw: dict[str, Any]) -> int | None:
    direct = extract_view_count_from_value(raw)
    if direct is not None:
        return direct
    for key in ("count", "views", "viewCount", "totalViews", "totalViewCount"):
        value = find_nested_key(raw, key)
        count = extract_view_count_from_value(value)
        if count is not None:
            return count
    return None


def extract_view_count_from_value(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.replace(",", "").strip()
        if normalized.isdigit():
            return int(normalized)
    if isinstance(value, dict):
        for key in ("count", "value", "total", "views", "viewCount"):
            count = extract_view_count_from_value(value.get(key))
            if count is not None:
                return count
    return None


def find_nested_key(value: Any, target_key: str) -> Any:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if str(key).casefold() == target_key.casefold():
                return nested_value
            found = find_nested_key(nested_value, target_key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_nested_key(item, target_key)
            if found is not None:
                return found
    return None


def extract_view_count_from_text(text: str) -> int | None:
    patterns = [
        r'"(?:viewCount|views|totalViews|totalViewCount)"\s*:\s*"?([0-9][0-9,]*)"?',
        r'data-(?:view-count|views)\s*=\s*"([0-9][0-9,]*)"',
        r'([0-9][0-9,]*)\s+(?:views|viewers|page views)',
        r'(?:views|page views)\D{0,40}([0-9][0-9,]*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def view_sort_value(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return -1


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def strip_cql_order_by(cql: str) -> str:
    return re.sub(r"\s+ORDER\s+BY\s+.+$", "", cql, flags=re.IGNORECASE).strip()


def with_cql_order_by(cql: str, order_by: str) -> str:
    return f"{strip_cql_order_by(cql)} ORDER BY {order_by}"


def build_web_url(item: dict[str, Any], base_url: str) -> str | None:
    """根据响应中的 webui 链接拼出可访问的页面地址。"""
    links = item.get("_links", {})
    content_links = item.get("content", {}).get("_links", {})
    webui = links.get("webui") or content_links.get("webui")
    if not webui:
        return None
    return f"{base_url}{webui}"


def _escape_cql(value: str) -> str:
    """将用户输入嵌入 CQL 字符串前进行转义。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def html_to_text(html: str) -> str:
    extractor = HtmlTextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.text()


def cache_page_json(page: dict[str, Any], cache_dir: Path = CACHE_DIR) -> Path | None:
    """将读取到的 Confluence 页面内容缓存为 JSON 文件。"""
    page_id = str(page.get("id") or "").strip()
    if not page_id:
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    output_path = cache_dir / f"page-{safe_filename(page_id)}.json"
    output_path.write_text(
        json.dumps(page, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def safe_filename(value: str) -> str:
    """将页面 ID 等值转换成适合用于文件名的字符串。"""
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)


def print_json(data: Any) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    _write_stdout(payload + "\n")


def _write_stdout(text: str) -> None:
    """以 UTF-8 优先写出，避免 Windows 控制台编码导致的输出失败。"""
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
    """以 UTF-8 优先写出错误信息。"""
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
    """尽量兼容不同来源的 stdin 编码。"""
    raw = sys.stdin.buffer.read()
    if not raw:
        return ""

    encodings = ("utf-8-sig", "utf-8")
    preferred = locale.getpreferredencoding(False)
    if preferred and preferred.lower() not in encodings:
        encodings = encodings + (preferred,)

    last_error: UnicodeDecodeError | None = None
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc

    if last_error is not None:
        return raw.decode("utf-8", errors="replace")
    return ""


def build_client(config_path: str | Path = CONFIG_PATH) -> ConfluenceClient:
    return ConfluenceClient(load_config(Path(config_path)))


def handle_json_command(command: dict[str, Any], config_path: Path = CONFIG_PATH) -> Any:
    """执行与原 TypeScript 脚本兼容的 JSON action。"""
    action = command.get("action")

    if action == "check_config":
        return check_config(config_path)

    client = build_client(config_path)

    if action == "search":
        return client.search_ranked_by_views(
            str(command["cql"]),
            int(command.get("recallLimit", command.get("limit", 50))),
            int(command.get("topLimit", 5)),
            bool(command.get("readTop", True)),
        )
    if action == "search_raw":
        return client.search(
            str(command["cql"]),
            int(command.get("limit", 10)),
            int(command.get("start", 0)),
        )
    if action == "search_ranked_by_views":
        return client.search_ranked_by_views(
            str(command["cql"]),
            int(command.get("recallLimit", 50)),
            int(command.get("topLimit", 5)),
            bool(command.get("readTop", False)),
        )
    if action == "search_pages_ranked_by_views":
        return client.search_pages_ranked_by_views(
            str(command["keyword"]),
            int(command.get("recallLimit", 50)),
            command.get("spaceKey") and str(command.get("spaceKey")),
            int(command.get("topLimit", 5)),
            bool(command.get("readTop", False)),
        )
    if action == "search_by_title":
        return client.search_by_title(
            str(command["title"]),
            command.get("spaceKey") and str(command.get("spaceKey")),
            int(command.get("limit", 10)),
            int(command.get("start", 0)),
        )
    if action == "get_page_views":
        return client.get_page_views(str(command["pageId"]))
    if action == "get_pages_views":
        raw_page_ids = command.get("pageIds", [])
        if not isinstance(raw_page_ids, list):
            raise ConfluenceApiError("pageIds must be a list")
        return client.get_pages_views([str(page_id) for page_id in raw_page_ids])
    if action == "get_page":
        return client.get_page_summary(str(command["pageId"]))
    if action == "list_spaces":
        return client.list_spaces(int(command.get("limit", 50)), int(command.get("start", 0)))
    if action == "list_pages":
        return client.list_pages(
            str(command["spaceKey"]),
            int(command.get("limit", 25)),
            int(command.get("start", 0)),
        )
    if action == "list_children":
        return client.list_children(
            str(command["pageId"]),
            int(command.get("limit", 25)),
            int(command.get("start", 0)),
        )
    if action == "create_page":
        return client.create_page(
            str(command["spaceKey"]),
            str(command["title"]),
            str(command["body"]),
            command.get("parentId") and str(command.get("parentId")),
        )
    if action == "update_page":
        return client.update_page(
            str(command["pageId"]),
            str(command["title"]),
            str(command["body"]),
            int(command["version"]),
        )
    if action == "add_comment":
        return client.add_comment(str(command["pageId"]), str(command["body"]))
    if action == "list_attachments":
        return client.list_attachments(
            str(command["pageId"]),
            int(command.get("limit", 25)),
        )
    if action == "upload_attachment":
        return client.upload_attachment(
            str(command["pageId"]),
            Path(str(command["filePath"])),
            str(command.get("comment", "")),
        )

    supported_actions = [
        "check_config",
        "search",
        "search_raw",
        "search_ranked_by_views",
        "search_pages_ranked_by_views",
        "search_by_title",
        "get_page_views",
        "get_pages_views",
        "get_page",
        "list_spaces",
        "list_pages",
        "list_children",
        "create_page",
        "update_page",
        "add_comment",
        "list_attachments",
        "upload_attachment",
    ]
    raise ConfluenceApiError(f"Unknown action: {action}. Supported actions: {supported_actions}")


def handle_check_config(args: argparse.Namespace) -> None:
    print_json(check_config(Path(args.config)))


def handle_search_cql(args: argparse.Namespace) -> None:
    client = build_client(args.config)
    if args.raw:
        print_json(client.search(args.cql, limit=args.limit, start=args.start))
    else:
        print_json(
            client.search_ranked_by_views(
                args.cql,
                recall_limit=args.recall_limit,
                top_limit=args.top_limit,
                read_top=args.read_top,
            )
        )


def handle_search_ranked_by_views(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).search_ranked_by_views(
            args.cql,
            recall_limit=args.recall_limit,
            top_limit=args.top_limit,
            read_top=args.read_top,
        )
    )


def handle_search_pages(args: argparse.Namespace) -> None:
    client = build_client(args.config)
    if args.raw:
        print_json(
            client.search_pages(
                args.keyword,
                limit=args.limit,
                space_key=args.space,
                start=args.start,
            )
        )
    else:
        print_json(
            client.search_pages_ranked_by_views(
                args.keyword,
                recall_limit=args.recall_limit,
                space_key=args.space,
                top_limit=args.top_limit,
                read_top=args.read_top,
            )
        )


def handle_search_pages_ranked_by_views(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).search_pages_ranked_by_views(
            args.keyword,
            recall_limit=args.recall_limit,
            space_key=args.space,
            top_limit=args.top_limit,
            read_top=args.read_top,
        )
    )


def handle_search_cql_raw(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).search(args.cql, limit=args.limit, start=args.start))


def handle_search_pages_raw(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).search_pages(
            args.keyword,
            limit=args.limit,
            space_key=args.space,
            start=args.start,
        )
    )


def handle_search_by_title(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).search_by_title(
            args.title,
            space_key=args.space,
            limit=args.limit,
            start=args.start,
        )
    )


def handle_search_spaces(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).search_spaces(
            args.keyword,
            limit=args.limit,
            scan_limit=args.scan_limit,
        )
    )


def handle_get_space(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).get_space(args.space_key, expand=args.expand))


def handle_list_spaces(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).list_spaces(limit=args.limit, start=args.start))


def handle_list_pages(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).list_pages(
            args.space_key,
            limit=args.limit,
            start=args.start,
        )
    )


def handle_list_children(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).list_children(
            args.page_id,
            limit=args.limit,
            start=args.start,
        )
    )


def handle_get_page_views(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).get_page_views(args.page_id))


def handle_get_pages_views(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).get_pages_views(args.page_ids))


def handle_get_page(args: argparse.Namespace) -> None:
    client = build_client(args.config)
    if args.summary:
        print_json(client.get_page_summary(args.page_id))
    else:
        print_json(client.get_page(args.page_id, expand=args.expand))


def handle_get_page_by_title(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).get_page_by_title(
            args.space_key,
            args.title,
            expand=args.expand,
        )
    )


def handle_create_page(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).create_page(
            args.space_key,
            args.title,
            args.body,
            parent_id=args.parent_id,
        )
    )


def handle_update_page(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).update_page(
            args.page_id,
            args.title,
            args.body,
            args.version,
        )
    )


def handle_add_comment(args: argparse.Namespace) -> None:
    print_json(build_client(args.config).add_comment(args.page_id, args.body))


def handle_list_attachments(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).list_attachments(
            args.page_id,
            limit=args.limit,
        )
    )


def handle_upload_attachment(args: argparse.Namespace) -> None:
    print_json(
        build_client(args.config).upload_attachment(
            args.page_id,
            Path(args.file_path),
            comment=args.comment,
        )
    )


def handle_download_page(args: argparse.Namespace) -> None:
    output_path = build_client(args.config).download_page(
        args.page_id,
        Path(args.output),
        body_format=args.body_format,
        as_text=args.text,
    )
    print(f"Saved page content to {output_path}")


def add_paging_args(parser: argparse.ArgumentParser, default_limit: int) -> None:
    parser.add_argument("--limit", type=int, default=default_limit, help="Maximum result count.")
    parser.add_argument("--start", type=int, default=0, help="Result offset.")


def add_ranked_search_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--recall-limit",
        type=int,
        default=50,
        help="Maximum results to recall per relevance/recency pass, capped at 50.",
    )
    parser.add_argument(
        "--top-limit",
        type=int,
        default=5,
        help="Maximum view-ranked results to return.",
    )
    parser.add_argument(
        "--read-top",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch summary content for the top view-ranked pages.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Use the legacy lightweight search without views or page-body reads.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search and manage Confluence spaces/pages using Basic auth."
    )
    parser.add_argument(
        "--config",
        default=str(CONFIG_PATH),
        help="Path to config.yaml. Defaults to config.yaml next to this script.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    check_config_parser = subparsers.add_parser("check-config", help="Validate config file.")
    check_config_parser.set_defaults(handler=handle_check_config)

    search_cql = subparsers.add_parser(
        "search-cql",
        help="Run the default views-ranked search workflow for a raw CQL query.",
    )
    search_cql.add_argument("cql", help="Confluence CQL expression.")
    add_paging_args(search_cql, 10)
    add_ranked_search_args(search_cql)
    search_cql.set_defaults(handler=handle_search_cql)

    search_ranked = subparsers.add_parser(
        "search-ranked-by-views",
        help="Recall by relevance and recency, then rank candidates by views.",
    )
    search_ranked.add_argument("cql", help="Confluence CQL expression without ORDER BY.")
    search_ranked.add_argument(
        "--recall-limit",
        type=int,
        default=50,
        help="Maximum results to recall per relevance/recency pass, capped at 50.",
    )
    search_ranked.add_argument(
        "--top-limit",
        type=int,
        default=5,
        help="Maximum view-ranked results to return.",
    )
    search_ranked.add_argument(
        "--read-top",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch summary content for the top view-ranked pages.",
    )
    search_ranked.set_defaults(handler=handle_search_ranked_by_views)

    search_pages = subparsers.add_parser(
        "search-pages",
        help="Run the default views-ranked search workflow by keyword.",
    )
    search_pages.add_argument("keyword", help="Keyword to search for.")
    search_pages.add_argument("--space", help="Optional Confluence space key.")
    add_paging_args(search_pages, 10)
    add_ranked_search_args(search_pages)
    search_pages.set_defaults(handler=handle_search_pages)

    search_pages_ranked = subparsers.add_parser(
        "search-pages-ranked-by-views",
        help="Search pages by keyword, then rank recalled candidates by views.",
    )
    search_pages_ranked.add_argument("keyword", help="Keyword to search for.")
    search_pages_ranked.add_argument("--space", help="Optional Confluence space key.")
    search_pages_ranked.add_argument(
        "--recall-limit",
        type=int,
        default=50,
        help="Maximum results to recall per relevance/recency pass, capped at 50.",
    )
    search_pages_ranked.add_argument(
        "--top-limit",
        type=int,
        default=5,
        help="Maximum view-ranked results to return.",
    )
    search_pages_ranked.add_argument(
        "--read-top",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch summary content for the top view-ranked pages.",
    )
    search_pages_ranked.set_defaults(handler=handle_search_pages_ranked_by_views)

    search_cql_raw = subparsers.add_parser(
        "search-cql-raw",
        help="Legacy lightweight CQL search without views ranking or page-body reads.",
    )
    search_cql_raw.add_argument("cql", help="Confluence CQL expression.")
    add_paging_args(search_cql_raw, 10)
    search_cql_raw.set_defaults(handler=handle_search_cql_raw)

    search_pages_raw = subparsers.add_parser(
        "search-pages-raw",
        help="Legacy lightweight keyword search without views ranking or page-body reads.",
    )
    search_pages_raw.add_argument("keyword", help="Keyword to search for.")
    search_pages_raw.add_argument("--space", help="Optional Confluence space key.")
    add_paging_args(search_pages_raw, 10)
    search_pages_raw.set_defaults(handler=handle_search_pages_raw)

    search_by_title = subparsers.add_parser("search-by-title", help="Search pages by title.")
    search_by_title.add_argument("title", help="Page title or title keyword.")
    search_by_title.add_argument("--space", help="Optional Confluence space key.")
    add_paging_args(search_by_title, 10)
    search_by_title.set_defaults(handler=handle_search_by_title)

    search_spaces = subparsers.add_parser("search-spaces", help="Search spaces by keyword.")
    search_spaces.add_argument("keyword", help="Keyword to search for.")
    search_spaces.add_argument("--limit", type=int, default=10, help="Maximum result count.")
    search_spaces.add_argument(
        "--scan-limit",
        type=int,
        default=500,
        help="Maximum number of spaces to scan while searching.",
    )
    search_spaces.set_defaults(handler=handle_search_spaces)

    get_space = subparsers.add_parser("get-space", help="Get a space by key.")
    get_space.add_argument("space_key", help="Confluence space key.")
    get_space.add_argument("--expand", help="Optional Confluence expand parameter.")
    get_space.set_defaults(handler=handle_get_space)

    list_spaces = subparsers.add_parser("list-spaces", help="List spaces.")
    add_paging_args(list_spaces, 50)
    list_spaces.set_defaults(handler=handle_list_spaces)

    list_pages = subparsers.add_parser("list-pages", help="List pages in a space.")
    list_pages.add_argument("space_key", help="Confluence space key.")
    add_paging_args(list_pages, 25)
    list_pages.set_defaults(handler=handle_list_pages)

    list_children = subparsers.add_parser("list-children", help="List child pages.")
    list_children.add_argument("page_id", help="Parent page ID.")
    add_paging_args(list_children, 25)
    list_children.set_defaults(handler=handle_list_children)

    get_page_views = subparsers.add_parser(
        "get-page-views",
        help="Get a page view count through the analytics API.",
    )
    get_page_views.add_argument("page_id", help="Confluence page ID.")
    get_page_views.set_defaults(handler=handle_get_page_views)

    get_pages_views = subparsers.add_parser(
        "get-pages-views",
        help="Get page view counts for multiple page IDs.",
    )
    get_pages_views.add_argument("page_ids", nargs="+", help="Confluence page IDs.")
    get_pages_views.set_defaults(handler=handle_get_pages_views)

    get_page = subparsers.add_parser("get-page", help="Get a page by page ID.")
    get_page.add_argument("page_id", help="Confluence page ID.")
    get_page.add_argument(
        "--expand",
        default="body.storage,version,ancestors,space",
        help="Confluence expand parameter.",
    )
    get_page.add_argument("--summary", action="store_true", help="Return slim page content.")
    get_page.set_defaults(handler=handle_get_page)

    get_page_by_title = subparsers.add_parser(
        "get-page-by-title",
        help="Get a page by space key and exact title.",
    )
    get_page_by_title.add_argument("space_key", help="Confluence space key.")
    get_page_by_title.add_argument("title", help="Exact page title.")
    get_page_by_title.add_argument(
        "--expand",
        default="body.storage,version,ancestors,space",
        help="Confluence expand parameter.",
    )
    get_page_by_title.set_defaults(handler=handle_get_page_by_title)

    create_page = subparsers.add_parser("create-page", help="Create a page.")
    create_page.add_argument("space_key", help="Confluence space key.")
    create_page.add_argument("title", help="Page title.")
    create_page.add_argument("body", help="Storage Format body.")
    create_page.add_argument("--parent-id", help="Optional parent page ID.")
    create_page.set_defaults(handler=handle_create_page)

    update_page = subparsers.add_parser("update-page", help="Update a page.")
    update_page.add_argument("page_id", help="Confluence page ID.")
    update_page.add_argument("title", help="Page title.")
    update_page.add_argument("body", help="Storage Format body.")
    update_page.add_argument("version", type=int, help="Target version number.")
    update_page.set_defaults(handler=handle_update_page)

    add_comment = subparsers.add_parser("add-comment", help="Add a page comment.")
    add_comment.add_argument("page_id", help="Confluence page ID.")
    add_comment.add_argument("body", help="Storage Format comment body.")
    add_comment.set_defaults(handler=handle_add_comment)

    list_attachments = subparsers.add_parser("list-attachments", help="List page attachments.")
    list_attachments.add_argument("page_id", help="Confluence page ID.")
    list_attachments.add_argument("--limit", type=int, default=25, help="Maximum result count.")
    list_attachments.set_defaults(handler=handle_list_attachments)

    upload_attachment = subparsers.add_parser("upload-attachment", help="Upload an attachment.")
    upload_attachment.add_argument("page_id", help="Confluence page ID.")
    upload_attachment.add_argument("file_path", help="File path to upload.")
    upload_attachment.add_argument("--comment", default="", help="Optional upload comment.")
    upload_attachment.set_defaults(handler=handle_upload_attachment)

    download_page = subparsers.add_parser(
        "download-page",
        help="Download a page body to a local file.",
    )
    download_page.add_argument("page_id", help="Confluence page ID.")
    download_page.add_argument("output", help="Output file path.")
    download_page.add_argument(
        "--body-format",
        choices=("storage", "view"),
        default="storage",
        help="Page body representation to save.",
    )
    download_page.add_argument(
        "--text",
        action="store_true",
        help="Convert the HTML body to plain text before saving.",
    )
    download_page.set_defaults(handler=handle_download_page)

    return parser


def should_read_json_from_stdin(argv: list[str]) -> bool:
    """无命令参数且 stdin 有管道输入时，启用 JSON action 模式。"""
    return not argv and not sys.stdin.isatty()


def main(argv: list[str] | None = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)

    try:
        if should_read_json_from_stdin(actual_argv):
            command_text = _read_stdin_text()
            if not command_text.strip():
                raise ConfluenceApiError("No JSON command received from stdin")
            command = json.loads(command_text)
            print_json(handle_json_command(command))
            return 0

        parser = build_parser()
        args = parser.parse_args(actual_argv)
        args.handler(args)
        return 0
    except KeyError as exc:
        _write_stderr(f"Error: Missing required field: {exc}\n")
    except (ConfluenceApiError, json.JSONDecodeError) as exc:
        _write_stderr(f"Error: {exc}\n")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

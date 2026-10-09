"""Liner 검색 결과를 정규화한다. 원격 페이지를 직접 다운로드하지 않는다."""
import ipaddress
from urllib.parse import urlsplit
import requests
from django.conf import settings

class SearchUnavailable(Exception):
    pass

def valid_source_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            return False
        if "." not in host or host.lower().endswith((".local", ".localhost")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False

def search_sources(query, kind, *, timeout=None):
    if kind not in ("SCHOLAR", "WEB"):
        raise ValueError("Unsupported search kind")
    mode = "scholar" if kind == "SCHOLAR" else "web"
    payload = {
        "query": query,
        "max_results": min(5, max(1, settings.LINER_SEARCH_MAX_RESULTS)),
        "lang": "en" if kind == "SCHOLAR" else "ko",
    }
    try:
        response = requests.post(
            f"https://platform.liner.com/api/v1/tools/search/{mode}",
            headers={"x-api-key": settings.LINER_API_KEY, "Content-Type": "application/json"},
            json=payload, timeout=(5, timeout or settings.LINER_SEARCH_TIMEOUT_SECONDS),
        )
        if not response.ok:
            raise SearchUnavailable(f"Search HTTP {response.status_code}")
        results = response.json().get("results")
        if not isinstance(results, list):
            raise SearchUnavailable("Invalid search response")
    except (requests.RequestException, ValueError, AttributeError):
        raise SearchUnavailable("Search connection or response error") from None
    normalized = []
    for item in results[:payload["max_results"]]:
        if not isinstance(item, dict) or not valid_source_url(item.get("url")):
            continue
        title, description = item.get("title"), item.get("description")
        if not isinstance(title, str) or not title.strip() or not isinstance(description, str) or not description.strip():
            continue
        authors = item.get("authors", [])
        if not isinstance(authors, list):
            authors = []
        publisher = item.get("journal") or item.get("hostname") or urlsplit(item["url"]).hostname
        normalized.append({
            "kind": kind, "title": title[:500], "url": item["url"],
            "publisher": str(publisher)[:255],
            "authors": [str(author)[:255] for author in authors[:20]],
            "published_date": str(item.get("date") or "")[:50],
            "description": description[:4000],
        })
    return normalized

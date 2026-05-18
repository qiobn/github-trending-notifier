"""
GitHub Trending 每日推送脚本
- 抓取当天 GitHub Trending 前 10 个项目
- 检索近一年热门项目随机推送 5 个
- 使用 AI 批量生成中文概要（主要功能、技术栈、应用场景）
- 通过 Server酱推送到微信
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

GITHUB_TRENDING_URL = "https://github.com/trending"
GITHUB_SEARCH_API = "https://api.github.com/search/repositories"
SERVERCHAN_API = "https://sctapi.ftqq.com/{key}.send"


def fetch_trending_repos(limit=10):
    """抓取 GitHub Trending 页面，返回前 limit 个项目信息"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36"
    }
    resp = requests.get(GITHUB_TRENDING_URL, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    repo_list = soup.select("article.Box-row")

    repos = []
    for article in repo_list[:limit]:
        h2 = article.select_one("h2 a")
        if not h2:
            continue
        repo_path = h2.get("href", "").strip()
        repo_name = "/".join(
            part.strip() for part in h2.get_text().strip().split("/")
        )
        repo_url = f"https://github.com{repo_path}"

        desc_tag = article.select_one("p")
        description = desc_tag.get_text().strip() if desc_tag else "暂无描述"

        lang_tag = article.select_one("span[itemprop='programmingLanguage']")
        language = lang_tag.get_text().strip() if lang_tag else "未知"

        star_today_tag = article.select_one(
            "span.d-inline-block.float-sm-right"
        )
        stars_today = (
            star_today_tag.get_text().strip() if star_today_tag else "N/A"
        )

        repos.append({
            "name": repo_name,
            "url": repo_url,
            "description": description,
            "language": language,
            "stars_today": stars_today,
        })

    return repos


def fetch_yearly_hot_repos(count=5):
    """通过 GitHub Search API 检索近一年星标最多的项目，随机返回 count 个"""
    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    params = {
        "q": f"created:>{one_year_ago} stars:>500",
        "sort": "stars",
        "order": "desc",
        "per_page": 50,
    }
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GitHub-Trending-Notifier",
    }
    gh_token = os.environ.get("GITHUB_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    resp = requests.get(
        GITHUB_SEARCH_API, params=params, headers=headers, timeout=30
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])

    if len(items) <= count:
        selected = items
    else:
        selected = random.sample(items, count)

    repos = []
    for item in selected:
        repos.append({
            "name": item["full_name"],
            "url": item["html_url"],
            "description": item.get("description") or "暂无描述",
            "language": item.get("language") or "未知",
            "stars": item.get("stargazers_count", 0),
        })
    return repos


def fetch_readme(repo_name):
    """获取仓库 README 内容（截取前 1500 字符）"""
    headers = {
        "Accept": "application/vnd.github.v3.raw",
        "User-Agent": "GitHub-Trending-Notifier",
    }
    gh_token = os.environ.get("GITHUB_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    url = f"https://api.github.com/repos/{repo_name}/readme"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.text[:1500]
    except Exception:
        pass
    return ""


def _repair_json_quotes(s):
    """把 JSON 字符串值内部未转义的 " 替换成中文引号，避免解析失败"""
    out = []
    i = 0
    in_string = False
    while i < len(s):
        ch = s[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue
        if ch == "\\":
            out.append(ch)
            if i + 1 < len(s):
                out.append(s[i + 1])
                i += 2
            else:
                i += 1
            continue
        if ch == '"':
            j = i + 1
            while j < len(s) and s[j] in " \t\n\r":
                j += 1
            if j < len(s) and s[j] in ',:}]':
                out.append(ch)
                in_string = False
                i += 1
            else:
                out.append("”")
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def ai_batch_summarize(repos, max_retries=3):
    """批量调用 AI 接口，使用 JSON 格式严格匹配项目与概要"""
    import re

    api_key = os.environ.get("AI_API_KEY")
    api_base = os.environ.get("AI_API_BASE", "https://api.openai.com/v1")
    ai_model = os.environ.get("AI_MODEL", "gpt-4o-mini")

    if not api_key:
        return {repo["name"]: repo["description"] for repo in repos}

    projects_text = ""
    for i, repo in enumerate(repos, 1):
        readme = fetch_readme(repo["name"])
        projects_text += (
            f"\n[项目{i}] 名称: {repo['name']}\n"
            f"  描述: {repo['description']}\n"
            f"  README: {readme[:500] if readme else '无'}\n"
        )

    prompt = f"""你是一个项目分析助手。请阅读下面 {len(repos)} 个 GitHub 项目，为每个项目生成一句中文概要。

要求：
- 每个概要 50-80 字，涵盖：主要功能、技术栈、应用场景
- 必须严格按照 JSON 格式输出，禁止输出任何解释、思考过程或前后缀
- 输出必须是包含 {len(repos)} 个对象的 JSON 数组
- 每个对象包含 "id"（项目编号 1 到 {len(repos)}）和 "summary"（中文概要）字段
- 概要内容中如需引号，必须使用中文引号「」或『』，绝对不要使用英文双引号 "

输出格式示例：
[
  {{"id": 1, "summary": "项目1的中文概要..."}},
  {{"id": 2, "summary": "项目2的中文概要..."}}
]

项目信息：
{projects_text}

请直接输出 JSON 数组，不要任何其他文字："""

    # 主模型 + 多个 fallback 备用模型（已验证当前 OpenRouter 真实存在的免费模型）
    fallback_models = [
        ai_model,
        "z-ai/glm-4.5-air:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen3-coder:free",
        "nousresearch/hermes-3-llama-3.1-405b:free",
    ]
    seen = set()
    models_to_try = [m for m in fallback_models if not (m in seen or seen.add(m))]

    for model_idx, model in enumerate(models_to_try):
        print(f"  尝试模型 [{model_idx + 1}/{len(models_to_try)}]: {model}")
        for attempt in range(2):
            try:
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 3000,
                    "temperature": 0.3,
                }
                if "openrouter" in api_base:
                    payload["reasoning"] = {"enabled": False}

                resp = requests.post(
                    f"{api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=90,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"].strip()
                print(f"    AI 返回内容长度: {len(content)} 字符")

                # 尝试匹配 ```json ... ``` 包裹和裸 JSON 数组两种格式
                cleaned = content
                code_block_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', content, re.DOTALL)
                if code_block_match:
                    cleaned = code_block_match.group(1)
                else:
                    start = cleaned.find("[")
                    end = cleaned.rfind("]")
                    if start != -1 and end > start:
                        cleaned = cleaned[start : end + 1]

                try:
                    items = json.loads(cleaned)
                except json.JSONDecodeError as e:
                    repaired = _repair_json_quotes(cleaned)
                    try:
                        items = json.loads(repaired)
                        print(f"    JSON 修复后解析成功")
                    except json.JSONDecodeError:
                        print(f"    JSON 解析失败: {e}")
                        print(f"    返回前 300 字: {content[:300]}")
                        break

                result = {}
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    idx = item.get("id")
                    summary = item.get("summary", "").strip()
                    if isinstance(idx, int) and 1 <= idx <= len(repos) and summary:
                        result[repos[idx - 1]["name"]] = summary

                for repo in repos:
                    if repo["name"] not in result:
                        result[repo["name"]] = repo["description"]

                matched = sum(1 for r in repos if result.get(r["name"]) != r["description"])
                if matched > 0:
                    print(f"  成功匹配概要: {matched}/{len(repos)} 个项目（模型: {model}）")
                    return result

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else None
                if status == 429:
                    print(f"    {model} 限流（429），切换备用模型")
                    break
                else:
                    print(f"    HTTP {status} 错误，等待 5 秒后重试")
                    time.sleep(5)
            except Exception as e:
                print(f"    调用异常: {e}")
                if attempt == 0:
                    time.sleep(3)

    print("  所有模型均失败，回退为英文描述")
    return {repo["name"]: repo["description"] for repo in repos}


def format_message(trending_repos, yearly_repos):
    """将项目列表格式化为 Markdown 消息"""
    today = datetime.now().strftime("%Y-%m-%d")
    title = f"GitHub 热门项目日报 ({today})"

    lines = [f"# {title}\n"]

    lines.append("## 今日 Trending Top 10\n")
    for i, repo in enumerate(trending_repos, 1):
        lines.append(f"### {i}. [{repo['name']}]({repo['url']})\n")
        lines.append(f"{repo.get('summary', repo['description'])}\n")
        lines.append(f"- 语言：{repo['language']}")
        lines.append(f"- 今日 Star：{repo['stars_today']}")
        lines.append("")

    lines.append("---\n")
    lines.append("## 近一年热门项目随机推荐\n")
    for i, repo in enumerate(yearly_repos, 1):
        lines.append(f"### {i}. [{repo['name']}]({repo['url']})\n")
        lines.append(f"{repo.get('summary', repo['description'])}\n")
        lines.append(f"- 语言：{repo['language']}")
        lines.append(f"- 总 Star：{repo['stars']:,}")
        lines.append("")

    if not trending_repos and not yearly_repos:
        lines.append("今日暂无热门项目数据，请稍后重试。")

    return title, "\n".join(lines)


def send_to_serverchan(title, content, sendkey):
    """通过 Server酱推送消息到微信"""
    url = SERVERCHAN_API.format(key=sendkey)
    data = {"title": title, "desp": content}
    resp = requests.post(url, data=data, timeout=30)
    resp.raise_for_status()
    result = resp.json()

    if result.get("code") == 0:
        print("推送成功！")
    else:
        print(f"推送失败：{result}")
        sys.exit(1)


def main():
    sendkey = os.environ.get("SERVERCHAN_SENDKEY")
    if not sendkey:
        print("错误：未设置环境变量 SERVERCHAN_SENDKEY")
        sys.exit(1)

    # 1. 抓取今日 Trending Top 10
    print("正在抓取 GitHub Trending Top 10...")
    trending_repos = fetch_trending_repos(limit=10)
    print(f"  获取到 {len(trending_repos)} 个今日热门项目")

    # 2. 检索近一年热门项目随机 5 个
    print("正在检索近一年热门项目...")
    yearly_repos = fetch_yearly_hot_repos(count=5)
    print(f"  随机选取 {len(yearly_repos)} 个年度热门项目")

    # 3. AI 批量生成概要（OpenRouter 限流宽松，分 2 批，间隔 5 秒）
    print("正在批量生成 AI 概要...")

    print("  [1/2] Trending Top 10...")
    batch1 = ai_batch_summarize(trending_repos)
    for repo in trending_repos:
        repo["summary"] = batch1.get(repo["name"], repo["description"])

    time.sleep(5)

    print("  [2/2] 年度推荐项目...")
    batch2 = ai_batch_summarize(yearly_repos)
    for repo in yearly_repos:
        repo["summary"] = batch2.get(repo["name"], repo["description"])

    # 4. 格式化并推送
    title, content = format_message(trending_repos, yearly_repos)
    print(f"\n标题：{title}")
    print("---")
    print(content[:800] + "\n...(截断)")
    print("---")

    print("\n正在推送到微信...")
    send_to_serverchan(title, content, sendkey)


if __name__ == "__main__":
    main()

"""
GitHub Trending 每日推送脚本
- 抓取当天 GitHub Trending 前 10 个项目
- 检索近一年热门项目随机推送 5 个
- 使用 AI 生成中文概要（主要功能、技术栈、应用场景）
- 通过 Server酱推送到微信
"""

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

    resp = requests.get(GITHUB_SEARCH_API, params=params, headers=headers, timeout=30)
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
    """获取仓库 README 内容（截取前 3000 字符避免 token 过长）"""
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
            return resp.text[:3000]
    except Exception:
        pass
    return ""


def ai_summarize(repo_name, description, readme_content):
    """调用 AI 接口生成中文项目概要"""
    api_key = os.environ.get("AI_API_KEY")
    api_base = os.environ.get("AI_API_BASE", "https://api.openai.com/v1")
    ai_model = os.environ.get("AI_MODEL", "gpt-4o-mini")

    if not api_key:
        return f"*{description}*"

    prompt = f"""请根据以下 GitHub 项目信息，用中文给出简洁的项目概要（100-150字），包含：
1. 主要功能
2. 技术栈
3. 应用场景

项目名：{repo_name}
项目描述：{description}
README 摘录：
{readme_content[:2000]}
"""

    try:
        resp = requests.post(
            f"{api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": ai_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.7,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  AI 概要生成失败({repo_name}): {e}")
        return f"*{description}*"


def format_message(trending_repos, yearly_repos):
    """将项目列表格式化为 Markdown 消息"""
    today = datetime.now().strftime("%Y-%m-%d")
    title = f"GitHub 热门项目日报 ({today})"

    lines = [f"# {title}\n"]

    # 今日热门 Top 10
    lines.append("## 今日 Trending Top 10\n")
    for i, repo in enumerate(trending_repos, 1):
        lines.append(f"### {i}. [{repo['name']}]({repo['url']})\n")
        lines.append(f"{repo.get('summary', repo['description'])}\n")
        lines.append(f"- 语言：{repo['language']}")
        lines.append(f"- 今日 Star：{repo['stars_today']}")
        lines.append("")

    # 近一年热门随机推荐
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

    # 3. AI 生成概要（每次调用间隔 4 秒，避免触发频率限制）
    print("正在生成 AI 概要...")
    all_repos = trending_repos + yearly_repos
    for idx, repo in enumerate(all_repos):
        print(f"  处理: {repo['name']}")
        readme = fetch_readme(repo["name"])
        repo["summary"] = ai_summarize(repo["name"], repo["description"], readme)
        if idx < len(all_repos) - 1:
            time.sleep(4)

    # 4. 格式化并推送
    title, content = format_message(trending_repos, yearly_repos)
    print(f"\n标题：{title}")
    print("---")
    print(content[:500] + "...(截断)")
    print("---")

    print("\n正在推送到微信...")
    send_to_serverchan(title, content, sendkey)


if __name__ == "__main__":
    main()

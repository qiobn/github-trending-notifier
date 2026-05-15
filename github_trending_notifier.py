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


def ai_batch_summarize(repos, max_retries=3):
    """批量调用 AI 接口，一次性为多个项目生成中文概要（最多 5 个一批）"""
    api_key = os.environ.get("AI_API_KEY")
    api_base = os.environ.get("AI_API_BASE", "https://api.openai.com/v1")
    ai_model = os.environ.get("AI_MODEL", "gpt-4o-mini")

    if not api_key:
        return {repo["name"]: repo["description"] for repo in repos}

    projects_text = ""
    for i, repo in enumerate(repos, 1):
        readme = fetch_readme(repo["name"])
        projects_text += (
            f"\n【项目{i}】{repo['name']}\n"
            f"描述：{repo['description']}\n"
            f"README：{readme[:600] if readme else '无'}\n"
        )

    prompt = f"""请对以下{len(repos)}个GitHub项目各写一句中文概要（50-80字），涵盖功能、技术栈、应用场景。

严格按格式返回，每行一个，格式为"序号. 概要内容"，如：
1. 这是第一个项目的概要...
2. 这是第二个项目的概要...

{projects_text}

请输出{len(repos)}行概要："""

    for attempt in range(max_retries):
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
                    "max_tokens": 1500,
                    "temperature": 0.7,
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            print(f"  AI 返回内容长度: {len(content)} 字符")

            # 解析编号列表格式 "1. xxx\n2. xxx\n..."
            summaries = []
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # 去掉开头的序号 "1. " "2. " 等
                for prefix_len in range(1, 4):
                    prefix = line[:prefix_len + 2]
                    if prefix.endswith(". ") or prefix.endswith("．"):
                        line = line[prefix_len + 2:].strip()
                        break
                    elif prefix.endswith("."):
                        line = line[prefix_len + 1:].strip()
                        break
                if line:
                    summaries.append(line)

            if len(summaries) >= len(repos):
                return {
                    repos[i]["name"]: summaries[i]
                    for i in range(len(repos))
                }
            elif summaries:
                print(f"  部分解析成功: {len(summaries)}/{len(repos)}")
                result = {}
                for i in range(len(repos)):
                    if i < len(summaries):
                        result[repos[i]["name"]] = summaries[i]
                    else:
                        result[repos[i]["name"]] = repos[i]["description"]
                return result

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                wait_time = 20 * (attempt + 1)
                print(f"  触发频率限制，等待 {wait_time} 秒后重试 ({attempt + 1}/{max_retries})...")
                time.sleep(wait_time)
            else:
                print(f"  AI 调用失败: {e}")
                break
        except Exception as e:
            print(f"  AI 调用异常: {e}")
            if attempt < max_retries - 1:
                time.sleep(15)
            else:
                break

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

    # 3. AI 批量生成概要（分 3 批，每批 5 个，间隔 20 秒）
    print("正在批量生成 AI 概要...")

    print("  [1/3] Trending 1-5...")
    batch1 = ai_batch_summarize(trending_repos[:5])
    for repo in trending_repos[:5]:
        repo["summary"] = batch1.get(repo["name"], repo["description"])

    time.sleep(20)

    print("  [2/3] Trending 6-10...")
    batch2 = ai_batch_summarize(trending_repos[5:])
    for repo in trending_repos[5:]:
        repo["summary"] = batch2.get(repo["name"], repo["description"])

    time.sleep(20)

    print("  [3/3] 年度推荐项目...")
    batch3 = ai_batch_summarize(yearly_repos)
    for repo in yearly_repos:
        repo["summary"] = batch3.get(repo["name"], repo["description"])

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

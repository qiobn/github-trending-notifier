"""
GitHub Trending 每日推送脚本
抓取 GitHub 当天热门项目 Top 5，通过 Server酱推送到微信
"""

import os
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup

GITHUB_TRENDING_URL = "https://github.com/trending"
SERVERCHAN_API = "https://sctapi.ftqq.com/{key}.send"


def fetch_trending_repos(limit=5):
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
        # 项目名称和链接
        h2 = article.select_one("h2 a")
        if not h2:
            continue
        repo_path = h2.get("href", "").strip()
        repo_name = "/".join(
            part.strip() for part in h2.get_text().strip().split("/")
        )
        repo_url = f"https://github.com{repo_path}"

        # 项目描述
        desc_tag = article.select_one("p")
        description = desc_tag.get_text().strip() if desc_tag else "暂无描述"

        # 编程语言
        lang_tag = article.select_one(
            "span[itemprop='programmingLanguage']"
        )
        language = lang_tag.get_text().strip() if lang_tag else "未知"

        # 今日 Star 数
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


def format_message(repos):
    """将项目列表格式化为 Markdown 消息"""
    today = datetime.now().strftime("%Y-%m-%d")
    title = f"GitHub 热门项目日报 ({today})"

    lines = [f"## {title}\n"]
    for i, repo in enumerate(repos, 1):
        lines.append(f"### {i}. [{repo['name']}]({repo['url']})\n")
        lines.append(f"**描述：** {repo['description']}\n")
        lines.append(f"- 语言：{repo['language']}")
        lines.append(f"- 今日 Star：{repo['stars_today']}")
        lines.append("")

    if not repos:
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

    print("正在抓取 GitHub Trending...")
    repos = fetch_trending_repos(limit=5)
    print(f"获取到 {len(repos)} 个热门项目")

    title, content = format_message(repos)
    print(f"标题：{title}")
    print("---")
    print(content)
    print("---")

    print("正在推送到微信...")
    send_to_serverchan(title, content, sendkey)


if __name__ == "__main__":
    main()

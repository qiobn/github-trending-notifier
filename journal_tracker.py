"""
期刊最新文献追踪器
- 通过 CrossRef API 获取指定期刊最近发表的文章
- 通过 Semantic Scholar API 补充摘要和关键词
- 通过 OpenAlex API 作为备选摘要来源
- AI 翻译标题和摘要为中文
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests

# Cities 期刊 ISSN
DEFAULT_ISSN = "0264-2751"
DEFAULT_JOURNAL_NAME = "Cities"

CROSSREF_API = "https://api.crossref.org/journals/{issn}/works"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
OPENALEX_API = "https://api.openalex.org/works"
SERVERCHAN_API = "https://sctapi.ftqq.com/{key}.send"


def fetch_recent_articles(issn=DEFAULT_ISSN, days=30, limit=20):
    """通过 CrossRef 获取指定期刊近 N 天的最新文章"""
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {
        "filter": f"from-pub-date:{from_date}",
        "sort": "published",
        "order": "desc",
        "rows": limit,
    }
    headers = {
        "User-Agent": "JournalTracker/1.0 (mailto:research@example.com)",
    }

    print(f"正在查询 CrossRef（ISSN: {issn}，近 {days} 天）...")
    resp = requests.get(
        CROSSREF_API.format(issn=issn), params=params, headers=headers, timeout=30
    )
    resp.raise_for_status()
    data = resp.json()

    items = data.get("message", {}).get("items", [])
    articles = []
    for item in items:
        import re
        title = item.get("title", ["无标题"])[0]
        authors = ", ".join(
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in item.get("author", [])[:5]
        )
        if len(item.get("author", [])) > 5:
            authors += " et al."

        doi = item.get("DOI", "")
        url = item.get("URL", f"https://doi.org/{doi}" if doi else "")

        date_parts = item.get("published", {}).get("date-parts", [[]])
        if date_parts and date_parts[0]:
            parts = date_parts[0]
            pub_date = "-".join(str(p) for p in parts)
        else:
            pub_date = "未知"

        abstract = item.get("abstract", "")
        if abstract:
            abstract = re.sub(r"<[^>]+>", "", abstract).strip()

        articles.append({
            "title": title,
            "authors": authors,
            "doi": doi,
            "url": url,
            "pub_date": pub_date,
            "abstract": abstract,
            "keywords": item.get("subject", []),
        })

    return articles


def enrich_with_semantic_scholar(articles):
    """通过 Semantic Scholar API 补充摘要和关键词（免费，无需 Key）"""
    enriched = 0
    for article in articles:
        if not article["doi"]:
            continue

        try:
            url = SEMANTIC_SCHOLAR_API.format(doi=article["doi"])
            params = {"fields": "abstract,tldr,fieldsOfStudy"}
            resp = requests.get(url, params=params, timeout=15)

            if resp.status_code == 200:
                data = resp.json()
                if not article["abstract"] and data.get("abstract"):
                    article["abstract"] = data["abstract"]
                    enriched += 1

                if data.get("fieldsOfStudy"):
                    article["keywords"] = list(set(
                        article["keywords"] + data["fieldsOfStudy"]
                    ))

                if data.get("tldr") and data["tldr"].get("text"):
                    article["tldr"] = data["tldr"]["text"]

            # Semantic Scholar 有频率限制，间隔请求
            time.sleep(1)
        except Exception:
            pass

    return enriched


def enrich_with_openalex(articles):
    """通过 OpenAlex API 补充仍缺失摘要的文章"""
    enriched = 0
    for article in articles:
        if article["abstract"] or not article["doi"]:
            continue

        try:
            params = {"filter": f"doi:{article['doi']}"}
            resp = requests.get(OPENALEX_API, params=params, timeout=15)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if results:
                    work = results[0]
                    inv_abstract = work.get("abstract_inverted_index")
                    if inv_abstract:
                        article["abstract"] = reconstruct_abstract(inv_abstract)
                        enriched += 1
                    # OpenAlex 也有 keywords/concepts
                    concepts = work.get("concepts", [])
                    if concepts:
                        kws = [c["display_name"] for c in concepts[:5]]
                        article["keywords"] = list(set(article["keywords"] + kws))
        except Exception:
            pass

    return enriched


def reconstruct_abstract(inverted_index):
    """从 OpenAlex 的倒排索引还原摘要文本"""
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(word for _, word in word_positions)


def _repair_json_quotes(s):
    """把 JSON 字符串值内部未转义的 " 替换成中文引号，避免解析失败"""
    import re

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
        # 在字符串内部
        if ch == "\\":
            out.append(ch)
            if i + 1 < len(s):
                out.append(s[i + 1])
                i += 2
            else:
                i += 1
            continue
        if ch == '"':
            # 看下一个非空白字符判断是不是真的字符串结束
            j = i + 1
            while j < len(s) and s[j] in " \t\n\r":
                j += 1
            if j < len(s) and s[j] in ',:}]':
                out.append(ch)
                in_string = False
                i += 1
            else:
                # 字符串内部的 "，替换成中文引号
                out.append("”" if "“" not in "".join(out[-30:]) else "“")
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _call_ai(prompt, api_key, api_base, model, retry_on_429=True):
    """统一的 AI 调用封装（返回 content 字符串或抛异常）"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4000,
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
    return resp.json()["choices"][0]["message"]["content"].strip()


def ai_translate_batch(articles):
    """批量翻译标题和摘要为中文（JSON+id 格式，多模型 fallback）"""
    import re

    api_key = os.environ.get("AI_API_KEY")
    api_base = os.environ.get("AI_API_BASE", "https://openrouter.ai/api/v1")

    if not api_key:
        print("  未配置 AI_API_KEY，跳过翻译")
        return

    # 主模型 + 多个 fallback 备用模型（已验证当前 OpenRouter 真实存在的免费模型）
    primary_model = os.environ.get("AI_MODEL", "deepseek/deepseek-v4-flash:free")
    fallback_models = [
        primary_model,
        "z-ai/glm-4.5-air:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen3-coder:free",
        "nousresearch/hermes-3-llama-3.1-405b:free",
    ]
    # 去重保持顺序
    seen = set()
    models_to_try = [m for m in fallback_models if not (m in seen or seen.add(m))]

    items_text = ""
    for i, article in enumerate(articles, 1):
        abstract_part = article["abstract"][:400] if article["abstract"] else "无"
        items_text += (
            f"\n[文章{i}] 标题: {article['title']}\n"
            f"  摘要: {abstract_part}\n"
        )

    prompt = f"""你是一个学术翻译助手。请将以下 {len(articles)} 篇英文学术论文的标题和摘要翻译为中文。

要求：
- 标题翻译准确、学术化
- 摘要翻译简洁，保留核心信息，控制在 100 字以内
- 必须严格按 JSON 格式输出，禁止输出任何解释、思考过程或前后缀
- 输出必须是包含 {len(articles)} 个对象的 JSON 数组
- 每个对象包含 "id"（文章编号 1-{len(articles)}）、"title_zh"（中文标题）、"abstract_zh"（中文摘要）字段
- 如果原摘要为「无」，abstract_zh 填「暂无摘要」
- 翻译内容中如需引号，必须使用中文引号「」或『』，绝对不要使用英文双引号 "

输出格式示例：
[
  {{"id": 1, "title_zh": "中文标题1", "abstract_zh": "中文摘要1..."}},
  {{"id": 2, "title_zh": "中文标题2", "abstract_zh": "中文摘要2..."}}
]

文章列表：
{items_text}

请直接输出 JSON 数组，不要任何其他文字："""

    for model_idx, model in enumerate(models_to_try):
        print(f"  尝试模型 [{model_idx + 1}/{len(models_to_try)}]: {model}")
        for attempt in range(2):  # 每个模型最多重试 2 次
            try:
                content = _call_ai(prompt, api_key, api_base, model)
                print(f"    AI 返回内容长度: {len(content)} 字符")

                # 尝试匹配 ```json ... ``` 包裹和裸 JSON 数组两种格式
                cleaned = content
                code_block_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', content, re.DOTALL)
                if code_block_match:
                    cleaned = code_block_match.group(1)
                else:
                    # 找第一个 [ 到最后一个 ]，最大化 JSON 范围
                    start = cleaned.find("[")
                    end = cleaned.rfind("]")
                    if start != -1 and end > start:
                        cleaned = cleaned[start : end + 1]

                try:
                    items = json.loads(cleaned)
                except json.JSONDecodeError as e:
                    # 兜底：把 JSON 字段值内部出现的非转义 " 转成中文引号后重试
                    repaired = _repair_json_quotes(cleaned)
                    try:
                        items = json.loads(repaired)
                        print(f"    JSON 修复后解析成功")
                    except json.JSONDecodeError:
                        print(f"    JSON 解析失败: {e}")
                        print(f"    返回前 300 字: {content[:300]}")
                        break  # 换下个模型

                for item in items:
                    if not isinstance(item, dict):
                        continue
                    idx = item.get("id")
                    if isinstance(idx, int) and 1 <= idx <= len(articles):
                        articles[idx - 1]["title_zh"] = (item.get("title_zh") or "").strip()
                        articles[idx - 1]["abstract_zh"] = (item.get("abstract_zh") or "").strip()

                translated = sum(1 for a in articles if a.get("title_zh"))
                if translated > 0:
                    print(f"  AI 翻译完成：{translated}/{len(articles)} 篇（模型: {model}）")
                    return

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else None
                if status == 429:
                    print(f"    {model} 限流（429），切换备用模型")
                    break  # 立即换下个模型
                else:
                    print(f"    HTTP {status} 错误，等待 5 秒后重试")
                    time.sleep(5)
            except Exception as e:
                print(f"    调用异常: {e}")
                if attempt == 0:
                    time.sleep(3)

    print("  所有模型均失败，跳过翻译")


def select_articles(articles, mode="latest", count=3, days_back=5):
    """
    选取文章：
    - mode="latest": 取最新的 count 篇
    - mode="daily": 从今天往前，每天取 count 篇（共 days_back 天）
    """
    if mode == "latest":
        return articles[:count]

    elif mode == "daily":
        # 按日期分组
        from collections import defaultdict
        by_date = defaultdict(list)
        for article in articles:
            date_key = article["pub_date"][:10]  # 取 YYYY-M-D 或 YYYY-MM-DD
            by_date[date_key].append(article)

        # 按日期倒序，每天取 count 篇
        result = []
        sorted_dates = sorted(by_date.keys(), reverse=True)
        for date in sorted_dates[:days_back]:
            result.extend(by_date[date][:count])

        return result

    return articles[:count]


def display_articles(articles, journal_name=DEFAULT_JOURNAL_NAME):
    """在终端中展示文章列表"""
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*70}")
    print(f"  {journal_name} 期刊文献追踪  ({today})")
    print(f"{'='*70}\n")

    if not articles:
        print("  未找到近期文献。")
        return

    for i, article in enumerate(articles, 1):
        print(f"[{i}] {article['title']}")
        if article.get("title_zh"):
            print(f"    中文: {article['title_zh']}")
        print(f"    作者: {article['authors'] or '未知'}")
        print(f"    日期: {article['pub_date']}")
        print(f"    DOI:  {article['doi']}")
        print(f"    链接: {article['url']}")

        if article.get("keywords"):
            print(f"    关键词: {', '.join(article['keywords'][:6])}")

        if article.get("abstract_zh"):
            print(f"    摘要(中文): {article['abstract_zh']}")
        elif article.get("abstract"):
            abstract_preview = article["abstract"][:250]
            if len(article["abstract"]) > 250:
                abstract_preview += "..."
            print(f"    摘要: {abstract_preview}")
        else:
            print(f"    摘要: （暂无）")

        if article.get("tldr"):
            print(f"    TL;DR: {article['tldr']}")

        print()

    print(f"{'='*70}")
    print(f"  共 {len(articles)} 篇文章")
    print(f"{'='*70}")


def format_message(articles, journal_name=DEFAULT_JOURNAL_NAME):
    """将文章列表格式化为 Markdown 推送消息"""
    today = datetime.now().strftime("%Y-%m-%d")
    title = f"{journal_name} 期刊文献日报 ({today})"

    if not articles:
        return title, "今日暂无新文献。"

    lines = [f"# {title}\n"]
    for i, article in enumerate(articles, 1):
        zh_title = article.get("title_zh", "")
        if zh_title:
            lines.append(f"## {i}. {zh_title}\n")
            lines.append(f"**原标题**: {article['title']}\n")
        else:
            lines.append(f"## {i}. {article['title']}\n")

        lines.append(f"- **作者**: {article['authors'] or '未知'}")
        lines.append(f"- **发表日期**: {article['pub_date']}")
        lines.append(f"- **DOI**: [{article['doi']}]({article['url']})")

        if article.get("keywords"):
            kw_str = ", ".join(article["keywords"][:6])
            lines.append(f"- **关键词**: {kw_str}")

        lines.append("")

        if article.get("abstract_zh") and article["abstract_zh"] != "暂无摘要":
            lines.append(f"**中文摘要**: {article['abstract_zh']}\n")
        elif article.get("abstract"):
            abstract_preview = article["abstract"][:300]
            if len(article["abstract"]) > 300:
                abstract_preview += "..."
            lines.append(f"**摘要(原文)**: {abstract_preview}\n")
        else:
            lines.append("*暂无摘要*\n")

        lines.append("---\n")

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
        return True
    else:
        print(f"推送失败：{result}")
        return False


def main():
    # 参数: python3 journal_tracker.py [模式] [ISSN]
    # 模式: latest (最近3篇) / daily (每天3篇往前追溯) / both (两者合并)
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    issn = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ISSN
    journal_name = os.environ.get("JOURNAL_NAME", DEFAULT_JOURNAL_NAME)

    # 1. 从 CrossRef 获取较多文章（供后续筛选）
    articles = fetch_recent_articles(issn=issn, days=60, limit=30)
    print(f"  CrossRef 返回 {len(articles)} 篇文章")

    # 2. 补充摘要和关键词
    print("正在通过 Semantic Scholar 补充摘要和关键词...")
    s2_count = enrich_with_semantic_scholar(articles)
    print(f"  Semantic Scholar 补充了 {s2_count} 篇")

    print("正在通过 OpenAlex 补充剩余摘要...")
    oa_count = enrich_with_openalex(articles)
    print(f"  OpenAlex 补充了 {oa_count} 篇")

    has_abstract = sum(1 for a in articles if a["abstract"])
    print(f"  摘要覆盖率: {has_abstract}/{len(articles)}")

    # 3. 选取文章
    if mode == "latest":
        selected = select_articles(articles, mode="latest", count=3)
    elif mode == "daily":
        selected = select_articles(articles, mode="daily", count=3, days_back=5)
    else:
        latest = select_articles(articles, mode="latest", count=3)
        daily = select_articles(articles, mode="daily", count=3, days_back=3)
        latest_dois = {a["doi"] for a in latest}
        daily = [a for a in daily if a["doi"] not in latest_dois]
        selected = latest + daily

    # 4. AI 翻译
    if selected:
        print("正在进行 AI 中文翻译...")
        ai_translate_batch(selected)

    # 5. 输出到终端
    display_articles(selected, journal_name=journal_name)

    # 6. 推送到微信（若已配置 SERVERCHAN_SENDKEY）
    sendkey = os.environ.get("SERVERCHAN_SENDKEY")
    if sendkey and selected:
        print("\n正在推送到微信...")
        title, content = format_message(selected, journal_name=journal_name)
        send_to_serverchan(title, content, sendkey)


if __name__ == "__main__":
    main()

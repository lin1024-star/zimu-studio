# -*- coding: utf-8 -*-
"""中文自检：翻译完成后，把「可能翻错、会让你剪错」的句子挑出来给使用者看。

## 为什么要做（2026-10-13 实测记录）

使用者的痛点是：**翻译错了，他照着错的意思剪片子，成片就错了**。
试过三种做法，只有下面这个组合达标：

| 做法 | 问题 |
| --- | --- |
| 只比「日文 vs 中文」 | 抓不到「识别错导致的中文错」—— 中文是忠实翻译，两边对得上 |
| 只看中文读不读得通 | 抓不到「翻译不忠实」的 |
| **两者合一**（本模块） | 提示词里同时给日文和中文，让模型自己选视角 → 互补 |

另外，「同一字符连续堆叠」（`前前前前前前`、识别乱码的典型形态）**提示词教不稳**
（模型总当成"重复"放行），所以用**本地规则**判，确定性、免费。

## 实测成绩

- 素材甲 147 条（识别质量差）：召回 **7/7**，报 12 处，有效精确率约 92%
- 素材乙 112 条（识别质量好）：报 3 处，**3 处全中**（精确率 100%）
  → 素材干净它就安静（每 37 条 1 处）；脏素材是每 12 条 1 处
- 成本：一期（112~147 条）约 ¥0.002

## 设计约束

1. **只报不改** —— 改由使用者决定，跟纠正表一个规矩
2. **本地规则不误伤正常复读**：有的素材台词里全是同一个词的复读，
   规则只认"同一个字连着 ≥4 次"，两字词往复抓不到 ✓ 已用 12 个用例验过
3. **失败不影响主流程** —— 自检出错只记日志，绝不能让翻译/导出失败
"""

# 常见语气字：这些连着重复是正常的（哈哈、啊啊、嗯嗯、呜呜…），不报
LOCAL_OK_CHARS = "嗯啊哈呀哦噢呃诶嘿呜哇呵欸哎嗨咦唔嘛吧啦呢哟"

BATCH = 25                 # 每批喂给模型多少条
MAX_ITEMS_PER_BATCH = 4    # 每批最多让它报几条（宁可漏，不要凑数）
MAX_GLOSSARY_CHARS = 900   # 术语表塞进提示词的截断长度


def stacked_run(text):
    """中文里「同一字符连续出现 ≥4 次」的片段；没有就返回 None。

    这是识别乱码的典型形态：日文重复喊「リナ」，识别成「マリナエ」，
    中文就变成「前前前前前前」。正常复读（「莉娜莉娜莉娜」）是两个字往复，
    抓不到 —— 这是故意的，见模块开头第 2 条约束。
    """
    import re
    for match in re.finditer(r"(.)\1{3,}", text or ""):
        if match.group(0)[0] not in LOCAL_OK_CHARS:
            return match.group(0)
    return None


def local_flags(cues):
    """本地规则：挑出中文里出现字符堆叠的条目。cues 是 project.json 的 cues。"""
    found = []
    for cue in cues:
        run = stacked_run(cue.get("zh") or "")
        if not run:
            continue
        found.append({
            "id": int(cue["id"]),
            "start": float(cue["start"]),
            "source": cue.get("source") or "",
            "zh": cue.get("zh") or "",
            "from": "本地规则",
            "why": "中文里「%s」连续重复了 %d 次 —— 像是识别乱码，不是正常重复" % (run[0], len(run)),
        })
    return found


def system_prompt(glossary, language="ja"):
    """自检用的系统提示词。

    注意：里面写死了**判据**（六类）和**不许报的清单**。
    判据放宽过一次（"中文跟日文对不上"），精度直接掉到 10% —— 别改宽。

    language：原文是什么语言。日语那套话术是实测调出来的（六轮迭代），
    **一个字都不改** ✓ 其它语言（英语等）用下面那套对应话术 ——
    判据完全一样，只是把"日文"换成"原文"、例子换成对应语言的。
    """
    if (language or "ja").strip().lower().startswith("ja"):
        return (
            "你是中文字幕质检员，替一位剪辑师把关。他不懂日语，只看中文；"
            "中文意思错了，他就会照着错的意思把片子剪错。\n"
            "\n"
            "下面每一组是【日文字幕】和【中文译文】（按时间顺序）。"
            "请挑出会让他剪错的条目。你可以比对日文，也可以只看中文读不读得通。\n"
            "\n"
            "典型情况（真人真事，照这个尺度找）：\n"
            "· 专有名词被听错或乱猜：日文「カンジャ」（=审会）写成「物端」；"
            "日文「高杉」（=太多了）写成人名「高杉」；日文「レイハヤト」（两个角色名组合）猜成「雷隼人」\n"
            "· 中文不成话：「容易被误解和容易被误解是女主角吗？」这种\n"
            "· 前后自相矛盾：前面说「我来解除翻译」，后面又说「我开下翻译」\n"
            "· 词义变了：日文「入れる」在打字场景是「输入」，写成「加入」；"
            "「分かち合う」（分享）写成「互相理解」；「超言われてて」（被反复要求）写成「被疯狂吐槽」\n"
            "\n"
            "人名以术语表为准：下面是这份字幕在用的固定译法，这些写法是正确的，不要报：\n"
            + (glossary or "（这次没有术语表）")[:MAX_GLOSSARY_CHARS] + "\n"
            "\n"
            "以下一律不要报：重复（角色复读、强调、口癖、拟声词都算正常）、漏语气词助词、"
            "措辞不同、不够文雅、断句、短句、翻译腔。\n"
            "不要报你只是觉得「可以翻得更好」的句子。\n"
            "\n"
            "每批最多报 %d 条，挑最明显的；拿不准就不要报。"
            '只输出 JSON：{"items":[{"id":1,"why":"一句话说清哪里会让你剪错"}]}；'
            "都没问题就返回空数组。" % MAX_ITEMS_PER_BATCH
        )
    # 非日语（目前主要是英语）：判据一模一样，只换话术和例子
    return (
        "你是中文字幕质检员，替一位剪辑师把关。他看不懂原文，只看中文；"
        "中文意思错了，他就会照着错的意思把片子剪错。\n"
        "\n"
        "下面每一组是【原文字幕】和【中文译文】（按时间顺序）。"
        "请挑出会让他剪错的条目。你可以比对原文，也可以只看中文读不读得通。\n"
        "\n"
        "典型情况（照这个尺度找）：\n"
        "· 专有名词被听错或乱猜：人名/地名/术语听成了另一个词，中文跟着错"
        "（例：\"Kendra\" 听成 \"Kendra's\"、\"Saitama\" 写成 \"Saitama City\"）\n"
        "· 中文不成话：「容易被误解和容易被误解是女主角吗？」这种\n"
        "· 前后自相矛盾：前面说「我来关掉翻译」，后面又说「我开下翻译」\n"
        "· 词义变了：\"right\" 在该表示「右边」的地方译成「正确」；"
        "\"present\" 在该表示「礼物」的地方译成「现在」；"
        "\"draw\" 在该表示「平局」的地方译成「画画」\n"
        "· 数字被改掉或丢掉：原文说 \"twenty thousand\"，中文写成「两千」或干脆没提\n"
        "\n"
        "人名以术语表为准：下面是这份字幕在用的固定译法，这些写法是正确的，不要报：\n"
        + (glossary or "（这次没有术语表）")[:MAX_GLOSSARY_CHARS] + "\n"
        "\n"
        "以下一律不要报：重复（口头禅、强调、拟声词都算正常）、漏语气词、"
        "措辞不同、不够文雅、断句、短句、翻译腔。\n"
        "不要报你只是觉得「可以翻得更好」的句子。\n"
        "\n"
        "每批最多报 %d 条，挑最明显的；拿不准就不要报。"
        '只输出 JSON：{"items":[{"id":1,"why":"一句话说清哪里会让你剪错"}]}；'
        "都没问题就返回空数组。" % MAX_ITEMS_PER_BATCH
    )


def batch_payload(cues):
    """把一批 cue 拼成给模型的 JSON 载荷。"""
    import json
    return json.dumps({"items": [{"id": c["id"], "日文": c["source"], "中文": c.get("zh") or ""}
                                 for c in cues]}, ensure_ascii=False)


def parse_reply(data, cues):
    """把模型返回的 JSON 转成 flag 列表。

    两条过滤（都是实测踩出来的）：
      · 模型有时**一边在理由里写「不报」一边还是报出来** —— 这种滤掉
      · id 可能不存在或不是数字 —— 滤掉
    """
    by_id = {int(c["id"]): c for c in cues}
    out = []
    for item in (data or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        why = str(item.get("why", ""))
        if "不报" in why:
            continue
        try:
            cue_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        cue = by_id.get(cue_id)
        if cue is None:
            continue
        out.append({
            "id": cue_id,
            "start": float(cue["start"]),
            "source": cue.get("source") or "",
            "zh": cue.get("zh") or "",
            "from": "模型",
            "why": why,
        })
    return out


def merge(local, model):
    """本地规则 + 模型结果合并：同一个 cue 只留一条（本地优先），按时间排序。"""
    merged = {int(item["id"]): dict(item) for item in local}
    for item in model:
        merged.setdefault(int(item["id"]), dict(item))
    return sorted(merged.values(), key=lambda item: item["start"])


def summarize(items, total=None):
    """一句话摘要，给日志和按钮用。"""
    if not items:
        return "中文自检：没有发现可疑的地方。"
    head = "中文自检：发现 %d 处可能翻错的地方。" % len(items)
    if total:
        head += "（共 %d 条，平均每 %d 条 1 处）" % (total, max(1, round(total / len(items))))
    return head


def check(cues, glossary, client, emit=None, stop=None, language="ja"):
    """跑完整自检：本地规则 + 模型分批。

    client 需要有 json_chat(system, user, max_tokens) 方法（core.DeepSeekClient ✓）。
    任何一批失败都只是少一批结果，不会抛出去 —— 自检绝不能让主流程失败。
    返回 {"items": [...], "batches": n, "failed": n}

    language：原文语言，决定提示词用哪套话术（日语 / 其它）。
    **判据两边完全一样**，只有"日文"这类措辞和举例不同 —— 见 system_prompt。
    """
    def say(message):
        if emit:
            try:
                emit("log", message)
            except Exception:
                pass

    local = local_flags(cues)
    say("中文自检：本地规则报 %d 处。" % len(local))

    system = system_prompt(glossary, language)
    model_items, failed, batches = [], 0, 0
    for index in range(0, len(cues), BATCH):
        if stop is not None and getattr(stop, "is_set", lambda: False)():
            say("中文自检：已取消。")
            break
        part = cues[index:index + BATCH]
        batches += 1
        data = None
        for attempt in range(3):
            try:
                data = client.json_chat(system, batch_payload(part), max_tokens=1400)
                break
            except Exception as exc:                      # noqa: BLE001
                if attempt == 2:
                    failed += 1
                    say("中文自检：第 %d 批失败（%s），跳过。" % (batches, type(exc).__name__))
                else:
                    import time
                    time.sleep(2 * (attempt + 1))
        model_items.extend(parse_reply(data, part))

    items = merge(local, model_items)
    say(summarize(items, len(cues)))
    return {"items": items, "batches": batches, "failed": failed}

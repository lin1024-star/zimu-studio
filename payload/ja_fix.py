"""日文纠正表：识别听错的日文词，在翻译之前按表改对。

为什么要有这个东西
------------------
实测（2026-10-12，SURGECITY 日语直播素材）：原音是「おしょく」，
识别模型有时写对（汚職）、有时写成一个日语里根本不存在的组合（お食）——
同一段素材里**写错 19 处、写对 17 处**。

换模型解决不了：turbo / anime-whisper / small 三个都写「お食」——
音频里就没有区分这两个词的信息。所以走硬替换：识别之后、翻译之前按表改对。
**这一层是确定性的，不靠模型猜。**

改完实测（同一批 17 条 × 四种组合：v4-pro / flash × 带术语表 / 不带）：

    · 日文错字（お食）          19 处 → 0        ✓
    · 中文里由错字带出的怪词      5 处 → 0        ✓（系官 / 系感 / 器官）
    · 中文「吃饭」               0 处 → 0 处      ← 见下面的提醒

    ⚠️ 提醒：现在这两个模型拿到「お食」也会结合上下文翻成"贪污"，
    **「吃饭」在改前就已经是 0**，不是纠正表救回来的。
    素材里那几处"吃饭"是更早一轮翻译留下的旧文字，只有重新翻译才会变。
    别把"吃饭归零"当成这个功能的功劳。

表放在数据根目录（`D:\\字幕工坊\\日文纠正表.txt`），
重装软件不会被清掉，**批量识别和实时字幕共用同一份**。

设计要点
--------
· 只改日文原文，**中文和时间轴一个字都不动**（Cue 的 start/end/zh 原样带过去）。
· **长的词优先替换**：「お食系官」必须先于「お食」，否则会替成「汚職系官」——
  数字上"错词没了"，其实新造了一个词。
· 一次扫描（正则交替）而不是连着 replace：替换出来的文字**不会再被后面的规则命中**，
  不会出现"改完又被改一遍"的连锁。
· 表不存在 / 读不了 → 当作空表，不报错、不拦着识别。
· 每一条改动都记账，供日志直接展示（使用者不懂日语，日志是他唯一的检查手段）。
"""
import re
from pathlib import Path

from core import Cue
from paths import data_root

TABLE_NAME = "日文纠正表.txt"

# 放一份带说明的表时的初始内容。**只是初始内容，不是写死的规则**：
# 删掉哪条就不生效，加一条就多一条。
#
# 下面 5 条来自真实素材实测：同一段直播里，模型自己在别处写了 17 次「汚職警官」，
# 说明原音就是「おしょく」，写成「系官 / 系感 / 器官」都是听错。
SEED = """# 日文纠正表 —— 识别把日文听错时，翻译之前按这张表改回正确的词
#
# 写法：一行一条，「识别错的 = 改对的」
#   · 井号开头是说明，不生效
#   · 只改日文原文，中文和时间轴一个字都不动
#   · 长短两个词都能匹配同一处时，程序按「长的优先」替换
#
# 这张表放在数据根目录，重装软件不会被清掉；批量识别和实时字幕共用同一份。
#
# ---------------------------------------------------------------------------
# 下面 5 条是一次真实素材实测出来的（SURGECITY 日语直播）：
# 原音都是「おしょく」，模型把「汚職」写成了几种日语里不存在的汉字组合
# —— 同一段素材里写错 19 处、写对 17 处。
#
# 为什么长的必须写在前面：「お食系官」如果先按「お食」替换，会变成「汚職系官」，
# 数字上看"错词没了"，其实新造了一个词。程序自己也会按长的优先，
# 写清楚只是方便人看。
#
# 不需要的话可以整段删掉。
# ---------------------------------------------------------------------------

お食系官 = 汚職警官
お食系感 = 汚職警官
お食器官 = 汚職警官
お食警官 = 汚職警官
お食 = 汚職
"""


def table_path(root=None):
    """表的位置。root 只给测试用，不传就是数据根目录。"""
    return Path(root) / TABLE_NAME if root is not None else data_root() / TABLE_NAME


def parse_table(text):
    """把表文解析成 [(错的, 对的), ...]，长的优先，重复的以第一条为准。

    解析不了的行直接跳过——表是给人手工编辑的，写错一行不该影响其它行。
    """
    pairs, seen = [], set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        wrong, _, right = line.partition("=")
        wrong, right = wrong.strip(), right.strip()
        if not wrong or not right or wrong == right or wrong in seen:
            continue
        seen.add(wrong)
        pairs.append((wrong, right))
    # 长的先替换：お食系官 必须比 お食 先匹配上。
    pairs.sort(key=lambda pair: (-len(pair[0]), pair[0]))
    return pairs


def load_table(root=None):
    """读表。不存在或读不了都返回空表，不抛异常。"""
    path = table_path(root)
    try:
        if not path.is_file():
            return []
        return parse_table(path.read_text("utf-8"))
    except (OSError, UnicodeError):
        return []


def ensure_table(root=None):
    """放一份带说明的表；已经存在就一个字都不动。

    **只在数据根目录已经存在时写**：目录都还没有，说明这只是一次测试、或者还没装好，
    不该凭空往人家机器上建目录。（装好的机器上，数据根目录一定在。）
    """
    path = table_path(root)
    if path.is_file() or not path.parent.is_dir():
        return path
    try:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(SEED)
    except OSError:
        pass
    return path


def _compile(table):
    """把表编成一个正则：交替分支按「长的在前」排，匹配到就是最长的那个。"""
    return re.compile("|".join(re.escape(wrong) for wrong, _ in table))


def apply_text(text, table):
    """按表替换一段文字，返回 (新文字, [(错的, 对的, 次数), ...])。

    只扫一遍：替换结果不会再被其它规则命中。
    """
    source = text or ""
    if not source or not table:
        return source, []
    mapping = dict(table)
    counts = {}

    def replace(match):
        word = match.group(0)
        counts[word] = counts.get(word, 0) + 1
        return mapping[word]

    fixed = _compile(table).sub(replace, source)
    order = [wrong for wrong, _ in table if wrong in counts]
    return fixed, [(word, mapping[word], counts[word]) for word in order]


def fix_cues(cues, table=None, root=None):
    """逐条修正日文原文，返回 (新列表, 报告)。

    只动 source；start / end / zh 原样带过去（时间轴和译文一个字都不变）。
    """
    if table is None:
        table = load_table(root)
    out, changes = [], []
    for cue in cues:
        fixed, hits = apply_text(cue.source, table)
        if fixed == cue.source:
            out.append(cue)
            continue
        changes.append({"kind": "ja_fix", "id": cue.id, "before": cue.source, "after": fixed,
                        "hits": [{"wrong": w, "right": r, "count": n} for w, r, n in hits]})
        out.append(Cue(cue.id, cue.start, cue.end, fixed, cue.zh))
    by_rule = {}
    for change in changes:
        for hit in change["hits"]:
            key = (hit["wrong"], hit["right"])
            by_rule[key] = by_rule.get(key, 0) + hit["count"]
    report = {"table_size": len(table), "cues_changed": len(changes),
              "replacements": sum(hit["count"] for change in changes for hit in change["hits"]),
              "by_rule": by_rule, "changes": changes}
    return out, report


def report_lines(report, per_cue=True):
    """把报告变成可以直接写进日志的几行中文。没改动时返回空列表。"""
    if not report.get("changes"):
        return []
    rules = "、".join(f"{wrong}→{right} ×{count}" for (wrong, right), count in report["by_rule"].items())
    lines = [f"日文纠正表：改了 {report['cues_changed']} 条、共 {report['replacements']} 处（{rules}）。"]
    if per_cue:
        for change in report["changes"]:
            lines.append(f"　第 {change['id']} 条：{change['before']} → {change['after']}")
    return lines


def count_word(cues, word):
    """数某个词在日文原文里出现了几次（验收用：改前改后各数一次）。"""
    return sum((cue.source or "").count(word) for cue in cues)


class TableCache:
    """按文件改动时间缓存表——改完表立刻生效，不用重启。

    实时字幕每翻一批就来取一次；文件没动过就用缓存，动过就读一次。
    读不到 / 表被删掉 → 当空表，不影响出字幕。
    """

    def __init__(self, root=None):
        self.root = root
        self._stamp = object()
        self._table = []

    def get(self):
        path = table_path(self.root)
        try:
            stamp = (path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None
        except OSError:
            stamp = None
        if stamp != self._stamp:
            self._stamp = stamp
            self._table = load_table(self.root)
        return self._table

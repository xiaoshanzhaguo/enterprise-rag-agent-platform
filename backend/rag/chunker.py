"""
RAG 文档切块模块。

职责：
1. 规范化上传文档文本，统一换行和空行格式。
2. 按段落优先切分文档，让每个 chunk 尽量保持语义完整。
3. 对企业制度、员工手册这类结构化文档，控制 chunk 粒度，避免引用片段过长。
4. 在必要时保留少量上下文重叠，但遇到新章节标题时不继承上一节尾部内容。

说明：
- 当前切块策略面向求职展示项目中的企业知识库问答场景。
- chunk 太大会导致检索命中片段冗长，前端引用面板难以阅读。
- chunk 太小会导致回答缺少上下文，因此默认使用中等偏小的切块粒度。
- 这里不引入复杂 NLP 分句器，优先使用可解释、易维护的规则。
"""

# 导入正则表达式模块，用于识别标题、压缩空行和处理文本切分
import re


# 默认文本块大小。企业制度类文档通常结构清晰，使用较小 chunk 更利于引用展示。
DEFAULT_CHUNK_SIZE = 360
# 默认重叠长度。只保留很少上下文，避免上一节内容大量混入下一节。
DEFAULT_CHUNK_OVERLAP = 40


def normalize_text(text: str) -> str:
    """
    规范化文档文本。

    函数说明：
    1. 统一 Windows、Mac 和 Linux 换行符。
    2. 将连续 3 个及以上空行压缩成 2 个换行。
    3. 去除首尾空白，避免生成空 chunk。

    :param text: 原始文档文本
    :return: 规范化后的文档文本
    """
    # 统一换行符，避免不同系统上传的文件切分结果不一致
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 压缩过多空行，保留段落之间的基本分隔
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 去除首尾空白
    return text.strip()


def is_section_heading(paragraph: str) -> bool:
    """
    判断段落是否像一个新章节标题。

    函数说明：
    1. 支持 Markdown 标题，例如：## 试用期制度。
    2. 支持中文章节标题，例如：第八章 试用期制度。
    3. 支持编号标题，例如：8.1 试用期长度。
    4. 用于决定切块时是否应该清除上一块的重叠内容。

    :param paragraph: 当前段落文本
    :return: True 表示当前段落像新章节标题；False 表示普通段落
    """
    # 取第一行判断标题，避免长段落中间内容影响识别
    first_line = paragraph.strip().splitlines()[0].strip() if paragraph.strip() else ""
    # 空段落不是标题
    if not first_line:
        return False

    # Markdown 标题，例如：# 标题、## 标题
    if re.match(r"^#{1,6}\s+", first_line):
        return True
    # 中文章节标题，例如：第一章、第八节、第十条
    if re.match(r"^第[一二三四五六七八九十百千万\d]+[章节条]", first_line):
        return True
    # 多级编号标题，例如：1.1 标题、8.1 试用期长度
    if re.match(r"^\d+(?:\.\d+)+\s*[\u4e00-\u9fffA-Za-z]", first_line):
        return True

    # 其他情况按普通段落处理
    return False


def build_overlap_text(chunk_text: str, overlap: int) -> str:
    """
    从上一块文本中提取少量重叠上下文。

    函数说明：
    1. overlap 小于等于 0 时不保留重叠。
    2. 优先按字符长度截取上一块尾部，降低切块导致的信息断裂。
    3. 去除截取结果首尾空白，避免新 chunk 开头出现多余空行。

    :param chunk_text: 上一个 chunk 的文本
    :param overlap: 希望保留的重叠字符数
    :return: 上一个 chunk 尾部的重叠文本
    """
    # 不需要重叠时直接返回空字符串
    if overlap <= 0:
        return ""

    # 清理上一块文本
    normalized_chunk = chunk_text.strip()
    # 上一块为空时没有可重叠内容
    if not normalized_chunk:
        return ""

    # 截取上一块尾部少量文本，并清理空白
    return normalized_chunk[-overlap:].strip()


def split_long_paragraph(paragraph: str, chunk_size: int) -> list[str]:
    """
    将超长段落进一步切成较短片段。

    函数说明：
    1. 普通段落如果超过 chunk_size，会导致单个 chunk 仍然过长。
    2. 这里优先按行聚合，适合 Markdown 列表和制度条款。
    3. 单行仍然过长时，再按固定长度切分。

    :param paragraph: 当前段落文本
    :param chunk_size: 单个 chunk 的目标最大长度
    :return: 切分后的短段落列表
    """
    # 段落没有超长时直接返回
    if len(paragraph) <= chunk_size:
        return [paragraph]

    # 按行切分，保留 Markdown 列表和制度条款的自然边界
    lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
    # 存放切分后的短段落
    parts: list[str] = []
    # 当前正在聚合的短段落
    current_part = ""

    for line in lines:
        # 单行过长时，先提交当前聚合内容，再按固定长度切分这一行
        if len(line) > chunk_size:
            if current_part:
                parts.append(current_part)
                current_part = ""
            for start in range(0, len(line), chunk_size):
                parts.append(line[start:start + chunk_size])
            continue

        # 尝试把当前行合并到现有短段落中
        candidate = f"{current_part}\n{line}".strip() if current_part else line
        # 合并后没有超过 chunk_size，就继续聚合
        if len(candidate) <= chunk_size:
            current_part = candidate
        else:
            # 合并后过长，则先提交已有内容，再从当前行重新开始
            if current_part:
                parts.append(current_part)
            current_part = line

    # 提交最后一段聚合内容
    if current_part:
        parts.append(current_part)

    # 返回非空短段落
    return [part for part in parts if part.strip()]


def split_text_into_chunks(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[str]:
    """
    将长文本切分成多个 RAG 文本块。

    函数说明：
    1. 先规范化文本，再按空行拆成段落。
    2. 优先尊重章节标题边界，避免不同章节被合并到同一个 chunk。
    3. 再把相邻短段落合并到 chunk_size 范围内。
    4. 超长段落会进一步按行切成较短片段。
    5. 普通段落切换时保留少量重叠，减少语义断裂。
    6. 遇到新章节标题时不保留上一节重叠，避免引用片段混入上一章内容。

    :param text: 上传文档的完整文本
    :param chunk_size: 单个 chunk 的目标最大长度
    :param overlap: 相邻 chunk 的重叠字符数
    :return: 文本块列表
    """
    # 规范化文本
    text = normalize_text(text)
    # 空文本不生成 chunk
    if not text:
        return []

    # 按空行拆成段落，再把超长段落继续拆短
    paragraphs: list[str] = []
    for paragraph in [p.strip() for p in text.split("\n\n") if p.strip()]:
        paragraphs.extend(split_long_paragraph(paragraph, chunk_size))

    # 存放最终 chunk
    chunks: list[str] = []
    # 当前正在构建的 chunk
    current_chunk = ""

    for paragraph in paragraphs:
        # 当前 chunk 为空时，直接放入段落
        if not current_chunk:
            current_chunk = paragraph
            continue

        # 遇到新章节标题时，即使当前 chunk 还没超过长度，也单独开启新块，避免跨章节引用过长
        if is_section_heading(paragraph):
            chunks.append(current_chunk)
            current_chunk = paragraph
            continue

        # 尝试将段落合并进当前 chunk
        candidate = f"{current_chunk}\n\n{paragraph}"
        # 合并后未超过目标大小，则继续聚合
        if len(candidate) <= chunk_size:
            current_chunk = candidate
            continue

        # 当前 chunk 已满，先提交
        chunks.append(current_chunk)

        # 新段落像章节标题时，不保留上一块尾部，避免不同章节互相污染
        if is_section_heading(paragraph):
            current_chunk = paragraph
            continue

        # 普通段落保留少量上一块尾部上下文
        overlap_text = build_overlap_text(current_chunk, overlap)
        # 有重叠文本时拼接重叠和当前段落
        if overlap_text:
            current_chunk = f"{overlap_text}\n\n{paragraph}".strip()
        else:
            current_chunk = paragraph

    # 提交最后一个 chunk
    if current_chunk:
        chunks.append(current_chunk)

    # 清理空白 chunk 后返回
    return [chunk.strip() for chunk in chunks if chunk.strip()]

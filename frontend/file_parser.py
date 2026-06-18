import hashlib
from io import BytesIO


try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


def extract_text_from_uploaded_file(uploaded_file) -> tuple[str | None, str | None]:
    """
    从上传文件中提取文本。

    返回:
    - (text, None) 表示成功
    - (None, error_message) 表示失败
    """
    file_name = uploaded_file.name.lower()
    # 拿到文件的原始字节内容，可以理解为：把上传的文件整个读进内存
    file_bytes = uploaded_file.getvalue()

    # 处理 txt / md
    if file_name.endswith(".txt") or file_name.endswith(".md"):
        for encoding in ("utf-8", "utf-8-sig", "gbk"):
            try:
                # 把“字节数据”按某种编码规则，转换成“字符串文本”
                return file_bytes.decode(encoding), None
            except UnicodeDecodeError:
                continue
        return None, "文件编码无法识别, 请尝试使用 UTF-8 编码保存后再上传。"

    # 处理pdf
    if file_name.endswith(".pdf"):
        if PdfReader is None:
            return None, "当前环境未安装 pypdf, 请先在 requirements.txt 中添加 pypdf 并安装依赖。"

        try:
            # 把上传的 PDF 字节流变成一个可供 PDF 解析器读取的对象。BytesIO 的作用：把内存里的 bytes，包装成一个“像文件一样可以读取的对象”
            reader = PdfReader(BytesIO(file_bytes))
            # 列表，用于收集每一页提取出来的文本
            page_texts = []

            # 遍历PDF的每一页
            for page in reader.pages:
                # 提取当前页的内容
                text = page.extract_text() or ""
                # 如果这页提出的文本不为空，就加进列表
                if text.strip():
                    page_texts.append(text)

            # 拼接每页内容，变成整份PDF的文本内容
            full_text = "\n\n".join(page_texts).strip()

            if not full_text:
                return None, "PDF 中未提取到可用文本。若这是扫描版 PDF, 后续需要 OCR 才能支持。"

            return full_text, None
        except Exception as e:
            return None, f"PDF 解析失败: {str(e)}"

    return None, "暂不支持该文件类型, 请上传 txt、md 或 pdf 文件。"


def build_text_fingerprint(text: str) -> str:
    """
    为文档生成一个简单指纹，用于判断是否需要重新索引。
    """
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def build_user_display_text(user_text: str, uploaded_file_name: str | list[str] | None) -> str:
    """
    构造聊天区展示给用户看的输入文本。

    说明:
    - 如果用户既输入了问题，又附加了文件，则两者都显示。
    - 如果用户上传了多份文件，则逐行展示附件名称。
    - 如果用户只附加文件，则显示附件名称。
    """
    parts = []

    if user_text.strip():
        parts.append(user_text.strip())

    # 兼容单文件字符串和多文件列表两种调用方式
    if isinstance(uploaded_file_name, list):
        # 过滤空文件名，避免展示空附件行
        file_names = [file_name for file_name in uploaded_file_name if file_name]
        # 多文件时逐行展示，用户能清楚看到本轮提交了哪些资料
        if file_names:
            parts.extend(f"【附件】 {file_name}" for file_name in file_names)
    elif uploaded_file_name:
        # 单文件时保持原有展示格式
        parts.append(f"【附件】 {uploaded_file_name}")

    # 如果前面的结果不是空字符串，就返回前面的；如果是空字符串，就返回后面的默认值。
    return "\n\n".join(parts).strip() or " 【仅上传附件】"


def build_non_rag_input_text(user_text: str, uploaded_file_text: str) -> str:
    """
    构造“不启用 RAG“时真正发给后端的 input_text。

    说明：
    - 如果只有文件，没有额外问题，则直接把全文作为输入
    - 如果用户还补充了问题或要求，则把“文件全文 + 用户要求”一起发给后端
    """
    clean_user_text = user_text.strip()

    if clean_user_text:
        return (
            "以下是用户上传的文档内容：\n\n"
            f"{uploaded_file_text}\n\n"
            "用户的处理要求如下: \n"
            f"{clean_user_text}"
        )

    return uploaded_file_text

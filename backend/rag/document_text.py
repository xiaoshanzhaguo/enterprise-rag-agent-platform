"""Extract searchable text from supported binary knowledge-base uploads."""

import logging
from functools import lru_cache
from io import BytesIO
from pathlib import PurePath
from threading import Lock
from xml.etree import ElementTree
from zipfile import ZipFile

from PIL import Image, ImageOps
from pypdf import PdfReader

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_IMAGE_PIXELS = 12_000_000
IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OCR_LOCK = Lock()
logger = logging.getLogger(__name__)


class DocumentExtractionError(ValueError):
    def __init__(self, message: str, *, status: int = 422):
        super().__init__(message)
        self.status = status


class WordTreeBuilder(ElementTree.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise DocumentExtractionError("Word 文档含有不支持的 XML 声明。")


def _word_text(data: bytes) -> str:
    with ZipFile(BytesIO(data)) as archive:
        with archive.open("word/document.xml") as document:
            xml = document.read(MAX_DOCX_XML_BYTES + 1)
    if len(xml) > MAX_DOCX_XML_BYTES:
        raise DocumentExtractionError("Word 文档正文过大，请拆分后上传。")
    body = ElementTree.fromstring(xml, parser=ElementTree.XMLParser(target=WordTreeBuilder())).find(f"{WORD_NS}body")
    if body is None:
        raise DocumentExtractionError("无法读取 Word 正文。")
    separators = {f"{WORD_NS}tab": "\t", f"{WORD_NS}br": "\n", f"{WORD_NS}cr": "\n"}
    paragraphs = []
    for paragraph in body.iter(f"{WORD_NS}p"):
        parts = [(node.text or "") if node.tag == f"{WORD_NS}t" else separators.get(node.tag, "")
                 for node in paragraph.iter()]
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _pdf_text(data: bytes) -> str:
    if b"%PDF-" not in data[:1024]:
        raise DocumentExtractionError("无法读取 PDF，请确认文件格式正确。")
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        raise DocumentExtractionError("PDF 已加密，请解除密码保护后上传。")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise DocumentExtractionError(f"PDF 最多支持 {MAX_PDF_PAGES} 页，请拆分后上传。")
    pages, length = [], 0
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        length += len(text) + 2
        if length > MAX_DOCUMENT_BYTES:
            raise DocumentExtractionError("PDF 正文过大，请拆分后上传。")
        if text:
            pages.append(text)
    if not pages:
        raise DocumentExtractionError("PDF 没有可提取文字。请使用可选中文字的 PDF，或将扫描页转为图片后上传。")
    return "\n\n".join(pages)


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr import RapidOCR

    return RapidOCR(params={
        "Det.model_type": "lite", "Rec.model_type": "lite", "Global.use_cls": False,
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
    })


def _image_text(data: bytes, image_format: str) -> str:
    with Image.open(BytesIO(data), formats=[image_format]) as source:
        if source.width * source.height > MAX_IMAGE_PIXELS or getattr(source, "is_animated", False):
            raise DocumentExtractionError("请选择不超过 1200 万像素的静态图片。")
        image = ImageOps.exif_transpose(source).convert("RGBA")
    opaque = Image.new("RGBA", image.size, "white")
    opaque.alpha_composite(image)
    prepared = BytesIO()
    opaque.convert("RGB").save(prepared, format="PNG")
    try:
        with OCR_LOCK:
            result = _ocr_engine()(prepared.getvalue(), use_cls=False)
    except Exception as exc:
        logger.exception("Knowledge-base image OCR failed")
        raise DocumentExtractionError("图片识别服务暂不可用，请检查后端 OCR 依赖。", status=503) from exc
    return "\n".join(result.txts or ())


def extract_document_text(file_name: str, data: bytes) -> str:
    extension = PurePath(file_name).suffix.lower()
    if extension not in {".docx", ".pdf", *IMAGE_FORMATS}:
        raise DocumentExtractionError("此文件格式不支持提取。", status=415)
    if not data:
        raise DocumentExtractionError("文件内容为空。")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentExtractionError("文件超过 2 MB，请缩小后重试。", status=413)
    try:
        if extension == ".docx":
            text = _word_text(data)
        elif extension == ".pdf":
            text = _pdf_text(data)
        else:
            text = _image_text(data, IMAGE_FORMATS[extension])
    except DocumentExtractionError:
        raise
    except Exception as exc:
        raise DocumentExtractionError("文件无法解析，请确认格式正确且文件未损坏。") from exc
    text = text.replace("\x00", "").strip()
    if not text:
        raise DocumentExtractionError("没有识别到可入库的文字，请换用清晰的图片或含正文的文档。")
    if len(text) > MAX_DOCUMENT_BYTES:
        raise DocumentExtractionError("提取的正文过大，请拆分文件后上传。")
    return text

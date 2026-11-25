from io import BytesIO
from unittest import TestCase
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.rag.document_text import MAX_DOCUMENT_BYTES, WORD_NS, DocumentExtractionError, extract_document_text


def word_file():
    buffer = BytesIO()
    namespace = WORD_NS[1:-1]
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", f'''<w:document xmlns:w="{namespace}"><w:body>
            <w:p><w:r><w:t>年度</w:t></w:r><w:r><w:t>差旅制度</w:t></w:r></w:p>
            <w:tbl><w:tr><w:tc><w:p><w:r><w:t>差旅报销提交行程单和发票</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
            </w:body></w:document>'''.encode())
    return buffer.getvalue()


def pdf_file(*, with_text=True):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=200)
    if with_text:
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({
                NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })}),
        })
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 20 Tf 30 100 Td (Travel approval requires a receipt.) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def image_file(image_format="PNG"):
    buffer = BytesIO()
    image = Image.new("RGB", (720, 160), "white")
    ImageDraw.Draw(image).text((36, 38), "Invoice 2026", fill="black", font=ImageFont.load_default(size=56))
    image.save(buffer, format=image_format)
    return buffer.getvalue()


class DocumentTextTests(TestCase):
    def test_word_preserves_paragraphs_and_table_text(self):
        self.assertEqual(extract_document_text("制度.DOCX", word_file()), "年度差旅制度\n差旅报销提交行程单和发票")

    def test_pdf_extracts_text_and_rejects_textless_pdf(self):
        self.assertIn("Travel approval requires a receipt.", extract_document_text("policy.pdf", pdf_file()))
        with self.assertRaisesRegex(DocumentExtractionError, "没有可提取文字"):
            extract_document_text("scan.pdf", pdf_file(with_text=False))

    def test_recognizes_images_using_bundled_ocr(self):
        for name, image_format in [("scan.png", "PNG"), ("scan.jpg", "JPEG"), ("scan.webp", "WEBP")]:
            with self.subTest(name=name):
                self.assertIn("Invoice 2026", extract_document_text(name, image_file(image_format)))

    def test_limit_and_malformed_or_renamed_files(self):
        for name, data, status in [
            ("legacy.doc", b"fake", 415), ("missing.docx", b"", 422), ("renamed.pdf", word_file(), 422),
            ("renamed.jpg", image_file(), 422), ("oversized.pdf", b"x" * (MAX_DOCUMENT_BYTES + 1), 413),
        ]:
            with self.subTest(name=name), self.assertRaises(DocumentExtractionError) as error:
                extract_document_text(name, data)
            self.assertEqual(error.exception.status, status)

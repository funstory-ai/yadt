import base64
import logging
import re

import pdfminer.pdfinterp
import pymupdf
from pdfminer.layout import LTChar, LTFigure
from pdfminer.pdffont import PDFCIDFont, PDFFont
from pdfminer.psparser import PSLiteral

from yadt.document_il import il_version_1
from yadt.translation_config import TranslationConfig

logger = logging.getLogger(__name__)


class ILCreater:
    stage_name = "解析PDF并创建中间表示"

    def __init__(self, translation_config: TranslationConfig):
        self.progress = None
        self.current_page: il_version_1.Page = None
        self.mupdf: pymupdf.Document = None
        self.model = translation_config.doc_layout_model
        self.docs = il_version_1.Document(page=[])
        self.stroking_color_space_name = None
        self.non_stroking_color_space_name = None
        self.passthrough_per_char_instruction: list[tuple[str, str]] = []
        self.translation_config = translation_config
        self.passthrough_per_char_instruction_stack: list[list[tuple[str, str]]] = []
        self.xobj_id = 0
        self.xobj_inc = 0
        self.xobj_map: dict[int, il_version_1.PdfXobject] = {}
        self.xobj_stack = []

    def on_finish(self):
        self.progress.__exit__(None, None, None)

    def is_passthrough_per_char_operation(self, operator: str):
        return re.match("^(sc|scn|g|rg|k|cs|gs|ri)$", operator, re.IGNORECASE)

    def on_passthrough_per_char(self, operator: str, args: list[str]):
        if not self.is_passthrough_per_char_operation(operator):
            logger.error("Unknown passthrough_per_char operation: %s", operator)
            return
        # logger.debug("xobj_id: %d, on_passthrough_per_char: %s ( %s )", self.xobj_id, operator, args)
        args = [self.parse_arg(arg) for arg in args]
        for i, value in enumerate(self.passthrough_per_char_instruction.copy()):
            op, arg = value
            if op == operator:
                self.passthrough_per_char_instruction.remove(value)
                break
        self.passthrough_per_char_instruction.append((operator, " ".join(args)))
        pass

    def remove_latest_passthrough_per_char_instruction(self):
        if self.passthrough_per_char_instruction:
            self.passthrough_per_char_instruction.pop()

    def parse_arg(self, arg: str):
        if isinstance(arg, PSLiteral):
            return f"/{arg.name}"
        if not isinstance(arg, str):
            return str(arg)
        return arg

    def pop_passthrough_per_char_instruction(self):
        if self.passthrough_per_char_instruction_stack:
            self.passthrough_per_char_instruction = (
                self.passthrough_per_char_instruction_stack.pop()
            )
        else:
            self.passthrough_per_char_instruction = []
            logging.error(
                "pop_passthrough_per_char_instruction error on page: %s",
                self.current_page.page_number,
            )

    def push_passthrough_per_char_instruction(self):
        self.passthrough_per_char_instruction_stack.append(
            self.passthrough_per_char_instruction.copy()
        )

    # pdf32000 page 171
    def on_stroking_color_space(self, color_space_name):
        self.stroking_color_space_name = color_space_name

    def on_non_stroking_color_space(self, color_space_name):
        self.non_stroking_color_space_name = color_space_name

    def on_new_stream(self):
        self.stroking_color_space_name = None
        self.non_stroking_color_space_name = None
        self.passthrough_per_char_instruction = []

    def push_xobj(self):
        self.xobj_stack.append(
            (self.current_page_font_name_id_map.copy(), self.xobj_id)
        )
        self.current_page_font_name_id_map = {}

    def pop_xobj(self):
        self.current_page_font_name_id_map, self.xobj_id = self.xobj_stack.pop()

    def on_xobj_begin(self, bbox, xref_id):
        self.push_passthrough_per_char_instruction()
        self.push_xobj()
        self.xobj_inc += 1
        self.xobj_id = self.xobj_inc
        xobject = il_version_1.PdfXobject(
            box=il_version_1.Box(
                x=float(bbox[0]),
                y=float(bbox[1]),
                x2=float(bbox[2]),
                y2=float(bbox[3]),
            ),
            xobj_id=self.xobj_id,
            xref_id=xref_id,
        )
        self.current_page.pdf_xobject.append(xobject)
        self.xobj_map[self.xobj_id] = xobject
        return self.xobj_id

    def on_xobj_end(self, xobj_id, base_op):
        self.pop_passthrough_per_char_instruction()
        self.pop_xobj()
        xobj = self.xobj_map[xobj_id]
        xobj.base_operations = il_version_1.BaseOperations(value=base_op)
        self.xobj_inc += 1

    def on_page_start(self):
        self.current_page = il_version_1.Page(
            pdf_font=[],
            pdf_character=[],
            page_layout=[],
            # currently don't support UserUnit page parameter
            # pdf32000 page 79
            unit="point",
        )
        self.current_page_font_name_id_map = {}
        self.passthrough_per_char_instruction_stack = []
        self.xobj_stack = []
        self.non_stroking_color_space_name = None
        self.stroking_color_space_name = None
        self.docs.page.append(self.current_page)

    def on_page_end(self):
        self.progress.advance(1)

    def on_page_crop_box(
        self,
        x0: float | int,
        y0: float | int,
        x1: float | int,
        y1: float | int,
    ):
        box = il_version_1.Box(x=float(x0), y=float(y0), x2=float(x1), y2=float(y1))
        self.current_page.cropbox = il_version_1.Cropbox(box=box)

    def on_page_media_box(
        self,
        x0: float | int,
        y0: float | int,
        x1: float | int,
        y1: float | int,
    ):
        box = il_version_1.Box(x=float(x0), y=float(y0), x2=float(x1), y2=float(y1))
        self.current_page.mediabox = il_version_1.Mediabox(box=box)

    def on_page_number(self, page_number: int):
        assert isinstance(page_number, int)
        assert page_number >= 0
        self.current_page.page_number = page_number

    def on_page_base_operation(self, operation: str):
        self.current_page.base_operations = il_version_1.BaseOperations(value=operation)

    def on_page_resource_font(self, font: PDFFont, xref_id: int, font_id: str):
        font_name = font.fontname
        if isinstance(font_name, bytes):
            try:
                font_name = font_name.decode("utf-8")
            except UnicodeDecodeError:
                font_name = "BASE64:" + base64.b64encode(font_name).decode("utf-8")
        encoding_length = 1
        if isinstance(font, PDFCIDFont):
            try:
                # pdf 32000:2008 page 273
                # Table 118 - Predefined CJK CMap names
                _, encoding = self.mupdf.xref_get_key(xref_id, "Encoding")
                if encoding == "/Identity-H":
                    encoding_length = 2
                elif encoding == "/Identity-V":
                    encoding_length = 2
                else:
                    _, to_unicode_id = self.mupdf.xref_get_key(xref_id, "ToUnicode")
                    to_unicode_bytes = self.mupdf.xref_stream(
                        int(to_unicode_id.split(" ")[0])
                    )
                    range = re.search(
                        b"begincodespacerange\n?.*<(\\d+?)>.*", to_unicode_bytes
                    ).group(1)
                    encoding_length = len(range) // 2
            except Exception:
                if max(font.unicode_map.cid2unichr.keys()) > 255:
                    encoding_length = 2
                else:
                    encoding_length = 1
        try:
            mupdf_font = pymupdf.Font(fontbuffer=self.mupdf.extract_font(xref_id)[3])
            bold = mupdf_font.is_bold
            italic = mupdf_font.is_italic
            monospaced = mupdf_font.is_monospaced
            serif = mupdf_font.is_serif
        except Exception:
            bold = None
            italic = None
            monospaced = None
            serif = None
        il_font_metadata = il_version_1.PdfFont(
            name=font_name,
            xref_id=xref_id,
            font_id=font_id,
            encoding_length=encoding_length,
            bold=bold,
            italic=italic,
            monospace=monospaced,
            serif=serif,
            ascent=font.ascent,
            descent=font.descent,
        )
        self.current_page_font_name_id_map[font_name] = font_id
        if self.xobj_id in self.xobj_map:
            self.xobj_map[self.xobj_id].pdf_font.append(il_font_metadata)
        else:
            self.current_page.pdf_font.append(il_font_metadata)

    def create_graphic_state(self, gs: pdfminer.pdfinterp.PDFGraphicState):
        graphic_state = il_version_1.GraphicState()
        for k, v in gs.__dict__.items():
            if v is None:
                continue
            if k in ["scolor", "ncolor"]:
                if isinstance(v, tuple):
                    v = list(v)
                else:
                    v = [v]
                setattr(graphic_state, k, v)
                continue
            if k == "linewidth":
                graphic_state.linewidth = float(v)
                continue
            continue
            raise NotImplementedError

        graphic_state.stroking_color_space_name = self.stroking_color_space_name
        graphic_state.non_stroking_color_space_name = self.non_stroking_color_space_name

        graphic_state.passthrough_per_char_instruction = " ".join(
            f"{arg} {op}" for op, arg in gs.passthrough_instruction
        )

        return graphic_state

    def on_lt_char(self, char: LTChar):
        gs = self.create_graphic_state(char.graphicstate)
        # Get font from current page or xobject
        font = None
        for pdf_font in self.xobj_map.get(self.xobj_id, self.current_page).pdf_font:
            if pdf_font.font_id == char.aw_font_id:
                font = pdf_font
                break

        # Get descent from font
        descent = 0
        if font and hasattr(font, "descent"):
            descent = font.descent * char.size / 1000

        char_id = char.cid
        char_unicode = char.get_text()
        if "(cid:" not in char_unicode and len(char_unicode) > 1:
            return
        advance = char.adv
        if char.matrix[0] == 0 and char.matrix[3] == 0:
            vertical = True
            bbox = il_version_1.Box(
                x=char.bbox[0] - descent,
                y=char.bbox[1],
                x2=char.bbox[2] - descent,
                y2=char.bbox[3],
            )
        else:
            vertical = False
            # Add descent to y coordinates
            bbox = il_version_1.Box(
                x=char.bbox[0],
                y=char.bbox[1] + descent,
                x2=char.bbox[2],
                y2=char.bbox[3] + descent,
            )
        pdf_style = il_version_1.PdfStyle(
            font_id=char.aw_font_id,
            font_size=char.size,
            graphic_state=gs,
        )
        pdf_char = il_version_1.PdfCharacter(
            box=bbox,
            pdf_character_id=char_id,
            advance=advance,
            char_unicode=char_unicode,
            vertical=vertical,
            pdf_style=pdf_style,
            xobj_id=char.xobj_id,
        )
        self.current_page.pdf_character.append(pdf_char)

    def create_il(self):
        pages = [
            page
            for page in self.docs.page
            if self.translation_config.should_translate_page(page.page_number + 1)
        ]
        self.docs.page = pages
        return self.docs

    def on_total_pages(self, total_pages: int):
        assert isinstance(total_pages, int)
        assert total_pages > 0
        self.docs.total_pages = total_pages
        total = 0
        for page in range(total_pages):
            if self.translation_config.should_translate_page(page + 1) is False:
                continue
            total += 1
        self.progress = self.translation_config.progress_monitor.stage_start(
            self.stage_name, total
        )

    def on_pdf_figure(self, figure: LTFigure):
        box = il_version_1.Box(
            figure.bbox[0], figure.bbox[1], figure.bbox[2], figure.bbox[3]
        )
        self.current_page.pdf_figure.append(il_version_1.PdfFigure(box=box))

import numpy as np
import pdfminer.pdfinterp
import pymupdf
from pdfminer.layout import LTChar
from pdfminer.pdffont import PDFFont

from yadt.il_try_1.doclayout import DocLayoutModel
from yadt.il_try_1.document_il import il_try_1


class ILCreater:
    def __init__(self):
        self.current_page: il_try_1.Page = None
        self.mupdf: pymupdf.Document = None
        self.model = DocLayoutModel.load_available()
        self.docs = il_try_1.Document(page=[])
        self.stroking_color_space_name = None
        self.non_stroking_color_space_name = None

    # pdf32000 page 171
    def on_stroking_color_space(self, color_space_name):
        self.stroking_color_space_name = color_space_name

    def on_non_stroking_color_space(self, color_space_name):
        self.non_stroking_color_space_name = color_space_name

    def on_page_start(self):
        self.current_page = il_try_1.Page(
            pdf_font=[],
            pdf_character=[],
            page_layout=[],
            # currently don't support UserUnit page parameter
            # pdf32000 page 79
            unit='point',
        )
        self.current_page_font_name_id_map = {}
        self.docs.page.append(self.current_page)

    def on_page_crop_box(
        self,
        x0: float | int,
        y0: float | int,
        x1: float | int,
        y1: float | int,
    ):
        box = il_try_1.Box(
            x=float(x0), y=float(y0), x2=float(x1), y2=float(y1)
        )
        self.current_page.cropbox = il_try_1.Cropbox(box=box)

    def on_page_media_box(
        self,
        x0: float | int,
        y0: float | int,
        x1: float | int,
        y1: float | int,
    ):
        box = il_try_1.Box(
            x=float(x0), y=float(y0), x2=float(x1), y2=float(y1)
        )
        self.current_page.mediabox = il_try_1.Mediabox(box=box)

    def on_page_number(self, page_number: int):
        assert isinstance(page_number, int)
        assert page_number >= 0
        self.current_page.page_number = page_number

        self.on_page_layout(page_number)

    def on_page_base_operation(self, operation: str):
        self.current_page.base_operations = il_try_1.BaseOperations(
            value=operation
        )

    def on_page_resource_font(self, font: PDFFont, xref_id: int, font_id: str):
        font_name = font.fontname
        if isinstance(font.fontname, bytes):
            font_name = font.fontname.decode("utf-8")
        il_font_metadata = il_try_1.PdfFont(
            name=font_name, xref_id=xref_id, font_id=font_id
        )
        self.current_page_font_name_id_map[font_name] = font_id
        self.current_page.pdf_font.append(il_font_metadata)

    def create_graphic_state(self, gs: pdfminer.pdfinterp.PDFGraphicState):
        graphic_state = il_try_1.GraphicState()
        for k, v in gs.__dict__.items():
            if v is None:
                continue
            if k in ["scolor", "ncolor"]:
                setattr(graphic_state, k, list(v))
                continue
            if k == "linewidth":
                graphic_state.linewidth = float(v)
                continue
            raise NotImplementedError

        graphic_state.stroking_color_space_name = (
            self.stroking_color_space_name
        )
        graphic_state.non_stroking_color_space_name = (
            self.non_stroking_color_space_name
        )
        return graphic_state

    def on_lt_char(self, char: LTChar):
        gs = self.create_graphic_state(char.graphicstate)
        bbox = il_try_1.Box(
            char.bbox[0], char.bbox[1], char.bbox[2], char.bbox[3]
        )

        font_id = self.current_page_font_name_id_map[char.font.fontname]
        char_id = char.cid
        char_unicode = char.get_text()
        advance = char.adv
        pdf_char = il_try_1.PdfCharacter(
            box=bbox,
            pdf_font_id=font_id,
            pdf_character_id=char_id,
            advance=advance,
            char_unicode=char_unicode,
            size=char.size,
            graphic_state=gs,
        )
        self.current_page.pdf_character.append(pdf_char)

    def on_page_layout(self, page_number):
        pix = self.mupdf[page_number].get_pixmap()
        image = np.fromstring(pix.samples, np.uint8).reshape(
            pix.height, pix.width, 3
        )[:, :, ::-1]
        layouts = self.model.predict(image, imgsz=int(pix.height / 32) * 32)[0]
        for layout in layouts.boxes:
            page_layout = il_try_1.PageLayout(
                box=il_try_1.Box(
                    layout.xyxy[0].item(),
                    layout.xyxy[1].item(),
                    layout.xyxy[2].item(),
                    layout.xyxy[3].item(),
                ),
                conf=layout.conf.item(),
                class_name=layouts.names[layout.cls],
            )
            self.docs.page[page_number].page_layout.append(page_layout)

    def create_il(self):
        return self.docs

    def on_total_pages(self, total_pages: int):
        assert isinstance(total_pages, int)
        assert total_pages > 0
        self.docs.total_pages = total_pages

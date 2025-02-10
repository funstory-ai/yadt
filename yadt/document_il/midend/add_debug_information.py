import logging

import yadt.document_il.il_version_1 as il_version_1
from yadt.document_il import GraphicState
from yadt.document_il.utils.style_helper import BLUE, ORANGE, YELLOW
from yadt.translation_config import TranslationConfig

logger = logging.getLogger(__name__)


class AddDebugInformation:
    stage_name = "添加DEBUG信息"

    def __init__(self, translation_config: TranslationConfig):
        self.translation_config = translation_config
        self.model = translation_config.doc_layout_model

    def process(self, docs: il_version_1.Document):
        if not self.translation_config.debug:
            return

        for page in docs.page:
            self.process_page(page)

    def _create_rectangle(self, box: il_version_1.Box, color: GraphicState):
        rect = il_version_1.PdfRectangle(
            box=box,
            graphic_state=color,
            debug_info=True,
        )
        return rect

    def _create_text(self, text: str, color: GraphicState, box: il_version_1.Box):
        style = il_version_1.PdfStyle(
            font_id="china-ss",
            font_size=4,
            graphic_state=color,
        )
        return il_version_1.PdfParagraph(
            first_line_indent=False,
            box=il_version_1.Box(
                x=box.x,
                y=box.y2,
                x2=box.x2,
                y2=box.y2 + 5,
            ),
            vertical=False,
            pdf_style=style,
            unicode=text,
            pdf_paragraph_composition=[
                il_version_1.PdfParagraphComposition(
                    pdf_same_style_unicode_characters=il_version_1.PdfSameStyleUnicodeCharacters(
                        unicode=text,
                        pdf_style=style,
                        debug_info=True,
                    )
                )
            ],
            xobj_id=-1,
        )

    def process_page(self, page: il_version_1.Page):
        # Add page number text at top-left corner
        page_width = page.cropbox.box.x2 - page.cropbox.box.x
        page_height = page.cropbox.box.y2 - page.cropbox.box.y
        page_number_text = f"pagenumber: {page.page_number}"
        page_number_box = il_version_1.Box(
            x=page.cropbox.box.x + page_width * 0.02,
            y=page.cropbox.box.y,
            x2=page.cropbox.box.x2,
            y2=page.cropbox.box.y2 - page_height * 0.02,
        )
        page_number_paragraph = self._create_text(
            page_number_text, BLUE, page_number_box
        )
        page.pdf_paragraph.append(page_number_paragraph)

        new_paragraphs = []

        for paragraph in page.pdf_paragraph:
            if any(
                (
                    x.pdf_same_style_unicode_characters.debug_info
                    for x in paragraph.pdf_paragraph_composition
                    if x.pdf_same_style_unicode_characters
                )
            ):
                continue
            # Create a rectangle box
            rect = self._create_rectangle(paragraph.box, BLUE)

            page.pdf_rectangle.append(rect)

            # Create text label at top-left corner
            # Note: PDF coordinates are from bottom-left,
            # so we use y2 for top position

            debug_text = "paragraph"
            if hasattr(paragraph, "debug_id") and paragraph.debug_id:
                debug_text = f"paragraph[{paragraph.debug_id}]"
            new_paragraphs.append(self._create_text(debug_text, BLUE, paragraph.box))

            for composition in paragraph.pdf_paragraph_composition:
                if composition.pdf_formula:
                    new_paragraphs.append(
                        self._create_text(
                            "formula",
                            ORANGE,
                            composition.pdf_formula.box,
                        )
                    )
                    page.pdf_rectangle.append(
                        self._create_rectangle(
                            composition.pdf_formula.box,
                            ORANGE,
                        )
                    )

            for xobj in page.pdf_xobject:
                new_paragraphs.append(
                    self._create_text(
                        "xobj",
                        YELLOW,
                        xobj.box,
                    )
                )
                page.pdf_rectangle.append(
                    self._create_rectangle(
                        xobj.box,
                        YELLOW,
                    )
                )

        page.pdf_paragraph.extend(new_paragraphs)

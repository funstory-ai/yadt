import logging
import os
import re

import pymupdf
from bitstring import BitStream

from yadt.document_il import il_version_1
from yadt.document_il.utils.fontmap import FontMapper
from yadt.translation_config import TranslateResult, TranslationConfig

logger = logging.getLogger(__name__)


class PDFCreater:
    stage_name = "创建PDF文件"

    def __init__(
        self,
        original_pdf_path: str,
        document: il_version_1.Document,
        translation_config: TranslationConfig,
    ):
        self.original_pdf_path = original_pdf_path
        self.docs = document
        self.font_path = translation_config.font
        self.font_mapper = FontMapper(translation_config)
        self.translation_config = translation_config

    def render_graphic_state(
        self, draw_op: BitStream, graphic_state: il_version_1.GraphicState
    ):
        if graphic_state is None:
            return
        # if graphic_state.stroking_color_space_name:
        #     draw_op.append(
        #         f"/{graphic_state.stroking_color_space_name} CS \n".encode()
        #     )
        # if graphic_state.non_stroking_color_space_name:
        #     draw_op.append(
        #         f"/{graphic_state.non_stroking_color_space_name}"
        #         f" cs \n".encode()
        #     )
        # if graphic_state.ncolor is not None:
        #     if len(graphic_state.ncolor) == 1:
        #         draw_op.append(f"{graphic_state.ncolor[0]} g \n".encode())
        #     elif len(graphic_state.ncolor) == 3:
        #         draw_op.append(
        #             f"{' '.join((str(x) for x in graphic_state.ncolor))} sc \n".encode()
        #         )
        # if graphic_state.scolor is not None:
        #     if len(graphic_state.scolor) == 1:
        #         draw_op.append(f"{graphic_state.scolor[0]} G \n".encode())
        #     elif len(graphic_state.scolor) == 3:
        #         draw_op.append(
        #             f"{' '.join((str(x) for x in graphic_state.scolor))} SC \n".encode()
        #         )

        if graphic_state.passthrough_per_char_instruction:
            draw_op.append(
                f"{graphic_state.passthrough_per_char_instruction} \n".encode()
            )

    def render_paragraph_to_char(
        self, paragraph: il_version_1.PdfParagraph
    ) -> list[il_version_1.PdfCharacter]:
        chars = []
        for composition in paragraph.pdf_paragraph_composition:
            if not isinstance(composition.pdf_character, il_version_1.PdfCharacter):
                logger.error(
                    f"Unknown composition type. "
                    f"This type only appears in the IL "
                    f"after the translation is completed."
                    f"During pdf rendering, this type is not supported."
                    f"Composition: {composition}. "
                    f"Paragraph: {paragraph}. "
                )
                continue
            chars.append(composition.pdf_character)
        if not chars and paragraph.unicode:
            logger.error(
                f"Unable to export paragraphs that have "
                f"not yet been formatted: {paragraph}"
            )
            return chars
        return chars

    def get_available_font_list(self, pdf, page):
        page_xref_id = pdf[page.page_number].xref
        return self.get_xobj_available_fonts(page_xref_id, pdf)

    def get_xobj_available_fonts(self, page_xref_id, pdf):
        resources_type, r_id = pdf.xref_get_key(page_xref_id, "Resources")
        if resources_type == "xref":
            resource_xref_id = re.search("(\\d+) 0 R", r_id).group(1)
            r_id = pdf.xref_object(int(resource_xref_id))
            resources_type = "dict"
        if resources_type == "dict":
            xref_id = re.search("/Font (\\d+) 0 R", r_id)
            if xref_id is not None:
                xref_id = xref_id.group(1)
                font_dict = pdf.xref_object(int(xref_id))
            else:
                search = re.search("/Font *<<(.+?)>>", r_id.replace("\n", " "))
                if search is None:
                    # Have resources but no fonts
                    return set()
                font_dict = search.group(1)
        else:
            r_id = int(r_id.split(" ")[0])
            _, font_dict = pdf.xref_get_key(r_id, "Font")
        fonts = re.findall("/([^ ]+?) ", font_dict)
        return set(fonts)

    def _debug_render_rectangle(
        self, draw_op: BitStream, rectangle: il_version_1.PdfRectangle
    ):
        """Draw a debug rectangle in PDF for visualization purposes.

        Args:
            draw_op: BitStream to append PDF drawing operations
            rectangle: Rectangle object containing position information
        """
        x1 = rectangle.box.x
        y1 = rectangle.box.y
        x2 = rectangle.box.x2
        y2 = rectangle.box.y2
        # Save graphics state
        draw_op.append(b"q ")

        # Set green color for debug visibility
        draw_op.append(
            rectangle.graphic_state.passthrough_per_char_instruction.encode()
        )  # Green stroke
        draw_op.append(b" 1 w ")  # Line width

        # Draw four lines manually
        # Bottom line
        draw_op.append(f"{x1} {y1} m {x2} {y1} l S ".encode())
        # Right line
        draw_op.append(f"{x2} {y1} m {x2} {y2} l S ".encode())
        # Top line
        draw_op.append(f"{x2} {y2} m {x1} {y2} l S ".encode())
        # Left line
        draw_op.append(f"{x1} {y2} m {x1} {y1} l S ".encode())

        # Restore graphics state
        draw_op.append(b"Q\n")

    def write_debug_info(
        self, pdf: pymupdf.Document, translation_config: TranslationConfig
    ):
        self.font_mapper.add_font(pdf, self.docs)

        for page in self.docs.page:
            _, r_id = pdf.xref_get_key(pdf[page.page_number].xref, "Contents")
            resource_xref_id = re.search("(\\d+) 0 R", r_id).group(1)
            base_op = pdf.xref_stream(int(resource_xref_id))
            translation_config.raise_if_cancelled()
            xobj_available_fonts = {}
            xobj_draw_ops = {}
            xobj_encoding_length_map = {}
            available_font_list = self.get_available_font_list(pdf, page)

            page_encoding_length_map = {
                f.font_id: f.encoding_length for f in page.pdf_font
            }
            page_op = BitStream()
            # q {ops_base}Q 1 0 0 1 {x0} {y0} cm {ops_new}
            page_op.append(b"q ")
            page_op.append(base_op)
            page_op.append(b" Q ")
            page_op.append(
                f"q Q 1 0 0 1 {page.cropbox.box.x} {page.cropbox.box.y} cm \n".encode()
            )
            # 收集所有字符
            chars = []
            # 首先添加页面级别的字符
            if page.pdf_character:
                chars.extend(page.pdf_character)
            # 然后添加段落中的字符
            for paragraph in page.pdf_paragraph:
                chars.extend(self.render_paragraph_to_char(paragraph))

            # 渲染所有字符
            for char in chars:
                if not getattr(char, "debug_info", False):
                    continue
                if char.char_unicode == "\n":
                    continue
                if char.pdf_character_id is None:
                    # dummy char
                    continue
                char_size = char.pdf_style.font_size
                font_id = char.pdf_style.font_id

                if font_id not in available_font_list:
                    continue
                draw_op = page_op
                encoding_length_map = page_encoding_length_map

                draw_op.append(b"q ")
                self.render_graphic_state(draw_op, char.pdf_style.graphic_state)
                if char.vertical:
                    draw_op.append(
                        f"BT /{font_id} {char_size:f} Tf 0 1 -1 0 {char.box.x2:f} {char.box.y:f} Tm ".encode()
                    )
                else:
                    draw_op.append(
                        f"BT /{font_id} {char_size:f} Tf 1 0 0 1 {char.box.x:f} {char.box.y:f} Tm ".encode()
                    )

                encoding_length = encoding_length_map[font_id]
                # pdf32000-2008 page14:
                # As hexadecimal data enclosed in angle brackets < >
                # see 7.3.4.3, "Hexadecimal Strings."
                draw_op.append(
                    f"<{char.pdf_character_id:0{encoding_length * 2}x}>".upper().encode()
                )

                draw_op.append(b" Tj ET Q \n")
            for rect in page.pdf_rectangle:
                if not rect.debug_info:
                    continue
                self._debug_render_rectangle(page_op, rect)
            draw_op = page_op
            # Since this is a draw instruction container,
            # no additional information is needed
            pdf.update_stream(int(resource_xref_id), draw_op.tobytes())
        translation_config.raise_if_cancelled()
        pdf.subset_fonts(fallback=False)

    def write(self, translation_config: TranslationConfig) -> TranslateResult:
        basename = os.path.basename(translation_config.input_file.rsplit(".", 1)[0])
        debug_suffix = ".debug" if translation_config.debug else ""
        mono_out_path = translation_config.get_output_file_path(
            f"{basename}{debug_suffix}.{translation_config.lang_out}.mono.pdf"
        )
        pdf = pymupdf.open(self.original_pdf_path)
        self.font_mapper.add_font(pdf, self.docs)
        with self.translation_config.progress_monitor.stage_start(
            self.stage_name, len(self.docs.page) + 2
        ) as pbar:
            for page in self.docs.page:
                translation_config.raise_if_cancelled()
                xobj_available_fonts = {}
                xobj_draw_ops = {}
                xobj_encoding_length_map = {}
                available_font_list = self.get_available_font_list(pdf, page)

                for xobj in page.pdf_xobject:
                    xobj_available_fonts[xobj.xobj_id] = available_font_list.copy()
                    try:
                        xobj_available_fonts[xobj.xobj_id].update(
                            self.get_xobj_available_fonts(xobj.xref_id, pdf)
                        )
                    except Exception:
                        pass
                    xobj_encoding_length_map[xobj.xobj_id] = {
                        f.font_id: f.encoding_length for f in xobj.pdf_font
                    }
                    xobj_op = BitStream()
                    xobj_op.append(xobj.base_operations.value.encode())
                    xobj_draw_ops[xobj.xobj_id] = xobj_op
                page_encoding_length_map = {
                    f.font_id: f.encoding_length for f in page.pdf_font
                }
                page_op = BitStream()
                # q {ops_base}Q 1 0 0 1 {x0} {y0} cm {ops_new}
                page_op.append(b"q ")
                page_op.append(page.base_operations.value.encode())
                page_op.append(b" Q ")
                page_op.append(
                    f"q Q 1 0 0 1 {page.cropbox.box.x} {page.cropbox.box.y} cm \n".encode()
                )
                # 收集所有字符
                chars = []
                # 首先添加页面级别的字符
                if page.pdf_character:
                    chars.extend(page.pdf_character)
                # 然后添加段落中的字符
                for paragraph in page.pdf_paragraph:
                    chars.extend(self.render_paragraph_to_char(paragraph))

                # 渲染所有字符
                for char in chars:
                    if char.char_unicode == "\n":
                        continue
                    if char.pdf_character_id is None:
                        # dummy char
                        continue
                    char_size = char.pdf_style.font_size
                    font_id = char.pdf_style.font_id
                    if char.xobj_id in xobj_available_fonts:
                        if font_id not in xobj_available_fonts[char.xobj_id]:
                            continue
                        draw_op = xobj_draw_ops[char.xobj_id]
                        encoding_length_map = xobj_encoding_length_map[char.xobj_id]
                    else:
                        if font_id not in available_font_list:
                            continue
                        draw_op = page_op
                        encoding_length_map = page_encoding_length_map

                    draw_op.append(b"q ")
                    self.render_graphic_state(draw_op, char.pdf_style.graphic_state)
                    if char.vertical:
                        draw_op.append(
                            f"BT /{font_id} {char_size:f} Tf 0 1 -1 0 {char.box.x2:f} {char.box.y:f} Tm ".encode()
                        )
                    else:
                        draw_op.append(
                            f"BT /{font_id} {char_size:f} Tf 1 0 0 1 {char.box.x:f} {char.box.y:f} Tm ".encode()
                        )

                    encoding_length = encoding_length_map[font_id]
                    # pdf32000-2008 page14:
                    # As hexadecimal data enclosed in angle brackets < >
                    # see 7.3.4.3, "Hexadecimal Strings."
                    draw_op.append(
                        f"<{char.pdf_character_id:0{encoding_length * 2}x}>".upper().encode()
                    )

                    draw_op.append(b" Tj ET Q \n")
                for xobj in page.pdf_xobject:
                    draw_op = xobj_draw_ops[xobj.xobj_id]
                    pdf.update_stream(xobj.xref_id, draw_op.tobytes())
                    # pdf.update_stream(xobj.xref_id, b'')
                for rect in page.pdf_rectangle:
                    self._debug_render_rectangle(page_op, rect)
                draw_op = page_op
                op_container = pdf.get_new_xref()
                # Since this is a draw instruction container,
                # no additional information is needed
                pdf.update_object(op_container, "<<>>")
                pdf.update_stream(op_container, draw_op.tobytes())
                pdf[page.page_number].set_contents(op_container)
                pbar.advance()
            translation_config.raise_if_cancelled()
            if not translation_config.skip_clean:
                pdf.subset_fonts(fallback=False)
            if not translation_config.no_mono:
                if translation_config.debug:
                    translation_config.raise_if_cancelled()
                    pdf.save(
                        f"{mono_out_path}.decompressed.pdf",
                        expand=True,
                        pretty=True,
                    )
                translation_config.raise_if_cancelled()
                pdf.save(
                    mono_out_path,
                    garbage=3,
                    deflate=True,
                    clean=not translation_config.skip_clean,
                    deflate_fonts=True,
                    linear=True,
                )
            pbar.advance()
            dual_out_path = None
            if not translation_config.no_dual:
                dual_out_path = translation_config.get_output_file_path(
                    f"{basename}{debug_suffix}.{translation_config.lang_out}.dual.pdf"
                )
                translation_config.raise_if_cancelled()
                dual = pymupdf.open(self.original_pdf_path)
                if translation_config.debug:
                    translation_config.raise_if_cancelled()
                    self.write_debug_info(dual, translation_config)
                dual.insert_file(pdf)
                page_count = pdf.page_count
                for id in range(page_count):
                    if translation_config.dual_translate_first:
                        dual.move_page(page_count + id, id * 2)
                    else:
                        dual.move_page(page_count + id, id * 2 + 1)
                dual.save(
                    dual_out_path,
                    garbage=3,
                    deflate=True,
                    clean=not translation_config.skip_clean,
                    deflate_fonts=True,
                    linear=True,
                )
                if translation_config.debug:
                    translation_config.raise_if_cancelled()
                    dual.save(
                        f"{dual_out_path}.decompressed.pdf",
                        expand=True,
                        pretty=True,
                    )
            pbar.advance()
        return TranslateResult(mono_out_path, dual_out_path)

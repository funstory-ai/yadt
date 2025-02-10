import logging
import statistics
from functools import lru_cache
import unicodedata
from typing import Optional, Union

import pymupdf

from yadt.document_il import (
    Box,
    PdfCharacter,
    PdfFormula,
    PdfParagraphComposition,
    PdfStyle,
    il_version_1,
)
from yadt.document_il.utils.fontmap import FontMapper
from yadt.translation_config import TranslationConfig

logger = logging.getLogger(__name__)


class TypesettingUnit:
    def __str__(self):
        return self.try_get_unicode()

    def __init__(
        self,
        char: PdfCharacter = None,
        formular: PdfFormula = None,
        unicode: str = None,
        font: Optional[pymupdf.Font] = None,
        font_size: float = None,
        style: PdfStyle = None,
        xobj_id: int = None,
        debug_info: bool = False,
    ):
        assert sum((x is not None for x in [char, formular, unicode])) == 1, (
            "Only one of chars and formular can be not None"
        )
        self.char = char
        self.formular = formular
        self.unicode = unicode
        self.x = None
        self.y = None
        self.scale = None
        self.debug_info = debug_info

        if unicode:
            assert font_size, "Font size must be provided when unicode is provided"
            assert style, "Style must be provided when unicode is provided"
            assert len(unicode) == 1, "Unicode must be a single character"
            assert xobj_id is not None, (
                "Xobj id must be provided when unicode is provided"
            )

            self.font = font
            self.font_id = font.font_id
            self.font_size = font_size
            self.style = style
            self.xobj_id = xobj_id

    def try_get_unicode(self) -> Optional[str]:
        if self.char:
            return self.char.char_unicode
        elif self.formular:
            return None
        elif self.unicode:
            return self.unicode

    @property
    def mixed_character_blacklist(self):
        unicode = self.try_get_unicode()
        if unicode:
            return unicode in [
                "。",
                "，",
                "：",
                "？",
                "！",
            ]
        return False

    @property
    def is_chinese_char(self):
        if self.formular:
            return False
        unicode = self.try_get_unicode()
        if not unicode:
            return False
        if "(cid" in unicode:
            return False
        if len(unicode) > 1:
            return False
        assert len(unicode) == 1, "Unicode must be a single character"
        if unicode in [
            "（",
            "）",
            "【",
            "】",
            "《",
            "》",
            "〔",
            "〕",
            "〈",
            "〉",
            "〖",
            "〗",
            "「",
            "」",
            "『",
            "』",
            "、",
            "。",
            "：",
            "？",
            "！",
            "，",
        ]:
            return True
        if unicode:
            try:
                unicodedata_name = unicodedata.name(unicode)
                return (
                    "CJK UNIFIED IDEOGRAPH" in unicodedata_name
                    or "FULLWIDTH" in unicodedata_name
                )
            except ValueError:
                return False
        return False

    @property
    def is_space(self):
        if self.formular:
            return False
        unicode = self.try_get_unicode()
        return unicode == " "

    @property
    def is_hung_punctuation(self):
        if self.formular:
            return False
        unicode = self.try_get_unicode()

        if unicode:
            return unicode in [
                ",",
                ".",
                ":",
                ";",
                "?",
                "!",
                "，",
                "。",
                "：",
                "？",
                "！",
                "]",
                "}",
                "）",
                "〕",
                "〉",
                "】",
                "〗",
                "」",
                "』",
                "、",
                "”",
                '"',
                "；",
            ]
        return False

    def passthrough(self) -> [PdfCharacter]:
        if self.char:
            return [self.char]
        elif self.formular:
            return self.formular.pdf_character
        elif self.unicode:
            logger.error(f"Cannot passthrough unicode. TypesettingUnit: {self}. ")
            logger.error(f"Cannot passthrough unicode. TypesettingUnit: {self}. ")
            return []

    @property
    def can_passthrough(self):
        return self.unicode is None

    @property
    def box(self):
        if self.char:
            return self.char.box
        elif self.formular:
            return self.formular.box
        elif self.unicode:
            char_width = self.font.char_lengths(self.unicode, self.font_size)[0]
            if self.x is None or self.y is None or self.scale is None:
                return Box(0, 0, char_width, self.font_size)
            return Box(self.x, self.y, self.x + char_width, self.y + self.font_size)

    @property
    def width(self):
        return self.box.x2 - self.box.x

    @property
    def height(self):
        return self.box.y2 - self.box.y

    def relocate(self, x: float, y: float, scale: float) -> "TypesettingUnit":
        """重定位并缩放排版单元

        Args:
            x: 新的 x 坐标
            y: 新的 y 坐标
            scale: 缩放因子

        Returns:
            新的排版单元
        """
        if self.char:
            # 创建新的字符对象
            new_char = PdfCharacter(
                pdf_character_id=self.char.pdf_character_id,
                char_unicode=self.char.char_unicode,
                box=Box(
                    x=x,
                    y=y,
                    x2=x + self.width * scale,
                    y2=y + self.height * scale,
                ),
                pdf_style=PdfStyle(
                    font_id=self.char.pdf_style.font_id,
                    font_size=self.char.pdf_style.font_size * scale,
                    graphic_state=self.char.pdf_style.graphic_state,
                ),
                scale=scale,
                vertical=self.char.vertical,
                advance=self.char.advance * scale if self.char.advance else None,
                debug_info=self.debug_info,
            )
            return TypesettingUnit(char=new_char)

        elif self.formular:
            # 创建新的公式对象，保持内部字符的相对位置
            new_chars = []
            min_x = min(char.box.x for char in self.formular.pdf_character)
            min_y = min(char.box.y for char in self.formular.pdf_character)

            for char in self.formular.pdf_character:
                # 计算相对位置
                rel_x = char.box.x - min_x
                rel_y = char.box.y - min_y

                # 创建新的字符对象
                new_char = PdfCharacter(
                    pdf_character_id=char.pdf_character_id,
                    char_unicode=char.char_unicode,
                    box=Box(
                        x=x + (rel_x + self.formular.x_offset) * scale,
                        y=y + (rel_y + self.formular.y_offset) * scale,
                        x2=x
                        + (rel_x + (char.box.x2 - char.box.x) + self.formular.x_offset)
                        * scale,
                        y2=y
                        + (rel_y + (char.box.y2 - char.box.y) + self.formular.y_offset)
                        * scale,
                    ),
                    pdf_style=PdfStyle(
                        font_id=char.pdf_style.font_id,
                        font_size=char.pdf_style.font_size * scale,
                        graphic_state=char.pdf_style.graphic_state,
                    ),
                    scale=scale,
                    vertical=char.vertical,
                    advance=char.advance * scale if char.advance else None,
                )
                new_chars.append(new_char)

            # Calculate bounding box from new_chars
            min_x = min(char.box.x for char in new_chars)
            min_y = min(char.box.y for char in new_chars)
            max_x = max(char.box.x2 for char in new_chars)
            max_y = max(char.box.y2 for char in new_chars)

            new_formula = PdfFormula(
                box=Box(
                    x=min_x,
                    y=min_y,
                    x2=max_x,
                    y2=max_y,
                ),
                pdf_character=new_chars,
                x_offset=self.formular.x_offset * scale,
                y_offset=self.formular.y_offset * scale,
            )
            return TypesettingUnit(formular=new_formula)

        elif self.unicode:
            # 对于 Unicode 字符，我们存储新的位置信息
            new_unit = TypesettingUnit(
                unicode=self.unicode,
                font=self.font,
                font_size=self.font_size * scale,
                style=self.style,
                xobj_id=self.xobj_id,
                debug_info=self.debug_info,
            )
            new_unit.x = x
            new_unit.y = y
            new_unit.scale = scale
            return new_unit

    def render(self) -> [PdfCharacter]:
        """渲染排版单元为 PdfCharacter 列表

        Returns:
            PdfCharacter 列表
        """
        if self.can_passthrough:
            return self.passthrough()
        elif self.unicode:
            assert self.x is not None, (
                "x position must be set, should be set by `relocate`"
            )
            assert self.y is not None, (
                "y position must be set, should be set by `relocate`"
            )
            assert self.scale is not None, (
                "scale must be set, should be set by `relocate`"
            )
            # 计算字符宽度
            char_width = self.width

            new_char = PdfCharacter(
                pdf_character_id=self.font.has_glyph(ord(self.unicode)),
                char_unicode=self.unicode,
                box=Box(
                    x=self.x,  # 使用存储的位置
                    y=self.y,
                    x2=self.x + char_width,
                    y2=self.y + self.font_size,
                ),
                pdf_style=PdfStyle(
                    font_id=self.font_id,
                    font_size=self.font_size,
                    graphic_state=self.style.graphic_state,
                ),
                scale=self.scale,
                vertical=False,
                advance=char_width,
                xobj_id=self.xobj_id,
                debug_info=self.debug_info,
            )
            return [new_char]
        else:
            logger.error(f"Unknown typesetting unit. TypesettingUnit: {self}. ")
            logger.error(f"Unknown typesetting unit. TypesettingUnit: {self}. ")
            return []


class Typesetting:
    stage_name = "排版"

    def __init__(self, translation_config: TranslationConfig):
        self.font_mapper = FontMapper(translation_config)
        self.translation_config = translation_config

    def typsetting_document(self, document: il_version_1.Document):
        with self.translation_config.progress_monitor.stage_start(
            self.stage_name, len(document.page)
        ) as pbar:
            for page in document.page:
                self.translation_config.raise_if_cancelled()
                self.render_page(page)
                pbar.advance()

    def render_page(self, page: il_version_1.Page):
        fonts: dict[
            str | int,
            Union[il_version_1.PdfFont, dict[str, il_version_1.PdfFont]],
        ] = {f.font_id: f for f in page.pdf_font}
        page_fonts = {f.font_id: f for f in page.pdf_font}
        for k, v in self.font_mapper.fontid2font.items():
            fonts[k] = v
        for xobj in page.pdf_xobject:
            fonts[xobj.xobj_id] = page_fonts.copy()
            for font in xobj.pdf_font:
                fonts[xobj.xobj_id][font.font_id] = font
        if page.page_number == 0:
            self.add_watermark(page)
        # 开始实际的渲染过程
        for paragraph in page.pdf_paragraph:
            self.render_paragraph(paragraph, page, fonts)

    def add_watermark(self, page: il_version_1.Page):
        page_width = page.cropbox.box.x2 - page.cropbox.box.x
        page_height = page.cropbox.box.y2 - page.cropbox.box.y
        style = il_version_1.PdfStyle(
            font_id="base",
            font_size=6,
            graphic_state=il_version_1.GraphicState(),
        )
        text = "本文档由funstory.ai的开源PDF翻译库yadt(http://yadt.io)翻译，本仓库正在积极的建设当中，欢迎star和关注。"
        if self.translation_config.debug:
            text += "\n 当前为DEBUG模式，将显示更多辅助信息。请注意，部分框的位置对应原文，但在译文中可能不正确。"
        page.pdf_paragraph.append(
            il_version_1.PdfParagraph(
                first_line_indent=False,
                box=il_version_1.Box(
                    x=page.cropbox.box.x + page_width * 0.05,
                    y=page.cropbox.box.y,
                    x2=page.cropbox.box.x2,
                    y2=page.cropbox.box.y2 - page_height * 0.05,
                ),
                vertical=False,
                pdf_style=style,
                pdf_paragraph_composition=[
                    il_version_1.PdfParagraphComposition(
                        pdf_same_style_unicode_characters=il_version_1.PdfSameStyleUnicodeCharacters(
                            unicode=text,
                            pdf_style=style,
                        )
                    )
                ],
                xobj_id=-1,
            )
        )

    def render_paragraph(
        self,
        paragraph: il_version_1.PdfParagraph,
        page: il_version_1.Page,
        fonts: dict[
            str | int,
            Union[il_version_1.PdfFont, dict[str, il_version_1.PdfFont]],
        ],
    ):
        typesetting_units = self.create_typesetting_units(paragraph, fonts)
        # 如果所有单元都可以直接传递，则直接传递
        if all(unit.can_passthrough for unit in typesetting_units):
            paragraph.scale = 1.0
            paragraph.pdf_paragraph_composition = self.create_passthrough_composition(
                typesetting_units
            )
            return

        # 如果有单元无法直接传递，则进行重排版
        paragraph.pdf_paragraph_composition = []
        self.retypeset(paragraph, page, typesetting_units)

    def _layout_typesetting_units(
        self,
        typesetting_units: list[TypesettingUnit],
        box: Box,
        scale: float,
        line_spacing: float,
        paragraph: il_version_1.PdfParagraph,
    ) -> tuple[list[TypesettingUnit], bool]:
        """布局排版单元。

        Args:
            typesetting_units: 要布局的排版单元列表
            box: 布局边界框
            scale: 缩放因子
            line_spacing: 行间距

        Returns:
            tuple[list[TypesettingUnit], bool]: (已布局的排版单元列表, 是否所有单元都放得下)
        """
        # 计算字号众数
        font_sizes = []
        for unit in typesetting_units:
            if getattr(unit, "font_size", None):
                font_sizes.append(unit.font_size)
            if getattr(unit, "char", None):
                font_sizes.append(unit.char.pdf_style.font_size)
        font_sizes.sort()
        font_size = statistics.mode(font_sizes)

        space_width = (
            self.font_mapper.base_font.char_lengths("你", font_size * scale)[0] * 0.5
        )

        # 计算平均行高
        avg_height = (
            sum(unit.height * scale for unit in typesetting_units)
            / len(typesetting_units)
            if typesetting_units
            else 0
        )

        # 初始化位置为右上角，并减去一个平均行高
        current_x = box.x
        current_y = box.y2 - avg_height
        line_height = 0

        # 存储已排版的单元
        typeset_units = []
        all_units_fit = True
        last_unit: Optional[TypesettingUnit] = None

        if paragraph.first_line_indent:
            current_x += space_width * 4
        # 遍历所有排版单元
        for unit in typesetting_units:
            # 计算当前单元在当前缩放下的尺寸
            unit_width = unit.width * scale
            unit_height = unit.height * scale

            # 跳过行首的空格
            if current_x == box.x and unit.is_space:
                continue

            if (
                last_unit  # 有上一个单元
                and last_unit.is_chinese_char ^ unit.is_chinese_char  # 中英文交界处
                and (
                    last_unit.box
                    and last_unit.box.y
                    and current_y - 0.1
                    <= last_unit.box.y2
                    <= current_y + line_height + 0.1
                )  # 在同一行，且有垂直重叠
                and not last_unit.mixed_character_blacklist  # 不是混排空格黑名单字符
                and not unit.mixed_character_blacklist  # 同上
                and current_x > box.x  # 不是行首
                and unit.try_get_unicode() != " "  # 不是空格
                and last_unit.try_get_unicode() != " "  # 不是空格
            ):
                current_x += space_width * 0.5

            # 如果当前行放不下这个元素，换行
            if current_x + unit_width > box.x2 and not unit.is_hung_punctuation:
                # 换行
                current_x = box.x
                current_y -= line_height * line_spacing
                line_height = 0.0

                # 检查是否超出底部边界
                if current_y - unit_height < box.y:
                    all_units_fit = False
                    break

                if unit.is_space:
                    line_height = max(line_height, unit_height)
                    continue

            # 放置当前单元
            relocated_unit = unit.relocate(current_x, current_y, scale)
            typeset_units.append(relocated_unit)

            # workaround: 超长行距暂时没找到具体原因，有待进一步修复。这里的1.2是魔法数字！
            # 更新当前行的最大高度
            if line_height == 0 or line_height * 1.2 > unit_height > line_height:
                line_height = unit_height

            # 更新 x 坐标
            current_x = relocated_unit.box.x2

            last_unit = relocated_unit

        return typeset_units, all_units_fit

    def retypeset(
        self,
        paragraph: il_version_1.PdfParagraph,
        page: il_version_1.Page,
        typesetting_units: list[TypesettingUnit],
    ):
        box = paragraph.box
        scale = 1.0
        line_spacing = 1.5  # 初始行距为1.7
        min_scale = 0.1  # 最小缩放因子
        min_line_spacing = 1.4  # 最小行距
        expand_space_flag = False

        while scale >= min_scale:
            # 尝试布局排版单元
            typeset_units, all_units_fit = self._layout_typesetting_units(
                typesetting_units, box, scale, line_spacing, paragraph
            )

            # 如果所有单元都放得下，就完成排版
            if all_units_fit:
                # 将排版后的单元转换为段落组合
                paragraph.scale = scale
                paragraph.pdf_paragraph_composition = []
                for unit in typeset_units:
                    for char in unit.render():
                        paragraph.pdf_paragraph_composition.append(
                            PdfParagraphComposition(pdf_character=char)
                        )
                return

            if not expand_space_flag:
                # 如果尚未扩展空格，进行扩展
                max_x = self.get_max_right_space(box, page)
                # 只有当有额外空间时才扩展
                if max_x > box.x2:
                    expanded_box = Box(
                        x=box.x,
                        y=box.y,
                        x2=max_x,  # 直接扩展到最大可用位置
                        y2=box.y2,
                    )
                    # 更新段落的边界框
                    paragraph.box = expanded_box
                expand_space_flag = True
                continue

            # 如果当前行距大于最小行距，先减小行距
            if line_spacing > min_line_spacing:
                line_spacing -= 0.1
            else:
                # 行距已经最小，减小缩放因子
                if scale > 0.6:
                    scale -= 0.05
                else:
                    scale -= 0.1
                line_spacing = 1.5  # 重置行距

            if scale < 0.7 and min_line_spacing > 1.1:
                min_line_spacing = 1.1
                scale = 1.0
                line_spacing = 1.5

    def create_typesetting_units(
        self,
        paragraph: il_version_1.PdfParagraph,
        fonts: dict[str, il_version_1.PdfFont],
    ) -> list[TypesettingUnit]:
        if not paragraph.pdf_paragraph_composition:
            return []
        result = []

        @lru_cache(maxsize=None)
        def get_font(font_id: str, xobj_id: int):
            if xobj_id in fonts:
                font = fonts[xobj_id][font_id]
            else:
                font = fonts[font_id]
            return font

        for composition in paragraph.pdf_paragraph_composition:
            if composition is None:
                continue
            if composition.pdf_line:
                result.extend(
                    [
                        TypesettingUnit(char=char)
                        for char in composition.pdf_line.pdf_character
                    ]
                )
            elif composition.pdf_character:
                result.append(
                    TypesettingUnit(
                        char=composition.pdf_character,
                        debug_info=paragraph.debug_info,
                    )
                )
            elif composition.pdf_same_style_characters:
                result.extend(
                    [
                        TypesettingUnit(char=char)
                        for char in composition.pdf_same_style_characters.pdf_character
                    ]
                )
            elif composition.pdf_same_style_unicode_characters:
                font_id = (
                    composition.pdf_same_style_unicode_characters.pdf_style.font_id
                )
                font = get_font(font_id, paragraph.xobj_id)
                result.extend(
                    [
                        TypesettingUnit(
                            unicode=char_unicode,
                            font=self.font_mapper.map(
                                font,
                                char_unicode,
                            ),
                            font_size=composition.pdf_same_style_unicode_characters.pdf_style.font_size,
                            style=composition.pdf_same_style_unicode_characters.pdf_style,
                            xobj_id=paragraph.xobj_id,
                            debug_info=composition.pdf_same_style_unicode_characters.debug_info,
                        )
                        for char_unicode in composition.pdf_same_style_unicode_characters.unicode
                        if char_unicode not in ("\n",)
                    ]
                )
            elif composition.pdf_formula:
                result.extend([TypesettingUnit(formular=composition.pdf_formula)])
            else:
                logger.error(
                    f"Unknown composition type. "
                    f"Composition: {composition}. "
                    f"Paragraph: {paragraph}. "
                )
                continue
        result = list(
            filter(
                lambda x: getattr(x, "unicode") is None
                or getattr(x, "font") is not None,
                result,
            )
        )
        return result

    def create_passthrough_composition(
        self, typesetting_units: list[TypesettingUnit]
    ) -> list[PdfParagraphComposition]:
        """从排版单元创建直接传递的段落组合。

        Args:
            typesetting_units: 排版单元列表

        Returns:
            段落组合列表
        """
        composition = []
        for unit in typesetting_units:
            composition.extend(
                [
                    PdfParagraphComposition(pdf_character=char)
                    for char in unit.passthrough()
                ]
            )
        return composition

    def get_max_right_space(self, current_box: Box, page) -> float:
        """获取段落右侧最大可用空间

        Args:
            current_box: 当前段落的边界框
            page: 当前页面

        Returns:
            可以扩展到的最大 x 坐标
        """
        # TODO: try to find right margin of page
        # 获取页面的裁剪框作为初始最大限制
        max_x = page.cropbox.box.x2 * 0.9

        # 检查所有可能的阻挡元素
        for para in page.pdf_paragraph:
            if para.box == current_box or para.box is None:  # 跳过当前段落
                continue
            # 只考虑在当前段落右侧且有垂直重叠的元素
            if para.box.x > current_box.x and not (
                para.box.y >= current_box.y2 or para.box.y2 <= current_box.y
            ):
                max_x = min(max_x, para.box.x)

        # 检查图形
        for figure in page.pdf_figure:
            if figure.box.x > current_box.x and not (
                figure.box.y >= current_box.y2 or figure.box.y2 <= current_box.y
            ):
                max_x = min(max_x, figure.box.x)

        return max_x

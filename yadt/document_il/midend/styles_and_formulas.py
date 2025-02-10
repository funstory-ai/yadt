import base64
import math
import re
import unicodedata

from yadt.document_il.il_version_1 import (
    Box,
    Document,
    GraphicState,
    Page,
    PdfCharacter,
    PdfFormula,
    PdfLine,
    PdfParagraphComposition,
    PdfSameStyleCharacters,
    PdfStyle,
)
from yadt.document_il.utils.fontmap import FontMapper
from yadt.document_il.utils.layout_helper import (
    LEFT_BRACKET,
    RIGHT_BRACKET,
    formular_height_ignore_char,
    is_same_style,
)
from yadt.translation_config import TranslationConfig


class StylesAndFormulas:
    stage_name = "解析公式与样式"

    def __init__(self, translation_config: TranslationConfig):
        self.translation_config = translation_config
        self.font_mapper = FontMapper(translation_config)

    def process(self, document: Document):
        with self.translation_config.progress_monitor.stage_start(
            self.stage_name, len(document.page)
        ) as pbar:
            for page in document.page:
                self.translation_config.raise_if_cancelled()
                self.process_page(page)
                pbar.advance()

    def process_page(self, page: Page):
        """处理页面，包括公式识别和偏移量计算"""
        self.process_page_formulas(page)
        self.process_page_offsets(page)
        self.process_comma_formulas(page)
        self.merge_overlapping_formulas(page)
        self.process_page_offsets(page)
        self.process_translatable_formulas(page)
        self.process_page_styles(page)

    def update_line_data(self, line: PdfLine):
        min_x = min(char.box.x for char in line.pdf_character)
        min_y = min(char.box.y for char in line.pdf_character)
        max_x = max(char.box.x2 for char in line.pdf_character)
        max_y = max(char.box.y2 for char in line.pdf_character)
        line.box = Box(min_x, min_y, max_x, max_y)

    def process_page_formulas(self, page: Page):
        if not page.pdf_paragraph:
            return

        # 收集该页所有的公式字体ID
        formula_font_ids = set()
        for font in page.pdf_font:
            if self.is_formulas_font(font.name):
                formula_font_ids.add(font.font_id)

        for paragraph in page.pdf_paragraph:
            if not paragraph.pdf_paragraph_composition:
                continue

            new_compositions = []

            for composition in paragraph.pdf_paragraph_composition:
                current_chars = []
                in_formula_state = False  # 当前是否在处理公式字符
                in_corner_mark_state = False

                line = composition.pdf_line
                if not line:
                    new_compositions.append(composition)
                    continue
                for char in line.pdf_character:
                    is_formula = (
                        (  # 区分公式开头的字符&公式中间的字符。主要是逗号不能在公式开头，但是可以在中间。
                            (
                                self.is_formulas_start_char(char.char_unicode)
                                and not in_formula_state
                            )
                            or (
                                self.is_formulas_middle_char(char.char_unicode)
                                and in_formula_state
                            )
                        )  # 公式字符
                        or char.pdf_style.font_id in formula_font_ids  # 公式字体
                        or char.vertical  # 垂直字体
                        or (
                            #   如果是程序添加的dummy空格
                            char.char_unicode is None and in_formula_state
                        )
                    )

                    # isspace = get_char_unicode_string(current_chars).isspace()
                    isspace = all((x.char_unicode.isspace() for x in current_chars))
                    is_corner_mark = (
                        len(current_chars) > 0
                        and not isspace
                        # 角标字体，有 0.76 的角标和 0.799 的大写，这里用 0.79 取中，同时考虑首字母放大的情况
                        and char.pdf_style.font_size
                        < current_chars[-1].pdf_style.font_size * 0.79
                        and not in_corner_mark_state
                    ) or (
                        len(current_chars) > 0
                        and not isspace
                        # 角标字体，有 0.76 的角标和 0.799 的大写，这里用 0.79 取中，同时考虑首字母放大的情况
                        and char.pdf_style.font_size
                        < current_chars[-1].pdf_style.font_size * 1.1
                        and in_corner_mark_state
                    )

                    is_formula = is_formula or is_corner_mark

                    if char.char_unicode == " ":
                        is_formula = in_formula_state

                    if is_formula != in_formula_state and current_chars:
                        # 字符类型发生切换，处理之前的字符
                        new_compositions.append(
                            self.create_composition(current_chars, in_formula_state)
                        )
                        current_chars = []
                    in_formula_state = is_formula
                    in_corner_mark_state = is_corner_mark

                    current_chars.append(char)

                # 处理行末的字符
                if current_chars:
                    new_compositions.append(
                        self.create_composition(current_chars, in_formula_state)
                    )
                    current_chars = []

            paragraph.pdf_paragraph_composition = new_compositions

    def process_translatable_formulas(self, page: Page):
        """将需要正常翻译的公式（如纯数字、数字加逗号等）转换为普通文本行"""
        if not page.pdf_paragraph:
            return

        for paragraph in page.pdf_paragraph:
            if not paragraph.pdf_paragraph_composition:
                continue

            new_compositions = []
            for composition in paragraph.pdf_paragraph_composition:
                if composition.pdf_formula is not None and self.is_translatable_formula(
                    composition.pdf_formula
                ):
                    # 将可翻译公式转换为普通文本行
                    new_line = PdfLine(
                        pdf_character=composition.pdf_formula.pdf_character
                    )
                    self.update_line_data(new_line)
                    new_compositions.append(PdfParagraphComposition(pdf_line=new_line))
                else:
                    new_compositions.append(composition)

            paragraph.pdf_paragraph_composition = new_compositions

    def process_page_styles(self, page: Page):
        """处理页面中的文本样式，识别相同样式的文本"""
        if not page.pdf_paragraph:
            return

        for paragraph in page.pdf_paragraph:
            if not paragraph.pdf_paragraph_composition:
                continue

            # 计算基准样式（除公式外所有文字样式的交集）
            base_style = self._calculate_base_style(paragraph)
            paragraph.pdf_style = base_style

            # 重新组织段落中的文本，将相同样式的文本组合在一起
            new_compositions = []
            current_chars = []
            current_style = None

            for comp in paragraph.pdf_paragraph_composition:
                if comp.pdf_formula is not None:
                    if current_chars:
                        new_comp = self._create_same_style_composition(
                            current_chars, current_style
                        )
                        new_compositions.append(new_comp)
                        current_chars = []
                    new_compositions.append(comp)
                    continue

                if not comp.pdf_line:
                    new_compositions.append(comp)
                    continue

                for char in comp.pdf_line.pdf_character:
                    char_style = char.pdf_style
                    if current_style is None:
                        current_style = char_style
                        current_chars.append(char)
                    elif is_same_style(char_style, current_style):
                        current_chars.append(char)
                    else:
                        if current_chars:
                            new_comp = self._create_same_style_composition(
                                current_chars, current_style
                            )
                            new_compositions.append(new_comp)
                        current_chars = [char]
                        current_style = char_style

            if current_chars:
                new_comp = self._create_same_style_composition(
                    current_chars, current_style
                )
                new_compositions.append(new_comp)

            paragraph.pdf_paragraph_composition = new_compositions

    def _calculate_base_style(self, paragraph) -> PdfStyle:
        """计算段落的基准样式（除公式外所有文字样式的交集）"""
        styles = []
        for comp in paragraph.pdf_paragraph_composition:
            if isinstance(comp, PdfFormula):
                continue
            if not comp.pdf_line:
                continue
            for char in comp.pdf_line.pdf_character:
                styles.append(char.pdf_style)

        if not styles:
            return None

        # 返回所有样式的交集
        base_style = styles[0]
        for style in styles[1:]:
            # 更新基准样式为所有样式的交集
            base_style = self._merge_styles(base_style, style)

        # 如果font_id或font_size为None，则使用众数
        if base_style.font_id is None:
            base_style.font_id = self._get_mode_value([s.font_id for s in styles])
        if base_style.font_size is None:
            base_style.font_size = self._get_mode_value([s.font_size for s in styles])

        return base_style

    def _get_mode_value(self, values):
        """计算列表中的众数"""
        if not values:
            return None
        from collections import Counter

        counter = Counter(values)
        return counter.most_common(1)[0][0]

    def _merge_styles(self, style1, style2):
        """合并两个样式，返回它们的交集"""
        if style1 is None or style1.font_size is None:
            return style2
        if style2 is None or style2.font_size is None:
            return style1

        return PdfStyle(
            font_id=style1.font_id if style1.font_id == style2.font_id else None,
            font_size=(
                style1.font_size
                if math.fabs(style1.font_size - style2.font_size) < 0.02
                else None
            ),
            graphic_state=self._merge_graphic_states(
                style1.graphic_state, style2.graphic_state
            ),
        )

    def _merge_graphic_states(self, state1, state2):
        """合并两个GraphicState，返回它们的交集"""
        if state1 is None:
            return state2
        if state2 is None:
            return state1

        return GraphicState(
            linewidth=(
                state1.linewidth if state1.linewidth == state2.linewidth else None
            ),
            dash=state1.dash if state1.dash == state2.dash else None,
            flatness=state1.flatness if state1.flatness == state2.flatness else None,
            intent=state1.intent if state1.intent == state2.intent else None,
            linecap=state1.linecap if state1.linecap == state2.linecap else None,
            linejoin=state1.linejoin if state1.linejoin == state2.linejoin else None,
            miterlimit=(
                state1.miterlimit if state1.miterlimit == state2.miterlimit else None
            ),
            ncolor=state1.ncolor if state1.ncolor == state2.ncolor else None,
            scolor=state1.scolor if state1.scolor == state2.scolor else None,
            stroking_color_space_name=(
                state1.stroking_color_space_name
                if state1.stroking_color_space_name == state2.stroking_color_space_name
                else None
            ),
            non_stroking_color_space_name=(
                state1.non_stroking_color_space_name
                if state1.non_stroking_color_space_name
                == state2.non_stroking_color_space_name
                else None
            ),
            passthrough_per_char_instruction=(
                state1.passthrough_per_char_instruction
                if state1.passthrough_per_char_instruction
                == state2.passthrough_per_char_instruction
                else None
            ),
        )

    def _create_same_style_composition(
        self, chars: list[PdfCharacter], style
    ) -> PdfParagraphComposition:
        """创建具有相同样式的文本组合"""
        if not chars:
            return None

        # 计算边界框
        min_x = min(char.box.x for char in chars)
        min_y = min(char.box.y for char in chars)
        max_x = max(char.box.x2 for char in chars)
        max_y = max(char.box.y2 for char in chars)
        box = Box(min_x, min_y, max_x, max_y)

        return PdfParagraphComposition(
            pdf_same_style_characters=PdfSameStyleCharacters(
                box=box,
                pdf_style=style,
                pdf_character=chars,
            )
        )

    def process_page_offsets(self, page: Page):
        """计算公式的x和y偏移量"""
        if not page.pdf_paragraph:
            return

        for paragraph in page.pdf_paragraph:
            if not paragraph.pdf_paragraph_composition:
                continue

            # 计算该段落的行间距，用其80%作为容差
            line_spacing = self.calculate_line_spacing(paragraph)
            y_tolerance = line_spacing * 0.8

            for i, composition in enumerate(paragraph.pdf_paragraph_composition):
                if not composition.pdf_formula:
                    continue

                formula = composition.pdf_formula
                left_line = None
                right_line = None

                # 查找左边最近的同一行的文本
                for j in range(i - 1, -1, -1):
                    comp = paragraph.pdf_paragraph_composition[j]
                    if comp.pdf_line:
                        # 检查y坐标是否接近，判断是否在同一行
                        if abs(comp.pdf_line.box.y - formula.box.y) <= y_tolerance:
                            left_line = comp.pdf_line
                            break

                # 查找右边最近的同一行的文本
                for j in range(i + 1, len(paragraph.pdf_paragraph_composition)):
                    comp = paragraph.pdf_paragraph_composition[j]
                    if comp.pdf_line:
                        # 检查y坐标是否接近，判断是否在同一行
                        if abs(comp.pdf_line.box.y - formula.box.y) <= y_tolerance:
                            right_line = comp.pdf_line
                            break

                # 计算x偏移量（相对于左边文本）
                if left_line:
                    formula.x_offset = formula.box.x - left_line.box.x2
                else:
                    formula.x_offset = 0  # 如果左边没有文字，x_offset应该为0
                if abs(formula.x_offset) < 0.1:
                    formula.x_offset = 0
                if formula.x_offset > 0:
                    formula.x_offset = 0

                # 计算y偏移量
                if left_line:
                    # 使用底部坐标计算偏移量
                    formula.y_offset = formula.box.y - left_line.box.y
                elif right_line:
                    formula.y_offset = formula.box.y - right_line.box.y
                else:
                    formula.y_offset = 0

                if abs(formula.y_offset) < 0.1:
                    formula.y_offset = 0

    def calculate_line_spacing(self, paragraph) -> float:
        """计算段落中的平均行间距"""
        if not paragraph.pdf_paragraph_composition:
            return 0.0

        # 收集所有文本行的y坐标
        line_y_positions = []
        for comp in paragraph.pdf_paragraph_composition:
            if comp.pdf_line:
                line_y_positions.append(comp.pdf_line.box.y)

        if len(line_y_positions) < 2:
            return 10.0  # 如果只有一行或没有行，返回一个默认值

        # 计算相邻行之间的y差值
        line_spacings = []
        for i in range(len(line_y_positions) - 1):
            spacing = abs(line_y_positions[i] - line_y_positions[i + 1])
            if spacing > 0:  # 忽略重叠的行
                line_spacings.append(spacing)

        if not line_spacings:
            return 10.0  # 如果没有有效的行间距，返回默认值

        # 使用中位数来避免异常值的影响
        median_spacing = sorted(line_spacings)[len(line_spacings) // 2]
        return median_spacing

    def create_composition(
        self, chars: list[PdfCharacter], is_formula: bool
    ) -> PdfParagraphComposition:
        if is_formula:
            formula = PdfFormula(pdf_character=chars)
            self.update_formula_data(formula)
            return PdfParagraphComposition(pdf_formula=formula)
        else:
            new_line = PdfLine(pdf_character=chars)
            self.update_line_data(new_line)
            return PdfParagraphComposition(pdf_line=new_line)

    def update_formula_data(self, formula: PdfFormula):
        min_x = min(char.box.x for char in formula.pdf_character)
        max_x = max(char.box.x2 for char in formula.pdf_character)
        if not all(map(formular_height_ignore_char, formula.pdf_character)):
            min_y = min(
                char.box.y
                for char in formula.pdf_character
                if not formular_height_ignore_char(char)
            )
            max_y = max(
                char.box.y2
                for char in formula.pdf_character
                if not formular_height_ignore_char(char)
            )
        else:
            min_y = min(char.box.y for char in formula.pdf_character)
            max_y = max(char.box.y2 for char in formula.pdf_character)
        formula.box = Box(min_x, min_y, max_x, max_y)

    def is_translatable_formula(self, formula: PdfFormula) -> bool:
        """判断公式是否只包含需要正常翻译的字符（数字、空格和英文逗号）"""
        text = "".join(char.char_unicode for char in formula.pdf_character)
        if formula.y_offset > 0.1:
            return False
        return bool(re.match(r"^[0-9, ]+$", text))

    def is_formulas_font(self, font_name: str) -> bool:
        pattern2 = r"^(Cambria|Cambria-BoldItalic|Cambria-Bold|Cambria-Italic)$"
        if self.translation_config.formular_font_pattern:
            pattern = self.translation_config.formular_font_pattern
        else:
            pattern = (
                r"(CM[^RB]"
                r"|(MS|XY|MT|BL|RM|EU|LA|RS)[A-Z]"
                r"|LINE"
                r"|LCIRCLE"
                r"|TeX-"
                r"|rsfs"
                r"|txsy"
                r"|wasy"
                r"|stmary"
                r"|.*Mono"
                r"|.*Code"
                r"|.*Ital"
                r"|.*Sym"
                r"|.*Math"
                r")"
            )

        if font_name.startswith("BASE64:"):
            font_name_bytes = base64.b64decode(font_name[7:])
            font = font_name_bytes.split(b"+")[-1]
            pattern2 = pattern2.encode()
            pattern = pattern.encode()
        else:
            font = font_name.split("+")[-1]

        if re.match(pattern2, font):
            return False
        if re.match(pattern, font):
            return True

        return False

    def is_formulas_start_char(self, char: str) -> bool:
        if "(cid:" in char:
            return True
        if not self.font_mapper.has_char(char):
            return True
        if self.translation_config.formular_char_pattern:
            pattern = self.translation_config.formular_char_pattern
            if re.match(pattern, char):
                return True
        if (
            char
            and char != " "  # 非空格
            and (
                unicodedata.category(char[0])
                in [
                    "Lm",
                    "Mn",
                    "Sk",
                    "Sm",
                    "Zl",
                    "Zp",
                    "Zs",
                    "Co",  # private use character
                    "So",  # symbol
                ]  # 文字修饰符、数学符号、分隔符号
                or ord(char[0]) in range(0x370, 0x400)  # 希腊字母
            )
        ):
            return True
        if re.match("[0-9\\[\\]•]", char):
            return True
        return False

    def is_formulas_middle_char(self, char: str) -> bool:
        if self.is_formulas_start_char(char):
            return True

        if re.match(",", char):
            return True

    def should_split_formula(self, formula: PdfFormula) -> bool:
        """判断公式是否需要按逗号拆分（包含逗号且有其他特殊符号）"""
        text = "".join(char.char_unicode for char in formula.pdf_character)
        # 必须包含逗号
        if "," not in text:
            return False
        # 检查是否包含除了数字和[]之外的其他符号
        text_without_basic = re.sub(r"[0-9\[\],\s]", "", text)
        return bool(text_without_basic)

    def split_formula_by_comma(
        self, formula: PdfFormula
    ) -> list[tuple[list[PdfCharacter], PdfCharacter]]:
        """按逗号拆分公式字符，返回(字符组, 逗号字符)的列表，最后一组的逗号字符为None。
        只有不在括号内的逗号才会被用作分隔符。支持的括号对包括：
        - (cid:8) 和 (cid:9)
        - ( 和 )
        - (cid:16) 和 (cid:17)
        """
        result = []
        current_chars = []
        bracket_level = 0  # 跟踪括号的层数

        for char in formula.pdf_character:
            # 检查是否是左括号
            if char.char_unicode in LEFT_BRACKET:
                bracket_level += 1
                current_chars.append(char)
            # 检查是否是右括号
            elif char.char_unicode in RIGHT_BRACKET:
                bracket_level = max(0, bracket_level - 1)  # 防止括号不匹配的情况
                current_chars.append(char)
            # 检查是否是逗号，且不在括号内
            elif char.char_unicode == "," and bracket_level == 0:
                if current_chars:
                    result.append((current_chars, char))
                    current_chars = []
            else:
                current_chars.append(char)

        if current_chars:
            result.append((current_chars, None))  # 最后一组没有逗号

        return result

    def merge_formulas(self, formula1: PdfFormula, formula2: PdfFormula) -> PdfFormula:
        """合并两个公式，保持字符的相对位置"""
        # 合并所有字符
        all_chars = formula1.pdf_character + formula2.pdf_character
        # 按y坐标和x坐标排序，确保字符顺序正确
        sorted_chars = sorted(all_chars, key=lambda c: (c.box.y, c.box.x))

        merged_formula = PdfFormula(pdf_character=sorted_chars)
        self.update_formula_data(merged_formula)
        return merged_formula

    def merge_overlapping_formulas(self, page: Page):
        """
        合并x轴重叠且y轴有交集的相邻公式
        角标可能会被识别成单独的公式，需要合并
        """
        if not page.pdf_paragraph:
            return

        for paragraph in page.pdf_paragraph:
            if not paragraph.pdf_paragraph_composition:
                continue

            i = 0
            while i < len(paragraph.pdf_paragraph_composition) - 1:
                comp1 = paragraph.pdf_paragraph_composition[i]
                comp2 = paragraph.pdf_paragraph_composition[i + 1]

                # 检查是否都是公式
                if comp1.pdf_formula is None or comp2.pdf_formula is None:
                    i += 1
                    continue

                formula1 = comp1.pdf_formula
                formula2 = comp2.pdf_formula

                # 检查x轴重叠和y轴交集
                if self.is_x_axis_contained(
                    formula1.box, formula2.box
                ) and self.has_y_intersection(formula1.box, formula2.box):
                    # 合并公式
                    merged_formula = self.merge_formulas(formula1, formula2)
                    paragraph.pdf_paragraph_composition[i] = PdfParagraphComposition(
                        pdf_formula=merged_formula
                    )
                    # 删除第二个公式
                    del paragraph.pdf_paragraph_composition[i + 1]
                    # 不增加i，因为合并后的公式可能还需要和下一个公式合并
                else:
                    i += 1

    def is_x_axis_contained(self, box1: Box, box2: Box) -> bool:
        """判断box1的x轴是否完全包含在box2的x轴内，或反之"""
        return (box1.x >= box2.x and box1.x2 <= box2.x2) or (
            box2.x >= box1.x and box2.x2 <= box1.x2
        )

    def has_y_intersection(self, box1: Box, box2: Box) -> bool:
        """判断两个box的y轴是否有交集"""
        return not (box1.y2 < box2.y or box2.y2 < box1.y)

    def process_comma_formulas(self, page: Page):
        """处理包含逗号的复杂公式，将其按逗号拆分"""
        if not page.pdf_paragraph:
            return

        for paragraph in page.pdf_paragraph:
            if not paragraph.pdf_paragraph_composition:
                continue

            new_compositions = []
            for composition in paragraph.pdf_paragraph_composition:
                if composition.pdf_formula is not None and self.should_split_formula(
                    composition.pdf_formula
                ):
                    # 按逗号拆分公式
                    char_groups = self.split_formula_by_comma(composition.pdf_formula)
                    for chars, comma in char_groups:
                        if chars:  # 忽略空组（连续的逗号）
                            formula = PdfFormula(pdf_character=chars)
                            self.update_formula_data(formula)
                            new_compositions.append(
                                PdfParagraphComposition(pdf_formula=formula)
                            )

                            # 如果有逗号，添加为文本行
                            if comma:
                                comma_line = PdfLine(pdf_character=[comma])
                                self.update_line_data(comma_line)
                                new_compositions.append(
                                    PdfParagraphComposition(pdf_line=comma_line)
                                )
                else:
                    new_compositions.append(composition)

            paragraph.pdf_paragraph_composition = new_compositions

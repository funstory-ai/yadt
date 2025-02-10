import logging
import math
from typing import List, Optional, Union

from pymupdf import Font

from yadt.document_il import GraphicState
from yadt.document_il.il_version_1 import (
    Box,
    PdfCharacter,
    PdfParagraph,
    PdfParagraphComposition,
)

logger = logging.getLogger(__name__)
HEIGHT_NOT_USFUL_CHAR_IN_CHAR = (
    "∑︁",
    # 暂时假设cid:17和cid 16是特殊情况
    # 来源于 arXiv:2310.18608v2 第九页公式大括号
    "(cid:17)",
    "(cid:16)",
    # arXiv:2411.19509v2 第四页 []
    "(cid:104)",
    "(cid:105)",
    # arXiv:2411.19509v2 第四页 公式的|竖线
    "(cid:13)",
    "∑︁",
    # arXiv:2412.05265 27页 累加号
    "(cid:88)",
    # arXiv:2412.05265 16页 累乘号
    "(cid:89)",
    # arXiv:2412.05265 27页 积分
    "(cid:90)",
    # arXiv:2412.05265 32页 公式左右的中括号
    "(cid:2)",
    "(cid:3)",
)


LEFT_BRACKET = ("(cid:8)", "(", "(cid:16)", "{", "[", "(cid:104)", "(cid:2)")
RIGHT_BRACKET = ("(cid:9)", ")", "(cid:17)", "}", "]", "(cid:105)", "(cid:3)")


def formular_height_ignore_char(char: PdfCharacter):
    return (
        char.pdf_character_id is None
        or char.char_unicode in HEIGHT_NOT_USFUL_CHAR_IN_CHAR
    )


class Layout:
    def __init__(self, id, name):
        self.id = id
        self.name = name

    @staticmethod
    def is_newline(prev_char: PdfCharacter, curr_char: PdfCharacter) -> bool:
        # 如果没有前一个字符，不是换行
        if prev_char is None:
            return False

        # 获取两个字符的中心 y 坐标
        # prev_y = (prev_char.box.y + prev_char.box.y2) / 2
        # curr_y = (curr_char.box.y + curr_char.box.y2) / 2

        # 如果当前字符的 y 坐标明显低于前一个字符，说明换行了
        # 这里使用字符高度的一半作为阈值
        char_height = max(
            curr_char.box.y2 - curr_char.box.y,
            prev_char.box.y2 - prev_char.box.y,
        )
        char_width = max(
            curr_char.box.x2 - curr_char.box.x,
            prev_char.box.x2 - prev_char.box.x,
        )
        should_new_line = (
            curr_char.box.y2 < prev_char.box.y
            or curr_char.box.x2 < prev_char.box.x - char_width * 10
        )
        if should_new_line and (
            formular_height_ignore_char(curr_char)
            or formular_height_ignore_char(prev_char)
        ):
            return False
        return should_new_line


def get_paragraph_length_except(
    paragraph: PdfParagraph, except_chars: str, font: Font
) -> int:
    length = 0
    for composition in paragraph.pdf_paragraph_composition:
        if composition.pdf_character:
            length += (
                composition.pdf_character[0].box.x2 - composition.pdf_character[0].box.x
            )
        elif composition.pdf_same_style_characters:
            for pdf_char in composition.pdf_same_style_characters.pdf_character:
                if pdf_char.char_unicode in except_chars:
                    continue
                length += pdf_char.box.x2 - pdf_char.box.x
        elif composition.pdf_same_style_unicode_characters:
            for char_unicode in composition.pdf_same_style_unicode_characters.unicode:
                if char_unicode in except_chars:
                    continue
                length += font.char_lengths(
                    char_unicode,
                    composition.pdf_same_style_unicode_characters.pdf_style.font_size,
                )[0]
        elif composition.pdf_line:
            for pdf_char in composition.pdf_line.pdf_character:
                if pdf_char.char_unicode in except_chars:
                    continue
                length += pdf_char.box.x2 - pdf_char.box.x
        elif composition.pdf_formula:
            length += composition.pdf_formula.box.x2 - composition.pdf_formula.box.x
        else:
            logger.error(
                f"Unknown composition type. "
                f"Composition: {composition}. "
                f"Paragraph: {paragraph}. "
            )
            continue
    return length


def get_paragraph_unicode(paragraph: PdfParagraph) -> str:
    chars = []
    for composition in paragraph.pdf_paragraph_composition:
        if composition.pdf_line:
            chars.extend(composition.pdf_line.pdf_character)
        elif composition.pdf_same_style_characters:
            chars.extend(composition.pdf_same_style_characters.pdf_character)
        elif composition.pdf_same_style_unicode_characters:
            chars.extend(composition.pdf_same_style_unicode_characters.unicode)
        elif composition.pdf_formula:
            chars.extend(composition.pdf_formula.pdf_character)
        elif composition.pdf_character:
            chars.append(composition.pdf_character)
        else:
            logger.error(
                f"Unknown composition type. "
                f"Composition: {composition}. "
                f"Paragraph: {paragraph}. "
            )
            continue
    return get_char_unicode_string(chars)


def get_char_unicode_string(chars: List[Union[PdfCharacter, str]]) -> str:
    """
    将字符列表转换为 Unicode 字符串，根据字符间距自动插入空格。
    有些 PDF 不会显式编码空格，这时需要根据间距自动插入空格。

    Args:
        chars: 字符列表，可以是 PdfCharacter 对象或字符串

    Returns:
        str: 处理后的 Unicode 字符串
    """
    # 计算字符间距的中位数
    distances = []
    for i in range(len(chars) - 1):
        if not (
            isinstance(chars[i], PdfCharacter)
            and isinstance(chars[i + 1], PdfCharacter)
        ):
            continue
        distance = chars[i + 1].box.x - chars[i].box.x2
        if distance > 1:  # 只考虑正向距离
            distances.append(distance)

    # 去重后的距离
    distinct_distances = sorted(set(distances))

    if not distinct_distances:
        median_distance = 1
    elif len(distinct_distances) == 1:
        median_distance = distinct_distances[0]
    else:
        median_distance = distinct_distances[1]

    # 构建 unicode 字符串，根据间距插入空格
    unicode_chars = []
    for i in range(len(chars)):
        # 如果不是字符对象，直接添加，一般来说这个时候 chars[i] 是字符串
        if not isinstance(chars[i], PdfCharacter):
            unicode_chars.append(chars[i])
            continue
        unicode_chars.append(chars[i].char_unicode)

        # 如果是空格，跳过
        if chars[i].char_unicode == " ":
            continue

        # 如果两个字符都是 PdfCharacter，检查间距
        if i < len(chars) - 1 and isinstance(chars[i + 1], PdfCharacter):
            distance = chars[i + 1].box.x - chars[i].box.x2
            if distance >= median_distance or Layout.is_newline(  # 间距大于中位数
                chars[i], chars[i + 1]
            ):  # 换行
                unicode_chars.append(" ")  # 添加空格

    return "".join(unicode_chars)


def get_paragraph_max_height(paragraph: PdfParagraph) -> float:
    """
    获取段落中最高的排版单元高度。

    Args:
        paragraph: PDF段落对象

    Returns:
        float: 最大高度值
    """
    max_height = 0.0
    for composition in paragraph.pdf_paragraph_composition:
        if composition is None:
            continue
        if composition.pdf_character:
            char_height = (
                composition.pdf_character[0].box.y2 - composition.pdf_character[0].box.y
            )
            max_height = max(max_height, char_height)
        elif composition.pdf_same_style_characters:
            for pdf_char in composition.pdf_same_style_characters.pdf_character:
                char_height = pdf_char.box.y2 - pdf_char.box.y
                max_height = max(max_height, char_height)
        elif composition.pdf_same_style_unicode_characters:
            # 对于纯Unicode字符，我们使用其样式中的字体大小作为高度估计
            font_size = (
                composition.pdf_same_style_unicode_characters.pdf_style.font_size
            )
            max_height = max(max_height, font_size)
        elif composition.pdf_line:
            for pdf_char in composition.pdf_line.pdf_character:
                char_height = pdf_char.box.y2 - pdf_char.box.y
                max_height = max(max_height, char_height)
        elif composition.pdf_formula:
            formula_height = (
                composition.pdf_formula.box.y2 - composition.pdf_formula.box.y
            )
            max_height = max(max_height, formula_height)
        else:
            logger.error(
                f"Unknown composition type. "
                f"Composition: {composition}. "
                f"Paragraph: {paragraph}. "
            )
            continue
    return max_height


def is_same_style(style1, style2) -> bool:
    """判断两个样式是否相同"""
    if style1 is None or style2 is None:
        return style1 is style2

    return (
        style1.font_id == style2.font_id
        and math.fabs(style1.font_size - style2.font_size) < 0.02
        and is_same_graphic_state(style1.graphic_state, style2.graphic_state)
    )


def is_same_style_except_size(style1, style2) -> bool:
    """判断两个样式是否相同"""
    if style1 is None or style2 is None:
        return style1 is style2

    return (
        style1.font_id == style2.font_id
        and 0.7 < math.fabs(style1.font_size / style2.font_size) < 1.3
        and is_same_graphic_state(style1.graphic_state, style2.graphic_state)
    )


def is_same_style_except_font(style1, style2) -> bool:
    """判断两个样式是否相同"""
    if style1 is None or style2 is None:
        return style1 is style2

    return math.fabs(
        style1.font_size - style2.font_size
    ) < 0.02 and is_same_graphic_state(style1.graphic_state, style2.graphic_state)


def is_same_graphic_state(state1: GraphicState, state2: GraphicState) -> bool:
    """判断两个 GraphicState 是否相同"""
    if state1 is None or state2 is None:
        return state1 is state2

    return (
        state1.linewidth == state2.linewidth
        and state1.dash == state2.dash
        and state1.flatness == state2.flatness
        and state1.intent == state2.intent
        and state1.linecap == state2.linecap
        and state1.linejoin == state2.linejoin
        and state1.miterlimit == state2.miterlimit
        and state1.ncolor == state2.ncolor
        and state1.scolor == state2.scolor
        and state1.stroking_color_space_name == state2.stroking_color_space_name
        and state1.non_stroking_color_space_name == state2.non_stroking_color_space_name
        and state1.passthrough_per_char_instruction
        == state2.passthrough_per_char_instruction
    )


def add_space_dummy_chars(paragraph: PdfParagraph) -> None:
    """
    在PDF段落中添加表示空格的dummy字符。
    这个函数会直接修改传入的paragraph对象，在需要空格的地方添加dummy字符。
    同时也会处理不同组成部分之间的空格。

    Args:
        paragraph: 需要处理的PDF段落对象
    """
    # 首先处理每个组成部分内部的空格
    for composition in paragraph.pdf_paragraph_composition:
        if composition.pdf_line:
            chars = composition.pdf_line.pdf_character
            _add_space_dummy_chars_to_list(chars)
        elif composition.pdf_same_style_characters:
            chars = composition.pdf_same_style_characters.pdf_character
            _add_space_dummy_chars_to_list(chars)
        elif composition.pdf_same_style_unicode_characters:
            # 对于unicode字符，不需要处理。
            # 这种类型只会出现在翻译好的结果中
            continue
        elif composition.pdf_formula:
            chars = composition.pdf_formula.pdf_character
            _add_space_dummy_chars_to_list(chars)

    # 然后处理组成部分之间的空格
    for i in range(len(paragraph.pdf_paragraph_composition) - 1):
        curr_comp = paragraph.pdf_paragraph_composition[i]
        next_comp = paragraph.pdf_paragraph_composition[i + 1]

        # 获取当前组成部分的最后一个字符
        curr_last_char = _get_last_char_from_composition(curr_comp)
        if not curr_last_char:
            continue

        # 获取下一个组成部分的第一个字符
        next_first_char = _get_first_char_from_composition(next_comp)
        if not next_first_char:
            continue

        # 检查两个组成部分之间是否需要添加空格
        distance = next_first_char.box.x - curr_last_char.box.x2
        if distance > 1:  # 只考虑正向距离
            # 创建一个dummy字符作为空格
            space_box = Box(
                x=curr_last_char.box.x2,
                y=curr_last_char.box.y,
                x2=curr_last_char.box.x2 + distance,
                y2=curr_last_char.box.y2,
            )

            space_char = PdfCharacter(
                pdf_style=curr_last_char.pdf_style,
                box=space_box,
                char_unicode=" ",
                scale=curr_last_char.scale,
                advance=space_box.x2 - space_box.x,
            )

            # 将空格添加到当前组成部分的末尾
            if curr_comp.pdf_line:
                curr_comp.pdf_line.pdf_character.append(space_char)
            elif curr_comp.pdf_same_style_characters:
                curr_comp.pdf_same_style_characters.pdf_character.append(space_char)
            elif curr_comp.pdf_formula:
                curr_comp.pdf_formula.pdf_character.append(space_char)


def _get_first_char_from_composition(
    comp: PdfParagraphComposition,
) -> Optional[PdfCharacter]:
    """获取组成部分的第一个字符"""
    if comp.pdf_line and comp.pdf_line.pdf_character:
        return comp.pdf_line.pdf_character[0]
    elif (
        comp.pdf_same_style_characters and comp.pdf_same_style_characters.pdf_character
    ):
        return comp.pdf_same_style_characters.pdf_character[0]
    elif comp.pdf_formula and comp.pdf_formula.pdf_character:
        return comp.pdf_formula.pdf_character[0]
    elif comp.pdf_character:
        return comp.pdf_character
    return None


def _get_last_char_from_composition(
    comp: PdfParagraphComposition,
) -> Optional[PdfCharacter]:
    """获取组成部分的最后一个字符"""
    if comp.pdf_line and comp.pdf_line.pdf_character:
        return comp.pdf_line.pdf_character[-1]
    elif (
        comp.pdf_same_style_characters and comp.pdf_same_style_characters.pdf_character
    ):
        return comp.pdf_same_style_characters.pdf_character[-1]
    elif comp.pdf_formula and comp.pdf_formula.pdf_character:
        return comp.pdf_formula.pdf_character[-1]
    elif comp.pdf_character:
        return comp.pdf_character
    return None


def _add_space_dummy_chars_to_list(chars: List[PdfCharacter]) -> None:
    """
    在字符列表中的适当位置添加表示空格的dummy字符。

    Args:
        chars: PdfCharacter对象列表
    """
    if not chars:
        return

    # 计算字符间距的中位数
    distances = []
    for i in range(len(chars) - 1):
        distance = chars[i + 1].box.x - chars[i].box.x2
        if distance > 1:  # 只考虑正向距离
            distances.append(distance)

    # 去重后的距离
    distinct_distances = sorted(set(distances))

    if not distinct_distances:
        median_distance = 1
    elif len(distinct_distances) == 1:
        median_distance = distinct_distances[0]
    else:
        median_distance = distinct_distances[1]

    # 在需要的地方插入空格字符
    i = 0
    while i < len(chars) - 1:
        curr_char = chars[i]
        next_char = chars[i + 1]

        distance = next_char.box.x - curr_char.box.x2
        if distance >= median_distance or Layout.is_newline(curr_char, next_char):
            # 创建一个dummy字符作为空格
            space_box = Box(
                x=curr_char.box.x2,
                y=curr_char.box.y,
                x2=curr_char.box.x2 + min(distance, median_distance),
                y2=curr_char.box.y2,
            )

            space_char = PdfCharacter(
                pdf_style=curr_char.pdf_style,
                box=space_box,
                char_unicode=" ",
                scale=curr_char.scale,
                advance=space_box.x2 - space_box.x,
            )

            # 在当前位置后插入空格字符
            chars.insert(i + 1, space_char)
            i += 2  # 跳过刚插入的空格
        else:
            i += 1

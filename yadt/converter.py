import base64
import logging
import re
import unicodedata
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
from pdfminer.converter import PDFConverter
from pdfminer.layout import LTChar, LTComponent, LTFigure, LTLine, LTPage, LTText
from pdfminer.pdfcolor import PDFColorSpace
from pdfminer.pdffont import PDFCIDFont, PDFFont, PDFUnicodeNotDefined
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
from pdfminer.utils import Matrix, apply_matrix_pt, bbox2str, matrix2str, mult_matrix
from pymupdf import Font

from yadt.document_il.frontend.il_creater import ILCreater

log = logging.getLogger(__name__)


class PDFConverterEx(PDFConverter):
    def __init__(
        self,
        rsrcmgr: PDFResourceManager,
        il_creater: ILCreater = None,
    ) -> None:
        PDFConverter.__init__(self, rsrcmgr, None, "utf-8", 1, None)
        self.il_creater = il_creater

    def begin_page(self, page, ctm) -> None:
        # 重载替换 cropbox
        (x0, y0, x1, y1) = page.cropbox
        (x0, y0) = apply_matrix_pt(ctm, (x0, y0))
        (x1, y1) = apply_matrix_pt(ctm, (x1, y1))
        mediabox = (0, 0, abs(x0 - x1), abs(y0 - y1))
        self.il_creater.on_page_media_box(
            mediabox[0], mediabox[1], mediabox[2], mediabox[3]
        )
        self.il_creater.on_page_number(page.pageno)
        self.cur_item = LTPage(page.pageno, mediabox)

    def end_page(self, page):
        # 重载返回指令流
        return self.receive_layout(self.cur_item)

    def begin_figure(self, name, bbox, matrix) -> None:
        # 重载设置 pageid
        self._stack.append(self.cur_item)
        self.cur_item = LTFigure(name, bbox, mult_matrix(matrix, self.ctm))
        self.cur_item.pageid = self._stack[-1].pageid

    def end_figure(self, _: str) -> None:
        # 重载返回指令流
        fig = self.cur_item
        assert isinstance(self.cur_item, LTFigure), str(type(self.cur_item))
        self.cur_item = self._stack.pop()
        self.cur_item.add(fig)
        return self.receive_layout(fig)

    def render_char(
        self,
        matrix,
        font,
        fontsize: float,
        scaling: float,
        rise: float,
        cid: int,
        ncs,
        graphicstate: PDFGraphicState,
    ) -> float:
        # 重载设置 cid 和 font
        try:
            text = font.to_unichr(cid)
            assert isinstance(text, str), str(type(text))
        except PDFUnicodeNotDefined:
            text = self.handle_undefined_char(font, cid)
        textwidth = font.char_width(cid)
        textdisp = font.char_disp(cid)

        font_name = font.fontname
        if isinstance(font_name, bytes):
            try:
                font_name = font_name.decode("utf-8")
            except UnicodeDecodeError:
                font_name = "BASE64:" + base64.b64encode(font_name).decode("utf-8")
        font_id = self.il_creater.current_page_font_name_id_map[font_name]

        item = AWLTChar(
            matrix,
            font,
            fontsize,
            scaling,
            rise,
            text,
            textwidth,
            textdisp,
            ncs,
            graphicstate,
            self.il_creater.xobj_id,
            font_id,
        )
        self.cur_item.add(item)
        item.cid = cid  # hack 插入原字符编码
        item.font = font  # hack 插入原字符字体
        return item.adv


class AWLTChar(LTChar):
    """Actual letter in the text as a Unicode string."""

    def __init__(
        self,
        matrix: Matrix,
        font: PDFFont,
        fontsize: float,
        scaling: float,
        rise: float,
        text: str,
        textwidth: float,
        textdisp: Union[float, Tuple[Optional[float], float]],
        ncs: PDFColorSpace,
        graphicstate: PDFGraphicState,
        xobj_id: int,
        font_id: str,
    ) -> None:
        LTText.__init__(self)
        self._text = text
        self.matrix = matrix
        self.fontname = font.fontname
        self.ncs = ncs
        self.graphicstate = graphicstate
        self.xobj_id = xobj_id
        self.adv = textwidth * fontsize * scaling
        self.aw_font_id = font_id
        # compute the boundary rectangle.
        if font.is_vertical():
            # vertical
            assert isinstance(textdisp, tuple)
            (vx, vy) = textdisp
            if vx is None:
                vx = fontsize * 0.5
            else:
                vx = vx * fontsize * 0.001
            vy = (1000 - vy) * fontsize * 0.001
            bbox_lower_left = (-vx, vy + rise + self.adv)
            bbox_upper_right = (-vx + fontsize, vy + rise)
        else:
            # horizontal
            descent = font.get_descent() * fontsize
            bbox_lower_left = (0, descent + rise)
            bbox_upper_right = (self.adv, descent + rise + fontsize)
        (a, b, c, d, e, f) = self.matrix
        self.upright = 0 < a * d * scaling and b * c <= 0
        (x0, y0) = apply_matrix_pt(self.matrix, bbox_lower_left)
        (x1, y1) = apply_matrix_pt(self.matrix, bbox_upper_right)
        if x1 < x0:
            (x0, x1) = (x1, x0)
        if y1 < y0:
            (y0, y1) = (y1, y0)
        LTComponent.__init__(self, (x0, y0, x1, y1))
        if font.is_vertical() or matrix[0] == 0:
            self.size = self.width
        else:
            self.size = self.height
        return

    def __repr__(self) -> str:
        return "<{} {} matrix={} font={!r} adv={} text={!r}>".format(
            self.__class__.__name__,
            bbox2str(self.bbox),
            matrix2str(self.matrix),
            self.fontname,
            self.adv,
            self.get_text(),
        )

    def get_text(self) -> str:
        return self._text


class Paragraph:
    def __init__(self, y, x, x0, x1, size, brk):
        self.y: float = y  # 初始纵坐标
        self.x: float = x  # 初始横坐标
        self.x0: float = x0  # 左边界
        self.x1: float = x1  # 右边界
        self.size: float = size  # 字体大小
        self.brk: bool = brk  # 换行标记


# fmt: off
class TranslateConverter(PDFConverterEx):
    def __init__(
        self,
        rsrcmgr,
        vfont: str = None,
        vchar: str = None,
        thread: int = 0,
        layout={},
        lang_in: str = "",
        lang_out: str = "",
        service: str = "",
        resfont: str = "",
        noto: Font = None,
        envs: Dict = None,
        prompt: List = None,
        il_creater: ILCreater = None,
    ) -> None:
        super().__init__(rsrcmgr, il_creater)
        self.vfont = vfont
        self.vchar = vchar
        self.thread = thread
        self.layout = layout
        self.resfont = resfont
        self.noto = noto

    def receive_layout(self, ltpage: LTPage):
        # 段落
        sstk: list[str] = []            # 段落文字栈
        pstk: list[Paragraph] = []      # 段落属性栈
        vbkt: int = 0                   # 段落公式括号计数
        # 公式组
        vstk: list[LTChar] = []         # 公式符号组
        vlstk: list[LTLine] = []        # 公式线条组
        vfix: float = 0                 # 公式纵向偏移
        # 公式组栈
        var: list[list[LTChar]] = []    # 公式符号组栈
        varl: list[list[LTLine]] = []   # 公式线条组栈
        varf: list[float] = []          # 公式纵向偏移栈
        vlen: list[float] = []          # 公式宽度栈
        # 全局
        lstk: list[LTLine] = []         # 全局线条栈
        xt: LTChar = None               # 上一个字符
        xt_cls: int = -1                # 上一个字符所属段落，保证无论第一个字符属于哪个类别都可以触发新段落
        vmax: float = ltpage.width / 4  # 行内公式最大宽度
        ops: str = ""                   # 渲染结果

        def vflag(font: str, char: str):    # 匹配公式（和角标）字体
            if isinstance(font, bytes):     # 不一定能 decode，直接转 str
                font = str(font)
            font = font.split("+")[-1]      # 字体名截断
            if re.match(r"\(cid:", char):
                return True
            # 基于字体名规则的判定
            if self.vfont:
                if re.match(self.vfont, font):
                    return True
            else:
                if re.match(                                            # latex 字体
                    r"(CM[^R]|(MS|XY|MT|BL|RM|EU|LA|RS)[A-Z]|LINE|LCIRCLE|TeX-|rsfs|txsy|wasy|stmary|.*Mono|.*Code|.*Ital|.*Sym|.*Math)",
                    font,
                ):
                    return True
            # 基于字符集规则的判定
            if self.vchar:
                if re.match(self.vchar, char):
                    return True
            else:
                if (
                    char
                    and char != " "                                     # 非空格
                    and (
                        unicodedata.category(char[0])
                        in ["Lm", "Mn", "Sk", "Sm", "Zl", "Zp", "Zs"]   # 文字修饰符、数学符号、分隔符号
                        or ord(char[0]) in range(0x370, 0x400)          # 希腊字母
                    )
                ):
                    return True
            return False

        ############################################################
        # A. 原文档解析
        for child in ltpage:
            if isinstance(child, LTChar):
                self.il_creater.on_lt_char(child)
                continue
                cur_v = False
                layout = self.layout[ltpage.pageid]
                # ltpage.height 可能是 fig 里面的高度，这里统一用 layout.shape
                h, w = layout.shape
                # 读取当前字符在 layout 中的类别
                cx, cy = np.clip(int(child.x0), 0, w - 1), np.clip(int(child.y0), 0, h - 1)
                cls = layout[cy, cx]
                # 锚定文档中 bullet 的位置
                if child.get_text() == "•":
                    cls = 0
                # 判定当前字符是否属于公式
                if (                                                                                        # 判定当前字符是否属于公式
                    cls == 0                                                                                # 1. 类别为保留区域
                    or (cls == xt_cls and len(sstk[-1].strip()) > 1 and child.size < pstk[-1].size * 0.79)  # 2. 角标字体，有 0.76 的角标和 0.799 的大写，这里用 0.79 取中，同时考虑首字母放大的情况
                    or vflag(child.fontname, child.get_text())                                              # 3. 公式字体
                    or (child.matrix[0] == 0 and child.matrix[3] == 0)                                      # 4. 垂直字体
                ):
                    cur_v = True
                # 判定括号组是否属于公式
                if not cur_v:
                    if vstk and child.get_text() == "(":
                        cur_v = True
                        vbkt += 1
                    if vbkt and child.get_text() == ")":
                        cur_v = True
                        vbkt -= 1
                if (                                                        # 判定当前公式是否结束
                    not cur_v                                               # 1. 当前字符不属于公式
                    or cls != xt_cls                                        # 2. 当前字符与前一个字符不属于同一段落
                    # or (abs(child.x0 - xt.x0) > vmax and cls != 0)        # 3. 段落内换行，可能是一长串斜体的段落，也可能是段内分式换行，这里设个阈值进行区分
                    # 禁止纯公式（代码）段落换行，直到文字开始再重开文字段落，保证只存在两种情况
                    # A. 纯公式（代码）段落（锚定绝对位置）sstk[-1]=="" -> sstk[-1]=="{v*}"
                    # B. 文字开头段落（排版相对位置）sstk[-1]!=""
                    or (sstk[-1] != "" and abs(child.x0 - xt.x0) > vmax)    # 因为 cls==xt_cls==0 一定有 sstk[-1]==""，所以这里不需要再判定 cls!=0
                ):
                    if vstk:
                        if (                                                # 根据公式右侧的文字修正公式的纵向偏移
                            not cur_v                                       # 1. 当前字符不属于公式
                            and cls == xt_cls                               # 2. 当前字符与前一个字符属于同一段落
                            and child.x0 > max([vch.x0 for vch in vstk])    # 3. 当前字符在公式右侧
                        ):
                            vfix = vstk[0].y0 - child.y0
                        if sstk[-1] == "":
                            xt_cls = -1 # 禁止纯公式段落（sstk[-1]=="{v*}"）的后续连接，但是要考虑新字符和后续字符的连接，所以这里修改的是上个字符的类别
                        sstk[-1] += f"{{v{len(var)}}}"
                        var.append(vstk)
                        varl.append(vlstk)
                        varf.append(vfix)
                        vstk = []
                        vlstk = []
                        vfix = 0
                # 当前字符不属于公式或当前字符是公式的第一个字符
                if not vstk:
                    if cls == xt_cls:               # 当前字符与前一个字符属于同一段落
                        if child.x0 > xt.x1 + 1:    # 添加行内空格
                            sstk[-1] += " "
                        elif child.x1 < xt.x0:      # 添加换行空格并标记原文段落存在换行
                            sstk[-1] += " "
                            pstk[-1].brk = True
                    else:                           # 根据当前字符构建一个新的段落
                        sstk.append("")
                        pstk.append(Paragraph(child.y0, child.x0, child.x0, child.x0, child.size, False))
                if not cur_v:                                               # 文字入栈
                    if (                                                    # 根据当前字符修正段落属性
                        child.size > pstk[-1].size / 0.79                   # 1. 当前字符显著比段落字体大
                        or len(sstk[-1].strip()) == 1                       # 2. 当前字符为段落第二个文字（考虑首字母放大的情况）
                    ) and child.get_text() != " ":                          # 3. 当前字符不是空格
                        pstk[-1].y -= child.size - pstk[-1].size            # 修正段落初始纵坐标，假设两个不同大小字符的上边界对齐
                        pstk[-1].size = child.size
                    sstk[-1] += child.get_text()
                else:                                                       # 公式入栈
                    if (                                                    # 根据公式左侧的文字修正公式的纵向偏移
                        not vstk                                            # 1. 当前字符是公式的第一个字符
                        and cls == xt_cls                                   # 2. 当前字符与前一个字符属于同一段落
                        and child.x0 > xt.x0                                # 3. 前一个字符在公式左侧
                    ):
                        vfix = child.y0 - xt.y0
                    vstk.append(child)
                # 更新段落边界，因为段落内换行之后可能是公式开头，所以要在外边处理
                pstk[-1].x0 = min(pstk[-1].x0, child.x0)
                pstk[-1].x1 = max(pstk[-1].x1, child.x1)
                # 更新上一个字符
                xt = child
                xt_cls = cls
            elif isinstance(child, LTFigure):
                # 图表
                self.il_creater.on_pdf_figure(child)
                pass
            elif isinstance(child, LTLine):     # 线条
                continue
                layout = self.layout[ltpage.pageid]
                # ltpage.height 可能是 fig 里面的高度，这里统一用 layout.shape
                h, w = layout.shape
                # 读取当前线条在 layout 中的类别
                cx, cy = np.clip(int(child.x0), 0, w - 1), np.clip(int(child.y0), 0, h - 1)
                cls = layout[cy, cx]
                if vstk and cls == xt_cls:      # 公式线条
                    vlstk.append(child)
                else:                           # 全局线条
                    lstk.append(child)
            else:
                pass
        return
        # 处理结尾
        if vstk:    # 公式出栈
            sstk[-1] += f"{{v{len(var)}}}"
            var.append(vstk)
            varl.append(vlstk)
            varf.append(vfix)
        log.debug("\n==========[VSTACK]==========\n")
        for id, v in enumerate(var):  # 计算公式宽度
            l = max([vch.x1 for vch in v]) - v[0].x0
            log.debug(f'< {l:.1f} {v[0].x0:.1f} {v[0].y0:.1f} {v[0].cid} {v[0].fontname} {len(varl[id])} > v{id} = {"".join([ch.get_text() for ch in v])}')
            vlen.append(l)

        ############################################################
        # B. 段落翻译
        log.debug("\n==========[SSTACK]==========\n")

        news = sstk.copy()

        ############################################################
        # C. 新文档排版
        def raw_string(fcur: str, cstk: str):  # 编码字符串
            if fcur == 'noto':
                return "".join(["%04x" % self.noto.has_glyph(ord(c)) for c in cstk])
            elif isinstance(self.fontmap[fcur], PDFCIDFont):  # 判断编码长度
                return "".join(["%04x" % ord(c) for c in cstk])
            else:
                return "".join(["%02x" % ord(c) for c in cstk])

        _x, _y = 0, 0
        for id, new in enumerate(news):
            x: float = pstk[id].x           # 段落初始横坐标
            y: float = pstk[id].y           # 段落初始纵坐标
            x0: float = pstk[id].x0         # 段落左边界
            x1: float = pstk[id].x1         # 段落右边界
            size: float = pstk[id].size     # 段落字体大小
            brk: bool = pstk[id].brk        # 段落换行标记
            cstk: str = ""                  # 当前文字栈
            fcur: str = None                # 当前字体 ID
            tx = x
            fcur_ = fcur
            ptr = 0
            log.debug(f"< {y} {x} {x0} {x1} {size} {brk} > {sstk[id]} | {new}")
            while ptr < len(new):
                vy_regex = re.match(
                    r"\{\s*v([\d\s]+)\}", new[ptr:], re.IGNORECASE
                )  # 匹配 {vn} 公式标记
                mod = 0  # 文字修饰符
                if vy_regex:  # 加载公式
                    ptr += len(vy_regex.group(0))
                    try:
                        vid = int(vy_regex.group(1).replace(" ", ""))
                        adv = vlen[vid]
                    except Exception:
                        continue  # 翻译器可能会自动补个越界的公式标记
                    if var[vid][-1].get_text() and unicodedata.category(var[vid][-1].get_text()[0]) in ["Lm", "Mn", "Sk"]:  # 文字修饰符
                        mod = var[vid][-1].width
                else:  # 加载文字
                    ch = new[ptr]
                    fcur_ = None
                    try:
                        if fcur_ is None and self.fontmap["tiro"].to_unichr(ord(ch)) == ch:
                            fcur_ = "tiro"  # 默认拉丁字体
                    except Exception:
                        pass
                    if fcur_ is None:
                        fcur_ = self.resfont  # 默认非拉丁字体
                    if fcur_ == 'noto':
                        adv = self.noto.char_lengths(ch, size)[0]
                    else:
                        adv = self.fontmap[fcur_].char_width(ord(ch)) * size
                    ptr += 1
                if (                                # 输出文字缓冲区
                    fcur_ != fcur                   # 1. 字体更新
                    or vy_regex                     # 2. 插入公式
                    or x + adv > x1 + 0.1 * size    # 3. 到达右边界（可能一整行都被符号化，这里需要考虑浮点误差）
                ):
                    if cstk:
                        ops += f"/{fcur} {size:f} Tf 1 0 0 1 {tx:f} {y:f} Tm [<{raw_string(fcur, cstk)}>] TJ "
                        cstk = ""
                if brk and x + adv > x1 + 0.1 * size:  # 到达右边界且原文段落存在换行
                    x = x0
                    lang_space = {"zh-cn": 1.4, "zh-tw": 1.4, "zh-hans": 1.4, "zh-hant": 1.4, "zh": 1.4, "ja": 1.1, "ko": 1.2, "en": 1.2, "ar": 1.0, "ru": 0.8, "uk": 0.8, "ta": 0.8}
                    # y -= size * lang_space.get(self.translator.lang_out.lower(), 1.1)  # 小语种大多适配 1.1
                    y -= size * 1.4
                if vy_regex:  # 插入公式
                    fix = 0
                    if fcur is not None:  # 段落内公式修正纵向偏移
                        fix = varf[vid]
                    for vch in var[vid]:  # 排版公式字符
                        vc = chr(vch.cid)
                        ops += f"/{self.fontid[vch.font]} {vch.size:f} Tf 1 0 0 1 {x + vch.x0 - var[vid][0].x0:f} {fix + y + vch.y0 - var[vid][0].y0:f} Tm <{raw_string(self.fontid[vch.font], vc)}> TJ "
                        if log.isEnabledFor(logging.DEBUG):
                            lstk.append(LTLine(0.1, (_x, _y), (x + vch.x0 - var[vid][0].x0, fix + y + vch.y0 - var[vid][0].y0)))
                            _x, _y = x + vch.x0 - var[vid][0].x0, fix + y + vch.y0 - var[vid][0].y0
                    for l in varl[vid]:  # 排版公式线条
                        if l.linewidth < 5:  # hack 有的文档会用粗线条当图片背景
                            ops += f"ET q 1 0 0 1 {l.pts[0][0] + x - var[vid][0].x0:f} {l.pts[0][1] + fix + y - var[vid][0].y0:f} cm [] 0 d 0 J {l.linewidth:f} w 0 0 m {l.pts[1][0] - l.pts[0][0]:f} {l.pts[1][1] - l.pts[0][1]:f} l S Q BT "
                else:  # 插入文字缓冲区
                    if not cstk:  # 单行开头
                        tx = x
                        if x == x0 and ch == " ":  # 消除段落换行空格
                            adv = 0
                        else:
                            cstk += ch
                    else:
                        cstk += ch
                adv -= mod # 文字修饰符
                fcur = fcur_
                x += adv
                if log.isEnabledFor(logging.DEBUG):
                    lstk.append(LTLine(0.1, (_x, _y), (x, y)))
                    _x, _y = x, y
            # 处理结尾
            if cstk:
                ops += f"/{fcur} {size:f} Tf 1 0 0 1 {tx:f} {y:f} Tm <{raw_string(fcur, cstk)}> TJ "
        for l in lstk:  # 排版全局线条
            if l.linewidth < 5:  # hack 有的文档会用粗线条当图片背景
                ops += f"ET q 1 0 0 1 {l.pts[0][0]:f} {l.pts[0][1]:f} cm [] 0 d 0 J {l.linewidth:f} w 0 0 m {l.pts[1][0] - l.pts[0][0]:f} {l.pts[1][1] - l.pts[0][1]:f} l S Q BT "
        ops = f"BT {ops}ET "
        return ops

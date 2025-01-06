from yadt.il_try_1.document_il.il_try_1 import (
    Box,
    Page,
    PdfCharacter,
    PdfParagraph,
    PdfLine,
    GraphicState,
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

        # 获取两个字符的中心y坐标
        prev_y = (prev_char.box.y + prev_char.box.y2) / 2
        curr_y = (curr_char.box.y + curr_char.box.y2) / 2

        # 如果当前字符的y坐标明显低于前一个字符，说明换行了
        # 这里使用字符高度的一半作为阈值
        char_height = curr_char.box.y2 - curr_char.box.y
        return curr_y < prev_y - char_height / 2


class ParagraphFinder:
    def process(self, document):
        for page in document.page:
            self.process_page(page)

    def process_page(self, page: Page):
        # 第一步：根据layout创建paragraphs
        # 在这一步中，page.pdf_character中的字符会被移除
        paragraphs = self.create_paragraphs(page)
        page.pdf_paragraph = paragraphs

        # 第二步：处理段落中的空格和换行符
        for paragraph in paragraphs:
            self.process_paragraph_spacing(paragraph)
            self.update_paragraph_data(paragraph)

    def create_paragraphs(self, page: Page) -> list[PdfParagraph]:
        paragraphs: list[PdfParagraph] = []
        current_paragraph: PdfParagraph | None = None
        current_layout: Layout | None = None
        current_line_chars: list[PdfCharacter] = []
        chars = page.pdf_character.copy()

        for char in chars:
            char_layout = self.get_layout(char, page)
            if not self.is_text_layout(char_layout):
                continue

            page.pdf_character.remove(char)
            
            # 检查是否需要开始新行
            if current_line_chars and Layout.is_newline(current_line_chars[-1], char):
                # 创建新行
                if current_line_chars:
                    line = self.create_line(current_line_chars)
                    if current_paragraph is None:
                        current_paragraph = PdfParagraph(
                            box=line.box,
                            graphic_state=line.graphic_state,
                            pdf_line=[line],
                            unicode=line.unicode,
                            advance=line.advance,
                            size=line.size
                        )
                        paragraphs.append(current_paragraph)
                    else:
                        current_paragraph.pdf_line.append(line)
                        self.update_paragraph_data(current_paragraph)
                current_line_chars = []

            # 检查是否需要开始新段落
            if current_layout is None or char_layout.id != current_layout.id:
                if current_line_chars:
                    line = self.create_line(current_line_chars)
                    if current_paragraph is not None:
                        current_paragraph.pdf_line.append(line)
                        self.update_paragraph_data(current_paragraph)
                    current_line_chars = []
                current_paragraph = None
                current_layout = char_layout

            current_line_chars.append(char)

        # 处理最后一行的字符
        if current_line_chars:
            line = self.create_line(current_line_chars)
            if current_paragraph is None:
                current_paragraph = PdfParagraph(
                    box=line.box,
                    graphic_state=line.graphic_state,
                    pdf_line=[line],
                    unicode=line.unicode,
                    advance=line.advance,
                    size=line.size
                )
                paragraphs.append(current_paragraph)
            else:
                current_paragraph.pdf_line.append(line)
                self.update_paragraph_data(current_paragraph)

        return paragraphs

    def process_paragraph_spacing(self, paragraph: PdfParagraph):
        if not paragraph.pdf_line:
            return

        # 处理行级别的空格
        processed_lines = []
        for line in paragraph.pdf_line:
            if not line.unicode.strip():  # 跳过完全空白的行
                continue
                
            # 处理行内字符的尾随空格
            processed_chars = []
            for char in line.pdf_character:
                if not char.char_unicode.isspace():
                    processed_chars = processed_chars + [char]
                elif processed_chars:  # 只有在有非空格字符后才考虑保留空格
                    processed_chars.append(char)
            
            # 移除尾随空格
            while processed_chars and processed_chars[-1].char_unicode.isspace():
                processed_chars.pop()
                
            if processed_chars:  # 如果行内还有字符
                line.pdf_character = processed_chars
                line.unicode = "".join(char.char_unicode for char in processed_chars)
                processed_lines.append(line)
        
        paragraph.pdf_line = processed_lines

    def update_paragraph_data(self, paragraph: PdfParagraph):
        if not paragraph.pdf_line:
            return
            
        # 更新unicode（合并所有行的文本）
        paragraph.unicode = " ".join(line.unicode for line in paragraph.pdf_line)
        
        # 更新边界框
        min_x = min(line.box.x for line in paragraph.pdf_line)
        min_y = min(line.box.y for line in paragraph.pdf_line)
        max_x = max(line.box.x2 for line in paragraph.pdf_line)
        max_y = max(line.box.y2 for line in paragraph.pdf_line)
        paragraph.box = Box(min_x, min_y, max_x, max_y)
        
        # 更新advance和size
        paragraph.advance = max_x - min_x
        paragraph.size = max_y - min_y

    def is_text_layout(self, layout: Layout):
        return layout is not None and layout.name in [
            "plain text",
            "title",
            "abandon",
        ]

    def get_layout(
        self,
        char: PdfCharacter,
        page: Page,
    ):
        # current layouts
        # {
        #     "title",
        #     "plain text",
        #     "abandon",
        #     "figure",
        #     "figure_caption",
        #     "table",
        #     "table_caption",
        #     "table_footnote",
        #     "isolate_formula",
        #     "formula_caption",
        # }
        layout_priority = [
            "formula_caption",
            "isolate_formula",
            "table_footnote",
            "table_caption",
            "table",
            "figure_caption",
            "figure",
            "abandon",
            "plain text",
            "title",
        ]
        char_box = char.box
        char_x = (char_box.x + char_box.x2) / 2
        char_y = (char_box.y + char_box.y2) / 2

        # 按照优先级顺序检查每种布局
        matching_layouts = {}
        for layout in page.page_layout:
            layout_box = layout.box
            if (
                layout_box.x <= char_x <= layout_box.x2
                and layout_box.y <= char_y <= layout_box.y2
            ):
                matching_layouts[layout.class_name] = Layout(
                    layout.id, layout.class_name
                )

        # 按照优先级返回最高优先级的布局
        for layout_name in layout_priority:
            if layout_name in matching_layouts:
                return matching_layouts[layout_name]

        return None

    def create_line(self, chars: list[PdfCharacter]) -> PdfLine:
        if not chars:
            return None
        
        # 计算行的边界框
        min_x = min(char.box.x for char in chars)
        min_y = min(char.box.y for char in chars)
        max_x = max(char.box.x2 for char in chars)
        max_y = max(char.box.y2 for char in chars)
        box = Box(min_x, min_y, max_x, max_y)
        
        # 使用第一个字符的图形状态作为行的图形状态
        graphic_state = chars[0].graphic_state
        
        # 创建行对象
        line = PdfLine(
            box=box,
            graphic_state=graphic_state,
            pdf_character=chars,
            unicode="".join(char.char_unicode for char in chars),
            advance=max_x - min_x,  # 使用行的宽度作为advance
            size=max_y - min_y,     # 使用行的高度作为size
        )
        return line

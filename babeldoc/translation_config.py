import contextlib
import shutil
import tempfile
import threading
from pathlib import Path

from babeldoc.const import CACHE_FOLDER
from babeldoc.docvision.doclayout import DocLayoutModel
from babeldoc.progress_monitor import ProgressMonitor


class TranslationConfig:
    def __init__(
        self,
        input_file: str,
        translator,
        lang_in: str,
        lang_out: str,
        font: str | None = None,
        pages: str | None = None,
        output_dir: str | None = None,
        debug: bool = False,
        working_dir: str | None = None,
        no_dual: bool = False,
        no_mono: bool = False,
        formular_font_pattern: str | None = None,
        formular_char_pattern: str | None = None,
        qps: int = 1,
        split_short_lines: bool = True,  # 是否将比较短的行强制切分成不同段落，此功能可能会导致糟糕的排版&bug
        short_line_split_factor: float = 0.8,  # 切分阈值系数。实际阈值为当前页所有行长度中位数*此系数
        use_rich_pbar: bool = True,  # 是否使用 rich 进度条
        progress_monitor: ProgressMonitor | None = None,  # progress_monitor
        doc_layout_model=None,
        skip_clean: bool = False,
        dual_translate_first: bool = False,
        disable_rich_text_translate: bool = False,  # 是否禁用富文本翻译
        enhance_compatibility: bool = False,  # 增强兼容性模式
        report_interval: float = 0.1,  # Progress report interval in seconds
        min_text_length: int = 5,  # Minimum text length to translate
    ):
        self.input_file = input_file
        self.translator = translator
        self.font = font
        self.pages = pages
        self.page_ranges = self._parse_pages(pages) if pages else None
        self.debug = debug
        self.lang_in = lang_in
        self.lang_out = lang_out
        self.no_dual = no_dual
        self.no_mono = no_mono
        self.formular_font_pattern = formular_font_pattern
        self.formular_char_pattern = formular_char_pattern
        self.qps = qps
        self.split_short_lines = split_short_lines
        self.short_line_split_factor = short_line_split_factor
        self.use_rich_pbar = use_rich_pbar
        self.progress_monitor = progress_monitor
        self.skip_clean = skip_clean or enhance_compatibility
        self.dual_translate_first = dual_translate_first or enhance_compatibility
        self.disable_rich_text_translate = (
            disable_rich_text_translate or enhance_compatibility
        )
        self.report_interval = report_interval
        self.min_text_length = min_text_length
        if progress_monitor:
            if progress_monitor.cancel_event is None:
                progress_monitor.cancel_event = threading.Event()
            if progress_monitor.finish_event is None:
                progress_monitor.finish_event = threading.Event()

        if working_dir is None:
            if debug:
                working_dir = (
                    Path(CACHE_FOLDER) / "working" / Path(input_file).name.split(".")[0]
                )
            else:
                working_dir = tempfile.mkdtemp()
        self.working_dir = working_dir
        self._is_temp_dir = not debug and working_dir.startswith(tempfile.gettempdir())

        Path(working_dir).mkdir(parents=True, exist_ok=True)

        if output_dir is None:
            # output_dir = os.path.dirname(input_file)
            output_dir = Path.cwd()
        self.output_dir = output_dir

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        if doc_layout_model is None:
            doc_layout_model = DocLayoutModel.load_available()
        self.doc_layout_model = doc_layout_model

    def get_output_file_path(self, filename):
        return Path(self.output_dir) / filename

    def get_working_file_path(self, filename):
        return Path(self.working_dir) / filename

    def _parse_pages(self, pages_str: str | None) -> list[tuple[int, int]] | None:
        """解析页码字符串，返回页码范围列表

        Args:
            pages_str: 形如 "1-,2,-3,4" 的页码字符串

        Returns:
            包含 (start, end) 元组的列表，其中 -1 表示无限制
        """
        if not pages_str:
            return None

        ranges = []
        for part in pages_str.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-")
                start = int(start) if start else 1
                end = int(end) if end else -1
                ranges.append((start, end))
            else:
                page = int(part)
                ranges.append((page, page))
        return ranges

    def should_translate_page(self, page_number: int) -> bool:
        """判断指定页码是否需要翻译

        Args:
            page_number: 页码

        Returns:
            是否需要翻译该页
        """
        if not self.page_ranges:
            return True

        for start, end in self.page_ranges:
            if start <= page_number and (end == -1 or page_number <= end):
                return True
        return False

    def raise_if_cancelled(self):
        if self.progress_monitor:
            self.progress_monitor.raise_if_cancelled()

    def cancel_translation(self):
        if self.progress_monitor:
            self.progress_monitor.cancel()

    def __del__(self):
        """Clean up temporary directory if it was created."""
        if hasattr(self, "_is_temp_dir") and self._is_temp_dir:
            with contextlib.suppress(Exception):
                shutil.rmtree(self.working_dir, ignore_errors=True)


class TranslateResult:
    original_pdf_path: str
    total_seconds: float
    mono_pdf_path: str | None
    dual_pdf_path: str | None

    def __init__(self, mono_pdf_path: str | None, dual_pdf_path: str | None):
        self.mono_pdf_path = mono_pdf_path
        self.dual_pdf_path = dual_pdf_path

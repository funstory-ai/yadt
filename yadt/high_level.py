import asyncio
import hashlib
import logging
import os
import threading
import time
from asyncio import CancelledError
from typing import Any, BinaryIO, Optional

import httpx
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pymupdf import Document, Font

from yadt import asynchronize
from yadt.const import CACHE_FOLDER, get_cache_file_path
from yadt.converter import TranslateConverter
from yadt.document_il.backend.pdf_creater import PDFCreater
from yadt.document_il.frontend.il_creater import ILCreater
from yadt.document_il.midend.add_debug_information import AddDebugInformation
from yadt.document_il.midend.il_translator import ILTranslator
from yadt.document_il.midend.layout_parser import LayoutParser
from yadt.document_il.midend.paragraph_finder import ParagraphFinder
from yadt.document_il.midend.remove_descent import RemoveDescent
from yadt.document_il.midend.styles_and_formulas import StylesAndFormulas
from yadt.document_il.midend.typesetting import Typesetting
from yadt.document_il.utils.fontmap import FontMapper
from yadt.document_il.xml_converter import XMLConverter
from yadt.pdfinterp import PDFPageInterpreterEx
from yadt.progress_monitor import ProgressMonitor
from yadt.translation_config import TranslateResult, TranslationConfig

logger = logging.getLogger(__name__)

TRANSLATE_STAGES = [
    ILCreater.stage_name,
    LayoutParser.stage_name,
    ParagraphFinder.stage_name,
    StylesAndFormulas.stage_name,
    RemoveDescent.stage_name,
    ILTranslator.stage_name,
    Typesetting.stage_name,
    FontMapper.stage_name,
    PDFCreater.stage_name,
]

resfont_map = {
    "zh-cn": "china-ss",
    "zh-tw": "china-ts",
    "zh-hans": "china-ss",
    "zh-hant": "china-ts",
    "zh": "china-ss",
    "ja": "japan-s",
    "ko": "korea-s",
}

FONT_ASSETS = [
    (
        "noto.ttf",
        "https://github.com/satbyy/go-noto-universal"
        "/releases/download/v7.0/GoNotoKurrent-Regular.ttf",
        "2f2cee5fbb2403df352ca2005247f6c4faa70f3086ebd31b6c62308b5f2f9865",
    ),
    (
        "source-han-serif-cn.ttf",
        "https://github.com/junmer/source-han-serif-ttf/"
        "raw/refs/heads/master/SubsetTTF/CN/SourceHanSerifCN-Regular.ttf",
        "1e60cc2eedfa25bf5e4ecaa794402f581ad770d4c8be46d338bf52064b307ec7",
    ),
    (
        "source-han-serif-cn-bold.ttf",
        "https://github.com/junmer/source-han-serif-ttf/"
        "raw/refs/heads/master/SubsetTTF/CN/SourceHanSerifCN-Bold.ttf",
        "84c24723a47537fcf5057b788a51c41978ee6173931f19b8a9f5a4595b677dc9",
    ),
    (
        "SourceHanSansSC-Regular.ttf",
        "https://github.com/iizyd/SourceHanSansCN-TTF-Min/"
        "raw/refs/heads/main/source-file/ttf/SourceHanSansSC-Regular.ttf",
        "a878f16eed162dc5b211d888a4a29b1730b73f4cf632e720abca4eab7bd8a152",
    ),
    (
        "SourceHanSansSC-Bold.ttf",
        "https://github.com/iizyd/SourceHanSansCN-TTF-Min/"
        "raw/refs/heads/main/source-file/ttf/SourceHanSansSC-Bold.ttf",
        "485b27eb4f3603223e9c3c5ebfa317aee77772ea8f642f9330df7f030c8b7b43",
    ),
    (
        "LXGWWenKai-Regular.ttf",
        "https://github.com/lxgw/LxgwWenKai/"
        "raw/refs/heads/main/fonts/TTF/LXGWWenKai-Regular.ttf",
        "ea47ec17d0f3d0ed1e6d9c51d6146402d4d1e2f0ff397a90765aaaa0ddd382fb",
    ),
]


def verify_file_hash(file_path: str, expected_hash: str) -> bool:
    """Verify the SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        # Read the file in chunks to handle large files efficiently
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest() == expected_hash


def start_parse_il(
    inf: BinaryIO,
    pages: Optional[list[int]] = None,
    vfont: str = "",
    vchar: str = "",
    thread: int = 0,
    doc_zh: Document = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    resfont: str = "",
    noto: Font = None,
    cancellation_event: asyncio.Event = None,
    il_creater: ILCreater = None,
    translation_config: TranslationConfig = None,
    **kwarg: Any,
) -> None:
    rsrcmgr = PDFResourceManager()
    layout = {}
    device = TranslateConverter(
        rsrcmgr,
        vfont,
        vchar,
        thread,
        layout,
        lang_in,
        lang_out,
        service,
        resfont,
        noto,
        kwarg.get("envs", {}),
        kwarg.get("prompt", []),
        il_creater=il_creater,
    )
    # model = DocLayoutModel.load_available()

    assert device is not None
    assert il_creater is not None
    assert translation_config is not None
    obj_patch = {}
    interpreter = PDFPageInterpreterEx(rsrcmgr, device, obj_patch, il_creater)
    if pages:
        total_pages = len(pages)
    else:
        total_pages = doc_zh.page_count

    il_creater.on_total_pages(total_pages)

    parser = PDFParser(inf)
    doc = PDFDocument(parser)

    for pageno, page in enumerate(PDFPage.create_pages(doc)):
        if cancellation_event and cancellation_event.is_set():
            raise CancelledError("task cancelled")
        if pages and (pageno not in pages):
            continue
        page.pageno = pageno
        if not translation_config.should_translate_page(pageno + 1):
            continue
        translation_config.raise_if_cancelled()
        # The current program no longer relies on
        # the following layout recognition results,
        # but in order to facilitate the migration of pdf2zh,
        # the relevant code is temporarily retained.
        # pix = doc_zh[page.pageno].get_pixmap()
        # image = np.fromstring(pix.samples, np.uint8).reshape(
        #     pix.height, pix.width, 3
        # )[:, :, ::-1]
        # page_layout = model.predict(
        #     image, imgsz=int(pix.height / 32) * 32)[0]
        # # kdtree 是不可能 kdtree 的，不如直接渲染成图片，用空间换时间
        # box = np.ones((pix.height, pix.width))
        # h, w = box.shape
        # vcls = ["abandon", "figure", "table",
        #         "isolate_formula", "formula_caption"]
        # for i, d in enumerate(page_layout.boxes):
        #     if page_layout.names[int(d.cls)] not in vcls:
        #         x0, y0, x1, y1 = d.xyxy.squeeze()
        #         x0, y0, x1, y1 = (
        #             np.clip(int(x0 - 1), 0, w - 1),
        #             np.clip(int(h - y1 - 1), 0, h - 1),
        #             np.clip(int(x1 + 1), 0, w - 1),
        #             np.clip(int(h - y0 + 1), 0, h - 1),
        #         )
        #         box[y0:y1, x0:x1] = i + 2
        # for i, d in enumerate(page_layout.boxes):
        #     if page_layout.names[int(d.cls)] in vcls:
        #         x0, y0, x1, y1 = d.xyxy.squeeze()
        #         x0, y0, x1, y1 = (
        #             np.clip(int(x0 - 1), 0, w - 1),
        #             np.clip(int(h - y1 - 1), 0, h - 1),
        #             np.clip(int(x1 + 1), 0, w - 1),
        #             np.clip(int(h - y0 + 1), 0, h - 1),
        #         )
        #         box[y0:y1, x0:x1] = 0
        # layout[page.pageno] = box
        # 新建一个 xref 存放新指令流
        page.page_xref = doc_zh.get_new_xref()  # hack 插入页面的新 xref
        doc_zh.update_object(page.page_xref, "<<>>")
        doc_zh.update_stream(page.page_xref, b"")
        doc_zh[page.pageno].set_contents(page.page_xref)
        ops_base = interpreter.process_page(page)
        il_creater.on_page_base_operation(ops_base)
        il_creater.on_page_end()
    il_creater.on_finish()
    device.close()


def translate(translation_config: TranslationConfig) -> TranslateResult:
    with ProgressMonitor(
        translation_config,
        TRANSLATE_STAGES,
    ) as pm:
        return do_translate(pm, translation_config)


async def async_translate(translation_config: TranslationConfig):
    """Asynchronously translate a PDF file with real-time progress reporting.

    This function yields progress events that can be used to update progress bars
    or other UI elements. The events are dictionaries with the following structure:

    - progress_start: {
        "type": "progress_start",
        "stage": str,              # Stage name
        "stage_progress": float,   # Always 0.0
        "stage_current": int,      # Current count (0)
        "stage_total": int         # Total items in stage
    }
    - progress_update: {
        "type": "progress_update",
        "stage": str,              # Stage name
        "stage_progress": float,   # Stage progress (0-100)
        "stage_current": int,      # Current items processed
        "stage_total": int,        # Total items in stage
        "overall_progress": float  # Overall progress (0-100)
    }
    - progress_end: {
        "type": "progress_end",
        "stage": str,              # Stage name
        "stage_progress": float,   # Always 100.0
        "stage_current": int,      # Equal to stage_total
        "stage_total": int,        # Total items processed
        "overall_progress": float  # Overall progress (0-100)
    }
    - finish: {
        "type": "finish",
        "translate_result": TranslateResult
    }
    - error: {
        "type": "error",
        "error": str
    }

    Args:
        translation_config: Configuration for the translation process

    Yields:
        dict: Progress events during translation

    Raises:
        CancelledError: If the translation is cancelled
        Exception: Any other errors during translation
    """
    loop = asyncio.get_running_loop()
    callback = asynchronize.AsyncCallback()

    finish_event = asyncio.Event()
    cancel_event = threading.Event()
    with ProgressMonitor(
        translation_config,
        TRANSLATE_STAGES,
        progress_change_callback=callback.step_callback,
        finish_callback=callback.finished_callback,
        finish_event=finish_event,
        cancel_event=cancel_event,
        loop=loop,
    ) as pm:
        future = loop.run_in_executor(None, do_translate, pm, translation_config)
        try:
            async for event in callback:
                event = event.kwargs
                yield event
                if event["type"] == "error":
                    break
        except CancelledError:
            cancel_event.set()
        except KeyboardInterrupt:
            logger.info("Translation cancelled by user through keyboard interrupt")
            cancel_event.set()
    if cancel_event.is_set():
        future.cancel()
    logger.info("Waiting for translation to finish...")
    await finish_event.wait()


def do_translate(pm, translation_config):
    try:
        translation_config.progress_monitor = pm
        original_pdf_path = translation_config.input_file
        logger.info(f"start to translate: {original_pdf_path}")
        start_time = time.time()
        doc_input = Document(original_pdf_path)
        if translation_config.debug:
            logger.debug("debug mode, save decompressed input pdf")
            output_path = translation_config.get_working_file_path(
                "input.decompressed.pdf"
            )
            doc_input.save(output_path, expand=True, pretty=True)
        # Continue with original processing
        temp_pdf_path = translation_config.get_working_file_path("input.pdf")
        doc_pdf2zh = Document(original_pdf_path)
        resfont = "china-ss"
        for page in doc_pdf2zh:
            page.insert_font(resfont, None)
        doc_pdf2zh.save(temp_pdf_path)
        il_creater = ILCreater(translation_config)
        il_creater.mupdf = doc_input
        xml_converter = XMLConverter()
        logger.debug(f"start parse il from {temp_pdf_path}")
        with open(temp_pdf_path, "rb") as f:
            start_parse_il(
                f,
                doc_zh=doc_pdf2zh,
                resfont=resfont,
                il_creater=il_creater,
                translation_config=translation_config,
            )
        logger.debug(f"finish parse il from {temp_pdf_path}")
        docs = il_creater.create_il()
        logger.debug(f"finish create il from {temp_pdf_path}")
        if translation_config.debug:
            xml_converter.write_json(
                docs, translation_config.get_working_file_path("create_il.debug.json")
            )
        # Generate layouts for all pages
        logger.debug("start generating layouts")
        docs = LayoutParser(translation_config).process(docs, doc_input)
        logger.debug("finish generating layouts")
        if translation_config.debug:
            xml_converter.write_json(
                docs, translation_config.get_working_file_path("layout_generator.json")
            )
        ParagraphFinder(translation_config).process(docs)
        logger.debug(f"finish paragraph finder from {temp_pdf_path}")
        if translation_config.debug:
            xml_converter.write_json(
                docs, translation_config.get_working_file_path("paragraph_finder.json")
            )
        StylesAndFormulas(translation_config).process(docs)
        logger.debug(f"finish styles and formulas from {temp_pdf_path}")
        if translation_config.debug:
            xml_converter.write_json(
                docs,
                translation_config.get_working_file_path("styles_and_formulas.json"),
            )
        RemoveDescent(translation_config).process(docs)
        logger.debug(f"finish remove descent from {temp_pdf_path}")
        if translation_config.debug:
            xml_converter.write_json(
                docs,
                translation_config.get_working_file_path("remove_descent.json"),
            )
        translate_engine = translation_config.translator
        ILTranslator(translate_engine, translation_config).translate(docs)
        logger.debug(f"finish ILTranslator from {temp_pdf_path}")
        if translation_config.debug:
            xml_converter.write_json(
                docs, translation_config.get_working_file_path("il_translated.json")
            )

        if translation_config.debug:
            AddDebugInformation(translation_config).process(docs)
            xml_converter.write_json(
                docs,
                translation_config.get_working_file_path("add_debug_information.json"),
            )

        Typesetting(translation_config).typsetting_document(docs)
        logger.debug(f"finish typsetting from {temp_pdf_path}")
        if translation_config.debug:
            xml_converter.write_json(
                docs, translation_config.get_working_file_path("typsetting.json")
            )
        # deepcopy
        # docs2 = xml_converter.deepcopy(docs)
        pdf_creater = PDFCreater(temp_pdf_path, docs, translation_config)
        result = pdf_creater.write(translation_config)
        finish_time = time.time()
        result.original_pdf_path = original_pdf_path
        result.total_seconds = finish_time - start_time
        logger.info(
            f"finish translate: {original_pdf_path}, cost: {finish_time - start_time} s"
        )
        pm.translate_done(result)
        return result
    except Exception as e:
        logger.exception(f"translate error: {e}")
        pm.translate_error(e)
        raise
    finally:
        logger.debug("do_translate finally")
        pm.on_finish()


def download_font_assets():
    """Download and verify font assets."""
    for name, url, expected_hash in FONT_ASSETS:
        save_path = get_cache_file_path(name)

        # Check if file exists and has correct hash
        if os.path.exists(save_path):
            if verify_file_hash(save_path, expected_hash):
                continue
            else:
                logger.warning(f"Hash mismatch for {name}, re-downloading...")
                os.remove(save_path)

        # Download file
        r = httpx.get(url, follow_redirects=True)
        if not r.is_success:
            logger.critical("cannot download %s font", name, exc_info=True)
            exit(1)

        # Save and verify
        with open(save_path, "wb") as f:
            f.write(r.content)

        if not verify_file_hash(save_path, expected_hash):
            logger.critical(f"Downloaded file {name} has incorrect hash!")
            os.remove(save_path)
            exit(1)

        logger.info(f"Successfully downloaded and verified {name}")


def create_cache_folder():
    try:
        logger.debug(f"create cache folder at {CACHE_FOLDER}")
        os.makedirs(CACHE_FOLDER, exist_ok=True)
    except OSError:
        logger.critical(
            f"Failed to create cache folder at {CACHE_FOLDER}", exc_info=True
        )
        exit(1)


def init():
    create_cache_folder()
    download_font_assets()

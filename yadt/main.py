import asyncio
import logging
import os

import configargparse
import tqdm
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

import yadt.high_level
from yadt.const import get_cache_file_path
from yadt.document_il.translator.translator import (
    BingTranslator,
    GoogleTranslator,
    OpenAITranslator,
    set_translate_rate_limiter,
)
from yadt.translation_config import TranslationConfig  # noqa: E402

logger = logging.getLogger(__name__)
__version__ = "0.1.5"


def create_parser():
    parser = configargparse.ArgParser(
        config_file_parser_class=configargparse.TomlConfigParser(["yadt"]),
    )
    parser.add_argument(
        "-c",
        "--my-config",
        required=False,
        is_config_file=True,
        help="config file path",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--files",
        type=str,
        # nargs="*",
        action="append",
        help="One or more paths to PDF files.",
    )
    parser.add_argument(
        "--debug",
        "-d",
        default=False,
        action="store_true",
        help="Use debug logging level.",
    )
    parser.add_argument(
        "--warmup",
        default=False,
        action="store_true",
        help="Only download and verify required assets then exit.",
    )
    parser.add_argument(
        "--rpc-doclayout",
        default=None,
        help="RPC service host address for document layout analysis",
        type=str,
    )
    translation_params = parser.add_argument_group(
        "Translation",
        description="Used during translation",
    )
    translation_params.add_argument(
        "--pages",
        "-p",
        type=str,
        help="Pages to translate. If not set, translate all pages. like: 1,2,1-,-3,3-5",
    )
    translation_params.add_argument(
        "--lang-in",
        "-li",
        type=str,
        default="en",
        help="The code of source language.",
    )
    translation_params.add_argument(
        "--lang-out",
        "-lo",
        type=str,
        default="zh",
        help="The code of target language.",
    )
    translation_params.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output directory for files. if not set, use same as input.",
    )
    translation_params.add_argument(
        "--qps",
        "-q",
        type=int,
        default=4,
        help="QPS limit of translation service",
    )
    translation_params.add_argument(
        "--ignore-cache",
        "-ic",
        default=False,
        action="store_true",
        help="Ignore translation cache.",
    )
    translation_params.add_argument(
        "--no-dual",
        default=False,
        action="store_true",
        help="Do not output bilingual PDF files",
    )
    translation_params.add_argument(
        "--no-mono",
        default=False,
        action="store_true",
        help="Do not output monolingual PDF files",
    )
    translation_params.add_argument(
        "--formular-font-pattern",
        type=str,
        default=None,
        help="Font pattern to identify formula text",
    )
    translation_params.add_argument(
        "--formular-char-pattern",
        type=str,
        default=None,
        help="Character pattern to identify formula text",
    )
    translation_params.add_argument(
        "--split-short-lines",
        default=False,
        action="store_true",
        help="Force split short lines into different paragraphs (may cause poor typesetting & bugs)",
    )
    translation_params.add_argument(
        "--short-line-split-factor",
        type=float,
        default=0.8,
        help="Split threshold factor. The actual threshold is the median length of all lines on the current page * this factor",
    )
    translation_params.add_argument(
        "--skip-clean",
        default=False,
        action="store_true",
        help="Skip PDF cleaning step",
    )
    translation_params.add_argument(
        "--dual-translate-first",
        default=False,
        action="store_true",
        help="Put translated pages first in dual PDF mode",
    )
    service_params = translation_params.add_mutually_exclusive_group()
    service_params.add_argument(
        "--openai",
        default=False,
        action="store_true",
        help="Use OpenAI translator.",
    )
    service_params.add_argument(
        "--google",
        default=False,
        action="store_true",
        help="Use Google translator.",
    )
    service_params.add_argument(
        "--bing",
        default=False,
        action="store_true",
        help="Use Bing translator.",
    )
    openai_params = parser.add_argument_group(
        "Translation - OpenAI Options", description="OpenAI specific options"
    )
    openai_params.add_argument(
        "--openai-model",
        "-m",
        type=str,
        default="gpt-4o-mini",
        help="The OpenAI model to use for translation.",
    )
    openai_params.add_argument(
        "--openai-base-url",
        "-b",
        type=str,
        default=None,
        help="The base URL for the OpenAI API.",
    )
    openai_params.add_argument(
        "--openai-api-key",
        "-k",
        type=str,
        default=None,
        help="The API key for the OpenAI API.",
    )

    return parser


def create_progress_handler(translation_config: TranslationConfig):
    """Create a progress handler function based on the configuration.

    Args:
        translation_config: The translation configuration.

    Returns:
        A tuple of (progress_context, progress_handler), where progress_context is a context
        manager that should be used to wrap the translation process, and progress_handler
        is a function that will be called with progress events.
    """
    if translation_config.use_rich_pbar:
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        )
        translate_task_id = progress.add_task("translate", total=100)
        stage_tasks = {}

        def progress_handler(event):
            if event["type"] == "progress_start":
                stage_tasks[event["stage"]] = progress.add_task(
                    f"{event['stage']}", total=event.get("stage_total", 100)
                )
            elif event["type"] == "progress_update":
                stage = event["stage"]
                if stage in stage_tasks:
                    progress.update(
                        stage_tasks[stage],
                        completed=event["stage_current"],
                        total=event["stage_total"],
                        description=f"{event['stage']} ({event['stage_current']}/{event['stage_total']})",
                        refresh=True,
                    )
                progress.update(
                    translate_task_id, completed=event["overall_progress"], refresh=True
                )
            elif event["type"] == "progress_end":
                stage = event["stage"]
                if stage in stage_tasks:
                    progress.update(
                        stage_tasks[stage],
                        completed=event["stage_total"],
                        total=event["stage_total"],
                        description=f"{event['stage']} (Complete)",
                        refresh=True,
                    )
                    progress.update(
                        translate_task_id,
                        completed=event["overall_progress"],
                        refresh=True,
                    )
                progress.refresh()

        return progress, progress_handler
    else:
        pbar = tqdm.tqdm(total=100, desc="translate")

        def progress_handler(event):
            if event["type"] == "progress_update":
                pbar.update(event["overall_progress"] - pbar.n)
                pbar.set_description(
                    f"{event['stage']} ({event['stage_current']}/{event['stage_total']})"
                )
            elif event["type"] == "progress_end":
                pbar.set_description(f"{event['stage']} (Complete)")
                pbar.refresh()

        return pbar, progress_handler


async def main():
    parser = create_parser()
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.warmup:
        logger.info("Warmup completed, exiting...")
        return

    # 验证翻译服务选择
    if not (args.openai or args.google or args.bing):
        parser.error("必须选择一个翻译服务：--openai、--google 或 --bing")

    # 验证 OpenAI 参数
    if args.openai and not args.openai_api_key:
        parser.error("使用 OpenAI 服务时必须提供 API key")

    # 实例化翻译器
    if args.openai:
        translator = OpenAITranslator(
            lang_in=args.lang_in,
            lang_out=args.lang_out,
            model=args.openai_model,
            base_url=args.openai_base_url,
            api_key=args.openai_api_key,
            ignore_cache=args.ignore_cache,
        )
    elif args.bing:
        translator = BingTranslator(
            lang_in=args.lang_in,
            lang_out=args.lang_out,
            ignore_cache=args.ignore_cache,
        )
    else:
        translator = GoogleTranslator(
            lang_in=args.lang_in,
            lang_out=args.lang_out,
            ignore_cache=args.ignore_cache,
        )

    # 设置翻译速率限制
    set_translate_rate_limiter(args.qps)

    # 初始化文档布局模型
    if args.rpc_doclayout:
        from yadt.docvision.rpc_doclayout import RpcDocLayoutModel

        doc_layout_model = RpcDocLayoutModel(host=args.rpc_doclayout)
    else:
        from yadt.docvision.doclayout import DocLayoutModel

        doc_layout_model = DocLayoutModel.load_onnx()

    pending_files = []
    for file in args.files:
        # 清理文件路径，去除两端的引号
        if file.startswith("--files="):
            file = file.lstrip("--files=")
        file = file.lstrip("-").strip("\"'")
        if not os.path.exists(file):
            logger.error(f"文件不存在：{file}")
            exit(1)
        if not file.endswith(".pdf"):
            logger.error(f"文件不是 PDF 文件：{file}")
            exit(1)
        pending_files.append(file)

    font_path = get_cache_file_path("source-han-serif-cn.ttf")

    # 验证字体
    if font_path:
        if not os.path.exists(font_path):
            logger.error(f"字体文件不存在：{font_path}")
            exit(1)
        if not font_path.endswith(".ttf"):
            logger.error(f"字体文件不是 TTF 文件：{font_path}")
            exit(1)

    if args.output:
        if not os.path.exists(args.output):
            logger.info(f"输出目录不存在，创建：{args.output}")
            try:
                os.makedirs(args.output, exist_ok=True)
            except OSError:
                logger.critical(
                    f"Failed to create output folder at {args.output}", exc_info=True
                )
                exit(1)
    else:
        args.output = None

    for file in pending_files:
        # 清理文件路径，去除两端的引号
        file = file.strip("\"'")
        # 创建配置对象
        config = TranslationConfig(
            input_file=file,
            font=font_path,
            pages=args.pages,
            output_dir=args.output,
            translator=translator,
            debug=args.debug,
            lang_in=args.lang_in,
            lang_out=args.lang_out,
            no_dual=args.no_dual,
            no_mono=args.no_mono,
            qps=args.qps,
            formular_font_pattern=args.formular_font_pattern,
            formular_char_pattern=args.formular_char_pattern,
            split_short_lines=args.split_short_lines,
            short_line_split_factor=args.short_line_split_factor,
            doc_layout_model=doc_layout_model,
            skip_clean=args.skip_clean,
            dual_translate_first=args.dual_translate_first,
        )

        # Create progress handler
        progress_context, progress_handler = create_progress_handler(config)

        # 开始翻译
        with progress_context:
            async for event in yadt.high_level.async_translate(config):
                progress_handler(event)
                if config.debug:
                    logger.debug(event)
                if event["type"] == "finish":
                    result = event["translate_result"]
                    logger.info("Translation Result:")
                    logger.info(f"  Original PDF: {result.original_pdf_path}")
                    logger.info(f"  Time Cost: {result.total_seconds:.2f}s")
                    logger.info(f"  Mono PDF: {result.mono_pdf_path or 'None'}")
                    logger.info(f"  Dual PDF: {result.dual_pdf_path or 'None'}")
                    break


# for backward compatibility
def create_cache_folder():
    return yadt.high_level.create_cache_folder()


# for backward compatibility
def download_font_assets():
    return yadt.high_level.download_font_assets()


def cli():
    """Command line interface entry point."""
    from rich.logging import RichHandler

    logging.basicConfig(level=logging.INFO, handlers=[RichHandler()])

    logging.getLogger("httpx").setLevel("CRITICAL")
    logging.getLogger("httpx").propagate = False
    logging.getLogger("openai").setLevel("CRITICAL")
    logging.getLogger("openai").propagate = False
    logging.getLogger("httpcore").setLevel("CRITICAL")
    logging.getLogger("httpcore").propagate = False
    logging.getLogger("http11").setLevel("CRITICAL")
    logging.getLogger("http11").propagate = False
    for v in logging.Logger.manager.loggerDict.values():
        if getattr(v, "name", None) is None:
            continue
        if (
            v.name.startswith("pdfminer")
            or v.name.startswith("peewee")
            or v.name.startswith("httpx")
            or "http11" in v.name
            or "openai" in v.name
        ):
            v.disabled = True
            v.propagate = False

    yadt.high_level.init()
    asyncio.run(main())


if __name__ == "__main__":
    cli()

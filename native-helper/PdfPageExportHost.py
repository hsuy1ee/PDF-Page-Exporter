"""Native Messaging host for the PDF 页面导出图片 Edge extension."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path

LOGGER = logging.getLogger("pdf_page_exporter")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def configure_logging() -> None:
    try:
        log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PDF-Page-Exporter" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_dir / "native-host.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s pid=%(process)d %(levelname)s %(message)s"))
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
    except OSError:
        # Logging must never prevent exporting; stdout is reserved for messages.
        pass


def read_message() -> dict | None:
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    if len(raw_length) != 4:
        raise ValueError("Native Messaging message is incomplete.")
    length = struct.unpack("=I", raw_length)[0]
    payload = sys.stdin.buffer.read(length)
    if len(payload) != length:
        raise ValueError("Native Messaging payload is incomplete.")
    return json.loads(payload.decode("utf-8"))


def send_message(message: dict) -> None:
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def application_dir() -> Path:
    executable = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    return executable.parent.parent


def choose_pdf() -> Path | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update_idletasks()
    LOGGER.info("Waiting for PDF file selection")
    chosen = filedialog.askopenfilename(
        parent=root,
        title="选择要导出图片的 PDF",
        filetypes=[("PDF 文件", "*.pdf")],
    )
    root.destroy()
    return Path(chosen) if chosen else None


def parse_pages(value: str, total_pages: int) -> list[int]:
    if not value:
        return list(range(1, total_pages + 1))
    selected: set[int] = set()
    for part in value.replace(" ", "").split(","):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d*)?)?", part)
        if not match:
            raise ValueError("页码格式应类似 3-10 或 1,3,5-8。")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else (total_pages if "-" in part else start)
        if start < 1 or end < start or end > total_pages:
            raise ValueError(f"页码必须在 1 到 {total_pages} 之间。")
        selected.update(range(start, end + 1))
    if not selected:
        raise ValueError("请至少输入一个页码。")
    return sorted(selected)


def page_count(pdfinfo: Path, source: Path) -> int:
    completed = subprocess.run(
        [str(pdfinfo), str(source)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        creationflags=NO_WINDOW, timeout=60
    )
    match = re.search(r"^Pages:\s+(\d+)\s*$", completed.stdout, re.MULTILINE)
    if not match:
        raise RuntimeError("无法读取 PDF 页数。")
    return int(match.group(1))


def create_output_folder(desktop: Path, folder_name: str) -> Path:
    name = folder_name.strip() or f"PDF-Images-{datetime.now():%Y%m%d-%H%M%S}"
    if (
        re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
        or name.endswith(".")
        or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", name, re.IGNORECASE)
    ):
        raise ValueError('文件夹名称不能包含 <>:"/\\|?* 等字符，也不能使用 Windows 保留名称或以句点结尾。')
    candidate = desktop / name
    suffix = 2
    while True:
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = desktop / f"{name}-{suffix}"
            suffix += 1


def page_ranges(pages: list[int]):
    """Group the sorted, deduplicated selection into consecutive ranges."""
    if not pages:
        return
    start = end = pages[0]
    for page in pages[1:]:
        if page == end + 1:
            end = page
        else:
            yield start, end
            start = end = page
    yield start, end


@contextmanager
def rendering_directory(output: Path):
    # Poppler needs an ASCII output path. Use the final folder directly when
    # possible; otherwise prefer a temporary folder on the destination volume.
    if str(output).isascii():
        yield output
        return
    roots = [Path(tempfile.gettempdir()), output.parent, application_dir() / "native-helper"]
    roots = list(dict.fromkeys(root for root in roots if str(root).isascii() and root.is_dir()))
    roots.sort(key=lambda root: root.drive.casefold() != output.drive.casefold())
    for root in roots:
        try:
            staging = tempfile.TemporaryDirectory(prefix="pdf-export-", dir=root)
        except OSError:
            continue
        with staging as directory:
            yield Path(directory)
        return
    raise ValueError("未找到可用于生成图片的英文临时路径。")


def render_pages(pdftocairo: Path, source: Path, pages: list[int], image_format: str,
                 dpi: int, output: Path, pad: int) -> None:
    extension = "jpg" if image_format == "jpeg" else "png"
    with rendering_directory(output) as work:
        for start, end in page_ranges(pages):
            prefix = work / "render"
            command = [str(pdftocairo), f"-{image_format}", "-f", str(start), "-l", str(end), "-r", str(dpi)]
            if image_format == "jpeg":
                command.extend(["-jpegopt", "quality=92"])
            command.extend([str(source), str(prefix)])
            LOGGER.info("Rendering pages %d-%d; direct output=%s", start, end, work == output)
            subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
                           creationflags=NO_WINDOW)
            # Poppler chooses the number padding itself; preserve our existing
            # page-001 naming regardless of that padding or the selected range.
            images = {}
            for image in work.glob(f"render-*.{extension}"):
                match = re.fullmatch(rf"render-(\d+)\.{extension}", image.name)
                if match:
                    images[int(match.group(1))] = image
            if set(images) != set(range(start, end + 1)):
                raise RuntimeError(f"第 {start}-{end} 页导出结果不完整。")
            for page in range(start, end + 1):
                shutil.move(str(images[page]), str(output / f"page-{page:0{pad}d}.{extension}"))


def export(message: dict) -> dict:
    LOGGER.info("Export request received; local source supplied=%s", bool(message.get("sourcePath")))
    source_value = message.get("sourcePath")
    source = Path(source_value) if source_value else choose_pdf()
    if source is None:
        return {"ok": False, "error": "已取消选择文件。"}
    if not source.is_file() or source.suffix.lower() != ".pdf":
        return {"ok": False, "error": "请选择一个存在的 PDF 文件。"}

    bin_dir = application_dir() / "poppler" / "poppler-26.02.0" / "Library" / "bin"
    pdftocairo = bin_dir / "pdftocairo.exe"
    pdfinfo = bin_dir / "pdfinfo.exe"
    if not pdftocairo.is_file() or not pdfinfo.is_file():
        return {"ok": False, "error": "未找到 Poppler，请检查工具目录。"}

    image_format = message.get("format", "png")
    if image_format not in {"png", "jpeg"}:
        return {"ok": False, "error": "仅支持 PNG 或 JPEG。"}
    dpi = int(message.get("dpi", 200))
    if dpi not in {150, 200, 300}:
        return {"ok": False, "error": "清晰度只能是 150、200 或 300 DPI。"}

    total = page_count(pdfinfo, source)
    pages = parse_pages(str(message.get("pages", "")), total)
    LOGGER.info("PDF inspected; total=%d selected=%d format=%s dpi=%d", total, len(pages), image_format, dpi)
    desktop = Path.home() / "Desktop"
    output = create_output_folder(desktop, str(message.get("folderName", "")))
    pad = max(3, len(str(total)))

    render_pages(pdftocairo, source, pages, image_format, dpi, output, pad)

    LOGGER.info("Export completed; images=%d output=%s", len(pages), output)
    try:
        os.startfile(output)
    except OSError:
        LOGGER.exception("Images saved, but opening the output folder failed")
    return {"ok": True, "count": len(pages), "outputPath": str(output)}


def main() -> None:
    configure_logging()
    started = time.perf_counter()
    LOGGER.info("Native host started")
    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        message = read_message()
        if message is None:
            return
        if message.get("action") != "export":
            send_message({"ok": False, "error": "未知操作。"})
            return
        response = export(message)
        if not response.get("ok"):
            LOGGER.warning("Export rejected: %s", response.get("error"))
        send_message(response)
    except subprocess.CalledProcessError as error:
        LOGGER.exception("Poppler failed")
        detail = (error.stderr or error.stdout or "Poppler 无法处理此 PDF。").strip()
        send_message({"ok": False, "error": detail})
    except Exception as error:
        LOGGER.exception("Native host failed")
        send_message({"ok": False, "error": str(error)})
    finally:
        LOGGER.info("Native host finished; duration=%.3fs", time.perf_counter() - started)


if __name__ == "__main__":
    main()

import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("host", ROOT / "native-helper" / "PdfPageExportHost.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def write_pdf(path, page_count=1):
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(page_count))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode(),
    ]
    for i in range(page_count):
        # Different content per page catches accidental page-number mismatches.
        content = f"0.{i % 9 + 1} g {i % 30} 0 32 72 re f\n".encode()
        objects.extend([
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Resources << >> /Contents {4 + 2 * i} 0 R >>".encode(),
            f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream",
        ])
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(pdf)


class NativeHelperTests(unittest.TestCase):
    def test_batched_exports_match_individual_pages(self):
        with tempfile.TemporaryDirectory(prefix="pdf-batch-test-") as temp:
            root = Path(temp)
            source = root / "测试.pdf"
            write_pdf(source, page_count=12)
            cairo = ROOT / "poppler" / "poppler-26.02.0" / "Library" / "bin" / "pdftocairo.exe"
            real_run = subprocess.run
            real_move = host.shutil.move
            for fmt, extension in [("png", "png"), ("jpeg", "jpg")]:
                expected = {}
                for page in range(1, 13):
                    prefix = root / f"reference-{page}"
                    command = [str(cairo), f"-{fmt}", "-singlefile", "-f", str(page), "-l", str(page), "-r", "150"]
                    if fmt == "jpeg":
                        command.extend(["-jpegopt", "quality=92"])
                    real_run(command + [str(source), str(prefix)], capture_output=True, check=True, creationflags=host.NO_WINDOW)
                    expected[page] = prefix.with_suffix(f".{extension}").read_bytes()
                for folder in ["ascii", "中文"]:
                    for value, calls in [("1-12", 1), ("8,1,3,5-7,5", 3), ("12", 1), ("", 1)]:
                        with self.subTest(format=fmt, folder=folder, pages=value):
                            with patch.object(host.Path, "home", return_value=root), patch.object(host.os, "startfile"), \
                                 patch.object(host.subprocess, "run", wraps=real_run) as run, \
                                 patch.object(host.shutil, "move", wraps=real_move) as move:
                                result = host.export({"sourcePath": str(source), "folderName": folder, "pages": value, "format": fmt, "dpi": 150})
                            output = Path(result["outputPath"])
                            selected = host.parse_pages(value, 12)
                            render_calls = [call.args[0] for call in run.call_args_list if Path(call.args[0][0]) == cairo]
                            self.assertEqual(len(render_calls), calls)
                            self.assertEqual(result["count"], len(selected))
                            self.assertEqual({image.name for image in output.iterdir()}, {f"page-{page:03d}.{extension}" for page in selected})
                            for page in selected:
                                self.assertEqual((output / f"page-{page:03d}.{extension}").read_bytes(), expected[page])
                            for call in move.call_args_list:
                                src, dest = map(Path, call.args)
                                self.assertEqual(src.drive.lower(), dest.drive.lower(), "avoid cross-volume image copying")
                                self.assertEqual(src.parent == output, folder == "ascii", "ASCII output renders directly")
                            for command in render_calls:
                                self.assertTrue(command[-1].isascii())
                                if folder == "中文":
                                    self.assertFalse(Path(command[-1]).parent.exists(), "temporary directory is cleaned up")

    def test_real_exports_and_explorer_failure(self):
        with tempfile.TemporaryDirectory(prefix="pdf-export-test-") as temp:
            root = Path(temp)
            source = root / "测试.pdf"
            write_pdf(source)
            with patch.object(host.Path, "home", return_value=root), patch.object(host.os, "startfile") as explorer:
                for name, fmt, expected in [("中文名称", "png", "中文名称"), ("中文名称", "jpeg", "中文名称-2"), ("", "png", None)]:
                    result = host.export({"sourcePath": str(source), "folderName": name, "format": fmt, "dpi": 150, "pages": "1"})
                    output = Path(result["outputPath"])
                    self.assertTrue(result["ok"])
                    self.assertEqual(output.parent, root / "Desktop")
                    if expected:
                        self.assertEqual(output.name, expected)
                    else:
                        self.assertTrue(output.name.startswith("PDF-Images-"))
                    image = output / ("page-001.jpg" if fmt == "jpeg" else "page-001.png")
                    self.assertTrue(image.read_bytes().startswith(b"\xff\xd8" if fmt == "jpeg" else b"\x89PNG"))
                explorer.side_effect = OSError("Explorer unavailable")
                with self.assertLogs(host.LOGGER, level="ERROR"):
                    result = host.export({"sourcePath": str(source), "pages": "1"})
                self.assertTrue(result["ok"], "saved images must not be reported as failed if Explorer cannot open")

    def test_packaged_host_protocol_and_poppler(self):
        with tempfile.TemporaryDirectory(prefix="pdf-host-protocol-") as temp:
            root = Path(temp)
            source = root / "sample.pdf"
            write_pdf(source)
            # This exercises packaged imports, binary stdio and real pdfinfo,
            # but rejects the page range before creating output/opening Explorer.
            payload = json.dumps({"action": "export", "sourcePath": str(source), "pages": "2"}).encode()
            env = {**os.environ, "LOCALAPPDATA": temp}
            result = subprocess.run(
                [str(ROOT / "native-helper" / "PdfPageExportHost.exe")],
                input=struct.pack("=I", len(payload)) + payload, capture_output=True,
                check=True, timeout=30, creationflags=host.NO_WINDOW, env=env,
            )
            length = struct.unpack("=I", result.stdout[:4])[0]
            self.assertEqual(length, len(result.stdout) - 4)
            response = json.loads(result.stdout[4:])
            self.assertFalse(response["ok"])
            self.assertIn("1 到 1", response["error"])
            log = (root / "PDF-Page-Exporter" / "logs" / "native-host.log").read_text(encoding="utf-8")
            self.assertIn("Native host started", log)
            self.assertIn("Native host failed", log)


if __name__ == "__main__":
    unittest.main()

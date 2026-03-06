from __future__ import annotations

import base64
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class PdfImageConverterTool:
    def __init__(
        self,
        *,
        max_pages: int = 4,
        dpi: int = 96,
        max_side_px: int = 1280,
        jpeg_quality: int = 55,
    ) -> None:
        self.max_pages = max_pages
        self.dpi = dpi
        self.max_side_px = max_side_px
        self.jpeg_quality = jpeg_quality

    def convert(self, *, pdf_base64: str) -> list[str]:
        pdf_bytes = base64.b64decode(pdf_base64, validate=True)
        logger.info(
            "PDF image conversion start: max_pages=%s, dpi=%s, max_side_px=%s, jpeg_quality=%s",
            self.max_pages,
            self.dpi,
            self.max_side_px,
            self.jpeg_quality,
            extra={"function": f"{self.__class__.__name__}.convert"},
        )
        with tempfile.TemporaryDirectory(prefix="legisguard_pdf_img_") as tmpdir:
            tmp = Path(tmpdir)
            pdf_path = tmp / "notice.pdf"
            output_prefix = tmp / "page"
            pdf_path.write_bytes(pdf_bytes)
            self._render_pdf_pages(
                pdf_path=pdf_path,
                output_prefix=output_prefix,
                max_pages=self.max_pages,
                dpi=self.dpi,
                max_side_px=self.max_side_px,
                jpeg_quality=self.jpeg_quality,
            )

            rendered = sorted(tmp.glob("page-*.jpg"), key=self._page_sort_key)
            if not rendered:
                raise RuntimeError("PDF to image conversion produced no pages.")
            data_urls = [self._to_jpeg_data_url(path) for path in rendered]
            total_chars = sum(len(url) for url in data_urls)
            logger.info(
                "PDF image conversion complete: pages=%s, data_url_chars=%s",
                len(data_urls),
                total_chars,
                extra={"function": f"{self.__class__.__name__}.convert"},
            )
            return data_urls

    def _render_pdf_pages(
        self,
        *,
        pdf_path: Path,
        output_prefix: Path,
        max_pages: int,
        dpi: int,
        max_side_px: int,
        jpeg_quality: int,
    ) -> None:
        command = [
            "pdftoppm",
            "-f",
            "1",
            "-l",
            str(max_pages),
            "-r",
            str(dpi),
            "-scale-to",
            str(max_side_px),
            "-jpeg",
            "-jpegopt",
            f"quality={jpeg_quality},progressive=y,optimize=y",
            str(pdf_path),
            str(output_prefix),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise RuntimeError("Missing 'pdftoppm'. Install poppler to enable PDF image conversion.") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            if len(stderr) > 500:
                stderr = stderr[:500] + "..."
            raise RuntimeError(f"pdftoppm failed: {stderr}") from exc

    def _to_jpeg_data_url(self, image_path: Path) -> str:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    def _page_sort_key(self, path: Path) -> int:
        # pdftoppm output names follow "page-<number>.png".
        try:
            return int(path.stem.rsplit("-", maxsplit=1)[-1])
        except ValueError:
            return 0

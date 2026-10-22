"""OCR через установленный в системе Tesseract (командная строка, локально)."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .result import ExtractionError


@dataclass(frozen=True)
class OcrOptions:
    lang: str = "rus"
    dpi: int = 300            # разрешение, с которым страница PDF превращается в изображение
    timeout: int = 300        # секунд на одну страницу
    min_text_chars: int = 10  # меньше непробельных символов — страница считается сканом


def ocr_status(options: OcrOptions = OcrOptions()) -> Tuple[bool, str]:
    """(доступен ли OCR, пояснение)."""
    exe = shutil.which("tesseract")
    if not exe:
        return False, "Tesseract не найден в PATH"
    try:
        out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Не удалось запустить Tesseract: {exc}"
    langs = {ln.strip() for ln in (out.stdout + out.stderr).splitlines()}
    missing = [l for l in options.lang.split("+") if l not in langs]
    if missing:
        return False, f"Не установлен языковой пакет Tesseract: {', '.join(missing)}"
    return True, exe


def ocr_png(png: bytes, options: OcrOptions = OcrOptions()) -> str:
    """Распознаёт одно изображение (PNG). Временный файл удаляется сразу после работы."""
    ok, info = ocr_status(options)
    if not ok:
        raise ExtractionError(info)
    with tempfile.TemporaryDirectory(prefix="protocol-archive-ocr-") as tmp:
        img = Path(tmp) / "page.png"
        img.write_bytes(png)
        try:
            proc = subprocess.run(
                [info, str(img), "stdout", "-l", options.lang, "--dpi", str(options.dpi)],
                capture_output=True, timeout=options.timeout)
        except subprocess.TimeoutExpired as exc:
            raise ExtractionError(
                f"OCR страницы не уложился в {options.timeout} с") from exc
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise ExtractionError(f"Tesseract завершился с ошибкой: {err[:300]}")
        return proc.stdout.decode("utf-8", "replace")

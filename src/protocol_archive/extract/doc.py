"""DOC (Word 97–2003): конвертация в DOCX через LibreOffice без интерфейса, затем чтение."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .docx import extract_docx
from .result import ExtractionError, ExtractionResult


def libreoffice_exe() -> str:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise ExtractionError("LibreOffice (soffice) не найден: формат DOC без него не читается")
    return exe


def extract_doc(path: Path, timeout: int = 240) -> ExtractionResult:
    """Конвертирует ВРЕМЕННУЮ копию во временной папке (удаляется сразу).

    Оригинал LibreOffice не открывает, поэтому рядом с ним не появляются файлы
    блокировки ``.~lock.*#``, даже если конвертация зависнет и будет прервана.
    В архиве и базе копия не сохраняется.
    """
    exe = libreoffice_exe()
    with tempfile.TemporaryDirectory(prefix="protocol-archive-doc-") as tmp:
        work = Path(tmp)
        src = work / "input.doc"
        out_dir = work / "out"
        out_dir.mkdir()
        profile = work / "profile"  # отдельный профиль: не мешает уже открытому LibreOffice
        shutil.copyfile(path, src)
        cmd = [exe, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
               "--convert-to", "docx:MS Word 2007 XML", "--outdir", str(out_dir), str(src)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ExtractionError(f"LibreOffice не уложился в {timeout} с") from exc
        except OSError as exc:
            raise ExtractionError(f"Не удалось запустить LibreOffice: {exc}") from exc
        result = out_dir / "input.docx"
        if proc.returncode != 0 or not result.exists():
            detail = (proc.stderr or proc.stdout).strip()[:300]
            raise ExtractionError(f"LibreOffice не смог конвертировать DOC. {detail}")
        extracted = extract_docx(result, source="doc")
    extracted.warnings.insert(0, "DOC прочитан через конвертацию LibreOffice в DOCX.")
    return extracted

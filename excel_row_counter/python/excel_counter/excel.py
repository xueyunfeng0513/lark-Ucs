import json
import subprocess
import sys
import time
from pathlib import Path
from zipfile import ZipFile

import openpyxl
import xlrd


class WorkbookError(ValueError):
    """A workbook cannot be counted safely."""


def _has_value(value):
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def count_rows(path, *, max_expanded_bytes=64 * 1024 * 1024, max_scan_rows=100000,
               max_cells=2000000, max_columns=512, max_sheets=100, max_seconds=30):
    """Count actual nonempty rows, not worksheet dimensions or formatting."""
    path = Path(path)
    started = time.monotonic()
    scanned_rows = scanned_cells = 0

    def count(values):
        nonlocal scanned_rows, scanned_cells
        scanned_rows += 1
        scanned_cells += len(values)
        if scanned_rows > max_scan_rows or scanned_cells > max_cells or len(values) > max_columns:
            raise WorkbookError("工作簿扫描范围过大，请删除多余的空白行、列或格式后重试。")
        if time.monotonic() - started > max_seconds:
            raise WorkbookError("工作簿读取超时，请简化文件后重试。")
        return int(any(_has_value(value) for value in values))

    try:
        if path.stat().st_size > 20 * 1024 * 1024:
            raise WorkbookError("文件大小超过 20 MB，请拆分后发送。")
        if path.suffix.lower() == ".xlsx":
            with ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > 1000 or sum(item.file_size for item in entries) > max_expanded_bytes:
                    raise WorkbookError("工作簿解压大小超出限制，请简化文件后重试。")
                if any(item.flag_bits & 1 or item.filename.lower().endswith("vbaproject.bin") for item in entries):
                    raise WorkbookError("暂不支持加密或含宏的工作簿。")
            book = openpyxl.load_workbook(path, read_only=True, data_only=False, keep_links=False)
            try:
                if len(book.worksheets) > max_sheets:
                    raise WorkbookError("工作表数量超过 100 张，请拆分后发送。")
                result = {}
                for sheet in book.worksheets:
                    # Some producers incorrectly declare A1:A1 despite having more rows.
                    sheet.reset_dimensions()
                    result[sheet.title] = sum(count(row) for row in sheet.iter_rows(values_only=True))
                return result
            finally:
                book.close()
        if path.suffix.lower() == ".xls":
            book = xlrd.open_workbook(str(path), on_demand=True)
            try:
                if book.nsheets > max_sheets:
                    raise WorkbookError("工作表数量超过 100 张，请拆分后发送。")
                result = {}
                for index, name in enumerate(book.sheet_names()):
                    sheet = book.sheet_by_index(index)
                    result[name] = sum(count(sheet.row_values(row)) for row in range(sheet.nrows))
                    book.unload_sheet(index)
                return result
            finally:
                book.release_resources()
        raise WorkbookError("只支持 .xlsx 和 .xls 文件。")
    except WorkbookError:
        raise
    except Exception as exc:
        raise WorkbookError("无法读取 Excel，请确认文件未损坏、未加密，且扩展名正确。") from exc


def count_rows_isolated(path):
    """A malformed workbook must not hang the notifier or the web server."""
    try:
        result = subprocess.run([sys.executable, "-m", "excel_counter.excel", str(path)],
                                capture_output=True, timeout=45, encoding="utf-8")
        payload = json.loads(result.stdout)
        if result.returncode != 0 or "error" in payload:
            raise WorkbookError(payload.get("error", "无法读取 Excel。"))
        return payload["sheets"]
    except subprocess.TimeoutExpired as exc:
        raise WorkbookError("Excel 读取超时，请简化文件后重试。") from exc
    except (ValueError, KeyError) as exc:
        if isinstance(exc, WorkbookError):
            raise
        raise WorkbookError("无法读取 Excel，请确认文件完整后重试。") from exc


if __name__ == "__main__":
    # Codespaces/Linux: cap parser memory as well as the parent-enforced timeout.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except ImportError:
        pass  # Windows retains file/ZIP/scan limits and the process timeout.
    try:
        print(json.dumps({"sheets": count_rows(Path(sys.argv[1]))}, ensure_ascii=True))
    except WorkbookError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        sys.exit(1)

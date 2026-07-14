"""字体资源管理：列出、上传、删除 src/static/fonts/ 下的字体文件。

安全要点：
- 文件名走白名单扩展名 + basename 校验，杜绝目录穿越。
- 上传大小上限 20MB（中文 TTF 完整版通常 5-15MB）。
- 只允许写入 fonts 目录内部；删除时二次核对绝对路径落在 fonts 目录下。
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from core.config import SRC_DIR

FONTS_DIR = SRC_DIR / "static" / "fonts"
ALLOWED_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


class FontError(ValueError):
    """字体资源相关错误。"""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field

    def as_dict(self) -> dict[str, str]:
        result = {"message": str(self)}
        if self.field:
            result["field"] = self.field
        return result


def _ensure_dir() -> None:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)


def _validate_name(name: str) -> str:
    """校验并规范化字体文件名，返回可安全拼接的 basename。"""
    if not isinstance(name, str):
        raise FontError("文件名必须是字符串", "filename")
    trimmed = name.strip().replace("\\", "/").split("/")[-1]
    if not trimmed:
        raise FontError("文件名不能为空", "filename")
    if not _SAFE_NAME_RE.fullmatch(trimmed):
        raise FontError("文件名只能包含字母、数字、点、下划线和连字符", "filename")
    suffix = Path(trimmed).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise FontError(f"仅支持以下扩展名：{allowed}", "filename")
    return trimmed


def _resolve_inside_fonts(name: str) -> Path:
    """把 basename 拼到 fonts 目录，防止目录穿越（绝对路径必须在 FONTS_DIR 下）。"""
    _ensure_dir()
    target = (FONTS_DIR / name).resolve()
    fonts_root = FONTS_DIR.resolve()
    try:
        target.relative_to(fonts_root)
    except ValueError:
        raise FontError("文件路径越界", "filename")
    return target


def list_fonts() -> list[dict[str, Any]]:
    """返回 fonts/ 下所有合法字体文件的元数据。"""
    _ensure_dir()
    result: list[dict[str, Any]] = []
    for path in sorted(FONTS_DIR.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        result.append({
            "filename": path.name,
            "size": size,
            "url": f"/static/fonts/{path.name}",
        })
    return result


def upload_font(filename: str, data_b64: str) -> dict[str, Any]:
    """把 base64 编码的字体内容写入 fonts/。"""
    name = _validate_name(filename)
    if not isinstance(data_b64, str) or not data_b64:
        raise FontError("字体内容为空", "data")
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception as exc:
        raise FontError(f"字体内容不是有效 base64：{exc}", "data") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise FontError(f"文件过大，最大 {MAX_UPLOAD_BYTES // 1024 // 1024}MB", "data")
    if len(raw) < 4:
        raise FontError("文件内容过短，可能不是有效字体", "data")

    target = _resolve_inside_fonts(name)
    # 原子写入：先写临时文件再替换
    temp = target.with_name(target.name + ".tmp")
    try:
        temp.write_bytes(raw)
        temp.replace(target)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
    return {"filename": target.name, "size": len(raw), "url": f"/static/fonts/{target.name}"}


def delete_font(filename: str) -> dict[str, Any]:
    """删除 fonts/ 下的字体文件。"""
    name = _validate_name(filename)
    target = _resolve_inside_fonts(name)
    if not target.exists():
        raise FontError("字体文件不存在", "filename")
    try:
        target.unlink()
    except OSError as exc:
        raise FontError(f"删除失败：{exc}", "filename") from exc
    return {"filename": name, "deleted": True}


def font_exists(filename: str | None) -> bool:
    """判断某个字体文件是否存在于 fonts/。"""
    if not filename:
        return False
    try:
        name = _validate_name(filename)
    except FontError:
        return False
    return (FONTS_DIR / name).is_file()

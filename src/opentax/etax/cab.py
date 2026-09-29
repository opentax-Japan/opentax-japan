"""CAB（Microsoft Cabinet）を読む。標準ライブラリだけで、圧縮は「なし」と MSZIP に対応する。

国税庁の e-Tax 仕様書は CAB で配布される。外部コマンド（expand, cabextract, 7z）に頼らずに展開するために使う。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

_FLAG_PREV_CABINET = 0x0001
_FLAG_NEXT_CABINET = 0x0002
_FLAG_RESERVE_PRESENT = 0x0004
_ATTR_NAME_IS_UTF = 0x80
_COMPRESS_NONE = 0
_COMPRESS_MSZIP = 1
_MSZIP_WINDOW = 32768


class CabError(Exception):
    """CAB を読めないとき。"""


@dataclass(frozen=True)
class CabEntry:
    path: str  # 区切りは "/"
    size: int
    folder: int
    offset: int  # フォルダを展開した後の位置


class Cabinet:
    def __init__(self, data: bytes):
        if data[:4] != b"MSCF":
            raise CabError("CAB ファイルではありません")
        (coff_files,) = struct.unpack_from("<I", data, 16)
        c_folders, c_files, flags = struct.unpack_from("<HHH", data, 26)
        if flags & (_FLAG_PREV_CABINET | _FLAG_NEXT_CABINET):
            raise CabError("複数の CAB に分かれたものには対応していません")

        pos = 36
        cb_folder_reserve = cb_data_reserve = 0
        if flags & _FLAG_RESERVE_PRESENT:
            cb_header_reserve, cb_folder_reserve, cb_data_reserve = struct.unpack_from("<HBB", data, pos)
            pos += 4 + cb_header_reserve

        self._folders: list[tuple[int, int, int]] = []
        for _ in range(c_folders):
            coff_start, c_data, type_compress = struct.unpack_from("<IHH", data, pos)
            pos += 8 + cb_folder_reserve
            self._folders.append((coff_start, c_data, type_compress))

        entries = []
        pos = coff_files
        for _ in range(c_files):
            cb_file, offset, i_folder, _date, _time, attribs = struct.unpack_from("<IIHHHH", data, pos)
            end = data.index(b"\0", pos + 16)
            raw_name = data[pos + 16 : end]
            pos = end + 1
            if i_folder >= 0xFFFD:
                raise CabError("前後の CAB にまたがるファイルには対応していません")
            name = raw_name.decode("utf-8" if attribs & _ATTR_NAME_IS_UTF else "cp932")
            entries.append(CabEntry(name.replace("\\", "/"), cb_file, i_folder, offset))

        self.entries: list[CabEntry] = entries
        self._data = data
        self._cb_data_reserve = cb_data_reserve
        self._folder_index = -1
        self._folder_bytes = bytearray()

    def _expand_folder(self, index: int) -> bytearray:
        if index == self._folder_index:
            return self._folder_bytes
        coff_start, c_data, type_compress = self._folders[index]
        kind = type_compress & 0x000F
        if kind not in (_COMPRESS_NONE, _COMPRESS_MSZIP):
            raise CabError(f"対応していない圧縮形式です（種類 {kind}。対応は なし・MSZIP のみ）")

        out = bytearray()
        pos = coff_start
        for _ in range(c_data):
            _csum, cb_data, cb_uncomp = struct.unpack_from("<IHH", self._data, pos)
            pos += 8 + self._cb_data_reserve
            block = self._data[pos : pos + cb_data]
            pos += cb_data
            if kind == _COMPRESS_NONE:
                out += block
                continue
            if block[:2] != b"CK":
                raise CabError("MSZIP のブロックの形式が違います")
            # MSZIP は各ブロックが独立した deflate だが、直前 32KB を辞書として参照する
            if out:
                d = zlib.decompressobj(-15, zdict=bytes(out[-_MSZIP_WINDOW:]))
            else:
                d = zlib.decompressobj(-15)
            chunk = d.decompress(block[2:]) + d.flush()
            if len(chunk) != cb_uncomp:
                raise CabError("MSZIP の展開後の大きさが合いません")
            out += chunk

        # メモリを抑えるため、展開済みのフォルダは直近の1つだけ持つ
        self._folder_index = index
        self._folder_bytes = out
        return out

    def read(self, entry: CabEntry) -> bytes:
        folder = self._expand_folder(entry.folder)
        if entry.offset + entry.size > len(folder):
            raise CabError(f"ファイルの位置がフォルダの外です: {entry.path}")
        return bytes(folder[entry.offset : entry.offset + entry.size])

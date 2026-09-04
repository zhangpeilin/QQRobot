"""Re-apply the NapCat 4.18.9 nested-forward video/image URL patch.

Usage:
    venv\\Scripts\\python.exe patches\\apply_napcat_forward_video_url.py
    venv\\Scripts\\python.exe patches\\apply_napcat_forward_video_url.py --restore

Original napcat.mjs SHA256:
    845E15BE97ECD0F2B3F353C6A235E1F5C111CD53FEE6F1952A3A68A01DAA9CD9
Backup (gitignored):
    NapCat/napcat.mjs.orig
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "NapCat" / "napcat.mjs"
ORIG = ROOT / "NapCat" / "napcat.mjs.orig"
ORIG_SHA256 = "845E15BE97ECD0F2B3F353C6A235E1F5C111CD53FEE6F1952A3A68A01DAA9CD9"

REPLACEMENTS: list[tuple[str, str, str]] = [
    (
        "getVideoUrlPacket NaN guard",
        "        if (i && i === 1415)\n"
        "          return this.core.apis.PacketApi.pkt.operation.GetGroupVideoUrl(+e, {",
        "        const g = +e;\n"
        "        if (i && i === 1415 && Number.isInteger(g) && g > 0)\n"
        "          return this.core.apis.PacketApi.pkt.operation.GetGroupVideoUrl(g, {",
    ),
    (
        "videoElement parent group peer",
        "    videoElement: async (e, n, r, { disableGetUrl: i }) => {\n"
        "      const o = {\n"
        "        chatType: n.chatType,\n"
        "        peerUid: n.peerUid,\n"
        "        guildId: \"\"\n"
        "      };\n"
        "      let s;",
        "    videoElement: async (e, n, r, { disableGetUrl: i }) => {\n"
        "      const o = {\n"
        "        chatType: n.chatType,\n"
        "        peerUid: n.peerUid,\n"
        "        guildId: \"\"\n"
        "      };\n"
        "      const _uid = n.parentMsgPeer && /^\\d+$/.test(String(n.parentMsgPeer.peerUid || \"\")) "
        "? n.parentMsgPeer.peerUid : n.peerUid;\n"
        "      _uid !== n.peerUid && this.core.context.logger.logWarn(\"转发视频URL改用父会话 \" + _uid);\n"
        "      let s;",
    ),
    (
        "packet call uses parent peer",
        "                resourceFn: async () => await this.core.apis.FileApi.getVideoUrlPacket(n.peerUid, e.fileUuid, 1500),",
        "                resourceFn: async () => await this.core.apis.FileApi.getVideoUrlPacket(_uid, e.fileUuid, 1500),",
    ),
    (
        "picElement parent peer + 1406 MD5 fallback",
        "    picElement: async (e, n, r, { disableGetUrl: i }) => {\n"
        "      try {\n"
        "        const o = {\n"
        "          chatType: n.chatType,\n"
        "          peerUid: n.peerUid,\n"
        "          guildId: \"\"\n"
        "        };\n"
        "        return Jt.encode(\n"
        "          o,\n"
        "          n.msgId,\n"
        "          r.elementId,\n"
        "          e.fileUuid,\n"
        "          e.fileName\n"
        "        ), {\n"
        "          type: ze.image,\n"
        "          data: {\n"
        "            summary: e.summary,\n"
        "            file: e.fileName,\n"
        "            sub_type: e.picSubType,\n"
        "            url: i ? e.filePath ?? \"\" : await this.core.apis.FileApi.getImageUrl(e),\n"
        "            file_size: e.fileSize\n"
        "          }\n"
        "        };",
        "    picElement: async (e, n, r, { disableGetUrl: i }) => {\n"
        "      try {\n"
        "        const _uid = n.parentMsgPeer && /^\\d+$/.test(String(n.parentMsgPeer.peerUid || \"\")) "
        "? n.parentMsgPeer.peerUid : n.peerUid;\n"
        "        const o = {\n"
        "          chatType: _uid !== n.peerUid && n.parentMsgPeer && n.parentMsgPeer.chatType "
        "? n.parentMsgPeer.chatType : n.chatType,\n"
        "          peerUid: _uid,\n"
        "          guildId: \"\"\n"
        "        };\n"
        "        Jt.encode(\n"
        "          o,\n"
        "          n.msgId,\n"
        "          r.elementId,\n"
        "          e.fileUuid,\n"
        "          e.fileName\n"
        "        );\n"
        "        let _url = i ? e.filePath ?? \"\" : await this.core.apis.FileApi.getImageUrl(e);\n"
        "        if (!i && _uid !== n.peerUid && typeof _url === \"string\" && _url.includes(\"appid=1406\") && e.md5HexStr) {\n"
        "          const _md5 = this.core.apis.FileApi.getImageUrlFromMd5(e.md5HexStr, e.md5HexStr);\n"
        "          _md5 && (this.core.context.logger.logWarn(\"转发图片URL改用MD5 \" + _uid), _url = _md5);\n"
        "        }\n"
        "        return {\n"
        "          type: ze.image,\n"
        "          data: {\n"
        "            summary: e.summary,\n"
        "            file: e.fileName,\n"
        "            sub_type: e.picSubType,\n"
        "            url: _url,\n"
        "            file_size: e.fileSize\n"
        "          }\n"
        "        };",
    ),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest().upper()


def apply() -> int:
    if not TARGET.exists():
        print(f"missing {TARGET}", file=sys.stderr)
        return 1
    text = TARGET.read_text(encoding="utf-8")
    digest = sha256(TARGET)
    if digest != ORIG_SHA256 and "转发视频URL改用父会话" not in text:
        print(
            f"WARNING: {TARGET} sha256={digest} != original {ORIG_SHA256}. "
            "Upgrade may have rewritten napcat.mjs; unique-string apply may fail.",
            file=sys.stderr,
        )
    changed = False
    for name, old, new in REPLACEMENTS:
        if old not in text and new in text:
            print(f"already: {name}")
            continue
        n = text.count(old)
        if n != 1:
            print(f"unique match failed: {name} count={n}", file=sys.stderr)
            return 1
        text = text.replace(old, new, 1)
        changed = True
        print(f"applied: {name}")
    if not changed:
        print(f"already patched: {TARGET}")
        return 0
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"patched {TARGET}")
    return 0


def restore() -> int:
    if not ORIG.exists():
        print(f"missing backup {ORIG}", file=sys.stderr)
        return 1
    shutil.copy2(ORIG, TARGET)
    print(f"restored {TARGET} from {ORIG}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    return restore() if args.restore else apply()


if __name__ == "__main__":
    raise SystemExit(main())

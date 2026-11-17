#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 iphone/oa.js 打包成一个签名好的 .shortcut 文件。

为什么要有这个脚本：iPhone 快捷指令在手机上一个个加动作太容易出错（漏连变量、
动作名对不上、JS 动作灰掉），而这些动作在 plist 里都是明文。所以直接在 Mac 上
生成、签名，AirDrop / iCloud 到手机双击即可导入。

    python3 iphone/build_shortcut.py          # 生成 iphone/OA通知.shortcut
    open iphone/OA通知.shortcut               # 双击导入（Mac 与 iPhone 走 iCloud 同步）

第一步只放 4 个动作（打开页面 → 等待 → 跑 JS → 显示结果），先把链路验证通；
推送和自动化确认能跑之后再加。

结构刻意压到最少：跑 JS（唯一的实质动作）→ 显示通知 → 显示结果。
页面跳转交给用户自己在 Safari 里完成——脚本里 location.replace 会让 JS 上下文
当场销毁、动作永远不返回，整条快捷指令静默中断，这正是之前「点了没反应」的元凶。
"""

import os
import plistlib
import re
import subprocess
import sys
import uuid

BASE = os.path.dirname(os.path.abspath(__file__))
JS_PATH = os.path.join(BASE, "oa.js")
OUT_PATH = os.path.join(BASE, "OA通知.shortcut")


def uid():
    return str(uuid.uuid4()).upper()


def text_token(value):
    """纯文本参数。格式照抄 Apple 官方 Gallery.wflow：
    {"Value": {"string": ...}, "WFSerializationType": "WFTextTokenString"}
    （写成 WFTextTokenAttachment 是错的，那是给 URL/文件这类附件对象用的，
      动作会读不到值 → 报「请确保向打开URL传递了有效URL」）"""
    return {"Value": {"string": value}, "WFSerializationType": "WFTextTokenString"}


def output_token(action_uuid, name, serialization="WFTextTokenAttachment"):
    """「上一步的输出」变量：整串就是一个 U+FFFC 占位字符 + 附件指向该动作。"""
    return {
        "Value": {
            "string": "\ufffc",
            "attachmentsByRange": {
                "{0, 1}": {"OutputName": name, "OutputUUID": action_uuid, "Type": "ActionOutput"}
            },
        },
        "WFSerializationType": serialization,
    }


def build_actions(js_code):
    js_uuid = uid()
    return [
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.runjavascriptonwebpage",
            "WFWorkflowActionParameters": {"UUID": js_uuid, "WFJavaScript": js_code},
        },
        # 从 Safari 分享菜单触发时处于扩展环境，「显示结果」不一定弹得出来，
        # 先用通知兜一条：锁屏和通知中心里都能回看，不至于又是「没反应」
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.notification",
            "WFWorkflowActionParameters": {
                "UUID": uid(),
                "WFNotificationActionTitle": "OA通知",
                "WFNotificationActionBody": output_token(
                    js_uuid, "JavaScript Result", "WFTextTokenString"),
                "WFNotificationActionSound": True,
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
            "WFWorkflowActionParameters": {
                "UUID": uid(),
                "Text": output_token(js_uuid, "JavaScript Result", "WFTextTokenString"),
            },
        },
    ]


def strip_comments(js):
    """去掉块注释、整行 // 注释和空行。

    iOS 的「运行 JavaScript」动作对脚本体积敏感，塞进 plist 的长脚本更容易
    「无法在网页中运行JavaScript」。这里只删整行注释，不碰行尾，
    避免把 URL 里的 // 误删掉。"""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    lines = []
    for line in js.split("\n"):
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        lines.append(s)
    return "\n".join(lines)


def main():
    if not os.path.isfile(JS_PATH):
        print("找不到 {}".format(JS_PATH))
        return 2
    with open(JS_PATH, "r", encoding="utf-8") as f:
        js_code = strip_comments(f.read())
    print("脚本字符数（去注释后）：{}".format(len(js_code)))

    workflow = {
        "WFWorkflowClientVersion": "2606.0.1",
        "WFWorkflowClientRelease": "2606.0.1",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 4282601983,   # 蓝色
            "WFWorkflowIconGlyphNumber": 59511,       # 铃铛
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": ["ActionExtension"],
        "WFWorkflowInputContentItemClasses": ["WFStringContentItem", "WFURLContentItem"],
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowOutputContentItemClasses": ["WFStringContentItem"],
        "WFWorkflowActions": build_actions(js_code),
    }

    unsigned = os.path.join(BASE, ".oa-unsigned.shortcut")
    with open(unsigned, "wb") as f:
        plistlib.dump(workflow, f, fmt=plistlib.FMT_XML)
    print("已生成未签名文件：{}（{} 个动作）".format(unsigned, len(workflow["WFWorkflowActions"])))

    try:
        subprocess.check_call(["shortcuts", "sign", "-m", "anyone",
                              "-i", unsigned, "-o", OUT_PATH])
    except (OSError, subprocess.CalledProcessError) as exc:
        print("签名失败：{}（未签名文件留在 {} 便于排查）".format(exc, unsigned))
        return 1

    size = os.path.getsize(OUT_PATH)
    print("已签名：{}（{} 字节）".format(OUT_PATH, size))
    print("下一步：open '{}'  然后点「添加」/「替换」".format(OUT_PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

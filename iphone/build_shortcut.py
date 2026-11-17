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
"""

import os
import plistlib
import subprocess
import sys
import uuid

BASE = os.path.dirname(os.path.abspath(__file__))
JS_PATH = os.path.join(BASE, "oa.js")
OUT_PATH = os.path.join(BASE, "OA通知.shortcut")

# 打开哪个板块无所谓：oa.js 里会自己 fetch 另外三个，这里只是把浏览器带到
# vpn.jlu.edu.cn 这个域下（同源才能带 cookie 抓取）
LIST_URL = ("https://vpn.jlu.edu.cn/https/"
            "48714f71342f7a336d582f7e2857373750cd3d1004df80a0b5971c1b1a"
            "/defaultroot/PortalInformation!jldxList.action?channelId=179577")


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
    # 不再用「打开 URL」动作：它的 URL 参数在 macOS/iOS 上试了三种写法（纯文本 token、
    # 文本附件、URL 动作输出）都报「请确保向打开URL传递了有效URL」，不值得再赌。
    # 改成让第一段 JS 自己 location.replace 过去——「运行 JavaScript」这个动作本身是好的，
    # 而且它在跳转后的页面上继续跑第二段 JS，效果和「打开 URL + 等待」一样。
    jump_js = (
        "var target = '" + LIST_URL + "';\n"
        "if (location.host !== 'vpn.jlu.edu.cn') {\n"
        "  completion('JUMPING');\n"      # 必须先 completion 收尾，动作才肯放行
        "  location.replace(target);\n"   # 收尾后再发起跳转
        "} else {\n"
        "  completion('ALREADY_HERE');\n"
        "}"
    )
    js_uuid = uid()
    actions = [
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.runjavascriptonwebpage",
            "WFWorkflowActionParameters": {"UUID": uid(), "WFJavaScript": jump_js},
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.delay",
            "WFWorkflowActionParameters": {"UUID": uid(), "WFDelayTime": 8},
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.runjavascriptonwebpage",
            "WFWorkflowActionParameters": {"UUID": js_uuid, "WFJavaScript": js_code},
        },
        # 把 JS 的结论显示出来，不然「没有新通知」时点完毫无反应，没法判断到底跑没跑
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
            "WFWorkflowActionParameters": {
                "UUID": uid(),
                "Text": output_token(js_uuid, "JavaScript Result", "WFTextTokenString"),
            },
        },
    ]
    return actions


def main():
    if not os.path.isfile(JS_PATH):
        print("找不到 {}".format(JS_PATH))
        return 2
    with open(JS_PATH, "r", encoding="utf-8") as f:
        js_code = f.read()

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
        "WFWorkflowTypes": [],
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

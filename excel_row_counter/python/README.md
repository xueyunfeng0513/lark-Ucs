# 飞书 Excel 行数机器人

第一阶段功能：用户**单聊**发送 `.xlsx` 或 `.xls` → 回复“已收到” → 下载并统计 → 回复总行数和各工作表行数。基于仓库 `robot_quick_start/python` 的 Flask webhook 模式实现。没有 Excel 业务转换或用友接口调用。

示例回复：

```text
已收到 Excel：销售订单.xlsx
正在统计行数，请稍候。
任务：41a8bb4a

Excel 统计完成：销售订单.xlsx
总行数：2001
统计口径：所有工作表非空行合计，包含表头，空白行不计。
• 订单：2001 行
任务：41a8bb4a
```

## 行数定义

- 统计所有工作表（包含隐藏工作表）的**非空行**；表头也算一行，隐藏行也计算。
- 某行至少一个单元格有值就计一行。空字符串、纯空白字符不算值，数字 `0` 和布尔值 `False` 算值。
- 空白行、只有格式而没有内容的行不计。不是直接返回 Excel 的“已使用区域”或最后一行编号。
- `.xlsx` 公式按有内容计算，不执行公式。`.xls` 由 xlrd 读取保存的结果值，公式没有有效缓存值时可能作为空值；需要精确统计这类公式时请另存为 `.xlsx`。
- 不自动判断标题行，因此“2000 条数据 + 1 行表头”的结果是 **2001**。

支持 2000 行内的预期业务文件；本阶段仅计数，不按业务数据行数拦截 2001 行。为保护解析进程，另有限额：文件 20 MB，xlsx 展开后 64 MB，最多 100 张表、100000 个扫描行、2000000 个扫描单元格及 512 列。空白和格式行也消耗扫描额度；超限会提示清理文件。解析进程最长 45 秒，Linux 下限制为 512 MB 地址空间。加密文件、xlsx 内的宏和 `.xlsm` 不支持；旧式 `.xls` 的宏不会执行。

## 本地启动

需要 Python 3.11 或以上。进入本目录：

```bash
python -m venv .venv
# Linux / Codespaces
source .venv/bin/activate
# Windows PowerShell 使用：.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

在 `.env` 配置 `APP_ID`、`APP_SECRET`、`VERIFICATION_TOKEN`、`ENCRYPT_KEY`。
`ALLOWED_OPEN_IDS` 可限制为测试人员；留空时接受机器人可用范围内所有用户的单聊文件。只配置一个飞书应用，每个应用使用独立数据库。

```bash
python -m excel_counter --check-config
python -m excel_counter
```

服务默认端口 `3000`，回调路由 `/webhook/event`，健康检查 `/healthz`。使用 Waitress，无 Flask debug/reloader。启动命令同时运行通知与计数线程，只需一个终端。配置错误会在启动时说明缺失项，不打印密钥。

## Codespaces 测试

1. 在包含此功能的分支上创建 Codespace。使用高级创建选项选择 **Excel Row Counter (Python)** 配置；已有其他配置的 Codespace 也可手动执行上面的安装命令。
2. 进入 `excel_row_counter/python`，配置 `.env`。也可在 GitHub Codespaces secrets 配置同名变量并授权此仓库，避免把密钥写入文件。
3. 运行 `python -m excel_counter`，在 **PORTS** 面板把 `3000` 设为 **Public**。
4. 复制该端口的 HTTPS 地址，在飞书配置 `https://<codespace>-3000.app.github.dev/webhook/event`。内部 Flask/Waitress 仍使用 HTTP，无需另配证书。
5. Codespaces 停止时机器人无法工作；重启后重新运行服务并检查端口仍可公开访问。[GitHub 端口说明](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace)。

只公开 webhook 端口。`.env`、`.runtime/`、本地 Excel 均已忽略；不要提交密钥、真实附件或数据库。SQLite 文件保留在 `.runtime/jobs.sqlite3`，删除 Codespace 前如需保留历史应备份该目录。

## 飞书配置

使用**企业自建应用机器人**，在开发者后台启用机器人并配置测试人员可用范围。

1. 在“事件与回调”的加密策略设置 Verification Token 和 Encrypt Key，填入对应环境变量。本示例要求设置 Encrypt Key；它既用于验签，也用于解密加密回调。
2. 先启动服务，再把订阅方式设为“将事件发送至开发者服务器”，填入上面的回调 URL；程序支持明文/加密 URL challenge。
3. 添加事件 `im.message.receive_v1`（接收消息）。
4. 开通下列能力，具体权限以当前平台接口页面为准：
   - `im:message.p2p_msg:readonly`：接收用户发给机器人的单聊消息；
   - `im:message:readonly`：读取消息及消息中的资源；
   - `im:message:send_as_bot`：以应用身份发送消息。
5. 发布应用版本或启用企业内可用的测试版本，让测试人员能够找到机器人。单聊发送一个实际 Excel 附件，不是云文档链接。

用户上传的附件通过 `GET /open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file` 下载；不是只能下载本应用上传文件的 `/im/v1/files/{file_key}` 接口。消息返回原单聊的 `chat_id`。

参考：[消息事件与 API 列表](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1)、[接收消息事件](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive)、[发送消息](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/create)。

## 异步处理与恢复

```text
验签/解密 → 单聊文件 → SQLite 入队 → HTTP ACK
                                   ↓
                      已收到通知 → 下载并统计 → 结果通知 → 完成
```

HTTP 回调不下载附件、不等待消息 API。只有收到通知发送成功才开始统计；通知线程独立于计数线程。飞书重复投递按事件 ID 和消息 ID 去重；重新发送同一文件会产生新消息，可重新统计。

每步带 5 分钟租约。程序突然退出后重启，未完成的租约到期后会恢复。下载在独立进程中执行，父进程强制限制总时长 120 秒；解析另有 45 秒进程超时。下载和解析失败会返回可理解的原因，临时文件在正常完成/失败后清理。正常 Ctrl+C/SIGTERM 会等待在途任务清理，每个线程最多等待 180 秒；系统强制结束进程或再次强制中断仍可能留下临时目录，可在服务停止后清理。

回复使用稳定 UUID，尽量利用飞书发送接口的去重能力；飞书去重窗口之外或响应丢失后仍可能重复通知，不承诺消息恰好一次。通知最多重试 5 次，之后进入 `notification_failed` 并保留结果，日志只显示任务编号和异常类型。管理员修正应用权限或配置后，可让用户重新发送文件。任务记录不包含原始工作簿，暂不自动删除历史以保留去重能力。

## 测试

```bash
python -m pytest -q
```

测试生成临时 xlsx/xls，覆盖多表、空表、空白/格式行、公式、2000 行、损坏文件、扫描/解压限制、加密回调、验签、重复事件、任务租约恢复、接收与结果顺序、通知失败后仅补发结果，以及本地 HTTP 替身上的飞书请求契约。无需真实飞书密钥，不访问用友。

真实飞书联调仍需配置自己的应用并实际发送文件。CI 和本地模拟测试不能证明某个租户的权限配置已经正确。

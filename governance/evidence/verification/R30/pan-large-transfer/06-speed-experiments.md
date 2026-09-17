# R30 pan-large-transfer — 限速/UA 实测记录（2026-09-17）

所有测量都在同一主机、同一账号（SUPER_VIP）、同一分享/自有文件上完成。
目的是定位"转存回退可用"的两个硬闸；结论已固化进实现与文档。

## 1. 取链大小限制按 UA 判定（code 23018）

| 场景 | UA | 结果 |
|---|---|---|
| 自有 367MB `2026-09-04_financial.parquet` 取链 | Chrome/151（`quark_client.UA`） | HTTP 400 `code 23018 download file size limit` |
| 同上 | 官方客户端 `quark-cloud-drive/2.5.20 … Channel/pckk_other_ch` | HTTP 200（`download_url`） |
| 分享 367MB 直链 | 官方客户端 UA | HTTP 200 |
| 自有 45.7MB 日K增量 取链 | Chrome/151 | HTTP 200（阈值内） |

变体（`sys=pc`、`drive-h.quark.cn`、`drive.quark.cn`、带 `uc_param_str` 设备串）
均不能绕过；账号会员态接口返回 `member_type=SUPER_VIP`。
→ 实现：`transfer.DRIVE_CLIENT_UA`，`QuarkPcTransport` 全部请求携带。

## 2. 下载限速（同一链接、同一文件、fresh `download_url`）

| 方式 | 速率 |
|---|---|
| curl 整文件 GET（Chrome/151 UA） | ~0.10 MB/s |
| urllib 整文件 GET（Chrome/151 UA） | ~0.10 MB/s |
| curl 整文件 GET（客户端 UA） | ~8.0 MB/s |
| curl Range（Chrome/151 UA） | ~10.8 MB/s（52.4MB 4.9s） |
| 生产链路 `download_file(chunk_size=64MiB, ua=client)` | 7.3 MB/s（200MB 28.9s） |
| 生产链路 + `connections=4` | 7.7 MB/s（400MB 54.5s） |

串行 Range 在长跑中出现 0.1-1.2MB/s 的慢相（3.8GB 全量包 22min 仅 1.57GB）；
4 连接并行把聚合带宽拉回 ~7.7MB/s。速度噪声较大，但结论稳定：
**整文件 GET 必慢，客户端 UA + 分块 + 并行是可用路径**。

## 3. 对实现的影响

- `quark_client.http(..., ua=None)`：可选 UA 覆盖（默认模块 UA，旧调用不变）。
- `quark_client.download_file(..., chunk_size=None, ua=None, connections=1)`：
  分块（退避重试、同 offset 续拉、服务端忽略 Range 时整段重写）与多连接并行
  （先 1 字节探测 Range；不支持则退回串行分块）。
- `transfer.QuarkPcTransport`：`DRIVE_CHUNK_SIZE=64MiB`、
  `DRIVE_CLIENT_UA`、`DRIVE_CONNECTIONS=4`。
- 回归测试：`platform/tools/quark_download/tests/test_quark_download.py`
  （ranged/并行/回退/重试/UA）、`pan_update/tests/test_transfer.py`（接线断言）。

## 4. 端到端批量实测（rerun4，2026-09-17 20:37:07 起）

4 件超限/大件全部经「分享 save → task 轮询 → 自有直链 → 分块并行下载 → 校验后删副本」：

| 文件 | size | 下载完成时刻 | 备注 |
|---|---|---|---|
| 19910101至20260831A股日k线.zip | 3,787,622,963 | 20:49:29 | 全量日K（补 8/24-8/31 缺口） |
| 个股财务数据_2026-09-04_更新.zip | 802,433,855 | 20:52:41 | |
| 财务季报年报_2026-09-04_更新.zip | 785,637,584 | 20:53:28 | |
| 2026-09-04_financial.parquet | 367,597,336 | 17:13:57 | 优先级 1 小样（25.5s → 14.4MB/s） |

- 批量聚合（20:37:07 → 20:53:28，含转存轮询/取链/删副本开销）：5,743MB / 984s ≈ **5.8MB/s**。
- 单件峰值 14.4MB/s（小样）、低谷 ~4.2MB/s（802MB zip，3.2min）。
- ETA 经验值：**1GB ≈ 2-4min，10GB ≈ 20-40min**（5-8MB/s 区间）；慢速节点靠 4 连接并行兜底。
- 完成后 `factorlab_tmp` 清空（07-drive-tmp-after.txt），本地 size 独立复核见
  10-size-verify.txt（4/4 size 匹配 + sha256）。

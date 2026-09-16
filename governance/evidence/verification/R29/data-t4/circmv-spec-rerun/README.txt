== R29 Task4 Step3: circ_mv 相关代表 spec 抽样复跑（修复后） ==
date: 2026-09-16T21:37:13+08:00 | backend=ch | FACTORLAB_ST_DEGRADE=allow | FACTORLAB_MAX_MEMORY=8GB

[1] 命令（全市场全窗口，不改 spec；输出到 /tmp 与旧 runs/platform 产物隔离）
    cd platform && FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow FACTORLAB_MAX_MEMORY=8GB \
      .venv/bin/factorlab run ../research/factor/<spec>.yaml --output-dir <tmp>/<name>

[2] 被复跑 spec（sha256 锁定）
779d0c6b224f0325fc5aaa4316af1f94326a0984535d8c32385cc4527cd0c4f8  /data/students/gaolei/stock/research/factor/size/small_cap.yaml
c1ffbcc0b17521f5e4e7e8c90c5e892be5108161f2786839a3db08a14e6faa8e  /data/students/gaolei/stock/research/factor/reversal_20d/smallcap.yaml
c351fede1dd70e90b6d5f6f9e879f10be5460140708b235faecf5d2c6c7198e4  /data/students/gaolei/stock/research/factor/size/extreme.yaml

[3] 退出码
small_cap EXIT=0 2026-09-16T21:34:57+08:00
smallcap EXIT=0 2026-09-16T21:35:51+08:00
extreme EXIT=0 2026-09-16T21:36:44+08:00

[4] before/after（同一 spec、同窗口 2023-01-03..2026-07-31、同 universe；small_cap 有脚本
    修复前的真实 summary 直读对比，另两个的 before 口径来自 R07 审计 08c/08-new-gap-hunt）

--- small_cap（direction=1；before = runs/platform/small_cap/summary.json 12:31 旧产物）---
  panel_rows       4419466 -> 4419466  (同一产物口径，未变)
  signal_null_ratio 1.0 -> 0.0099
  n_weeks           0 -> 182
  coverage.valid_rows 0 -> 917661
  ic.mean/t          nan/nan -> 0.0230/1.583

--- reversal_20d_smallcap（direction=-1；before：无 runs 产物，R07 判定「10 spec 引用 circ_mv」且 08c 对照实验 null=1.0）---
  signal_null_ratio 0.0559 (valid 875924/934236)
  n_weeks           174
  ic.mean/t         -0.0367/-2.887（direction=-1：负 IC 与声明方向一致）

--- small_cap_extreme（direction=1；before 同上）---
  signal_null_ratio 0.0099 (valid 917661/934236)
  n_weeks           182
  ic.mean/t         -0.0227/-2.396（信号含掩码零值，IC 可算）

[5] R07 审计对照（before 证据路径）
  - governance/evidence/reviews/r07-2026-09-16-gap-audit/evidence/data-audit/08c-idx-circmv-impact.txt
    （对照实验 signal=idx_ret+log(circ_mv)：signal_null_ratio 1.0 n_weeks 0）
  - governance/evidence/reviews/r07-2026-09-16-gap-audit/evidence/data-audit/08-new-gap-hunt.txt
    （specs 用 circ_mv: 10）
  - governance/evidence/verification/R24/17-r07-fixes/data-i4/（circ_mv 重灌前后）

[6] 结论
  3/3 spec signal_null_ratio 从 1.0 降到 <=0.056，n_weeks 0 -> 174..182，IC 均可算；
  small_cap 为同产物口径的严格 before/after（panel_rows 4,419,466 不变）。
  残余 null（0.99%-5.59%）来自源 float_shares 缺失（1,251,010 行，R24 已记），非派生缺陷。

[7] 落盘清单
总用量 644
drwxrwxr-x 2 gaolei gaolei   4096 9月  16 21:37 .
drwxrwxr-x 3 gaolei gaolei   4096 9月  16 21:37 ..
-rw-rw-r-- 1 gaolei gaolei    126 9月  16 21:37 exit_codes.txt
-rw-rw-r-- 1 gaolei gaolei   2854 9月  16 21:37 README.txt
-rw-rw-r-- 1 gaolei gaolei   1574 9月  16 21:37 run-reversal_20d_smallcap.log
-rw-rw-r-- 1 gaolei gaolei   1744 9月  16 21:37 run-small_cap_extreme.log
-rw-rw-r-- 1 gaolei gaolei   1557 9月  16 21:37 run-small_cap.log
-rw-rw-r-- 1 gaolei gaolei    395 9月  16 21:37 spec_sha256.txt
-rw-rw-r-- 1 gaolei gaolei 201561 9月  16 21:37 summary-reversal_20d_smallcap.json
-rw-rw-r-- 1 gaolei gaolei 209958 9月  16 21:37 summary-small_cap_extreme.json
-rw-rw-r-- 1 gaolei gaolei 208404 9月  16 21:37 summary-small_cap.json

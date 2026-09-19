"""R36-CI-I1 证据插桩：cash bridge 破坏时打印精确差值。

用途：证明 `POLARS_MAX_THREADS=8` 下的失败是**浮点求和顺序噪声**（diff ≈ 1e-10），
而非账实不符——根因是 `ExecutionArtifact.__post_init__` 用**精确相等**校验 bridge。

用法（在 platform/ 下）：
  POLARS_MAX_THREADS=8 PYTHONPATH=<本目录> \
    .venv/bin/python -m pytest -p probe_diff <测试> -q -s
"""
import factorlab.core.domain.backtest as bt

_orig = bt.ExecutionArtifact.__post_init__


def _spy(self):
    delta = (self.fills.frame["effective_cash_delta"].sum()
             if self.fills.frame.height else 0.0)
    bad = self.post_state.cash != self.pre_state.cash + delta
    if bad:
        print(f"[PROBE-BAD] pre={self.pre_state.cash!r} post={self.post_state.cash!r} "
              f"delta={delta!r}({type(delta).__name__}) "
              f"pre+delta={self.pre_state.cash + delta!r} "
              f"post-diff={self.post_state.cash - (self.pre_state.cash + delta)!r}")
    return _orig(self)


bt.ExecutionArtifact.__post_init__ = _spy

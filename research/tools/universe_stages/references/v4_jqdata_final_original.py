from jqdata import *
import pandas as pd
import numpy as np


# ============================================================
# V4-FILE
#
# 核心：
#
# Base
#   ↓
# Fast
#   ↓
# Eligible
#   ↓
# V4 Score
#   ↓
# Top300 / Top100
#
#
# V4 Score：
#
# 30% 小市值
# 20% 低 activity_compress
# 10% vol_compress 接近 0.88
# 10% range60 接近 0.33
# 10% bottom_stability 接近 0.52
#  5% bottom_rebound 接近 0.075
#  5% start_distance 接近 -0.025
# 10% 最近60日没有创新低
#
#
# 注意：
#
# down_decay
# stress
# drawdown
# rs_change
# efficiency
#
# 仍然记录，但不参与V4最终排名。
#
#
# 输出：
#
# v4_top300.csv
# v4_layer_eval.csv
# v4_stock_labels.csv
# v4_factor_ic.csv
# v4_scan_summary.csv
# v4_research_summary.csv
#
#
# 日志极简：
#
# SCAN
# EVAL_DONE
# ERROR
#
# ============================================================


# ============================================================
# 初始化
# ============================================================

def initialize(context):

    set_benchmark('000905.XSHG')

    set_option(
        'use_real_price',
        True
    )

    set_option(
        'avoid_future_data',
        True
    )

    log.set_level(
        'order',
        'error'
    )


    # ========================================================
    # Base基本面池
    # ========================================================

    # 市值单位：亿元
    g.min_cap = 20
    g.max_cap = 150

    # 至少上市3年
    g.min_list_days = 365 * 3

    # 最大资产负债率
    g.max_debt_ratio = 0.75

    # 最近20日中位成交额
    g.min_median_money_20 = 15000000.0


    # ========================================================
    # Fast
    # ========================================================

    g.fast_history_count = 90
    g.fast_min_history = 61


    # ========================================================
    # Detail
    # ========================================================

    g.history_count = 520
    g.min_history_count = 480

    g.batch_size = 300


    # ========================================================
    # 原来的硬过滤
    #
    # 保持V3.4不变
    # ========================================================

    g.min_r20 = -0.25
    g.max_r20 = 0.15

    g.min_r60 = -0.45
    g.max_r60 = 0.25

    g.max_bottom_rebound = 0.25


    # ========================================================
    # 第一层输出数量
    # ========================================================

    g.pool_size = 300


    # ========================================================
    # 避免回测起点附近重复扫描
    # ========================================================

    g.min_scan_gap = 15
    g.last_scan_date = None


    # ========================================================
    # V4目标参数
    # ========================================================

    # 波动压缩理想中心
    g.target_vol = 0.88

    # 60日振幅理想中心
    g.target_range60 = 0.33

    # 120日结构理想中心
    g.target_bottom = 0.52

    # 当前距离20日低点
    g.target_rebound = 0.075

    # 启动距离
    #
    # V3.4数据显示：
    # -4% ~ 0%相对更好
    g.target_start = -0.025


    # ========================================================
    # V4权重
    # ========================================================

    g.w_smallcap = 0.30

    g.w_activity = 0.20

    g.w_vol = 0.10

    g.w_range = 0.10

    g.w_bottom = 0.10

    g.w_rebound = 0.05

    g.w_start = 0.05

    g.w_no_new_low = 0.10


    # ========================================================
    # 历史快照
    # ========================================================

    g.snapshots = []


    # ========================================================
    # V4 IC研究字段
    # ========================================================

    g.ic_factors = [

        # 原始变量

        'market_cap',

        'activity_compress',

        'vol_compress',

        'range60',

        'bottom_stability',

        'bottom_rebound',

        'start_distance',

        'no_new_low',


        # V4横截面得分

        's_smallcap',

        's_activity',

        's_vol',

        's_range',

        's_bottom',

        's_rebound',

        's_start',

        's_no_new_low',


        # 最终分数

        'score',


        # 保留旧研究变量

        'drawdown',

        'stress',

        'down_decay',

        'low_break_eff',

        'turnover_health',

        'rs_change',

        'efficiency',

        'clv20',

        'r20',

        'r60',

        'r120'
    ]


    # ========================================================
    # 输出文件
    # ========================================================

    g.top_file = (
        'v4_top300.csv'
    )

    g.layer_file = (
        'v4_layer_eval.csv'
    )

    g.stock_file = (
        'v4_stock_labels.csv'
    )

    g.ic_file = (
        'v4_factor_ic.csv'
    )

    g.scan_file = (
        'v4_scan_summary.csv'
    )

    g.research_file = (
        'v4_research_summary.csv'
    )


    init_output_files()


    # ========================================================
    # 日频回测
    # ========================================================

    run_monthly(
        scan_market,
        1,
        time='open'
    )


# ============================================================
# 文件初始化
# ============================================================

def init_output_files():

    # ========================================================
    # 当前Top300
    # ========================================================

    safe_write_file(

        g.top_file,

        (
            'scan_date,'
            'factor_date,'
            'rank,'
            'code,'
            'score,'

            'market_cap,'
            'price,'

            'no_new_low,'

            's_smallcap,'
            's_activity,'
            's_vol,'
            's_range,'
            's_bottom,'
            's_rebound,'
            's_start,'
            's_no_new_low,'

            'activity_compress,'
            'vol_compress,'
            'range60,'
            'bottom_stability,'
            'bottom_rebound,'
            'start_distance,'

            'drawdown,'
            'stress,'
            'down_decay,'
            'low_break_eff,'

            'turnover_health,'
            'rs_change,'
            'efficiency,'
            'clv20,'

            'r20,'
            'r60,'
            'r120\n'
        ),

        append=False
    )


    # ========================================================
    # 分层收益
    # ========================================================

    safe_write_file(

        g.layer_file,

        (
            'scan_date,'
            'factor_date,'
            'horizon,'
            'end_date,'
            'group,'
            'n,'

            'mean_return,'
            'median_return,'
            'win_rate,'

            'hit10,'
            'hit20,'
            'hit30,'

            'avg_stock_mdd,'
            'portfolio_mdd\n'
        ),

        append=False
    )


    # ========================================================
    # Eligible全部股票未来标签
    # ========================================================

    safe_write_file(

        g.stock_file,

        (
            'scan_date,'
            'factor_date,'

            'code,'
            'rank,'

            'in_top100,'
            'in_top300,'

            'score,'

            'market_cap,'
            'price,'

            'no_new_low,'

            's_smallcap,'
            's_activity,'
            's_vol,'
            's_range,'
            's_bottom,'
            's_rebound,'
            's_start,'
            's_no_new_low,'

            'activity_compress,'
            'vol_compress,'
            'range60,'
            'bottom_stability,'
            'bottom_rebound,'
            'start_distance,'

            'drawdown,'
            'stress,'
            'down_decay,'
            'low_break_eff,'
            'turnover_health,'
            'rs_change,'
            'efficiency,'
            'clv20,'

            'r20,'
            'r60,'
            'r120,'

            'fwd20,'
            'fwd60,'
            'fwd120,'

            'mdd20,'
            'mdd60,'
            'mdd120\n'
        ),

        append=False
    )


    # ========================================================
    # IC
    # ========================================================

    safe_write_file(

        g.ic_file,

        (
            'scan_date,'
            'factor_date,'
            'horizon,'
            'factor,'
            'n,'
            'pearson_ic,'
            'rank_ic\n'
        ),

        append=False
    )


    # ========================================================
    # 月度扫描摘要
    # ========================================================

    safe_write_file(

        g.scan_file,

        (
            'scan_date,'
            'factor_date,'

            'base_count,'
            'fast_count,'
            'eligible_count,'
            'top300_count,'

            'fast_ratio,'
            'eligible_ratio,'

            'score_mean,'

            'market_cap_mean,'
            'activity_compress_mean,'
            'vol_compress_mean,'
            'range60_mean,'
            'bottom_stability_mean,'
            'bottom_rebound_mean,'
            'start_distance_mean,'
            'no_new_low_ratio\n'
        ),

        append=False
    )


    # ========================================================
    # 最方便人工查看
    # ========================================================

    safe_write_file(

        g.research_file,

        (
            'scan_date,'
            'factor_date,'

            'n_base,'
            'n_fast,'
            'n_eligible,'

            'h20_base,'
            'h20_fast,'
            'h20_eligible,'
            'h20_top300,'
            'h20_top100,'

            'h60_base,'
            'h60_fast,'
            'h60_eligible,'
            'h60_top300,'
            'h60_top100,'

            'h120_base,'
            'h120_fast,'
            'h120_eligible,'
            'h120_top300,'
            'h120_top100,'

            'h20_t300_excess,'
            'h20_t100_excess,'

            'h60_t300_excess,'
            'h60_t100_excess,'

            'h120_t300_excess,'
            'h120_t100_excess\n'
        ),

        append=False
    )


# ============================================================
# 文件工具
# ============================================================

def safe_write_file(
        path,
        content,
        append=True
):

    try:

        write_file(
            path,
            content,
            append=append
        )

        return True

    except Exception:

        log.info(
            'ERROR WRITE {}'.format(
                path
            )
        )

        return False


def csv_value(x):

    try:

        if pd.isna(x):

            return ''

    except:

        pass

    return str(x)


# ============================================================
# 通用工具
# ============================================================

def safe_float(x):

    try:

        x = float(x)

        if np.isfinite(x):

            return x

    except:

        pass

    return np.nan


def safe_div(a, b):

    try:

        a = float(a)
        b = float(b)

        if (
            not np.isfinite(a)
            or
            not np.isfinite(b)
            or
            abs(b) < 1e-12
        ):

            return np.nan

        return a / b

    except:

        return np.nan


def chunks(lst, n):

    for i in range(
        0,
        len(lst),
        n
    ):

        yield lst[
            i:i+n
        ]


# ============================================================
# 扫描间隔
# ============================================================

def scan_gap_too_short(context):

    if g.last_scan_date is None:

        return False, None


    days = list(

        get_trade_days(

            start_date=g.last_scan_date,

            end_date=context.current_dt.date()
        )
    )


    gap = max(
        0,
        len(days) - 1
    )


    return (
        gap < g.min_scan_gap,
        gap
    )


# ============================================================
# Base股票池
# ============================================================

def get_base_pool(context):

    date = context.previous_date


    q = query(

        valuation.code,

        valuation.market_cap,

        valuation.pe_ratio,

        income.operating_revenue,

        balance.total_assets,

        balance.total_liability

    ).filter(

        valuation.market_cap
        >=
        g.min_cap,

        valuation.market_cap
        <=
        g.max_cap,

        valuation.pe_ratio
        >
        0,

        income.operating_revenue
        >
        0,

        balance.total_assets
        >
        0
    )


    df = get_fundamentals(
        q,
        date=date
    )


    if (
        df is None
        or
        len(df) == 0
    ):

        return [], pd.DataFrame()


    # ========================================================
    # 资产负债率
    # ========================================================

    df[
        'debt_ratio'
    ] = (

        df[
            'total_liability'
        ]

        /

        df[
            'total_assets'
        ]
    )


    df = df.replace(
        [
            np.inf,
            -np.inf
        ],
        np.nan
    )


    df = df.dropna(

        subset=[

            'code',

            'market_cap',

            'pe_ratio',

            'operating_revenue',

            'total_assets',

            'total_liability',

            'debt_ratio'
        ]
    )


    df = df[

        df[
            'debt_ratio'
        ]

        <=

        g.max_debt_ratio
    ]


    # ========================================================
    # 上市时间
    # ========================================================

    securities = get_all_securities(

        ['stock'],

        date=date
    )


    securities[
        'start_date'
    ] = pd.to_datetime(

        securities[
            'start_date'
        ]
    )


    cutoff = (

        pd.Timestamp(date)

        -

        pd.Timedelta(
            days=g.min_list_days
        )
    )


    old_stocks = securities[

        securities[
            'start_date'
        ]

        <=

        cutoff

    ].index


    df = df[

        df[
            'code'
        ].isin(
            old_stocks
        )
    ]


    stocks = df[
        'code'
    ].tolist()


    if len(stocks) == 0:

        return [], df


    # ========================================================
    # ST
    # ========================================================

    st = get_extras(

        'is_st',

        stocks,

        end_date=date,

        count=1,

        df=True
    )


    if (
        st is not None
        and
        len(st) > 0
    ):

        status = st.iloc[-1]


        stocks = [

            code

            for code in stocks

            if not bool(

                status.get(
                    code,
                    False
                )
            )
        ]


    df = df[

        df[
            'code'
        ].isin(
            stocks
        )
    ]


    return stocks, df


# ============================================================
# Fast预筛
# ============================================================

def fast_pre_filter(
        context,
        stocks
):

    survivors = []


    if len(stocks) == 0:

        return survivors


    for batch in chunks(
        stocks,
        g.batch_size
    ):

        data = get_price(

            batch,

            end_date=context.previous_date,

            count=g.fast_history_count,

            frequency='daily',

            fields=[
                'close',
                'money'
            ],

            skip_paused=False,

            fq='pre',

            panel=False
        )


        if (
            data is None
            or
            len(data) == 0
        ):

            continue


        if 'code' not in data.columns:

            data = data.reset_index()


        if 'code' not in data.columns:

            continue


        for (
            code,
            df
        ) in data.groupby(
            'code'
        ):

            df = df.sort_values(
                'time'
            )


            df[
                'close'
            ] = pd.to_numeric(

                df[
                    'close'
                ],

                errors='coerce'
            )


            df[
                'money'
            ] = pd.to_numeric(

                df[
                    'money'
                ],

                errors='coerce'
            )


            df = df.dropna(
                subset=[
                    'close',
                    'money'
                ]
            )


            # 去除无成交停牌日

            df = df[
                df[
                    'money'
                ] > 0
            ]


            if (
                len(df)
                <
                g.fast_min_history
            ):

                continue


            close = df[
                'close'
            ].values.astype(float)


            money = df[
                'money'
            ].values.astype(float)


            if len(close) < 61:

                continue


            r20 = (

                close[-1]
                /
                close[-21]
                -
                1.0
            )


            r60 = (

                close[-1]
                /
                close[-61]
                -
                1.0
            )


            median_money_20 = np.median(
                money[
                    -20:
                ]
            )


            if (
                median_money_20
                <
                g.min_median_money_20
            ):

                continue


            if r20 < g.min_r20:
                continue

            if r20 > g.max_r20:
                continue

            if r60 < g.min_r60:
                continue

            if r60 > g.max_r60:
                continue


            survivors.append(
                code
            )


    return survivors


# ============================================================
# 基准指数
# ============================================================

def get_index_close(context):

    data = get_price(

        '000905.XSHG',

        end_date=context.previous_date,

        count=g.history_count,

        frequency='daily',

        fields=[
            'close'
        ],

        skip_paused=False,

        fq='pre'
    )


    if (
        data is None
        or
        len(data) == 0
    ):

        return None


    return data[
        'close'
    ].dropna()


# ============================================================
# 下跌冲击
# ============================================================

def calc_down_impact(
        ret,
        activity
):

    mask = (

        (ret < 0)

        &

        np.isfinite(
            ret
        )

        &

        np.isfinite(
            activity
        )

        &

        (activity > 0)
    )


    if mask.sum() < 5:

        return np.nan


    denominator = activity[
        mask
    ].sum()


    if denominator <= 0:

        return np.nan


    return (

        np.abs(
            ret[
                mask
            ]
        ).sum()

        /

        denominator
    )


# ============================================================
# 上涨冲击
# ============================================================

def calc_up_impact(
        ret,
        activity
):

    mask = (

        (ret > 0)

        &

        np.isfinite(
            ret
        )

        &

        np.isfinite(
            activity
        )

        &

        (activity > 0)
    )


    if mask.sum() < 5:

        return np.nan


    denominator = activity[
        mask
    ].sum()


    if denominator <= 0:

        return np.nan


    return (

        ret[
            mask
        ].sum()

        /

        denominator
    )


# ============================================================
# 单股票详细因子
# ============================================================

def calc_factor(
        df,
        index_close
):

    df = df.copy()


    df = df.sort_values(
        'time'
    )


    for col in [

        'close',
        'high',
        'low',
        'money'

    ]:

        df[
            col
        ] = pd.to_numeric(

            df[
                col
            ],

            errors='coerce'
        )


    df = df.dropna(

        subset=[
            'close',
            'high',
            'low',
            'money'
        ]
    )


    df = df[
        df[
            'money'
        ] > 0
    ]


    if (
        len(df)
        <
        g.min_history_count
    ):

        return None


    df = df.tail(
        g.history_count
    )


    close = df[
        'close'
    ].values.astype(float)


    high = df[
        'high'
    ].values.astype(float)


    low = df[
        'low'
    ].values.astype(float)


    money = df[
        'money'
    ].values.astype(float)


    if len(close) < g.min_history_count:

        return None


    # ========================================================
    # 日收益
    # ========================================================

    ret = np.empty(
        len(close)
    )


    ret[:] = np.nan


    ret[
        1:
    ] = (

        close[
            1:
        ]

        /

        close[
            :-1
        ]

        -

        1.0
    )


    # ========================================================
    # 自身历史成交活跃度
    # ========================================================

    positive_money = money[
        -250:
    ][

        money[
            -250:
        ] > 0
    ]


    if len(
        positive_money
    ) < 100:

        return None


    base_money = np.median(
        positive_money
    )


    if (
        not np.isfinite(
            base_money
        )
        or
        base_money <= 0
    ):

        return None


    activity = (

        money

        /

        base_money
    )


    activity = np.clip(
        activity,
        0,
        5
    )


    # ========================================================
    # 收益
    # ========================================================

    r20 = (

        close[-1]

        /

        close[-21]

        -

        1.0
    )


    r60 = (

        close[-1]

        /

        close[-61]

        -

        1.0
    )


    r120 = (

        close[-1]

        /

        close[-121]

        -

        1.0
    )


    # ========================================================
    # 硬过滤
    # ========================================================

    if r20 < g.min_r20:
        return None

    if r20 > g.max_r20:
        return None

    if r60 < g.min_r60:
        return None

    if r60 > g.max_r60:
        return None


    median_money_20 = np.median(
        money[
            -20:
        ]
    )


    if (
        median_money_20
        <
        g.min_median_money_20
    ):

        return None


    # ========================================================
    # Drawdown
    # ========================================================

    high500 = np.max(
        high[
            -500:
        ]
    )


    if high500 <= 0:

        return None


    drawdown = (

        1.0

        -

        close[-1]

        /

        high500
    )


    # ========================================================
    # Stress
    # ========================================================

    stress_ret = ret[
        -250:-40
    ]


    stress_act = activity[
        -250:-40
    ]


    stress_mask = (

        (stress_ret < 0)

        &

        np.isfinite(
            stress_ret
        )

        &

        np.isfinite(
            stress_act
        )
    )


    if stress_mask.sum() < 20:

        stress = np.nan


    else:

        stress = np.mean(

            np.abs(

                stress_ret[
                    stress_mask
                ]
            )

            *

            stress_act[
                stress_mask
            ]
        )


    # ========================================================
    # Down Decay
    #
    # V4不参与最终排名
    # ========================================================

    old_di = calc_down_impact(

        ret[
            -160:-40
        ],

        activity[
            -160:-40
        ]
    )


    recent_di = calc_down_impact(

        ret[
            -40:
        ],

        activity[
            -40:
        ]
    )


    down_decay = safe_div(
        old_di,
        recent_di
    )


    if np.isfinite(
        down_decay
    ):

        down_decay = min(
            down_decay,
            4.0
        )


    # ========================================================
    # Low Break
    # ========================================================

    old_low = np.min(
        low[
            -180:-60
        ]
    )


    new_low = np.min(
        low[
            -60:
        ]
    )


    if old_low <= 0:

        low_break_eff = np.nan
        no_new_low = np.nan


    else:

        downside_extension = max(

            0.0,

            (
                old_low
                -
                new_low
            )

            /

            old_low
        )


        # V4真正使用的是这个状态变量

        if downside_extension <= 1e-12:

            no_new_low = 1.0

        else:

            no_new_low = 0.0


        recent60_ret = ret[
            -60:
        ]


        recent60_act = activity[
            -60:
        ]


        down_mask = (

            (recent60_ret < 0)

            &

            np.isfinite(
                recent60_ret
            )

            &

            np.isfinite(
                recent60_act
            )
        )


        down_activity = recent60_act[
            down_mask
        ].sum()


        if down_activity <= 0:

            low_break_eff = np.nan


        else:

            low_break_eff = (

                downside_extension

                /

                down_activity
            )


    # ========================================================
    # Activity Compression
    # ========================================================

    activity_recent = np.mean(
        activity[
            -20:
        ]
    )


    activity_previous = np.mean(
        activity[
            -120:-20
        ]
    )


    activity_compress = safe_div(

        activity_recent,

        activity_previous
    )


    # ========================================================
    # Turnover Health
    #
    # 仅保留硬过滤和研究
    # ========================================================

    turnover_health = safe_div(

        np.mean(
            money[
                -20:
            ]
        ),

        np.mean(
            money[
                -120:-20
            ]
        )
    )


    if (
        not np.isfinite(
            turnover_health
        )
        or
        turnover_health < 0.30
    ):

        return None


    # ========================================================
    # Volatility Compression
    # ========================================================

    rv20 = np.nanstd(
        ret[
            -20:
        ]
    )


    rv_old = np.nanstd(
        ret[
            -120:-20
        ]
    )


    vol_compress = safe_div(
        rv20,
        rv_old
    )


    # ========================================================
    # Bottom Stability 120
    # ========================================================

    low120 = np.min(
        low[
            -120:
        ]
    )


    high120 = np.max(
        high[
            -120:
        ]
    )


    bottom_stability = safe_div(

        high120 - low120,

        low120
    )


    # ========================================================
    # Range60
    # ========================================================

    low60 = np.min(
        low[
            -60:
        ]
    )


    high60 = np.max(
        high[
            -60:
        ]
    )


    range60 = safe_div(

        high60 - low60,

        low60
    )


    # ========================================================
    # Bottom Rebound
    # ========================================================

    low20 = np.min(
        low[
            -20:
        ]
    )


    bottom_rebound = safe_div(
        close[-1],
        low20
    )


    if np.isfinite(
        bottom_rebound
    ):

        bottom_rebound -= 1.0


    if (
        np.isfinite(
            bottom_rebound
        )
        and
        bottom_rebound
        >
        g.max_bottom_rebound
    ):

        return None


    # ========================================================
    # Start Distance
    # ========================================================

    start_distance = (

        0.60
        *
        r20

        +

        0.40
        *
        r60
    )


    # ========================================================
    # RS Change
    #
    # 仅研究
    # ========================================================

    rs_change = np.nan


    if (

        index_close is not None

        and

        len(
            index_close
        ) >= 61

    ):

        idx = index_close.values.astype(
            float
        )


        idx20 = (

            idx[-1]
            /
            idx[-21]
            -
            1.0
        )


        idx60 = (

            idx[-1]
            /
            idx[-61]
            -
            1.0
        )


        rs20 = (
            r20
            -
            idx20
        )


        rs60 = (
            r60
            -
            idx60
        )


        rs_change = (

            rs20

            -

            rs60
            /
            3.0
        )


    # ========================================================
    # Efficiency
    #
    # 仅研究
    # ========================================================

    up_imp = calc_up_impact(

        ret[
            -60:
        ],

        activity[
            -60:
        ]
    )


    down_imp = calc_down_impact(

        ret[
            -60:
        ],

        activity[
            -60:
        ]
    )


    efficiency = safe_div(
        up_imp,
        down_imp
    )


    if np.isfinite(
        efficiency
    ):

        efficiency = min(
            efficiency,
            3.0
        )


    # ========================================================
    # CLV20
    # ========================================================

    range20_arr = (

        high[
            -20:
        ]

        -

        low[
            -20:
        ]
    )


    valid = (
        range20_arr > 0
    )


    if valid.sum() >= 10:

        clv20 = np.mean(

            (

                close[
                    -20:
                ][
                    valid
                ]

                -

                low[
                    -20:
                ][
                    valid
                ]

            )

            /

            range20_arr[
                valid
            ]
        )


    else:

        clv20 = np.nan


    return {

        'price':
            close[-1],

        'median_money_20':
            median_money_20,

        'r20':
            r20,

        'r60':
            r60,

        'r120':
            r120,

        'drawdown':
            drawdown,

        'stress':
            stress,

        'down_decay':
            down_decay,

        'low_break_eff':
            low_break_eff,

        'no_new_low':
            no_new_low,

        'activity_compress':
            activity_compress,

        'turnover_health':
            turnover_health,

        'vol_compress':
            vol_compress,

        'bottom_stability':
            bottom_stability,

        'range60':
            range60,

        'bottom_rebound':
            bottom_rebound,

        'start_distance':
            start_distance,

        'rs_change':
            rs_change,

        'efficiency':
            efficiency,

        'clv20':
            clv20
    }


# ============================================================
# Detail全市场计算
# ============================================================

def calculate_market(
        context,
        stocks,
        fundamentals
):

    if len(stocks) == 0:

        return pd.DataFrame()


    index_close = get_index_close(
        context
    )


    if index_close is None:

        return pd.DataFrame()


    fund_map = fundamentals.set_index(
        'code'
    )


    result = []


    for batch in chunks(
        stocks,
        g.batch_size
    ):

        data = get_price(

            batch,

            end_date=context.previous_date,

            count=g.history_count,

            frequency='daily',

            fields=[
                'close',
                'high',
                'low',
                'money'
            ],

            skip_paused=False,

            fq='pre',

            panel=False
        )


        if (
            data is None
            or
            len(data) == 0
        ):

            continue


        if 'code' not in data.columns:

            data = data.reset_index()


        if 'code' not in data.columns:

            continue


        for (
            code,
            stock_df
        ) in data.groupby(
            'code'
        ):

            if code not in fund_map.index:

                continue


            factor = calc_factor(

                stock_df,

                index_close
            )


            if factor is None:

                continue


            f = fund_map.loc[
                code
            ]


            row = {

                'code':
                    code,

                'market_cap':
                    safe_float(
                        f[
                            'market_cap'
                        ]
                    ),

                'pe_ratio':
                    safe_float(
                        f[
                            'pe_ratio'
                        ]
                    ),

                'debt_ratio':
                    safe_float(
                        f[
                            'debt_ratio'
                        ]
                    )
            }


            row.update(
                factor
            )


            result.append(
                row
            )


    if len(result) == 0:

        return pd.DataFrame()


    return pd.DataFrame(
        result
    )


# ============================================================
# V4评分
# ============================================================

def score_factor_v4(df):

    df = df.copy()


    df = df.replace(
        [
            np.inf,
            -np.inf
        ],
        np.nan
    )


    # ========================================================
    # 重要：
    #
    # 为了保持V3.4已经验证过的Eligible定义，
    # 这里仍然要求旧因子完整。
    #
    # 但它们不参与V4排名。
    # ========================================================

    eligible_need = [

        'market_cap',

        'drawdown',
        'stress',

        'down_decay',
        'low_break_eff',

        'no_new_low',

        'activity_compress',
        'turnover_health',
        'vol_compress',

        'bottom_stability',
        'range60',

        'bottom_rebound',
        'start_distance',

        'rs_change',
        'efficiency'
    ]


    df = df.dropna(
        subset=eligible_need
    )


    if len(df) == 0:

        return df


    # ========================================================
    # 1 小市值
    #
    # 越小越好
    # ========================================================

    df[
        's_smallcap'
    ] = (

        -df[
            'market_cap'
        ]

    ).rank(
        pct=True
    )


    # ========================================================
    # 2 Activity Compression
    #
    # 越低越好
    # ========================================================

    df[
        's_activity'
    ] = (

        -df[
            'activity_compress'
        ]

    ).rank(
        pct=True
    )


    # ========================================================
    # 3 Vol Compression
    #
    # 倒U型
    #
    # 中心约0.88
    # ========================================================

    df[
        's_vol'
    ] = (

        -abs(

            df[
                'vol_compress'
            ]

            -

            g.target_vol
        )

    ).rank(
        pct=True
    )


    # ========================================================
    # 4 Range60
    #
    # 中心约33%
    # ========================================================

    df[
        's_range'
    ] = (

        -abs(

            df[
                'range60'
            ]

            -

            g.target_range60
        )

    ).rank(
        pct=True
    )


    # ========================================================
    # 5 Bottom Stability
    #
    # 中心约52%
    # ========================================================

    df[
        's_bottom'
    ] = (

        -abs(

            df[
                'bottom_stability'
            ]

            -

            g.target_bottom
        )

    ).rank(
        pct=True
    )


    # ========================================================
    # 6 Bottom Rebound
    #
    # 中心约7.5%
    # ========================================================

    df[
        's_rebound'
    ] = (

        -abs(

            df[
                'bottom_rebound'
            ]

            -

            g.target_rebound
        )

    ).rank(
        pct=True
    )


    # ========================================================
    # 7 Start Distance
    #
    # 中心约 -2.5%
    # ========================================================

    df[
        's_start'
    ] = (

        -abs(

            df[
                'start_distance'
            ]

            -

            g.target_start
        )

    ).rank(
        pct=True
    )


    # ========================================================
    # 8 最近60日不再创新低
    #
    # 二元因子
    # ========================================================

    df[
        's_no_new_low'
    ] = df[
        'no_new_low'
    ]


    # ========================================================
    # 最终V4 Score
    # ========================================================

    df[
        'score'
    ] = (

        100.0

        *

        (

            g.w_smallcap
            *
            df[
                's_smallcap'
            ]

            +

            g.w_activity
            *
            df[
                's_activity'
            ]

            +

            g.w_vol
            *
            df[
                's_vol'
            ]

            +

            g.w_range
            *
            df[
                's_range'
            ]

            +

            g.w_bottom
            *
            df[
                's_bottom'
            ]

            +

            g.w_rebound
            *
            df[
                's_rebound'
            ]

            +

            g.w_start
            *
            df[
                's_start'
            ]

            +

            g.w_no_new_low
            *
            df[
                's_no_new_low'
            ]
        )
    )


    return df.sort_values(

        'score',

        ascending=False
    )


# ============================================================
# 当前Top300直接写文件
# ============================================================

def write_top300(
        context,
        pool
):

    if pool.empty:

        return


    df = pool.copy()


    df[
        'rank'
    ] = np.arange(
        1,
        len(df) + 1
    )


    df.insert(
        0,
        'factor_date',
        str(
            context.previous_date
        )
    )


    df.insert(
        0,
        'scan_date',
        str(
            context.current_dt.date()
        )
    )


    cols = [

        'scan_date',
        'factor_date',

        'rank',
        'code',

        'score',

        'market_cap',
        'price',

        'no_new_low',

        's_smallcap',
        's_activity',
        's_vol',
        's_range',
        's_bottom',
        's_rebound',
        's_start',
        's_no_new_low',

        'activity_compress',
        'vol_compress',
        'range60',
        'bottom_stability',
        'bottom_rebound',
        'start_distance',

        'drawdown',
        'stress',
        'down_decay',
        'low_break_eff',

        'turnover_health',
        'rs_change',
        'efficiency',
        'clv20',

        'r20',
        'r60',
        'r120'
    ]


    content = df[
        cols
    ].to_csv(
        index=False,
        header=False
    )


    safe_write_file(

        g.top_file,

        content,

        append=True
    )


# ============================================================
# 保存研究快照
# ============================================================

def save_snapshot(
        context,
        base_codes,
        fast_codes,
        ranked
):

    ranked = ranked.copy()


    ranked[
        'rank'
    ] = np.arange(
        1,
        len(ranked) + 1
    )


    factor_cols = [

        'code',
        'rank',

        'score',

        'market_cap',
        'price',

        'no_new_low',

        's_smallcap',
        's_activity',
        's_vol',
        's_range',
        's_bottom',
        's_rebound',
        's_start',
        's_no_new_low',

        'activity_compress',
        'vol_compress',
        'range60',
        'bottom_stability',
        'bottom_rebound',
        'start_distance',

        'drawdown',
        'stress',
        'down_decay',
        'low_break_eff',

        'turnover_health',
        'rs_change',
        'efficiency',
        'clv20',

        'r20',
        'r60',
        'r120'
    ]


    snapshot = {

        'scan_date':
            context.current_dt.date(),

        'factor_date':
            context.previous_date,

        'base_codes':
            list(
                base_codes
            ),

        'fast_codes':
            list(
                fast_codes
            ),

        'eligible_codes':
            ranked[
                'code'
            ].tolist(),

        'top300_codes':
            ranked[
                'code'
            ].head(
                300
            ).tolist(),

        'top100_codes':
            ranked[
                'code'
            ].head(
                100
            ).tolist(),

        'eligible_factors':
            ranked[
                factor_cols
            ].copy(),

        'completed':
            False
    }


    g.snapshots.append(
        snapshot
    )


# ============================================================
# 获取未来120日价格
# ============================================================

def fetch_close_matrix(
        codes,
        start_date,
        end_date
):

    frames = []


    for batch in chunks(
        codes,
        g.batch_size
    ):

        data = get_price(

            batch,

            start_date=start_date,

            end_date=end_date,

            frequency='daily',

            fields=[
                'close'
            ],

            skip_paused=False,

            fq='pre',

            panel=False
        )


        if (
            data is None
            or
            len(data) == 0
        ):

            continue


        if 'code' not in data.columns:

            data = data.reset_index()


        if 'code' not in data.columns:

            continue


        frames.append(
            data
        )


    if len(frames) == 0:

        return pd.DataFrame()


    df = pd.concat(

        frames,

        ignore_index=True
    )


    df[
        'close'
    ] = pd.to_numeric(

        df[
            'close'
        ],

        errors='coerce'
    )


    df = df.dropna(
        subset=[
            'close'
        ]
    )


    if len(df) == 0:

        return pd.DataFrame()


    matrix = df.pivot_table(

        index='time',

        columns='code',

        values='close',

        aggfunc='last'
    )


    matrix = matrix.sort_index()


    matrix.index = pd.to_datetime(
        matrix.index
    ).date


    matrix = matrix.ffill()


    return matrix


# ============================================================
# 某一层组合统计
# ============================================================

def calc_group_stats(
        matrix,
        codes,
        start_date,
        end_date
):

    if (
        start_date not in matrix.index
        or
        end_date not in matrix.index
    ):

        return None


    valid_codes = [

        code

        for code in codes

        if code in matrix.columns
    ]


    if len(valid_codes) == 0:

        return None


    path = matrix[
        valid_codes
    ].loc[
        start_date:end_date
    ]


    start = path.loc[
        start_date
    ]


    end = path.loc[
        end_date
    ]


    valid = (

        start.notna()

        &

        end.notna()

        &

        (start > 0)
    )


    valid_codes = valid[
        valid
    ].index.tolist()


    if len(valid_codes) == 0:

        return None


    start = start[
        valid_codes
    ]


    norm = (

        path[
            valid_codes
        ]

        /

        start
    )


    returns = (

        norm.loc[
            end_date
        ]

        -

        1.0
    ).dropna()


    if len(returns) == 0:

        return None


    norm = norm[
        returns.index
    ]


    # ========================================================
    # 个股MDD
    # ========================================================

    running_max = norm.cummax()


    dd = (

        norm

        /

        running_max

        -

        1.0
    )


    stock_mdd = dd.min(
        axis=0
    )


    # ========================================================
    # 等权组合MDD
    # ========================================================

    portfolio = norm.mean(
        axis=1
    )


    portfolio_max = portfolio.cummax()


    portfolio_dd = (

        portfolio

        /

        portfolio_max

        -

        1.0
    )


    return {

        'n':
            len(
                returns
            ),

        'mean':
            returns.mean(),

        'median':
            returns.median(),

        'win_rate':
            (
                returns > 0
            ).mean(),

        'hit10':
            (
                returns > 0.10
            ).mean(),

        'hit20':
            (
                returns > 0.20
            ).mean(),

        'hit30':
            (
                returns > 0.30
            ).mean(),

        'avg_stock_mdd':
            stock_mdd.mean(),

        'portfolio_mdd':
            portfolio_dd.min()
    }


# ============================================================
# 股票级未来收益标签
# ============================================================

def calc_stock_labels(
        matrix,
        codes,
        factor_date,
        horizon_dates
):

    labels = pd.DataFrame({

        'code':
            codes
    })


    labels = labels.set_index(
        'code'
    )


    if factor_date not in matrix.index:

        return labels.reset_index()


    available = [

        code

        for code in codes

        if code in matrix.columns
    ]


    if len(available) == 0:

        return labels.reset_index()


    base = matrix.loc[
        factor_date,
        available
    ]


    valid = (

        base.notna()

        &

        (base > 0)
    )


    valid_codes = valid[
        valid
    ].index.tolist()


    if len(valid_codes) == 0:

        return labels.reset_index()


    base = base[
        valid_codes
    ]


    for horizon in [
        20,
        60,
        120
    ]:

        end_date = horizon_dates[
            horizon
        ]


        if end_date not in matrix.index:

            continue


        path = matrix[
            valid_codes
        ].loc[
            factor_date:end_date
        ]


        norm = (

            path

            /

            base
        )


        fwd = (

            norm.loc[
                end_date
            ]

            -

            1.0
        )


        running_max = norm.cummax()


        dd = (

            norm

            /

            running_max

            -

            1.0
        )


        mdd = dd.min(
            axis=0
        )


        labels.loc[
            valid_codes,
            'fwd{}'.format(
                horizon
            )
        ] = fwd


        labels.loc[
            valid_codes,
            'mdd{}'.format(
                horizon
            )
        ] = mdd


    return labels.reset_index()


# ============================================================
# IC
# ============================================================

def calc_ic(
        x,
        y
):

    temp = pd.DataFrame({

        'x':
            pd.to_numeric(
                x,
                errors='coerce'
            ),

        'y':
            pd.to_numeric(
                y,
                errors='coerce'
            )
    })


    temp = temp.replace(
        [
            np.inf,
            -np.inf
        ],
        np.nan
    ).dropna()


    n = len(temp)


    if n < 30:

        return (
            n,
            np.nan,
            np.nan
        )


    if temp[
        'x'
    ].nunique() < 3:

        return (
            n,
            np.nan,
            np.nan
        )


    if temp[
        'y'
    ].nunique() < 3:

        return (
            n,
            np.nan,
            np.nan
        )


    pearson_ic = temp[
        'x'
    ].corr(
        temp[
            'y'
        ]
    )


    rank_ic = (

        temp[
            'x'
        ].rank(
            method='average'
        )

        .corr(

            temp[
                'y'
            ].rank(
                method='average'
            )
        )
    )


    return (
        n,
        pearson_ic,
        rank_ic
    )


# ============================================================
# 批量写Layer结果
# ============================================================

def write_layer_results(
        snapshot,
        layer_rows
):

    lines = []


    for row in layer_rows:

        stats = row[
            'stats'
        ]


        values = [

            snapshot[
                'scan_date'
            ],

            snapshot[
                'factor_date'
            ],

            row[
                'horizon'
            ],

            row[
                'end_date'
            ],

            row[
                'group'
            ],

            stats[
                'n'
            ],

            stats[
                'mean'
            ],

            stats[
                'median'
            ],

            stats[
                'win_rate'
            ],

            stats[
                'hit10'
            ],

            stats[
                'hit20'
            ],

            stats[
                'hit30'
            ],

            stats[
                'avg_stock_mdd'
            ],

            stats[
                'portfolio_mdd'
            ]
        ]


        lines.append(

            ','.join(
                [
                    csv_value(v)
                    for v in values
                ]
            )

            +

            '\n'
        )


    if len(lines) > 0:

        safe_write_file(

            g.layer_file,

            ''.join(
                lines
            ),

            append=True
        )


# ============================================================
# 股票标签写文件
# ============================================================

def write_stock_labels(
        snapshot,
        labels
):

    factors = snapshot[
        'eligible_factors'
    ].copy()


    df = pd.merge(

        factors,

        labels,

        on='code',

        how='left'
    )


    df.insert(
        0,
        'factor_date',
        str(
            snapshot[
                'factor_date'
            ]
        )
    )


    df.insert(
        0,
        'scan_date',
        str(
            snapshot[
                'scan_date'
            ]
        )
    )


    df[
        'in_top100'
    ] = (

        df[
            'rank'
        ] <= 100

    ).astype(int)


    df[
        'in_top300'
    ] = (

        df[
            'rank'
        ] <= 300

    ).astype(int)


    cols = [

        'scan_date',
        'factor_date',

        'code',
        'rank',

        'in_top100',
        'in_top300',

        'score',

        'market_cap',
        'price',

        'no_new_low',

        's_smallcap',
        's_activity',
        's_vol',
        's_range',
        's_bottom',
        's_rebound',
        's_start',
        's_no_new_low',

        'activity_compress',
        'vol_compress',
        'range60',
        'bottom_stability',
        'bottom_rebound',
        'start_distance',

        'drawdown',
        'stress',
        'down_decay',
        'low_break_eff',
        'turnover_health',
        'rs_change',
        'efficiency',
        'clv20',

        'r20',
        'r60',
        'r120',

        'fwd20',
        'fwd60',
        'fwd120',

        'mdd20',
        'mdd60',
        'mdd120'
    ]


    for col in cols:

        if col not in df.columns:

            df[
                col
            ] = np.nan


    content = df[
        cols
    ].to_csv(
        index=False,
        header=False
    )


    safe_write_file(

        g.stock_file,

        content,

        append=True
    )


# ============================================================
# IC写文件
# ============================================================

def write_ic_results(
        snapshot,
        labels
):

    factors = snapshot[
        'eligible_factors'
    ].copy()


    df = pd.merge(

        factors,

        labels,

        on='code',

        how='inner'
    )


    lines = []


    for horizon in [
        20,
        60,
        120
    ]:

        target = (
            'fwd{}'.format(
                horizon
            )
        )


        if target not in df.columns:

            continue


        for factor in g.ic_factors:

            if factor not in df.columns:

                continue


            n, pearson, rank_ic = calc_ic(

                df[
                    factor
                ],

                df[
                    target
                ]
            )


            values = [

                snapshot[
                    'scan_date'
                ],

                snapshot[
                    'factor_date'
                ],

                horizon,

                factor,

                n,

                pearson,

                rank_ic
            ]


            lines.append(

                ','.join(
                    [
                        csv_value(v)
                        for v in values
                    ]
                )

                +

                '\n'
            )


    if len(lines) > 0:

        safe_write_file(

            g.ic_file,

            ''.join(
                lines
            ),

            append=True
        )


# ============================================================
# Research Summary
# ============================================================

def write_research_summary(
        snapshot,
        means
):

    def get_mean(
            horizon,
            group
    ):

        try:

            return means[
                horizon
            ][
                group
            ]

        except:

            return np.nan


    h20_e = get_mean(
        20,
        'Eligible'
    )

    h20_300 = get_mean(
        20,
        'Top300'
    )

    h20_100 = get_mean(
        20,
        'Top100'
    )


    h60_e = get_mean(
        60,
        'Eligible'
    )

    h60_300 = get_mean(
        60,
        'Top300'
    )

    h60_100 = get_mean(
        60,
        'Top100'
    )


    h120_e = get_mean(
        120,
        'Eligible'
    )

    h120_300 = get_mean(
        120,
        'Top300'
    )

    h120_100 = get_mean(
        120,
        'Top100'
    )


    values = [

        snapshot[
            'scan_date'
        ],

        snapshot[
            'factor_date'
        ],

        len(
            snapshot[
                'base_codes'
            ]
        ),

        len(
            snapshot[
                'fast_codes'
            ]
        ),

        len(
            snapshot[
                'eligible_codes'
            ]
        ),


        get_mean(
            20,
            'Base'
        ),

        get_mean(
            20,
            'Fast'
        ),

        h20_e,

        h20_300,

        h20_100,


        get_mean(
            60,
            'Base'
        ),

        get_mean(
            60,
            'Fast'
        ),

        h60_e,

        h60_300,

        h60_100,


        get_mean(
            120,
            'Base'
        ),

        get_mean(
            120,
            'Fast'
        ),

        h120_e,

        h120_300,

        h120_100,


        h20_300 - h20_e,

        h20_100 - h20_e,

        h60_300 - h60_e,

        h60_100 - h60_e,

        h120_300 - h120_e,

        h120_100 - h120_e
    ]


    line = (

        ','.join(
            [
                csv_value(v)
                for v in values
            ]
        )

        +

        '\n'
    )


    safe_write_file(

        g.research_file,

        line,

        append=True
    )


# ============================================================
# 评价成熟快照
# ============================================================

def evaluate_snapshot(
        snapshot,
        today
):

    factor_date = snapshot[
        'factor_date'
    ]


    trade_days = list(

        get_trade_days(

            start_date=factor_date,

            end_date=today
        )
    )


    if len(trade_days) <= 120:

        return False


    horizon_dates = {

        20:
            trade_days[
                20
            ],

        60:
            trade_days[
                60
            ],

        120:
            trade_days[
                120
            ]
    }


    end120 = horizon_dates[
        120
    ]


    # ========================================================
    # 一次读取Base未来120日
    # ========================================================

    matrix = fetch_close_matrix(

        snapshot[
            'base_codes'
        ],

        factor_date,

        end120
    )


    if matrix.empty:

        return False


    if factor_date not in matrix.index:

        return False


    groups = {

        'Base':
            snapshot[
                'base_codes'
            ],

        'Fast':
            snapshot[
                'fast_codes'
            ],

        'Eligible':
            snapshot[
                'eligible_codes'
            ],

        'Top300':
            snapshot[
                'top300_codes'
            ],

        'Top100':
            snapshot[
                'top100_codes'
            ]
    }


    layer_rows = []


    means = {

        20: {},
        60: {},
        120: {}
    }


    # ========================================================
    # 五层 × 三周期
    # ========================================================

    for horizon in [
        20,
        60,
        120
    ]:

        end_date = horizon_dates[
            horizon
        ]


        for (
            group_name,
            codes
        ) in groups.items():

            stats = calc_group_stats(

                matrix,

                codes,

                factor_date,

                end_date
            )


            if stats is None:

                continue


            means[
                horizon
            ][
                group_name
            ] = stats[
                'mean'
            ]


            layer_rows.append({

                'horizon':
                    horizon,

                'end_date':
                    end_date,

                'group':
                    group_name,

                'stats':
                    stats
            })


    labels = calc_stock_labels(

        matrix,

        snapshot[
            'eligible_codes'
        ],

        factor_date,

        horizon_dates
    )


    # ========================================================
    # 集中写文件
    # ========================================================

    write_layer_results(
        snapshot,
        layer_rows
    )


    write_stock_labels(
        snapshot,
        labels
    )


    write_ic_results(
        snapshot,
        labels
    )


    write_research_summary(
        snapshot,
        means
    )


    return True


# ============================================================
# 检查120日成熟快照
# ============================================================

def evaluate_snapshots(context):

    if len(
        g.snapshots
    ) == 0:

        return


    today = context.previous_date


    for snapshot in g.snapshots:

        if snapshot[
            'completed'
        ]:

            continue


        trade_days = list(

            get_trade_days(

                start_date=snapshot[
                    'factor_date'
                ],

                end_date=today
            )
        )


        if len(trade_days) <= 120:

            continue


        success = evaluate_snapshot(

            snapshot,

            today
        )


        if success:

            scan_date = snapshot[
                'scan_date'
            ]


            n = len(
                snapshot[
                    'eligible_codes'
                ]
            )


            snapshot[
                'completed'
            ] = True


            # =================================================
            # 释放内存
            # =================================================

            snapshot[
                'base_codes'
            ] = []


            snapshot[
                'fast_codes'
            ] = []


            snapshot[
                'eligible_codes'
            ] = []


            snapshot[
                'top300_codes'
            ] = []


            snapshot[
                'top100_codes'
            ] = []


            snapshot[
                'eligible_factors'
            ] = pd.DataFrame()


            log.info(

                'EVAL_DONE {} N={}'.format(

                    scan_date,

                    n
                )
            )


# ============================================================
# Pending
# ============================================================

def count_pending():

    n = 0


    for snapshot in g.snapshots:

        if not snapshot[
            'completed'
        ]:

            n += 1


    return n


# ============================================================
# 扫描摘要
# ============================================================

def write_scan_summary(
        context,
        base_count,
        fast_count,
        ranked,
        pool
):

    def avg(col):

        if col not in pool.columns:

            return np.nan


        return pool[
            col
        ].mean()


    values = [

        context.current_dt.date(),

        context.previous_date,

        base_count,

        fast_count,

        len(
            ranked
        ),

        len(
            pool
        ),

        safe_div(
            fast_count,
            base_count
        ),

        safe_div(
            len(ranked),
            base_count
        ),

        avg(
            'score'
        ),

        avg(
            'market_cap'
        ),

        avg(
            'activity_compress'
        ),

        avg(
            'vol_compress'
        ),

        avg(
            'range60'
        ),

        avg(
            'bottom_stability'
        ),

        avg(
            'bottom_rebound'
        ),

        avg(
            'start_distance'
        ),

        avg(
            'no_new_low'
        )
    ]


    line = (

        ','.join(
            [
                csv_value(v)
                for v in values
            ]
        )

        +

        '\n'
    )


    safe_write_file(

        g.scan_file,

        line,

        append=True
    )


# ============================================================
# 主扫描
# ============================================================

def scan_market(context):

    # ========================================================
    # 0 历史120日成熟信号先处理
    # ========================================================

    evaluate_snapshots(
        context
    )


    # ========================================================
    # 1 避免过近重复扫描
    # ========================================================

    too_short, gap = scan_gap_too_short(
        context
    )


    if too_short:

        return


    # ========================================================
    # 2 Base
    # ========================================================

    stocks, fundamentals = get_base_pool(
        context
    )


    base_count = len(
        stocks
    )


    if base_count == 0:

        log.info(
            'ERROR BASE {}'.format(
                context.current_dt.date()
            )
        )

        return


    # ========================================================
    # 3 Fast
    # ========================================================

    fast_stocks = fast_pre_filter(

        context,

        stocks
    )


    fast_count = len(
        fast_stocks
    )


    if fast_count == 0:

        log.info(
            'ERROR FAST {}'.format(
                context.current_dt.date()
            )
        )

        return


    # ========================================================
    # 4 Detail
    # ========================================================

    factor_df = calculate_market(

        context,

        fast_stocks,

        fundamentals
    )


    if factor_df.empty:

        log.info(
            'ERROR DETAIL {}'.format(
                context.current_dt.date()
            )
        )

        return


    # ========================================================
    # 5 保持原Eligible定义 + V4重新排名
    # ========================================================

    ranked = score_factor_v4(
        factor_df
    )


    if ranked.empty:

        log.info(
            'ERROR V4 {}'.format(
                context.current_dt.date()
            )
        )

        return


    # ========================================================
    # 6 Top300
    # ========================================================

    pool = ranked.head(
        g.pool_size
    ).copy()


    # ========================================================
    # 7 当前Top300马上写文件
    # ========================================================

    write_top300(
        context,
        pool
    )


    # ========================================================
    # 8 保存未来研究快照
    # ========================================================

    save_snapshot(

        context,

        stocks,

        fast_stocks,

        ranked
    )


    # ========================================================
    # 9 扫描摘要
    # ========================================================

    write_scan_summary(

        context,

        base_count,

        fast_count,

        ranked,

        pool
    )


    # ========================================================
    # 10 第一层候选
    # ========================================================

    g.first_layer_pool = pool[
        'code'
    ].tolist()


    g.first_layer_df = pool


    # ========================================================
    # 11 更新日期
    # ========================================================

    g.last_scan_date = (
        context.current_dt.date()
    )


    # ========================================================
    # 极简日志
    # ========================================================

    log.info(

        'SCAN {} B={} F={} E={} T={} P={}'.format(

            context.current_dt.date(),

            base_count,

            fast_count,

            len(
                ranked
            ),

            len(
                pool
            ),

            count_pending()
        )
    )
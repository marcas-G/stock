import pandas as pd


def audit_ranked_pool(ranked, labels, label_col='tail_120_50', ks=(50,100,200,300), universe_labels=None):
    x=ranked.merge(labels[['scan_date','code',label_col]],on=['scan_date','code'],how='left')
    rows=[]
    for sd,g in x.groupby('scan_date'):
        g=g.sort_values('rank')
        if universe_labels is not None:
            u=universe_labels[universe_labels['scan_date']==sd]
            total_tail=int(u[label_col].fillna(False).sum())
            base_rate=float(u[label_col].fillna(False).mean()) if len(u) else float('nan')
        else:
            total_tail=int(g[label_col].fillna(False).sum())
            base_rate=float(g[label_col].fillna(False).mean()) if len(g) else float('nan')
        for k in ks:
            ss=g.head(k); hit=int(ss[label_col].fillna(False).sum())
            precision=hit/len(ss) if len(ss) else float('nan')
            recall=hit/total_tail if total_tail else float('nan')
            lift=precision/base_rate if base_rate and base_rate>0 else float('nan')
            rows.append({'scan_date':sd,'k':k,'tail_hits':hit,'precision':precision,'recall':recall,'lift':lift,'base_rate':base_rate})
    return pd.DataFrame(rows)

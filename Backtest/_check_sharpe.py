import pandas as pd

df = pd.read_csv(r'C:\Users\ry092\Desktop\MIS 382N - Capstone\portfolio-tlh-optimizer\Backtest\full_results.csv')

high = df[df['Sharpe Ratio'].abs() > 3].copy()
print(f'Rows with |Sharpe| > 3: {len(high)} out of {len(df)}')

if len(high) > 0:
    cols = ['Category', 'Model Family', 'Portfolio', 'Market Condition', 'TLH Status',
            'CAGR', 'Volatility (Ann)', 'Sharpe Ratio', 'Final NAV']
    cols = [c for c in cols if c in high.columns]
    top = high[cols].sort_values('Sharpe Ratio', ascending=False).head(30)
    print(top.to_string(index=False))

print('\n--- Sharpe distribution ---')
print(df['Sharpe Ratio'].describe().to_string())
print(f'\n|Sharpe| > 5: {(df["Sharpe Ratio"].abs() > 5).sum()}')
print(f'|Sharpe| > 4: {(df["Sharpe Ratio"].abs() > 4).sum()}')
print(f'|Sharpe| > 3: {(df["Sharpe Ratio"].abs() > 3).sum()}')
print(f'|Sharpe| > 2: {(df["Sharpe Ratio"].abs() > 2).sum()}')
